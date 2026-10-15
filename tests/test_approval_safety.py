"""Regression tests for approval decisions and transport boundaries."""

import asyncio
import contextlib
import io
import json
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock


try:
    import bleak  # noqa: F401
except ImportError:
    fake_bleak = types.ModuleType("bleak")
    fake_bleak.BleakClient = object
    fake_bleak.BleakScanner = object
    sys.modules["bleak"] = fake_bleak

from host import claude_hook
from host.claude_deck_daemon import DeckBridge, HookHTTPHandler


class FakeClient:
    mtu_size = 40

    def __init__(self):
        self.writes = []

    async def write_gatt_char(self, _uuid, chunk, response=False):
        assert response is True
        self.writes.append(chunk)
        await asyncio.sleep(0)


class ApprovalSafetyTests(unittest.TestCase):
    def test_idle_never_approves_a_pending_request(self):
        async def run():
            bridge = DeckBridge()
            bridge.client = FakeClient()
            bridge.is_connected = True
            event = asyncio.Event()
            bridge.pending_requests["request"] = {"event": event, "response": None, "kind": "prompt"}
            self.assertFalse(await bridge.send_status("idle"))
            self.assertFalse(event.is_set())
            self.assertIsNone(bridge.pending_requests["request"]["response"])
            self.assertEqual(bridge.client.writes, [])
        asyncio.run(run())

    def test_matching_host_completion_cancels_without_allowing(self):
        async def run():
            bridge = DeckBridge()
            bridge.client = FakeClient()
            bridge.is_connected = True
            event = asyncio.Event()
            bridge.pending_requests["request"] = {
                "event": event, "response": None, "kind": "prompt", "request_key": "session-tool-input"
            }
            self.assertTrue(await bridge.send_status("idle", request_key="session-tool-input"))
            self.assertTrue(event.is_set())
            self.assertEqual(bridge.pending_requests["request"]["response"], "cancelled")
        asyncio.run(run())

    def test_concurrent_ble_frames_do_not_interleave(self):
        async def run():
            bridge = DeckBridge()
            bridge.client = FakeClient()
            bridge.is_connected = True
            await asyncio.gather(
                bridge.send_prompt({"type": "prompt", "id": "a", "desc": "A" * 80}),
                bridge.send_prompt({"type": "prompt", "id": "b", "desc": "B" * 80}),
            )
            text = b"".join(bridge.client.writes).decode()
            decoder = json.JSONDecoder()
            frames = []
            while text:
                frame, end = decoder.raw_decode(text)
                frames.append(frame)
                text = text[end:]
            self.assertEqual({frame["id"] for frame in frames}, {"a", "b"})
        asyncio.run(run())

    def test_notification_requires_matching_id_and_type(self):
        async def run():
            bridge = DeckBridge()
            event = asyncio.Event()
            bridge.pending_requests["abc"] = {"event": event, "response": None, "kind": "prompt"}
            bridge.on_notification(None, b"ALLOW")
            bridge.on_notification(None, b"SELECT:abc:0")
            bridge.on_notification(None, b"ALLOW:other")
            self.assertFalse(event.is_set())
            bridge.on_notification(None, b"ALLOW:abc")
            self.assertTrue(event.is_set())
            self.assertEqual(bridge.pending_requests["abc"]["response"], "allow")
        asyncio.run(run())

    def test_terminal_question_answer_races_the_deck(self):
        async def run():
            bridge = DeckBridge()
            event = asyncio.Event()
            bridge.pending_requests["question"] = {"event": event, "response": None, "kind": "question"}
            self.assertTrue(bridge.question_is_pending("question"))
            self.assertTrue(await bridge.answer_terminal_question("question", [1]))
            self.assertTrue(event.is_set())
            self.assertEqual(
                bridge.pending_requests["question"]["response"],
                {"kind": "select", "indexes": [1], "index": 1},
            )
            self.assertFalse(await bridge.answer_terminal_question("question", 0))
        asyncio.run(run())

    def test_terminal_prompt_answer_races_the_deck(self):
        async def run():
            bridge = DeckBridge()
            event = asyncio.Event()
            bridge.pending_requests["approval"] = {
                "event": event,
                "response": None,
                "kind": "prompt",
            }
            self.assertTrue(await bridge.answer_terminal_prompt("approval", "allow"))
            self.assertTrue(event.is_set())
            self.assertEqual(bridge.pending_requests["approval"]["response"], "allow")
            self.assertFalse(await bridge.answer_terminal_prompt("approval", "deny"))
        asyncio.run(run())

    def test_invalid_terminal_prompt_answer_is_ignored(self):
        async def run():
            bridge = DeckBridge()
            event = asyncio.Event()
            bridge.pending_requests["approval"] = {
                "event": event,
                "response": None,
                "kind": "prompt",
            }
            self.assertFalse(await bridge.answer_terminal_prompt("approval", "maybe"))
            self.assertFalse(event.is_set())
        asyncio.run(run())

    def test_http_post_requires_token(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), HookHTTPHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/status",
                data=b'{"status":"idle"}', method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_question_returns_structured_answer(self):
        question = {
            "header": "Choice",
            "question": "Which option?",
            "options": [{"label": "A", "description": "First"}, {"label": "B", "description": "Second"}],
            "multiSelect": False,
        }
        data = {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_input": {"questions": [question]}}
        output = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(data))), \
             mock.patch.object(claude_hook, "ask_deck_question", return_value=(1, question["options"][1], question["question"])), \
             contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            claude_hook.main()
        decision = json.loads(output.getvalue())["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "allow")
        self.assertEqual(decision["updatedInput"]["answers"], {"Which option?": "B"})

    def test_oversize_approval_requires_terminal_review(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            claude_hook.request_physical_approval("PreToolUse", "[BASH]", "CRITICAL", "Command", "x" * 901)
        self.assertEqual(json.loads(output.getvalue())["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_default_mode_gates_other_tools_and_keeps_full_command(self):
        self.assertEqual(claude_hook.INTERCEPT_MODE, "pre_tool_use")
        command = "echo " + "a" * 170 + " && rm -rf /important"
        self.assertIn("rm -rf /important", claude_hook.format_prompt("PreToolUse", "Bash", {"command": command})[3])
        data = {"hook_event_name": "PreToolUse", "tool_name": "SomeNewWriteTool", "tool_input": {"path": "/tmp/file"}}
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(data))), \
             mock.patch.object(claude_hook, "request_physical_approval", side_effect=SystemExit(0)) as approve, \
             self.assertRaises(SystemExit):
            claude_hook.main()
        approve.assert_called_once()

    def test_multiselect_question_uses_the_deck(self):
        question = {"question": "Pick any", "multiSelect": True,
                    "options": [{"label": "A"}, {"label": "B"}]}
        self.assertTrue(claude_hook.question_fits_deck(question))

    def test_multi_selection_is_preserved(self):
        async def run():
            bridge = DeckBridge()
            event = asyncio.Event()
            bridge.pending_requests["multi"] = {
                "event": event,
                "response": None,
                "kind": "question",
                "multi_select": True,
            }
            self.assertTrue(await bridge.answer_terminal_question("multi", [0, 2]))
            self.assertEqual(
                bridge.pending_requests["multi"]["response"],
                {"kind": "select_multi", "indexes": [0, 2], "index": 0},
            )
        asyncio.run(run())

    def test_long_option_descriptions_still_use_the_deck(self):
        question = {
            "header": "今天聊什么",
            "question": "今天最想聊哪条线?",
            "options": [
                {"label": "舅舅五金出海", "description": "登山扣/箱包五金独立站的八周验证进展"},
                {"label": "TK日区店铺", "description": "妙手ERP链路之后的选品、流量、第一单"},
            ],
        }
        payload = claude_hook.normalize_question_payload(question)
        self.assertTrue(claude_hook.question_fits_deck(question))
        self.assertLessEqual(len(payload["options"][0]["description"].encode()), 110)
        self.assertLessEqual(
            sum(14 if ord(char) > 127 else 8 for char in payload["options"][0]["description"]),
            205,
        )
        self.assertTrue(payload["options"][0]["description"].endswith("…"))


if __name__ == "__main__":
    unittest.main()
