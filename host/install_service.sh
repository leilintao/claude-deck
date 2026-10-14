#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PLIST_PATH="$HOME/Library/LaunchAgents/com.claude.deck.plist"
LOG_PATH="$HOME/Library/Logs/claude_deck.log"

echo "正在将 Claude Deck 注册为 macOS 后台服务..."

cat <<EOF > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude.deck</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which uv)</string>
        <string>run</string>
        <string>--with</string>
        <string>bleak</string>
        <string>python3</string>
        <string>$DIR/claude_deck_daemon.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DIR/..</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_PATH</string>
    <key>StandardErrorPath</key>
    <string>$LOG_PATH</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "✅ Claude Deck 服务已成功启动！"
echo "日志文件位置: $LOG_PATH"
echo "查看状态: launchctl list | grep claude.deck"
