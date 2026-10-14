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

HTTP_TIMEOUT = 35

SAFE_BASH_PREFIXES = [
    "pwd",
    "ls",
    "git status",
    "git branch",
    "git diff",
    "git log",
    "git show",
    "rg",
    "cat",
    "which",
    "whereis",
    "find",
    "head",
    "tail",
    "grep",
    "egrep",
    "echo",
    "wc",
    "file",
    "stat",
    "df",
    "du",
    "tree",
    "env",
    "printenv",
    "whoami",
    "uname",
    "uptime",
]

# 不允许的写操作/高危重定向或替换符
DANGEROUS_TOKENS = [">", "<", "`", "$("]


def is_single_command_safe(cmd):
    cmd = cmd.strip()
    if not cmd:
        return True
    return any(
        cmd == prefix or cmd.startswith(prefix + " ")
        for prefix in SAFE_BASH_PREFIXES
    )


def is_safe_readonly_bash(command):
    command = command.strip()
    if not command:
        return False
    # 禁止输出重定向、输入重定向、命令替换
    if any(token in command for token in DANGEROUS_TOKENS):
        return False

    # 支持将由 &&, ||, |, ; 连接的复合命令拆解检查：只要每个子命令都是安全的只读命令，即放行
    import re
    parts = re.split(r'&&|\|\||\||;', command)
    return all(is_single_command_safe(part) for part in parts)


def format_prompt(event_name, tool_name, tool_input):
    """根据不同的工具类型生成副屏友好的标题、描述、角标与分类标签"""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        tag = "CRITICAL" if any(k in cmd for k in ["rm", "force", "drop", "sudo", "delete"]) else "HIGH RISK"
        return "[BASH EXEC]", tag, "终端命令执行", f"执行: {cmd[:80]}"
    elif tool_name in ["Write", "Edit"]:
        file_path = tool_input.get("file_path", "")
        tag = "SRC WRITE" if tool_name == "Write" else "CONFIG EDIT"
        return f"[{tool_name.upper()} FILE]", tag, f"文件修改 ({tool_name})", f"目标: {file_path[:60]}"
    elif tool_name in ["WebSearch", "Web Search"]:
        query = tool_input.get("query", "")
        return "[WEB SEARCH]", "NETWORK", "联网搜索", f"检索: {query[:70]}"
    elif tool_name in ["WebFetch", "Web Fetch"]:
        url = tool_input.get("url", "")
        return "[WEB FETCH]", "NETWORK", "网页内容抓取", f"网址: {url[:70]}"
    elif tool_name == "NotebookEditCell":
        nb = tool_input.get("notebook_path", "")
        return "[NOTEBOOK]", "JUPYTER", "Notebook 编辑", f"文件: {nb[:60]}"
    elif tool_name.startswith("mcp__"):
        mcp_name = tool_name[5:20]
        return "[MCP TOOL]", mcp_name.upper(), f"MCP ({mcp_name})", json.dumps(tool_input, ensure_ascii=False)[:80]
    else:
        desc = json.dumps(tool_input, ensure_ascii=False) if tool_input else "是否允许执行此操作？"
        return "[PERMISSION]", "CONFIRM", f"权限确认: {tool_name[:15]}", desc[:80]


def notify_deck_question(question):
    options = []
    for option in question.get("options", [])[:4]:
        if isinstance(option, dict):
            options.append(
                {
                    "label": str(option.get("label", option.get("text", "")))[:36],
                    "description": str(option.get("description", ""))[:72],
                }
            )
        else:
            opt_str = str(option)
            options.append(
                {
                    "label": opt_str[:36],
                    "description": "",
                }
            )

    payload = {
        "header": str(question.get("header", "QUESTION"))[:20],
        "question": str(question.get("question", ""))[:140],
        "options": options,
    }
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:19842/notify_question",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.5)
    except Exception:
        pass


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
            badge, tag, title, desc = format_prompt(event_name, tool_name, tool_input)
        elif tool_name == "AskUserQuestion":
            questions = tool_input.get("questions", [])
            for question in questions:
                notify_deck_question(question)
            # 放行，让终端原生交互界面同时显示问题，两端同步
            sys.exit(0)
        elif tool_name in ["Write", "Edit"]:
            badge, tag, title, desc = format_prompt(event_name, tool_name, tool_input)
        else:
            # Other tools may still generate a PermissionRequest later.
            sys.exit(0)
    elif event_name == "PermissionRequest":
        if tool_name == "AskUserQuestion":
            # 终端已在处理交互，直接放行
            sys.exit(0)
        badge, tag, title, desc = format_prompt(event_name, tool_name, tool_input)
    else:
        sys.exit(0)

    req = urllib.request.Request(
        "http://127.0.0.1:19842/prompt",
        data=json.dumps({"badge": badge, "tag": tag, "title": title, "desc": desc}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            decision = res.get("decision", OFFLINE_POLICY)
            reason = res.get("reason") or (
                "用户在 Claude-Deck 物理副屏上点击了拒绝"
                if decision == "deny"
                else "Claude-Deck 允许本次操作"
            )

            if decision == "deny":
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
