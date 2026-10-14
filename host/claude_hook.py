#!/usr/bin/env python3
import sys
import json
import urllib.request

DAEMON_URL_PROMPT = "http://127.0.0.1:19842/prompt"
DAEMON_URL_NOTIFY = "http://127.0.0.1:19842/notify"

def send_to_daemon(url, payload, timeout=125):
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def format_tool_prompt(tool_name, tool_input):
    """根据不同的工具类型生成副屏友好的标题、描述与按键"""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "").strip()
        return "终端命令执行", f"执行: {cmd[:80]}", ["允许 (Y)", "拒绝 (N)"]
    elif tool_name in ["Write", "Edit"]:
        file_path = tool_input.get("file_path", "")
        return f"文件修改 ({tool_name})", f"目标: {file_path[:60]}", ["允许 (Y)", "拒绝 (N)"]
    elif tool_name in ["WebSearch", "Web Search"]:
        query = tool_input.get("query", "")
        return "联网搜索请求", f"检索: {query[:70]}", ["允许 (Y)", "拒绝 (N)"]
    elif tool_name in ["WebFetch", "Web Fetch"]:
        url = tool_input.get("url", "")
        return "网页内容抓取", f"网址: {url[:70]}", ["允许 (Y)", "拒绝 (N)"]
    elif tool_name == "NotebookEditCell":
        nb = tool_input.get("notebook_path", "")
        return "Notebook 编辑", f"文件: {nb[:60]}", ["允许 (Y)", "拒绝 (N)"]
    elif tool_name == "ExitPlanMode":
        return "计划审查确认", "Claude 已拟定开发计划，是否批准进入执行阶段？", ["批准计划 (Y)", "拒绝计划 (N)"]
    elif tool_name == "AskUserQuestion":
        questions = tool_input.get("questions", [])
        if questions:
            q = questions[0]
            q_text = q.get("question", "Claude 向您提问")
            opts = q.get("options", ["选项 A", "选项 B"])
            opt1 = opts[0] if len(opts) > 0 else "选项 A"
            opt2 = opts[1] if len(opts) > 1 else "选项 B"
            return "Claude 提问", q_text[:80], [opt1[:10], opt2[:10]]
        return "Claude 提问", "请查看终端输入选项", ["确认 (Y)", "取消 (N)"]
    elif tool_name.startswith("mcp__"):
        mcp_name = tool_name[5:20]
        return f"MCP 工具 ({mcp_name})", json.dumps(tool_input, ensure_ascii=False)[:80], ["允许 (Y)", "拒绝 (N)"]
    else:
        desc = json.dumps(tool_input, ensure_ascii=False) if tool_input else "是否允许执行此操作？"
        return f"权限确认: {tool_name[:15]}", desc[:80], ["允许 (Y)", "拒绝 (N)"]

def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.exit(0)

        data = json.loads(raw_input)
        event_name = data.get("hook_event_name", "PreToolUse")
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        # =========================================================
        # 1. PermissionRequest: Claude 正在等待用户授权确认 (阻塞交互)
        # =========================================================
        if event_name == "PermissionRequest":
            title, desc, options = format_tool_prompt(tool_name, tool_input)
            payload = {"title": title, "desc": desc, "options": options}
            res = send_to_daemon(DAEMON_URL_PROMPT, payload, timeout=125)

            if res:
                decision = res.get("decision", "allow")
                if decision == "deny":
                    output = {
                        "hookSpecificOutput": {
                            "hookEventName": "PermissionRequest",
                            "decision": {
                                "behavior": "deny",
                                "message": "用户在 Claude-Deck 物理副屏上点击了拒绝"
                            }
                        }
                    }
                else:
                    output = {
                        "hookSpecificOutput": {
                            "hookEventName": "PermissionRequest",
                            "decision": {
                                "behavior": "allow"
                            }
                        }
                    }
                print(json.dumps(output))
            sys.exit(0)

        # =========================================================
        # 2. PreToolUse: 高危工具或计划/提问主动拦截 (阻塞交互)
        # =========================================================
        elif event_name == "PreToolUse":
            if tool_name == "Bash":
                command = tool_input.get("command", "").strip()
                # 常见安全只读命令直接穿透放行
                if command in ["pwd", "ls", "git status", "git branch"]:
                    sys.exit(0)
                title, desc, options = format_tool_prompt(tool_name, tool_input)
            elif tool_name in ["Write", "Edit", "ExitPlanMode", "AskUserQuestion"]:
                title, desc, options = format_tool_prompt(tool_name, tool_input)
            else:
                # 其它工具放行由 PermissionRequest 或模型自行裁定
                sys.exit(0)

            payload = {"title": title, "desc": desc, "options": options}
            res = send_to_daemon(DAEMON_URL_PROMPT, payload, timeout=125)

            if res:
                decision = res.get("decision", "allow")
                if decision == "deny":
                    output = {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "用户在 Claude-Deck 物理副屏上点击了拒绝"
                        }
                    }
                else:
                    output = {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow"
                        }
                    }
                print(json.dumps(output))
            sys.exit(0)

        # =========================================================
        # 3. Notification: 系统通知与后台状态提醒 (非阻塞广播)
        # =========================================================
        elif event_name == "Notification":
            notif_type = data.get("notification_type", "")
            msg = data.get("message", "")
            
            if notif_type == "idle_prompt":
                title = "Claude 待命中"
                desc = "已等待超 1 分钟，终端正等待您的下一步指令"
            elif notif_type == "agent_needs_input":
                title = "后台 Agent 待命"
                desc = msg[:80] if msg else "子 Agent 正在等待您的回复"
            elif notif_type == "agent_completed":
                title = "后台任务完成"
                desc = msg[:80] if msg else "子 Agent 任务已执行完毕"
            elif notif_type == "permission_prompt":
                title = "权限确认提醒"
                desc = msg[:80] if msg else "Claude 正等待您的操作授权"
            else:
                title = data.get("title", "Claude 提醒")
                desc = msg[:80] if msg else "您有一条新的终端通知"

            send_to_daemon(DAEMON_URL_NOTIFY, {
                "type": "prompt",
                "title": title,
                "desc": desc,
                "options": ["收到 (OK)", "关闭 (X)"]
            }, timeout=3)
            sys.exit(0)

        # =========================================================
        # 4. Stop: 回答结束，副屏恢复绿灯待命态 (非阻塞广播)
        # =========================================================
        elif event_name == "Stop":
            send_to_daemon(DAEMON_URL_NOTIFY, {
                "type": "status",
                "status": "idle",
                "desc": "本轮问答已完成，等待输入"
            }, timeout=3)
            sys.exit(0)

        else:
            sys.exit(0)

    except Exception:
        sys.exit(0)

if __name__ == "__main__":
    main()
