#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR/.."

echo "=================================================="
echo "   🖥️ 启动 Claude Deck 桌面原生仿真器 (320x240)    "
echo "=================================================="
echo "快捷键:"
echo "  1-6 : 切换 Bash / Git / Write / Edit / Web / MCP 场景"
echo "  7   : 待命态 (Idle)"
echo "  8   : 思考态 (Thinking)"
echo "  9   : 超时态 (Expired)"
echo "  S   : 切换 1X (320x240) / 2X (640x480) 缩放"
echo "  Q   : 退出模拟器"
echo "=================================================="

uv run --with pygame-ce python3 host/virtual_deck.py
