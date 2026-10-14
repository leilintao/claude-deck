#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.request

# If the daemon or hardware is unavailable:
#   deny  = fail closed (recommended for a physical approval deck)
#   allow = fail open   (availability first, old behavior)
OFFLINE_POLICY = os.environ.get("CLAUDE_DECK_OFFLINE_POLICY", "deny").strip().lower()
if OFFLINE_POLICY not in {"allow", "deny"}:
    OFFLINE_POLICY = "deny"

HTTP_TIMEOUT = 125

SAFE_BASH_PREFIXES = [
    "pwd",
    "ls",
    "git status",
    "git branch",
    "git diff",
    "git log",
    "rg",
    "cat",
    "which",
]


def shell_has_control_operators(command):
    # This is intentionally conservative. It is a read-only noise filter, not
    # a shell parser or sandbox.
    return any(token in command for token in [";", "&&", "||", "|", ">", "<", "`", "$("])


def is_safe_readonly_bash(command):
    command = command.strip()
    if not command or shell_has_control_operators(command):
        return False
    return any(
        command == prefix or command.startswith(prefix + " ")
        for prefix in SAFE_BASH_PREFIXES
    )


def format_prompt(event_name, tool_name, tool_input):
    """根据不同的工具类型生成副屏友好的标题与描述"""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return "终端命令执行", f"执行: {cmd[:80]}"
    elif tool_name in ["Write", "Edit"]:
        file_path = tool_input.get("file_path", "")
        return f"文件修改 ({tool_name})", f"目标: {file_path[:60]}"
    elif tool_name in ["WebSearch", "Web Search"]:
        query = tool_input.get("query", "")
        return "联网搜索", f"检索: {query[:70]}"
    elif tool_name in ["WebFetch", "Web Fetch"]:
        url = tool_input.get("url", "")
        return "网页内容抓取", f"网址: {url[:70]}"
    elif tool_name == "NotebookEditCell":
        nb = tool_input.get("notebook_path", "")
        return "Notebook 编辑", f"文件: {nb[:60]}"
    elif tool_name.startswith("mcp__"):
        return f"MCP 工具 ({tool_name[5:20]})", json.dumps(tool_input, ensure_ascii=False)[:80]
    else:
        desc = json.dumps(tool_input, ensure_ascii=False) if tool_input else "是否允许执行此操作？"
        return f"权限确认: {tool_name[:15]}", desc[:80]


def print_pre_tool_use_decision(decision, reason=""):
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if reason:
        output["hookSpecificOutput"]["permissionDecisionReason"] = reason
    print(json.dumps(output, ensure_ascii=False))
    sys.exit(0)


def print_permission_request_decision(decision, reason=""):
    decision_payload = {"behavior": decision}
    if reason:
        decision_payload["message"] = reason

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision_payload,
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    sys.exit(0)


def fallback_for_offline(event_name):
    reason = "Claude-Deck 离线，按 CLAUDE_DECK_OFFLINE_POLICY=deny 自动拒绝"
    if OFFLINE_POLICY == "deny":
        if event_name == "PermissionRequest":
            print_permission_request_decision("deny", reason)
        else:
            print_pre_tool_use_decision("deny", reason)

    # fail-open mode: no output lets Claude Code fall back to its normal flow
    sys.exit(0)


def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.exit(0)

        data = json.loads(raw_input)
    except Exception:
        sys.exit(0)

    event_name = data.get("hook_event_name", "PreToolUse")
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    if event_name == "PreToolUse":
        if tool_name == "Bash":
            command = tool_input.get("command", "").strip()
            if is_safe_readonly_bash(command):
                sys.exit(0)
            title, desc = format_prompt(event_name, tool_name, tool_input)
        elif tool_name in ["Write", "Edit"]:
            title, desc = format_prompt(event_name, tool_name, tool_input)
        else:
            # Other tools may still generate a PermissionRequest later.
            sys.exit(0)
    elif event_name == "PermissionRequest":
        title, desc = format_prompt(event_name, tool_name, tool_input)
    else:
        sys.exit(0)

    req = urllib.request.Request(
        "http://127.0.0.1:19842/prompt",
        data=json.dumps({"title": title, "desc": desc}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            decision = res.get("decision", OFFLINE_POLICY)

            if decision == "deny":
                reason = "用户在 Claude-Deck 物理副屏上点击了拒绝"
                if event_name == "PermissionRequest":
                    print_permission_request_decision("deny", reason)
                else:
                    print_pre_tool_use_decision("deny", reason)

            if event_name == "PermissionRequest":
                print_permission_request_decision("allow")
            else:
                print_pre_tool_use_decision("allow")
    except Exception:
        fallback_for_offline(event_name)


if __name__ == "__main__":
    main()
