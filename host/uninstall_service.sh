#!/bin/bash
PLIST_PATH="$HOME/Library/LaunchAgents/com.claude.deck.plist"

if [ -f "$PLIST_PATH" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "✅ Claude Deck 后台服务已卸载并停止。"
else
    echo "未发现已安装的服务。"
fi
