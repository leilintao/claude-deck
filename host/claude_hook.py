#!/usr/bin/env python3
import sys
import json
import urllib.request

def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.exit(0)

        data = json.loads(raw_input)
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        # 仅针对高危工具（Bash 执行命令、文件编辑修改）唤醒物理副屏确认
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            # 常见只读命令可直接跳过副屏（如 git status, ls, pwd），也可全量拦截
            title = "终端命令执行"
            desc = f"是否允许执行: {command}"
        elif tool_name in ["Write", "Edit"]:
            file_path = tool_input.get("file_path", "")
            title = f"文件写入 ({tool_name})"
            desc = f"目标文件: {file_path}"
        else:
            # 其余只读类工具（如 Read、Grep、Glob）无需物理确认，直接放行
            sys.exit(0)

        # 向本机 Claude Deck 守护进程发送询问请求
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
                if decision == "deny":
                    sys.stderr.write("⚠️ 操作已被用户在 Claude-Deck 物理副屏上点击拒绝。\n")
                    sys.exit(2) # 退出码 2：正式拦截 Claude Code 本次工具调用！
                else:
                    sys.exit(0) # 退出码 0：允许调用，继续执行
        except Exception:
            # 若蓝牙未开或守护进程未启动，静默放行由终端默认交互接管，不卡死流程
            sys.exit(0)

    except Exception:
        sys.exit(0)

if __name__ == "__main__":
    main()
