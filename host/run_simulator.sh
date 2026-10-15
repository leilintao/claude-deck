#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR/.."

echo "================================================================"
echo "   🖥️ 启动 Claude Deck 桌面原生仿真器 (HMI v2.2 - 320x240)       "
echo "================================================================"
echo "交互亮点:"
echo "  • 点击中央黑色代码框 ➡️ 翻页查看长命令全文 ([▾ 点击翻页 1/2])"
echo "  • 点击顶栏右上角 [🔊SND] ➡️ 任意界面一键切换静音"
echo "  • 卡片底部实时缩短的动态倒计时进度条 + 2.5s 自动归位待命大盘"
echo "快捷键:"
echo "  1-4 : 切换 Bash / Git / Write / Edit 长命令授权场景"
echo "  5/0 : 多选分页问答页 (AskUserQuestion 2行卡片 + 右侧滑轨 + PREV/NEXT)"
echo "  6   : MCP 数据库高危调用场景"
echo "  7   : 待命态 (Idle)"
echo "  8   : 思考态 (Thinking)"
echo "  9   : 超时态 (Expired)"
echo "  M   : 切换静音 (Mute)"
echo "  S   : 切换 1X (320x240) / 2X (640x480) 缩放"
echo "  Q   : 退出模拟器"
echo "================================================================"

uv run --with pygame-ce python3 host/virtual_deck.py
