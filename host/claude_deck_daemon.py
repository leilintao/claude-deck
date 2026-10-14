import asyncio
import json
import logging
import os
import sys
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import threading

from bleak import BleakClient, BleakScanner

SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHAR_UUID_RX = "beb5483e-36e1-4688-b7f5-ea07361b26a8"  # Mac writes to this
CHAR_UUID_TX = "beb5483f-36e1-4688-b7f5-ea07361b26a8"  # ESP32 notifies to this

# Availability vs. safety: if the deck is offline, deny by default.
# Set CLAUDE_DECK_OFFLINE_POLICY=allow to restore the old fail-open behavior.
OFFLINE_POLICY = os.environ.get("CLAUDE_DECK_OFFLINE_POLICY", "deny").strip().lower()
if OFFLINE_POLICY not in {"allow", "deny"}:
    OFFLINE_POLICY = "deny"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def split_ble_payload(data, chunk_size):
    """Split a JSON payload into BLE-write-sized chunks (in order)."""
    if chunk_size <= 0:
        return [data]
    return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]


class DeckBridge:
    def __init__(self):
        self.client = None
        self.is_connected = False
        self.pending_requests = {}
        self.loop = None

    def offline_decision(self):
        return OFFLINE_POLICY

    async def connect_loop(self):
        while True:
            try:
                logging.info("正在搜索 Claude-Deck 蓝牙设备...")
                device = await BleakScanner.find_device_by_filter(
                    lambda d, ad: d.name and "Claude-Deck" in d.name,
                    timeout=6.0,
                )
                if not device:
                    logging.info("未发现 Claude-Deck，3 秒后重试...")
                    await asyncio.sleep(3)
                    continue

                logging.info(f"找到设备 {device.name} ({device.address})，正在连接...")
                async with BleakClient(device, disconnected_callback=self.on_disconnect) as client:
                    self.client = client
                    self.is_connected = True
                    logging.info("✅ 蓝牙已连接至 Claude-Deck 副屏！")
                    await client.start_notify(CHAR_UUID_TX, self.on_notification)

                    # Non-interactive welcome state. The first real request should
                    # not compete with a fake prompt.
                    await self.send_prompt(
                        {
                            "type": "status",
                            "status": "idle",
                            "desc": "副屏已就绪，正在监听 Claude Code 编程指令...",
                        }
                    )

                    while self.is_connected and client.is_connected:
                        await asyncio.sleep(1)

            except Exception as e:
                logging.warning(f"BLE 连接断开或异常: {e}，5 秒后尝试重连...")
                self.is_connected = False
                await asyncio.sleep(5)

    def on_disconnect(self, client):
        logging.warning("⚠️ Claude-Deck 蓝牙连接已断开！")
        self.is_connected = False

    def on_notification(self, sender, data):
        try:
            text = data.decode("utf-8", errors="ignore").strip().upper()
            logging.info(f"👉 收到副屏触控反馈: {text}")

            if text.startswith("SELECT:"):
                parts = text.split(":")
                if len(parts) != 3:
                    logging.warning("忽略格式错误的选项回复: %r", text)
                    return
                token = "SELECT"
                request_id = parts[1]
                decision = {"kind": "select", "index": int(parts[2])}
            else:
                token, request_id = (text.split(":", 1) if ":" in text else (text, ""))
                if token == "ALLOW":
                    decision = "allow"
                elif token == "DENY":
                    decision = "deny"
                else:
                    logging.warning("忽略未知 BLE payload: %r", text)
                    return

            # request_id 必须转为小写匹配 uuid.hex
            request_id = request_id.lower()
            waiter = self.pending_requests.get(request_id)
            if not waiter:
                logging.warning("忽略过期/未匹配的 BLE 回复: %s", request_id)
                return

            waiter["response"] = decision
            waiter["event"].set()
        except Exception as e:
            logging.error(f"解析触控数据失败: {e}")

    def ble_chunk_size(self):
        # macOS silently truncates write-without-response payloads larger than
        # (ATT MTU - 3), and the firmware then drops the half-written JSON.
        # Split into chunks with headroom for ATT/GATT overhead instead.
        try:
            mtu = int(self.client.mtu_size)
        except Exception:
            mtu = 512
        return max(20, min(mtu - 8, 480))

    async def send_prompt(self, payload):
        if not self.client or not self.is_connected:
            return False

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        chunks = split_ble_payload(data, self.ble_chunk_size())
        try:
            for chunk in chunks:
                await self.client.write_gatt_char(CHAR_UUID_RX, chunk, response=False)
            logging.info(
                "BLE 推送成功: %d 字节 / %d 块 (块大小 %d)",
                len(data), len(chunks), self.ble_chunk_size(),
            )
            return True
        except Exception as e:
            logging.error(f"BLE 写入失败 ({len(data)} 字节 / {len(chunks)} 块): {e}")
            return False

    async def send_status(self, status, desc=""):
        payload = {"type": "status", "status": status, "desc": desc}
        await self.send_prompt(payload)

    async def ask_question(self, header, question, options, timeout=30):
        """Show one Claude question on the deck and return the selected index."""
        if not self.is_connected:
            logging.warning("副屏未连接，Claude 问题回退到终端原生 UI")
            return None

        if len(options) < 2:
            return None

        request_id = uuid.uuid4().hex[:16]
        waiter = {"event": asyncio.Event(), "response": None}
        self.pending_requests[request_id] = waiter

        payload = {
            "type": "question",
            "id": request_id,
            "header": header[:20],
            "question": question[:140],
            "options": options[:4],
        }

        try:
            sent = await self.send_prompt(payload)
            if not sent:
                logging.warning("副屏问题推送失败，回退到终端原生 UI")
                return None

            logging.info(f"已推送 Claude 问题至副屏: [{request_id}] {question}")
            try:
                await asyncio.wait_for(waiter["event"].wait(), timeout=timeout)
                response = waiter["response"]
                if isinstance(response, dict) and response.get("kind") == "select":
                    index = int(response.get("index", -1))
                    if 0 <= index < len(options):
                        logging.info(f"副屏用户选择选项: {index}")
                        return index
                return None
            except asyncio.TimeoutError:
                logging.warning("副屏问题响应超时 (%ss)，回退到终端原生 UI", timeout)
                await self.send_status("idle", "Claude 问题超时，请在终端继续回答")
                return None
        finally:
            self.pending_requests.pop(request_id, None)

    async def ask_user(self, title, desc, options=["允许 (Y)", "拒绝 (N)"], badge="", tag="", timeout=30):
        offline_reason = f"副屏离线，按 CLAUDE_DECK_OFFLINE_POLICY={OFFLINE_POLICY} 自动拒绝"
        if not self.is_connected:
            logging.warning("%s", offline_reason)
            return self.offline_decision(), offline_reason

        request_id = uuid.uuid4().hex[:16]
        waiter = {
            "event": asyncio.Event(),
            "response": None,
        }
        self.pending_requests[request_id] = waiter

        payload = {
            "type": "prompt",
            "id": request_id,
            "badge": badge,
            "tag": tag,
            "title": title[:30],
            "desc": desc[:100],
            "options": options,
        }

        try:
            sent = await self.send_prompt(payload)
            if not sent:
                reason = "副屏 BLE 推送失败，按安全策略自动拒绝"
                logging.warning(reason)
                return self.offline_decision(), reason

            logging.info(f"已推送问题至副屏: [{request_id}] [{badge or title}] {desc}")

            try:
                await asyncio.wait_for(waiter["event"].wait(), timeout=timeout)
                decision = waiter["response"] or self.offline_decision()
                reason = "副屏响应无效，按安全策略自动拒绝"
                if decision == "deny":
                    reason = "用户在 Claude-Deck 物理副屏上点击了拒绝"
                elif decision == "allow":
                    reason = "用户在 Claude-Deck 物理副屏上点击了允许"
                logging.info(f"副屏用户做出选择: {decision}")
                return decision, reason
            except asyncio.TimeoutError:
                reason = f"副屏响应超时 ({timeout}s)，按安全策略自动拒绝"
                logging.warning(reason)
                await self.send_status("expired", "请求已超时，终端已自动拒绝")
                return "deny", reason
        finally:
            self.pending_requests.pop(request_id, None)


