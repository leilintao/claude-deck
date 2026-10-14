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

            if ":" in text:
                token, request_id = text.split(":", 1)
            else:
                token, request_id = text, ""

            if token == "ALLOW":
                decision = "allow"
            elif token == "DENY":
                decision = "deny"
            else:
                logging.warning("忽略未知 BLE payload: %r", text)
                return

            waiter = self.pending_requests.get(request_id)
            if not waiter:
                logging.warning("忽略过期/未匹配的 BLE 回复: %s", request_id)
                return

            waiter["response"] = decision
            waiter["event"].set()
        except Exception as e:
            logging.error(f"解析触控数据失败: {e}")

    async def send_prompt(self, payload):
        if not self.client or not self.is_connected:
            return False

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            await self.client.write_gatt_char(CHAR_UUID_RX, data, response=False)
            return True
        except Exception as e:
            logging.error(f"BLE 写入失败: {e}")
            return False

    async def send_status(self, status, desc=""):
        payload = {"type": "status", "status": status, "desc": desc}
        await self.send_prompt(payload)

    async def ask_user(self, title, desc, options=["允许 (Y)", "拒绝 (N)"], timeout=120):
        if not self.is_connected:
            logging.warning("副屏未连接，按离线策略返回: %s", OFFLINE_POLICY)
            return self.offline_decision()

        request_id = uuid.uuid4().hex[:16]
        waiter = {
            "event": asyncio.Event(),
            "response": None,
        }
        self.pending_requests[request_id] = waiter

        payload = {
            "type": "prompt",
            "id": request_id,
            "title": title[:30],
            "desc": desc[:100],
            "options": options,
        }

        try:
            sent = await self.send_prompt(payload)
            if not sent:
                logging.warning("副屏推送失败，按离线策略返回: %s", OFFLINE_POLICY)
                return self.offline_decision()

            logging.info(f"已推送问题至副屏: [{request_id}] [{title}] {desc}")

            try:
                await asyncio.wait_for(waiter["event"].wait(), timeout=timeout)
                decision = waiter["response"] or self.offline_decision()
                logging.info(f"副屏用户做出选择: {decision}")
                return decision
            except asyncio.TimeoutError:
                logging.warning("副屏响应超时 (%ss)，自动拒绝并回写状态", timeout)
                await self.send_status("expired", "请求已超时，终端已自动拒绝")
                return "deny"
        finally:
            self.pending_requests.pop(request_id, None)


bridge = DeckBridge()


class HookHTTPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/prompt":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                title = data.get("title", "Claude 权限请求")
                desc = data.get("desc", "是否允许执行此操作？")
                options = data.get("options", ["允许 (Y)", "拒绝 (N)"])

                future = asyncio.run_coroutine_threadsafe(
                    bridge.ask_user(title, desc, options),
                    bridge.loop,
                )
                decision = future.result(timeout=130)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"decision": decision}).encode("utf-8"))
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
