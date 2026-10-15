#!/usr/bin/env python3
import json
import hashlib
import os
import re
import shlex
import sys
import urllib.error
import urllib.request

try:
    from .deck_auth import load_token
except ImportError:
    from deck_auth import load_token

# If the daemon or hardware is unavailable:
#   deny  = fail closed (recommended for a physical approval deck)
#   allow = fail open   (availability first, old behavior)
OFFLINE_POLICY = os.environ.get("CLAUDE_DECK_OFFLINE_POLICY", "deny").strip().lower()
if OFFLINE_POLICY not in {"allow", "deny"}:
    OFFLINE_POLICY = "deny"

# Intercept mode for PreToolUse:
#   pre_tool_use (default) = gate every non-readonly tool call.
#   notify_only            = advisory display; Claude Code retains native permissions.
INTERCEPT_MODE = os.environ.get("CLAUDE_DECK_INTERCEPT_MODE", "pre_tool_use").strip().lower()

try:
    DEFAULT_TIMEOUT = max(5, min(540, int(os.environ.get("CLAUDE_DECK_TIMEOUT", "120"))))
except ValueError:
    DEFAULT_TIMEOUT = 120
HTTP_TIMEOUT = DEFAULT_TIMEOUT + 5

MAX_APPROVAL_DESC_BYTES = 900
READONLY_TOOLS = {"Read", "Glob", "Grep", "WebSearch", "WebFetch"}
SHELL_META = set("\r\n;&|<>`$\\(){}")
GIT_OPTIONS = {
    "status": {"--short", "-s", "--branch", "-b", "--porcelain", "--porcelain=v1"},
    "branch": {"-a", "-r", "--list"},
    "diff": {"--staged", "--cached", "--stat", "--name-only", "--name-status"},
    "log": {"--oneline", "--stat", "--all"},
    "show": {"--stat"},
}


def is_single_command_safe(cmd):
    if not cmd or any(char in SHELL_META for char in cmd):
        return False
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    if not tokens:
        return False
    program, *args = tokens
    if program in {"pwd", "env", "printenv", "whoami", "uname", "uptime"}:
        return not args
    if program == "ls":
        return all(not arg.startswith("-") or arg in {"-l", "-a", "-h", "-la", "-al", "-lah", "-alh"} for arg in args)
    if program == "git" and args and args[0] in GIT_OPTIONS:
        subcommand, *rest = args
        if subcommand == "log" and len(rest) >= 2 and rest[0] == "-n" and rest[1].isdigit():
            rest = rest[2:]
        return all(not arg.startswith("-") or arg in GIT_OPTIONS[subcommand] for arg in rest)
    if program == "find":
        i = 0
        while i < len(args) and not args[i].startswith("-"):
            i += 1
        while i < len(args):
            if args[i] not in {"-maxdepth", "-mindepth", "-name", "-iname", "-type"} or i + 1 >= len(args):
                return False
            if args[i] in {"-maxdepth", "-mindepth"} and not args[i + 1].isdigit():
                return False
            if args[i] == "-type" and args[i + 1] not in {"f", "d", "l"}:
                return False
            i += 2
        return True
    if program == "rg":
        return all(not arg.startswith("-") or arg in {"-n", "-i", "-l", "--files", "--hidden"} for arg in args)
    if program in {"cat", "which", "head", "tail", "grep", "wc", "file", "stat", "df", "du"}:
        return all(not arg.startswith("-") for arg in args)
    return False


def is_safe_readonly_bash(command):
    if not command or not command.strip():
        return False
    # A single simple command only. Shell composition and expansion go through approval.
    return is_single_command_safe(command.strip())


