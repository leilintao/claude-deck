#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["claude-agent-sdk>=0.2.167"]
# ///
"""Single-terminal Claude Code client with shared Claude Deck interaction."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import urllib.error
import urllib.request
import uuid

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

try:
    from .claude_hook import format_prompt, normalize_question_payload, question_fits_deck
    from .deck_auth import load_token
except ImportError:
    from claude_hook import format_prompt, normalize_question_payload, question_fits_deck
    from deck_auth import load_token


BASE_URL = "http://127.0.0.1:19842"
DEFAULT_TIMEOUT = 120


def _settings_path():
    return Path.home() / ".claude" / "settings.json"


def load_ccswitch_profile():
    """Load the selected CCSwitch provider without loading its Hook rules."""
    try:
        settings = json.loads(_settings_path().read_text())
    except (OSError, ValueError):
        settings = {}
    env = {
        str(key): str(value)
        for key, value in settings.get("env", {}).items()
        if isinstance(key, str) and isinstance(value, (str, int, float, bool))
    }
    env["CLAUDE_AGENT_SDK_CLIENT_APP"] = "claude-deck-code/0.1"
    sanitized = {
        key: value
        for key, value in settings.items()
        if key
        not in {
            "env",
            "hooks",
            "permissions",
            "skipAutoPermissionPrompt",
            "skipDangerousModePermissionPrompt",
        }
    }
    sanitized["permissions"] = {"defaultMode": "default"}
    return env, json.dumps(sanitized, ensure_ascii=False)


def post_json(path, payload, timeout):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Claude-Deck-Token": load_token(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.HTTPError):
        return None


def deck_connected():
    try:
        with urllib.request.urlopen(BASE_URL + "/health", timeout=1) as response:
            return bool(json.loads(response.read().decode("utf-8")).get("connected"))
    except (OSError, ValueError):
        return False


async def read_line(prompt):
    """Read one line without blocking SDK or BLE events."""
    print(prompt, end="", flush=True)
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def ready():
        if not future.done():
            future.set_result(sys.stdin.readline())

    try:
        loop.add_reader(sys.stdin.fileno(), ready)
    except (AttributeError, NotImplementedError):
        return await asyncio.to_thread(sys.stdin.readline)
    try:
        return await future
    finally:
        loop.remove_reader(sys.stdin.fileno())


def parse_choices(line, option_count, multi_select):
    try:
        values = [int(value.strip()) for value in line.split(",") if value.strip()]
    except ValueError:
        return None
    if not values or any(value < 1 or value > option_count for value in values):
        return None
    indexes = []
    for value in values:
        if value - 1 not in indexes:
            indexes.append(value - 1)
    if not multi_select and len(indexes) != 1:
        return None
    return indexes


async def race_question(question, timeout=DEFAULT_TIMEOUT):
    payload = normalize_question_payload(question)
    options = payload["options"]
    multi_select = payload["multiSelect"]
    request_id = uuid.uuid4().hex[:16]

    print(f"\n┌─ {payload['header']} ─────────────────────────")
    print(payload["question"])
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option['label']}")
        if option.get("description"):
            print(f"     {option['description']}")
    hint = "多个编号用逗号分隔" if multi_select else "输入编号"
    print(f"  {hint}；也可直接在副屏选择。")

    deck_task = None
    if question_fits_deck(question) and await asyncio.to_thread(deck_connected):
        deck_payload = dict(payload)
        deck_payload.update({"id": request_id, "timeout": timeout, "launchTerminal": False})
        deck_task = asyncio.create_task(
            asyncio.to_thread(post_json, "/question", deck_payload, timeout + 5)
        )

    while True:
        terminal_task = asyncio.create_task(read_line("选择 > "))
        active = {terminal_task}
        if deck_task is not None:
            active.add(deck_task)
        done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)

        if deck_task is not None and deck_task in done:
            response = deck_task.result()
            deck_task = None
            if response and response.get("answered"):
                terminal_task.cancel()
                await asyncio.gather(terminal_task, return_exceptions=True)
                indexes = response.get("indexes") or [response.get("index")]
                indexes = [int(index) for index in indexes if index is not None]
                print("\n已在副屏完成。")
                return indexes
            line = await terminal_task
            indexes = parse_choices(line, len(options), multi_select)
            if indexes is None:
                print("编号无效，请重新输入。")
                continue
            return indexes

        if terminal_task in done:
            line = terminal_task.result()
            if not line:
                return None
            indexes = parse_choices(line, len(options), multi_select)
            if indexes is None:
                print("编号无效，请重新输入。")
                continue
            if deck_task is not None:
                await asyncio.to_thread(
                    post_json,
                    "/question_answer",
                    {"id": request_id, "indexes": indexes},
                    3,
                )
                await deck_task
            return indexes


async def race_approval(tool_name, tool_input, context, timeout=DEFAULT_TIMEOUT):
    badge, tag, fallback_title, desc = format_prompt("PermissionRequest", tool_name, tool_input)
    title = context.title or context.display_name or fallback_title
    request_id = uuid.uuid4().hex[:16]
    print(f"\n┌─ 权限确认 · {tool_name} ─────────────────")
    print(title)
    print(desc)
    print("  y. 允许    n. 拒绝；也可直接在副屏选择。")

    deck_task = None
    if await asyncio.to_thread(deck_connected):
        deck_task = asyncio.create_task(
            asyncio.to_thread(
                post_json,
                "/prompt",
                {
                    "id": request_id,
                    "title": title,
                    "desc": desc,
                    "options": ["允许", "拒绝"],
                    "badge": badge,
                    "tag": tag,
                    "timeout": timeout,
                },
                timeout + 5,
            )
        )
    while True:
        terminal_task = asyncio.create_task(read_line("允许? [y/N] > "))
        active = {terminal_task}
        if deck_task is not None:
            active.add(deck_task)
        done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
        if deck_task is not None and deck_task in done:
            response = deck_task.result()
            deck_task = None
            terminal_task.cancel()
            await asyncio.gather(terminal_task, return_exceptions=True)
            if response and response.get("decision") in {"allow", "deny"}:
                print(f"\n已在副屏{'允许' if response['decision'] == 'allow' else '拒绝'}。")
                return response["decision"]
            print("副屏请求已结束，请在终端决定。")
            line = await read_line("允许? [y/N] > ")
        else:
            line = terminal_task.result()
        choice = line.strip().lower()
        if choice not in {"y", "yes", "n", "no", ""}:
            print("请输入 y 或 n。")
            continue
        decision = "allow" if choice in {"y", "yes"} else "deny"
        if deck_task is not None and not deck_task.done():
            await asyncio.to_thread(
                post_json,
                "/prompt_answer",
                {"id": request_id, "decision": decision},
                3,
            )
            await deck_task
        return decision


async def can_use_tool(tool_name, tool_input, context):
    if tool_name == "AskUserQuestion":
        questions = tool_input.get("questions", [])
        if not questions and tool_input.get("question"):
            questions = [tool_input]
        answers = {}
        for question in questions:
            indexes = await race_question(question)
            if not indexes:
                return PermissionResultDeny(message="用户取消了问题", interrupt=False)
            options = normalize_question_payload(question)["options"]
            labels = [options[index]["label"] for index in indexes]
            answers[str(question.get("question", ""))] = (
                labels if question.get("multiSelect") else labels[0]
            )
        updated_input = dict(tool_input)
        updated_input["questions"] = questions
        updated_input["answers"] = answers
        return PermissionResultAllow(updated_input=updated_input)

    decision = await race_approval(tool_name, tool_input, context)
    if decision == "allow":
        return PermissionResultAllow(updated_input=tool_input)
    return PermissionResultDeny(message="用户拒绝了此操作", interrupt=False)


def agent_options(cwd):
    env, settings = load_ccswitch_profile()
    cli_path = shutil.which("claude")
    if not cli_path:
        raise RuntimeError("找不到 Claude Code CLI")
    return ClaudeAgentOptions(
        cli_path=cli_path,
        cwd=cwd,
        env=env,
        settings=settings,
        setting_sources=[],
        system_prompt={"type": "preset", "preset": "claude_code"},
        tools={"type": "preset", "preset": "claude_code"},
        permission_mode="default",
        can_use_tool=can_use_tool,
    )


async def doctor(cwd):
    options = agent_options(cwd)
    options.tools = []
    options.can_use_tool = None
    options.permission_mode = "dontAsk"
    options.max_turns = 1
    result = None
    async for message in query(prompt="Reply with exactly: DECK_SDK_OK", options=options):
        if isinstance(message, ResultMessage):
            result = message
    if result and not result.is_error and result.result and "DECK_SDK_OK" in result.result:
        print("✓ CCSwitch / Claude Agent SDK 连接正常")
        try:
            with urllib.request.urlopen(BASE_URL + "/health", timeout=2) as response:
                health = json.loads(response.read().decode("utf-8"))
            state = "在线" if health.get("connected") else "离线"
            print(f"✓ Claude Deck 服务正常，副屏{state}")
        except (OSError, ValueError):
            print("! Claude Deck 服务未响应，仍可使用终端交互")
        return 0
    print("✗ Agent SDK 未得到预期响应")
    return 1


async def interactive(cwd):
    print("Claude Deck Code")
    print("CCSwitch 配置已继承；问题与审批可从当前终端或实体副屏完成。")
    print("输入 /exit 退出。\n")
    async with ClaudeSDKClient(options=agent_options(cwd)) as client:
        while True:
            prompt = (await read_line("you > ")).strip()
            if not prompt:
                continue
            if prompt in {"/exit", "/quit"}:
                return 0
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            print(f"\nclaude > {block.text}")
                        elif isinstance(block, ToolUseBlock) and block.name != "AskUserQuestion":
                            print(f"\n[tool] {block.name}")
                elif isinstance(message, ResultMessage) and message.is_error:
                    print(f"\n[error] {message.result or message.subtype}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doctor", action="store_true", help="测试 CCSwitch 与副屏连接")
    parser.add_argument("--cwd", default=os.getcwd(), help="Claude 工作目录")
    args = parser.parse_args()
    try:
        return asyncio.run(doctor(args.cwd) if args.doctor else interactive(args.cwd))
    except KeyboardInterrupt:
        print("\n已退出。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
