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


def ask_deck_question(question):
    options = []
    for option in question.get("options", [])[:8]:
        options.append(
            {
                "label": str(option.get("label", ""))[:36],
                "description": str(option.get("description", ""))[:72],
            }
        )

    payload = {
        "header": str(question.get("header", "QUESTION"))[:20],
        "question": str(question.get("question", ""))[:140],
        "options": options,
    }
    req = urllib.request.Request(
        "http://127.0.0.1:19842/question",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        if not result.get("answered"):
            return None
        index = int(result.get("index", -1))
        if 0 <= index < len(options):
            return options[index]["label"]
        return None


def print_ask_user_answers(answers):
    reason = {
        "source": "claude-deck",
        "instruction": "The user answered AskUserQuestion on the physical deck. Treat these as the user's answers and continue without calling AskUserQuestion again.",
        "answers": answers,
    }
    print_pre_tool_use_decision(
        "deny",
        json.dumps(reason, ensure_ascii=False),
    )


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
            answers = []
            for question in questions:
                selected = ask_deck_question(question)
                if selected is None:
                    if answers:
                        # Some questions were already answered on the deck. Give
                        # the model the partial result and ask it to redo the rest.
                        answers.append(
                            {
                                "header": str(question.get("header", "")),
                                "question": str(question.get("question", "")),
                                "selected": None,
                                "status": "unanswered: use terminal on retry",
                            }
                        )
                    else:
                        # No deck answer at all; let Claude Code use its native UI.
                        sys.exit(0)
                else:
                    answers.append(
                        {
                            "header": str(question.get("header", "")),
                            "question": str(question.get("question", "")),
                            "selected": selected,
                        }
                    )

            print_ask_user_answers(answers)
        elif tool_name in ["Write", "Edit"]:
            badge, tag, title, desc = format_prompt(event_name, tool_name, tool_input)
        else:
            # Other tools may still generate a PermissionRequest later.
            sys.exit(0)
    elif event_name == "PermissionRequest":
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
