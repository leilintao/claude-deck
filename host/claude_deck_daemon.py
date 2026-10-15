import asyncio
import base64
import errno
import json
import logging
import os
import secrets
import shlex
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bleak import BleakClient, BleakScanner

try:
    from .deck_auth import load_device_id, load_token
except ImportError:
    from deck_auth import load_device_id, load_token

SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHAR_UUID_RX = "beb5483e-36e1-4688-b7f5-ea07361b26a8"  # Mac writes to this
CHAR_UUID_TX = "beb5483f-36e1-4688-b7f5-ea07361b26a8"  # ESP32 notifies to this

# Availability vs. safety: if the deck is offline, deny by default.
# Set CLAUDE_DECK_OFFLINE_POLICY=allow to restore the old fail-open behavior.
OFFLINE_POLICY = os.environ.get("CLAUDE_DECK_OFFLINE_POLICY", "deny").strip().lower()
if OFFLINE_POLICY not in {"allow", "deny"}:
    OFFLINE_POLICY = "deny"

try:
    DEFAULT_TIMEOUT = max(5, min(540, int(os.environ.get("CLAUDE_DECK_TIMEOUT", "120"))))
except ValueError:
    DEFAULT_TIMEOUT = 120

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
IPC_TOKEN = load_token(create=True)


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
        self.write_lock = asyncio.Lock()
        self.approval_lock = asyncio.Lock()

    def offline_decision(self):
        return OFFLINE_POLICY

    async def connect_loop(self):
        try:
            pinned_id = load_device_id()
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            logging.error("副屏尚未绑定：请先运行 host/enroll_deck.py (%s)", exc)
            return
        while True:
            try:
                logging.info("正在搜索 Claude-Deck 蓝牙设备...")
                device = await asyncio.wait_for(
                    BleakScanner.find_device_by_filter(
                        lambda d, ad: d.address == pinned_id and d.name and "Claude-Deck" in d.name,
                        timeout=6.0,
                    ),
                    timeout=12.0,
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
            finally:
                # A BLE client can become disconnected without invoking the
                # callback. Do not report a stale online state or leave a
                # physical approval waiting after the connection is gone.
                self.is_connected = False
                self.client = None
                self._wake_disconnected_requests()
            await asyncio.sleep(5)

    def on_disconnect(self, client):
        logging.warning("⚠️ Claude-Deck 蓝牙连接已断开！")
        self.is_connected = False
        if self.loop:
            self.loop.call_soon_threadsafe(self._wake_disconnected_requests)

    def _wake_disconnected_requests(self):
        for waiter in self.pending_requests.values():
            if not waiter["event"].is_set():
                waiter["response"] = None
                waiter["event"].set()

    def on_notification(self, sender, data):
        try:
            text = data.decode("utf-8", errors="ignore").strip().upper()
            logging.info(f"👉 收到副屏触控反馈: {text}")

            if text.startswith("SELECTM:"):
                parts = text.split(":", 2)
                if len(parts) != 3:
                    logging.warning("忽略格式错误的多选回复: %r", text)
                    return
                request_id = parts[1]
                indexes = [int(value) for value in parts[2].split(",") if value.strip().isdigit()]
                decision = {"kind": "select_multi", "indexes": indexes}
            elif text.startswith("SELECT:"):
                parts = text.split(":")
                if len(parts) != 3:
                    logging.warning("忽略格式错误的选项回复: %r", text)
                    return
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
            waiter = self.pending_requests.get(request_id) if request_id else None
            if not waiter:
                logging.warning("忽略无 ID 或未匹配的 BLE 回复: %s", request_id)
                return
            if waiter["kind"] != ("question" if isinstance(decision, dict) else "prompt"):
                logging.warning("忽略类型不匹配的 BLE 回复: %s", request_id)
                return

            waiter["response"] = decision
            waiter["event"].set()
        except Exception as e:
            logging.error(f"解析触控数据失败: {e}")

    async def answer_terminal_question(self, request_id, indexes):
        """Accept a choice from the companion terminal for a live deck question."""
        waiter = self.pending_requests.get(str(request_id).lower())
        if not waiter or waiter["kind"] != "question" or waiter["event"].is_set():
            return False
        if indexes == [-1]:
            waiter["response"] = "cancelled"
        else:
            clean_indexes = []
            for index in indexes:
                if isinstance(index, int) and index >= 0 and index not in clean_indexes:
                    clean_indexes.append(index)
            if not clean_indexes:
                return False
            waiter["response"] = {
                "kind": "select_multi" if waiter.get("multi_select") else "select",
                "indexes": clean_indexes,
                "index": clean_indexes[0],
            }
        waiter["event"].set()
        await self.send_status("idle", "已在 Mac 终端完成选择", force=True)
        return True

    def question_is_pending(self, request_id):
        waiter = self.pending_requests.get(str(request_id).lower())
        return bool(waiter and waiter["kind"] == "question" and not waiter["event"].is_set())

    async def answer_terminal_prompt(self, request_id, decision):
        """Accept an allow/deny choice from an in-process terminal UI."""
        waiter = self.pending_requests.get(str(request_id).lower())
        if not waiter or waiter["kind"] != "prompt" or waiter["event"].is_set():
            return False
        if decision not in {"allow", "deny"}:
            return False
        waiter["response"] = decision
        waiter["terminal_answer"] = True
        waiter["event"].set()
        await self.send_status(
            "idle",
            "已在 Mac 终端允许" if decision == "allow" else "已在 Mac 终端拒绝",
            force=True,
        )
        return True

    def launch_terminal_question(self, request_id, header, question, options, timeout, multi_select):
        """Open a small companion terminal that races the physical deck."""
        if sys.platform != "darwin" or os.environ.get("CLAUDE_DECK_TERMINAL_QUESTION", "1") == "0":
            return
        helper = Path(__file__).with_name("terminal_question.py")
        payload = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "header": header,
                    "question": question,
                    "options": options,
                    "timeout": timeout,
                    "multiSelect": multi_select,
                },
                ensure_ascii=False,
            ).encode("utf-8")
        ).decode("ascii")
        command = shlex.join(
            [sys.executable, str(helper), "--request-id", request_id, "--payload", payload]
        )
        try:
            subprocess.Popen(
                ["osascript", "-e", f"tell application \"Terminal\" to do script {json.dumps(command)}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            logging.exception("无法打开 Claude Deck 终端选择窗口")

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
        if len(data) > 3800:
            logging.error("BLE 帧超过固件容量: %d 字节", len(data))
            return False
        chunks = split_ble_payload(data, self.ble_chunk_size())
        async with self.write_lock:
            try:
                for chunk in chunks:
                    await self.client.write_gatt_char(CHAR_UUID_RX, chunk, response=True)
                logging.info("BLE 推送成功: %d 字节 / %d 块", len(data), len(chunks))
                return True
            except Exception as e:
                logging.error("BLE 写入失败 (%d 字节 / %d 块): %s", len(data), len(chunks), e)
                return False

    async def send_status(self, status, desc="", badge="", tag="", title="", request_key="", force=False):
        # A matching host completion cancels its own deck prompt without granting it.
        # Unrelated tools and sessions cannot clear a live prompt.
        if self.pending_requests and not force:
            matching = [
                waiter for waiter in self.pending_requests.values()
                if status == "idle" and request_key and waiter.get("request_key") == request_key
            ]
            if not matching:
                logging.info("忽略待审批期间的 %s 状态更新", status)
                return False
            for waiter in matching:
                waiter["response"] = "cancelled"
                waiter["event"].set()
        payload = {
            "type": "status",
            "status": status,
            "badge": badge[:24],
            "tag": tag[:20],
            "title": title[:48],
            "desc": desc[:220],
        }
        return await self.send_prompt(payload)

    async def ask_question(
        self,
        header,
        question,
        options,
        timeout=DEFAULT_TIMEOUT,
        multi_select=False,
        launch_terminal=True,
        request_id="",
    ):
        """Show one Claude question on the deck and return the selected index."""
        async with self.approval_lock:
            return await self._ask_question(
                header, question, options, timeout, multi_select, launch_terminal, request_id
            )

    async def _ask_question(
        self, header, question, options, timeout, multi_select, launch_terminal, request_id
    ):
        if not self.is_connected:
            logging.warning("副屏未连接，Claude 问题回退到终端原生 UI")
            return None

        if len(options) < 2:
            return None

        request_id = str(request_id or uuid.uuid4().hex[:16]).lower()
        if not request_id.isalnum() or request_id in self.pending_requests:
            logging.warning("无效或重复的问题请求 ID: %s", request_id)
            return None
        waiter = {"event": asyncio.Event(), "response": None, "kind": "question", "multi_select": multi_select}
        self.pending_requests[request_id] = waiter

        payload = {
            "type": "question",
            "id": request_id,
            "header": header[:20],
            "question": question[:200],
            "options": options[:4],
            "multiSelect": multi_select,
            "timeout": int(timeout),
        }

        try:
            sent = await self.send_prompt(payload)
            if not sent:
                logging.warning("副屏问题推送失败，回退到终端原生 UI")
                return None

            logging.info(f"已推送 Claude 问题至副屏: [{request_id}] {question}")
            if launch_terminal:
                self.launch_terminal_question(request_id, header, question, options, timeout, multi_select)
            try:
                await asyncio.wait_for(waiter["event"].wait(), timeout=timeout)
                response = waiter["response"]
                if isinstance(response, dict) and response.get("kind") == "select":
                    index = int(response.get("index", -1))
                    if 0 <= index < len(options):
                        logging.info(f"Claude Deck 问题已选择选项: {index}")
                        return index
                if isinstance(response, dict) and response.get("kind") == "select_multi" and multi_select:
                    indexes = [int(index) for index in response.get("indexes", [])]
                    if indexes and all(0 <= index < len(options) for index in indexes):
                        logging.info("Claude Deck 多选问题已选择选项: %s", indexes)
                        return indexes
                if response == "cancelled":
                    await self.send_status("idle", "已转回 Claude 原生输入", force=True)
                return None
            except asyncio.TimeoutError:
                logging.warning("副屏问题响应超时 (%ss)，回退到终端原生 UI", timeout)
                await self.send_status("idle", "Claude 问题超时，请在终端继续回答", force=True)
                return None
        finally:
            self.pending_requests.pop(request_id, None)

    async def ask_user(
        self,
        title,
        desc,
        options=["允许 (Y)", "拒绝 (N)"],
        badge="",
        tag="",
        timeout=DEFAULT_TIMEOUT,
        request_key="",
        request_id="",
    ):
        async with self.approval_lock:
            return await self._ask_user(title, desc, options, badge, tag, timeout, request_key, request_id)

    async def _ask_user(self, title, desc, options, badge, tag, timeout, request_key, request_id):
        offline_reason = f"副屏离线，按 CLAUDE_DECK_OFFLINE_POLICY={OFFLINE_POLICY} 自动拒绝"
        if not self.is_connected:
            logging.warning("%s", offline_reason)
            return self.offline_decision(), offline_reason

        request_id = str(request_id or uuid.uuid4().hex[:16]).lower()
        if not request_id.isalnum() or request_id in self.pending_requests:
            return "deny", "无效或重复的审批请求 ID"
        waiter = {
            "event": asyncio.Event(),
            "response": None,
            "kind": "prompt",
            "request_key": request_key,
        }
        self.pending_requests[request_id] = waiter

        payload = {
            "type": "prompt",
            "id": request_id,
            "badge": badge[:24],
            "tag": tag[:20],
            "title": title[:48],
            "desc": desc,
            "options": options[:2],
            "timeout": int(timeout),
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
                decision = waiter["response"]
                if decision == "cancelled":
                    return "deny", "此请求已在 Mac 主屏完成，副屏审批已取消"
                if decision not in {"allow", "deny"}:
                    decision = self.offline_decision()
                reason = "副屏响应无效，按安全策略自动拒绝"
                if decision == "deny":
                    reason = (
                        "用户在 Mac 终端拒绝了此操作"
                        if waiter.get("terminal_answer")
                        else "用户在 Claude-Deck 物理副屏上点击了拒绝"
                    )
                elif decision == "allow":
                    reason = (
                        "用户在 Mac 终端允许了此操作"
                        if waiter.get("terminal_answer")
                        else "用户在 Claude-Deck 物理副屏上点击了允许"
                    )
                logging.info(f"副屏用户做出选择: {decision}")
                return decision, reason
            except asyncio.TimeoutError:
                reason = f"副屏响应超时 ({timeout}s)，按安全策略自动拒绝"
                logging.warning(reason)
                await self.send_status("expired", "请求已超时，终端已自动拒绝", force=True)
                return "deny", reason
        finally:
            self.pending_requests.pop(request_id, None)


bridge = DeckBridge()


class HookHTTPHandler(BaseHTTPRequestHandler):
    def _authorized(self):
        token = self.headers.get("X-Claude-Deck-Token", "")
        if secrets.compare_digest(token, IPC_TOKEN):
            return True
        self.send_error(403, "Invalid deck IPC token")
        return False

    def do_GET(self):
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        if parsed.path != "/question_status":
            self.send_error(404)
            return
        request_id = parse_qs(parsed.query).get("id", [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"pending": bridge.question_is_pending(request_id)}).encode("utf-8"))

    def do_POST(self):
        if not self._authorized():
            return
        length = int(self.headers.get("Content-Length", 0))
        if length < 0 or length > 8192:
            self.send_error(413, "Deck IPC payload too large")
            return
        if self.path == "/question_answer":
            body = self.rfile.read(length).decode("utf-8")
            try:
                data = json.loads(body)
                request_id = str(data.get("id") or "")
                indexes = data.get("indexes")
                if indexes is None:
                    indexes = [int(data.get("index"))]
                if not isinstance(indexes, list) or not all(isinstance(index, int) for index in indexes):
                    raise ValueError("Invalid terminal question indexes")
                future = asyncio.run_coroutine_threadsafe(
                    bridge.answer_terminal_question(request_id, indexes), bridge.loop
                )
                if not future.result(timeout=2):
                    self.send_error(409, "Question is no longer pending")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"accepted": true}')
            except Exception:
                logging.exception("处理终端问题选择失败")
                self.send_error(400, "Invalid terminal question answer")
        elif self.path == "/prompt_answer":
            body = self.rfile.read(length).decode("utf-8")
            try:
                data = json.loads(body)
                request_id = str(data.get("id") or "")
                decision = str(data.get("decision") or "").lower()
                future = asyncio.run_coroutine_threadsafe(
                    bridge.answer_terminal_prompt(request_id, decision), bridge.loop
                )
                if not future.result(timeout=2):
                    self.send_error(409, "Prompt is no longer pending")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"accepted": true}')
            except Exception:
                logging.exception("处理终端审批选择失败")
                self.send_error(400, "Invalid terminal prompt answer")
        elif self.path == "/notify_question":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                header = data.get("header", "QUESTION")
                question = data.get("question", "")
                options = data.get("options", [])

                payload = {
                    "type": "question",
                    "id": uuid.uuid4().hex[:16],
                    "header": header[:20],
                    "question": question[:200],
                    "options": options[:4],
                    "timeout": int(data.get("timeout", DEFAULT_TIMEOUT)),
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
        elif self.path == "/notify_prompt":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                title = data.get("title", "Claude 正在执行工具")
                desc = data.get("desc", "")
                badge = data.get("badge", "[ACTION]")
                tag = data.get("tag", "RUNNING")

                # 非阻塞通知统一渲染为 thinking 执行态卡片，避免渲染无响应的“假按钮”
                logging.info(f"已异步推送执行状态至副屏: [{badge}] {desc}")
                asyncio.run_coroutine_threadsafe(
                    bridge.send_status("thinking", desc=desc, badge=badge, tag=tag, title=title),
                    bridge.loop,
                )

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            except Exception as e:
                logging.exception("处理通知提示失败")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
        elif self.path == "/status":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                status = data.get("status", "idle")
                desc = data.get("desc", "")
                badge = data.get("badge", "")
                tag = data.get("tag", "")
                title = data.get("title", "")
                request_key = data.get("request_key", "")

                asyncio.run_coroutine_threadsafe(
                    bridge.send_status(status, desc=desc, badge=badge, tag=tag, title=title, request_key=request_key),
                    bridge.loop,
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            except Exception as e:
                logging.exception("处理状态更新失败")
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
                req_timeout = int(data.get("timeout", DEFAULT_TIMEOUT))
                multi_select = bool(data.get("multiSelect"))
                launch_terminal = bool(data.get("launchTerminal", True))
                request_id = str(data.get("id") or "")

                future = asyncio.run_coroutine_threadsafe(
                    bridge.ask_question(
                        header,
                        question,
                        options,
                        timeout=req_timeout,
                        multi_select=multi_select,
                        launch_terminal=launch_terminal,
                        request_id=request_id,
                    ),
                    bridge.loop,
                )
                index = future.result(timeout=req_timeout + 5)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "answered": index is not None,
                            "index": index[0] if isinstance(index, list) and index else index,
                            "indexes": index if isinstance(index, list) else ([index] if index is not None else []),
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
                req_timeout = int(data.get("timeout", DEFAULT_TIMEOUT))
                request_key = data.get("request_key", "")
                request_id = str(data.get("id") or "")

                future = asyncio.run_coroutine_threadsafe(
                    bridge.ask_user(
                        title,
                        desc,
                        options,
                        badge=badge,
                        tag=tag,
                        timeout=req_timeout,
                        request_key=request_key,
                        request_id=request_id,
                    ),
                    bridge.loop,
                )
                decision, reason = future.result(timeout=req_timeout + 5)

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
                        "timeout": DEFAULT_TIMEOUT,
                    }
                ).encode("utf-8")
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # 静默常规 HTTP 日志
        pass


def run_http_server(server):
    logging.info("🚀 本地 Hook 服务已在 http://127.0.0.1:19842 启动")
    server.serve_forever()


async def main():
    bridge.loop = asyncio.get_running_loop()
    # Bind before starting BLE work. If another instance owns the port, exit
    # rather than leaving a half-running process that competes for the deck.
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 19842), HookHTTPHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise SystemExit("Claude Deck 服务已在运行，不能重复启动。") from None
        raise
    http_thread = threading.Thread(target=run_http_server, args=(server,), daemon=True)
    http_thread.start()
    try:
        await bridge.connect_loop()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Claude Deck 守护进程已退出。")