bridge = DeckBridge()


class HookHTTPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/notify_question":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                header = data.get("header", "QUESTION")
                question = data.get("question", "")
                options = data.get("options", [])

                # 异步推送到 ESP32，不阻塞等待用户作答，两端同时显示
                payload = {
                    "type": "question",
                    "id": uuid.uuid4().hex[:16],
                    "header": header[:20],
                    "question": question[:140],
                    "options": options[:4],
                }
                logging.info(f"已异步推送 Claude 问题至副屏: {question}")
                asyncio.run_coroutine_threadsafe(
                    bridge.send_prompt(payload),
                    bridge.loop,
                )

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            except Exception as e:
                logging.exception("处理通知问题失败")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
        elif self.path == "/question":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                header = data.get("header", "QUESTION")
                question = data.get("question", "")
                options = data.get("options", [])

                future = asyncio.run_coroutine_threadsafe(
                    bridge.ask_question(header, question, options),
                    bridge.loop,
                )
                index = future.result(timeout=35)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "answered": index is not None,
                            "index": index,
                        }
                    ).encode("utf-8")
                )
            except Exception as e:
                logging.exception("处理 Claude 问题失败")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
        elif self.path == "/prompt":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                title = data.get("title", "Claude 权限请求")
                desc = data.get("desc", "是否允许执行此操作？")
                options = data.get("options", ["允许 (Y)", "拒绝 (N)"])
                badge = data.get("badge", "")
                tag = data.get("tag", "")

                future = asyncio.run_coroutine_threadsafe(
                    bridge.ask_user(title, desc, options, badge=badge, tag=tag),
                    bridge.loop,
                )
                decision, reason = future.result(timeout=35)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"decision": decision, "reason": reason}).encode("utf-8"))
            except Exception as e:
                logging.exception("处理 Hook 请求失败")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "connected": bridge.is_connected,
                        "offline_policy": OFFLINE_POLICY,
                    }
                ).encode("utf-8")
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # 静默常规 HTTP 日志
        pass


def run_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 19842), HookHTTPHandler)
    logging.info("🚀 本地 Hook 服务已在 http://127.0.0.1:19842 启动")
    server.serve_forever()


async def main():
    bridge.loop = asyncio.get_running_loop()
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()

    await bridge.connect_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Claude Deck 守护进程已退出。")
