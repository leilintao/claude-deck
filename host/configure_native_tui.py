#!/usr/bin/env python3
"""Install the Claude Code hooks required by Claude Deck."""

import json
from pathlib import Path
import shutil


def ensure_command_hook(hooks, event, matcher, command):
    entries = hooks.setdefault(event, [])
    target = next((entry for entry in entries if entry.get("matcher") == matcher), None)
    if target is None:
        target = {"matcher": matcher, "hooks": []}
        entries.append(target)
    commands = target.setdefault("hooks", [])
    if not any(item.get("type") == "command" and item.get("command") == command for item in commands):
        commands.append({"type": "command", "command": command})


def main():
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    backup_path = settings_path.with_suffix(".json.deck-backup")
    if settings_path.exists() and not backup_path.exists():
        shutil.copy2(settings_path, backup_path)

    hook_path = Path(__file__).with_name("claude_hook.py")
    command = f"python3 {hook_path}"
    hooks = settings.setdefault("hooks", {})
    ensure_command_hook(hooks, "PreToolUse", "AskUserQuestion", command)
    ensure_command_hook(hooks, "PostToolUse", "AskUserQuestion", command)
    ensure_command_hook(hooks, "PermissionRequest", "", command)

    temporary = settings_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(settings_path)
    print("Claude Deck 问题同步与权限审批 Hook 已配置。")
    print(f"备份：{backup_path}")


if __name__ == "__main__":
    main()