def smart_truncate_path(file_path, max_len=60):
    """尾部优先路径缩写：保留文件名和末尾目录层级，避免关键文件名被切掉"""
    file_path = str(file_path or "").strip()
    if len(file_path) <= max_len:
        return file_path
    if "/" in file_path:
        parts = [p for p in file_path.split("/") if p]
        if len(parts) >= 2:
            tail = "/".join(parts[-2:])
            candidate = f".../{tail}"
            if len(candidate) <= max_len:
                # 尝试多保留一层目录
                if len(parts) >= 3:
                    cand3 = f".../{'/'.join(parts[-3:])}"
                    if len(cand3) <= max_len:
                        return cand3
                return candidate
        filename = parts[-1] if parts else file_path
        if len(filename) + 4 <= max_len:
            return f".../{filename}"
    head_len = max(8, (max_len - 3) // 2)
    tail_len = max(8, max_len - 3 - head_len)
    return f"{file_path[:head_len]}...{file_path[-tail_len:]}"


def smart_truncate_text(text, max_len=160):
    """规范化空白并在超长时附加省略号 '...'，供副屏分页浏览"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def truncate_deck_line(text, max_width=205, max_bytes=110):
    """Fit auxiliary option text into the one-line physical question card."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""

    def width(value):
        return sum(14 if ord(char) > 127 else 8 for char in value)

    if len(text.encode("utf-8")) <= max_bytes and width(text) <= max_width:
        return text

    suffix = "…"
    result = ""
    for char in text:
        candidate = result + char
        if len((candidate + suffix).encode("utf-8")) > max_bytes or width(candidate + suffix) > max_width:
            break
        result = candidate
    return (result or suffix) + suffix


def format_prompt(event_name, tool_name, tool_input):
    """根据不同的工具类型生成副屏友好的标题、描述、角标与分类标签"""
    if tool_name == "Bash":
        cmd = str(tool_input.get("command") or "")
        cmd_lower = cmd.lower()
        is_critical = any(
            k in cmd_lower
            for k in ["rm", "force", "drop", "sudo", "delete", "mkfs", "dd ", "chmod", "chown"]
        )
        tag = "CRITICAL" if is_critical else "HIGH RISK"
        return "[BASH EXEC]", tag, "终端命令执行", f"执行: {cmd}"
    elif tool_name in ["Write", "Edit"]:
        file_path = tool_input.get("file_path", "")
        tag = "SRC WRITE" if tool_name == "Write" else "CONFIG EDIT"
        details = json.dumps(tool_input, ensure_ascii=False, indent=1)
        return f"[{tool_name.upper()} FILE]", tag, f"文件修改 ({tool_name})", f"目标: {file_path}\n{details}"
    elif tool_name in ["WebSearch", "Web Search"]:
        query = tool_input.get("query", "")
        return "[WEB SEARCH]", "NETWORK", "联网搜索", f"检索: {query}"
    elif tool_name in ["WebFetch", "Web Fetch"]:
        url = tool_input.get("url", "")
        return "[WEB FETCH]", "NETWORK", "网页内容抓取", f"网址: {url}"
    elif tool_name == "NotebookEditCell":
        nb = tool_input.get("notebook_path", "")
        return "[NOTEBOOK]", "JUPYTER", "Notebook 编辑", f"文件: {nb}\n{json.dumps(tool_input, ensure_ascii=False)}"
    elif tool_name.startswith("mcp__"):
        mcp_name = tool_name[5:20]
        return "[MCP TOOL]", mcp_name.upper(), f"MCP ({mcp_name})", json.dumps(tool_input, ensure_ascii=False)
    else:
        desc = json.dumps(tool_input, ensure_ascii=False) if tool_input else "是否允许执行此操作？"
        return "[PERMISSION]", "CONFIRM", f"权限确认: {tool_name[:15]}", desc


def normalize_question_payload(question):
    options = []
    for option in question.get("options", [])[:4]:
        if isinstance(option, dict):
            options.append(
                {
                    "label": str(option.get("label", option.get("text", ""))),
                    # The 320×240 display gives option descriptions one visual line.
                    # Keep the choice label unchanged so the answer returned to Claude
                    # always matches its original option exactly.
                    "description": truncate_deck_line(option.get("description", "")),
                }
            )
        else:
            opt_str = str(option)
            options.append(
                {
                    "label": opt_str,
                    "description": "",
                }
            )

    return {
        "header": str(question.get("header", "QUESTION")),
        "question": str(question.get("question", "")),
        "options": options,
        "multiSelect": bool(question.get("multiSelect")),
    }


def question_fits_deck(question):
    if len(question.get("options", [])) > 4:
        return False
    payload = normalize_question_payload(question)
    if not 2 <= len(payload["options"]) <= 4:
        return False
    if len(payload["header"].encode()) > 28 or len(payload["question"].encode()) > 290:
        return False
    def approximate_width(value):
        return sum(14 if ord(char) > 127 else 8 for char in value)
    question_lines = payload["question"].splitlines() or [""]
    if sum(max(1, (approximate_width(line) + 287) // 288) for line in question_lines) > 2:
        return False
    return all(
        len(option["label"].encode()) <= 60 and
        approximate_width(option["label"]) <= 205
        for option in payload["options"]
    )


def local_headers():
    return {"Content-Type": "application/json", "X-Claude-Deck-Token": load_token()}


def request_key(data):
    session_id = data.get("session_id")
    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input")
    if not session_id or not tool_name or not isinstance(tool_input, dict):
        return ""
    canonical = json.dumps([session_id, tool_name, tool_input], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ask_deck_question(question, timeout=DEFAULT_TIMEOUT):
    """Push one question to both Deck inputs and return its selected option(s)."""
    payload = normalize_question_payload(question)
    payload["timeout"] = timeout
    if len(payload["options"]) < 2:
        return None
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:19842/question",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=local_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if not res.get("answered"):
                return None
            indexes = res.get("indexes")
            if indexes is None and res.get("index") is not None:
                indexes = [res["index"]]
            if not isinstance(indexes, list):
                return None
            valid_indexes = []
            for value in indexes:
                idx = int(value)
                if idx not in valid_indexes and 0 <= idx < len(payload["options"]):
                    valid_indexes.append(idx)
            if not valid_indexes:
                return None
            results = [(idx, payload["options"][idx], payload["question"]) for idx in valid_indexes]
            return results if payload["multiSelect"] else results[0]
    except Exception:
        pass
    return None


def notify_deck_status(status, desc="", badge="", tag="", title="", key=""):
    """非阻塞更新副屏状态（如 thinking 执行态或 PostToolUse 完成后归位 idle）"""
    payload = {
        "status": status,
        "desc": desc,
        "badge": badge,
        "tag": tag,
        "title": title,
        "request_key": key,
    }
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:19842/status",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=local_headers(),
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.2)
    except Exception:
        pass


def notify_deck_prompt(badge, tag, title, desc):
    """非阻塞推送工具执行动态（在副屏渲染为 THINKING 状态卡片，不渲染无效的假审批按钮）"""
    payload = {
        "badge": badge,
        "tag": tag,
        "title": title,
        "desc": desc,
    }
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:19842/notify_prompt",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=local_headers(),
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.2)
    except Exception:
        pass


def print_pre_tool_use_decision(decision, reason="", updated_input=None):
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if reason:
        output["hookSpecificOutput"]["permissionDecisionReason"] = reason
    if updated_input is not None:
        output["hookSpecificOutput"]["updatedInput"] = updated_input
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


def request_physical_approval(event_name, badge, tag, title, desc, key=""):
    if len(desc.encode("utf-8")) > MAX_APPROVAL_DESC_BYTES:
        reason = "操作详情超出副屏容量，请在终端查看完整内容后审批"
        if event_name == "PreToolUse":
            print_pre_tool_use_decision("ask", reason)
        sys.exit(0)  # PermissionRequest: leave the native prompt in place.

    try:
        req = urllib.request.Request(
            "http://127.0.0.1:19842/prompt",
            data=json.dumps(
                {
                    "badge": badge,
                    "tag": tag,
                    "title": title,
                    "desc": desc,
                    "timeout": DEFAULT_TIMEOUT,
                    "request_key": key,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers=local_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            decision = res.get("decision", OFFLINE_POLICY)
            if decision not in {"allow", "deny"}:
                raise ValueError("Invalid deck decision")
            reason = res.get("reason") or (
                "用户在 Claude-Deck 物理副屏上点击了拒绝"
                if decision == "deny"
                else "Claude-Deck 允许本次操作"
            )

            if event_name == "PermissionRequest":
                if decision == "deny":
                    print_permission_request_decision("deny", reason)
                else:
                    print_permission_request_decision("allow")
            else:
                if decision == "deny":
                    print_pre_tool_use_decision("deny", reason)
                else:
                    print_pre_tool_use_decision("allow", reason)
    except Exception:
        fallback_for_offline(event_name)


def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.exit(0)

        data = json.loads(raw_input)
        if not isinstance(data, dict):
            raise ValueError("Invalid hook payload")
    except Exception:
        print_pre_tool_use_decision("deny", "Claude-Deck 收到无效 Hook 输入，已安全拦截")

    event_name = str(data.get("hook_event_name") or "PreToolUse")
    tool_name = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        fallback_for_offline(event_name)

    # 1. 工具执行完成或会话停止时：通知副屏自动消警归位 IDLE，防止主屏已确认但副屏仍挂着旧卡片
    if event_name in ("PostToolUse", "Stop", "SubagentStop"):
        notify_deck_status("idle", "副屏已就绪，正在监听 Claude Code 编程指令...", key=request_key(data))
        sys.exit(0)

    # 2. PreToolUse 阶段
    if event_name == "PreToolUse":
        if tool_name == "Bash":
            command = str(tool_input.get("command") or "").strip()
            if is_safe_readonly_bash(command):
                sys.exit(0)
            badge, tag, title, desc = format_prompt(event_name, tool_name, tool_input)
            if INTERCEPT_MODE != "notify_only":
                request_physical_approval(event_name, badge, tag, title, desc, key=request_key(data))
            else:
                notify_deck_prompt(badge, tag, title, desc)
                sys.exit(0)
        elif tool_name == "AskUserQuestion":
            questions = tool_input.get("questions", [])
            if not questions and "question" in tool_input:
                questions = [tool_input]
            if not questions or len(questions) > 4 or not all(isinstance(q, dict) and question_fits_deck(q) for q in questions):
                sys.exit(0)  # Native UI handles unsupported question shapes.
            answers = {}
            question_timeout = max(5, min(DEFAULT_TIMEOUT, (540 - 15) // len(questions)))
            for q in questions:
                ans = ask_deck_question(q, timeout=question_timeout)
                if ans is None:
                    sys.exit(0)
                if q.get("multiSelect"):
                    answers[q["question"]] = [option["label"] for _, option, _ in ans]
                else:
                    _, opt, q_text = ans
                    answers[q_text] = opt["label"]
            updated_input = dict(tool_input)
            updated_input["questions"] = questions
            updated_input["answers"] = answers
            print_pre_tool_use_decision("allow", updated_input=updated_input)
            sys.exit(0)
        elif tool_name not in READONLY_TOOLS:
            badge, tag, title, desc = format_prompt(event_name, tool_name, tool_input)
            if INTERCEPT_MODE != "notify_only":
                request_physical_approval(event_name, badge, tag, title, desc, key=request_key(data))
            else:
                notify_deck_prompt(badge, tag, title, desc)
                sys.exit(0)
        else:
            sys.exit(0)

    # 3. PermissionRequest 阶段：主屏弹出权限请求时，副屏同步亮起红绿实体审批按钮
    elif event_name == "PermissionRequest":
        if tool_name == "AskUserQuestion":
            sys.exit(0)
        badge, tag, title, desc = format_prompt(event_name, tool_name, tool_input)
        request_physical_approval(event_name, badge, tag, title, desc, key=request_key(data))
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
