import asyncio
import json
import logging
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from bleak import BleakClient, BleakScanner

SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHAR_UUID_RX = "beb5483e-36e1-4688-b7f5-ea07361b26a8" # Mac writes to this
CHAR_UUID_TX = "beb5483f-36e1-4688-b7f5-ea07361b26a8" # ESP32 notifies to this

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class DeckBridge:
    def __init__(self):
        self.client = None
        self.is_connected = False
        self.pending_response_event = asyncio.Event()
        self.last_response = None
        self.loop = None

    async def connect_loop(self):
        while True:
            try:
                logging.info("正在搜索 Claude-Deck 蓝牙设备...")
                device = await BleakScanner.find_device_by_filter(
                    lambda d, ad: d.name and "Claude-Deck" in d.name,
                    timeout=6.0
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

                    # 发送欢迎状态
                    welcome = {
                        "type": "prompt",
                        "title": "Claude Deck 在线",
                        "desc": "副屏已就绪，正在监听 Claude Code 编程指令...",
                        "options": ["测试 (Y)", "关闭 (N)"]
                    }
                    await self.send_prompt(welcome)

                    # 保持连接
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
            if "ALLOW" in text:
                self.last_response = {"decision": "allow"}
            elif "DENY" in text:
                self.last_response = {"decision": "deny"}
            else:
                self.last_response = {"decision": "allow"}
            self.pending_response_event.set()
        except Exception as e:
            logging.error(f"解析触控数据失败: {e}")

    async def send_prompt(self, payload):
        if not self.client or not self.is_connected:
            return False
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        await self.client.write_gatt_char(CHAR_UUID_RX, data, response=False)
        return True

    async def ask_user(self, title, desc, options=["允许 (Y)", "拒绝 (N)"], timeout=120):
        if not self.is_connected:
            logging.warning("副屏未连接，自动回退默认放行")
            return "allow"

        self.last_response = None
        self.pending_response_event.clear()

        payload = {
            "type": "prompt",
            "title": title[:30],
            "desc": desc[:100],
            "options": options
        }
        await self.send_prompt(payload)
        logging.info(f"已推送问题至副屏: [{title}] {desc}")

        try:
            await asyncio.wait_for(self.pending_response_event.wait(), timeout=timeout)
            if self.last_response:
                decision = self.last_response.get("decision", "allow")
                logging.info(f"副屏用户做出选择: {decision}")
                return decision
        except asyncio.TimeoutError:
            logging.warning("副屏响应超时 (120s)，自动拒绝")
            return "deny"

        return "allow"

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

                # 提交给 asyncio 任务并同步等待结果
                future = asyncio.run_coroutine_threadsafe(
                    bridge.ask_user(title, desc, options),
                    bridge.loop
                )
                decision = future.result(timeout=130)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"decision": decision}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
        elif self.path == "/notify":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                asyncio.run_coroutine_threadsafe(
                    bridge.send_prompt(data),
                    bridge.loop
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
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
            self.wfile.write(json.dumps({"connected": bridge.is_connected}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # 静默常规 HTTP 日志
        pass

def run_http_server():
    server = HTTPServer(("127.0.0.1", 19842), HookHTTPHandler)
    logging.info("🚀 本地 Hook 服务已在 http://127.0.0.1:19842 启动")
    server.serve_forever()

async def main():
    bridge.loop = asyncio.get_running_loop()
    # 启动后台 HTTP 服务器供 Claude Hook 调用
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()

    # 启动 BLE 蓝牙事件主循环
    await bridge.connect_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Claude Deck 守护进程已退出。")
