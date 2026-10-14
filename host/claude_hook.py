#!/usr/bin/env python3
import sys
import json
import urllib.request

def format_prompt(event_name, tool_name, tool_input):
    """根据不同的工具类型生成副屏友好的标题与描述"""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        # 只截取前80字符避免超出屏幕
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
        # 其它未枚举工具
        desc = json.dumps(tool_input, ensure_ascii=False) if tool_input else "是否允许执行此操作？"
        return f"权限确认: {tool_name[:15]}", desc[:80]

def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.exit(0)

        data = json.loads(raw_input)
        event_name = data.get("hook_event_name", "PreToolUse")
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        # 1. 如果是 PreToolUse 事件：
        if event_name == "PreToolUse":
            # 仅针对高危工具（Bash 和写入/编辑）主动在执行前拦截
            if tool_name == "Bash":
                command = tool_input.get("command", "").strip()
                # 针对绝对安全的只读命令直接放行
                if command in ["pwd", "ls", "git status", "git branch"]:
                    sys.exit(0)
                title, desc = format_prompt(event_name, tool_name, tool_input)
            elif tool_name in ["Write", "Edit"]:
                title, desc = format_prompt(event_name, tool_name, tool_input)
            else:
                # 其它工具（如 WebSearch、Read 等）在 PreToolUse 阶段不主动拦截，
                # 若 Claude Code 需要用户权限确认，后续会触发 PermissionRequest 事件
                sys.exit(0)

        # 2. 如果是 PermissionRequest 事件：
        # 说明 Claude Code 正在向用户发起授权确认（如 WebSearch、网络请求、未授权操作等）
        elif event_name == "PermissionRequest":
            title, desc = format_prompt(event_name, tool_name, tool_input)

        else:
            sys.exit(0)

        # 3. 向本机 Claude Deck 守护进程发送询问请求
        req = urllib.request.Request(
            "http://127.0.0.1:19842/prompt",
            data=json.dumps({"title": title, "desc": desc}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=125) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                decision = res.get("decision", "allow")

                if event_name == "PermissionRequest":
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

                elif event_name == "PreToolUse":
                    if decision == "deny":
                        output = {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "deny",
                                "permissionDecisionReason": "用户在 Claude-Deck 物理副屏上点击了拒绝"
                            }
                        }
                        print(json.dumps(output))
                        sys.exit(0)
                    else:
                        output = {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "allow"
                            }
                        }
                        print(json.dumps(output))
                        sys.exit(0)

        except Exception:
            # 若蓝牙未开或守护进程未启动，静默退出由终端默认交互接管
            sys.exit(0)

    except Exception:
        sys.exit(0)

if __name__ == "__main__":
    main()
