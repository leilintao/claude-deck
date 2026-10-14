#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR/.."

echo "=================================================="
echo "   🤖 Claude Deck 蓝牙副屏服务正在启动...          "
echo "=================================================="

uv run --with bleak python3 host/claude_deck_daemon.py
