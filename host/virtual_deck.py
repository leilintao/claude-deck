#!/usr/bin/env python3
"""
Claude Deck 桌面原生交互模拟器 (Virtual Deck Simulator)
===================================================
专为 ESP32-2432S028 (320x240) HMI v2.0 打造的原生桌面仿真器。
无需烧录开发板，直接在 Mac 桌面上验证所有 UI 场景、触控热区、反色动效与业务闭环。

运行方法:
  uv run --with pygame python3 host/virtual_deck.py
快捷键:
  1-6 : 切换各种真实授权场景 (Bash/Git/Write/Edit/Web/MCP)
  7   : 切换到待命就绪态 (Idle Standby)
  8   : 切换到深度思考态 (Thinking)
  9   : 切换到超时熔断态 (Expired)
  S   : 切换 1X (320x240 物理尺寸) / 2X (640x480 高清放大)
  Q   : 退出模拟器
"""

import json
import math
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import pygame
except ImportError:
    print("请使用 uv 运行: uv run --with pygame python3 host/virtual_deck.py")
    sys.exit(1)

# ==========================================
# 1. 颜色规范 (Cyberdeck Studio 2.0 RGB888)
# ==========================================
COLOR_BG = (9, 10, 15)           # #090A0F Ultra-deep Obsidian
COLOR_HEADER_BG = (16, 18, 26)    # #10121A Status Bar
COLOR_HEADER_LINE = (35, 42, 59)  # #232A3B Header Divider
COLOR_CARD_BG = (19, 22, 32)      # #131620 Card Surface
COLOR_CARD_BORDER = (40, 48, 68)  # #283044 Card Edge
COLOR_CARD_HEADER = (26, 30, 44)  # #1A1E2C Window Titlebar
COLOR_CODE_BG = (7, 8, 12)        # #07080C Terminal Codebox
COLOR_TERRACOTTA = (224, 122, 95) # #E07A5F Anthropic Terracotta
COLOR_AMBER_GLOW = (245, 158, 11) # #F59E0B Warning / Active Amber
COLOR_AMBER_HI = (252, 211, 77)   # #FCD34D Bright Amber
COLOR_TEXT_WHITE = (255, 255, 255)# #FFFFFF Crisp White
COLOR_TEXT_MUTED = (148, 163, 184)# #94A3B8 Slate Gray
COLOR_TEXT_DIM = (100, 116, 139)  # #64748B Dark Slate

COLOR_GREEN_BTN = (6, 78, 59)     # #064E3B Deep Emerald Surface
COLOR_GREEN_HI = (52, 211, 153)   # #34D399 Bright Emerald Highlight
COLOR_GREEN_BORDER = (16, 185, 129)# #10B981 Emerald Border
COLOR_GREEN_MINT = (167, 243, 208)# #A7F3D0 Light Mint

COLOR_RED_BTN = (69, 10, 10)      # #450A0A Deep Crimson Surface
COLOR_RED_HI = (248, 113, 113)    # #F87171 Bright Rose Highlight
COLOR_RED_BORDER = (239, 68, 68)  # #EF4444 Crimson Border
COLOR_RED_ROSE = (254, 205, 211)  # #FECDD3 Light Rose

COLOR_CYAN_ACCENT = (6, 182, 212) # #06B6D4 Electric Cyan
COLOR_CYAN_HI = (103, 232, 249)   # #67E8F9 Bright Cyan
COLOR_CYAN_BG = (8, 47, 73)       # #082F49 Deep Cyan Surface

# 物理按键坐标 (320x240 原生坐标系)
BTN_Y = 162
BTN_H = 68
BTN1_X = 8
BTN1_W = 148
BTN2_X = 164
BTN2_W = 148

SCENARIOS = {
    "1": {
        "badge": "[BASH EXEC]",
        "tag": "HIGH RISK",
        "title": "终端命令执行审批",
        "line1": "$ rm -rf dist && npm run build",
        "line2": "清理构建目录并重新打包生产资产",
        "state": "PROMPT",
        "accent": COLOR_AMBER_GLOW,
        "opt1": "允许 (Y)",
        "opt2": "拒绝 (N)",
    },
    "2": {
        "badge": "[GIT PUSH]",
        "tag": "CRITICAL",
        "title": "远端分支强制推送",
        "line1": "$ git push origin main --force",
        "line2": "强制覆盖远程仓库分支历史记录",
        "state": "PROMPT",
        "accent": (239, 68, 68),
        "opt1": "强推 (Y)",
        "opt2": "取消 (N)",
    },
    "3": {
        "badge": "[WRITE FILE]",
        "tag": "SRC WRITE",
        "title": "新建核心代码文件",
        "line1": "目标: src/auth/jwt_provider.py",
        "line2": "新建用户认证核心鉴权模块 (+148 行)",
        "state": "PROMPT",
        "accent": (168, 85, 247),
        "opt1": "写入 (Y)",
        "opt2": "放弃 (N)",
    },
    "4": {
        "badge": "[EDIT PATCH]",
        "tag": "CONFIG EDIT",
        "title": "配置文件代码补丁",
        "line1": "目标: config/production.json",
        "line2": "修改生产环境变量与 API 鉴权密钥",
        "state": "PROMPT",
        "accent": (168, 85, 247),
        "opt1": "应用 (Y)",
        "opt2": "撤销 (N)",
    },
    "5": {
        "badge": "[WEB SEARCH]",
        "tag": "NETWORK",
        "title": "联网检索授权确认",
        "line1": "检索: Next.js 15 Server Actions",
        "line2": "查询最新的官方架构文档与破坏性变动",
        "state": "PROMPT",
        "accent": COLOR_CYAN_ACCENT,
        "opt1": "搜索 (Y)",
        "opt2": "跳过 (N)",
    },
    "6": {
        "badge": "[MCP TOOL]",
        "tag": "MCP POSTGRES",
        "title": "MCP 扩展工具调用",
        "line1": "服务: mcp__postgres",
        "line2": "执行: DROP TABLE staging_users CASCADE;",
        "state": "PROMPT",
        "accent": (192, 132, 252),
        "opt1": "放行 (Y)",
        "opt2": "拦截 (N)",
    },
    "7": {
        "badge": "CLAUDE DECK",
        "tag": "STANDBY",
        "title": "Claude Code 待命就绪",
        "line1": "把窗口放后台去吧 · 遇到询问副屏会提醒",
        "line2": "STANDBY · BLE CONNECTED · FAIL-SAFE DENY",
        "state": "IDLE",
        "accent": COLOR_GREEN_BTN,
    },
    "8": {
        "badge": "[THINKING CoT]",
        "tag": "REASONING",
        "title": "Claude 正在深度思考中...",
        "line1": "分析系统架构拓扑与调用链依赖...",
        "line2": "THINKING · PLEASE WAIT",
        "state": "THINKING",
        "accent": COLOR_CYAN_ACCENT,
    },
    "9": {
        "badge": "[EXPIRED]",
        "tag": "TIMEOUT",
        "title": "请求已超时 (120s)",
        "line1": "超过 120 秒未收到物理触控响应",
        "line2": "为保护工程安全，终端已自动拦截",
        "state": "EXPIRED",
        "accent": COLOR_RED_BTN,
    },
}

class SimHTTPHandler(BaseHTTPRequestHandler):
    simulator = None

    def do_POST(self):
        if self.path == "/prompt":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                ev = threading.Event()
                req = {
                    "data": data,
                    "event": ev,
                    "response": "deny"
                }
                SimHTTPHandler.simulator.ipc_queue.put(("prompt", req))
                # 等待用户在模拟器中点击或超时 (120s)
                ev.wait(timeout=120)
                decision = req["response"]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"decision": decision}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
        elif self.path == "/status":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                SimHTTPHandler.simulator.ipc_queue.put(("status", data))
                self.send_response(200)
                self.end_headers()
            except Exception:
                self.send_response(500)
                self.end_headers()
        elif self.path == "/question":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                ev = threading.Event()
                req = {
                    "data": data,
                    "event": ev,
                    "index": -1,   # -1 = 超时/未答
                }
                SimHTTPHandler.simulator.ipc_queue.put(("question", req))
                # 等待用户在模拟器中点击选项或 120s 超时
                answered = ev.wait(timeout=120)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                idx = req["index"]
                self.wfile.write(json.dumps({
                    "answered": answered and idx >= 0,
                    "index": idx if idx >= 0 else None,
                }).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"simulator": "active"}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class VirtualDeckSimulator:
    def __init__(self):
        pygame.init()
        pygame.font.init()
        pygame.mixer.init(frequency=44100, size=-16, channels=1)

        self.scale = 2  # 默认 2X 高清缩放 (640x480)
        self.raw_w = 320
        self.raw_h = 240
        self.window = pygame.display.set_mode((self.raw_w * self.scale, self.raw_h * self.scale))
        pygame.display.set_caption("Claude Deck 桌面原生仿真器 (ESP32-2432S028 320x240)")

        self.raw_surface = pygame.Surface((self.raw_w, self.raw_h))

        # 字体加载 (尝试使用系统中文字体，回退使用系统无衬线)
        # PingFang SC 无法被 pygame 的 match_font 识别 (字体名解析不一致)，
        # 改用 Hiragino Sans GB — macOS 内置无衬线 CJK 字体，小字号下清晰锐利，
        # 风格接近固件 LovyanGFX 的渲染观感。
        self.font_zh = self.pick_font([
            "Hiragino Sans GB",
            "STHeiti Medium",
            "Arial Unicode MS",
            "Helvetica",
            "Arial",
        ])

        self.current_scenario_key = "1"
        self.current_scenario = SCENARIOS[self.current_scenario_key]
        self.current_state = self.current_scenario["state"]
        self.touch_flash_btn = None
        self.touch_flash_until = 0
        self.last_touch_pos = None
        self.last_touch_time = 0

        # HTTP IPC 实时桥接支持
        self.ipc_queue = queue.Queue()
        self.pending_request = None
        self.pending_question = None   # 多选题 IPC 请求
        SimHTTPHandler.simulator = self
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 19843), SimHTTPHandler)
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.http_thread.start()
        print("  🌐 虚拟副屏 IPC 桥接已在 http://127.0.0.1:19843 启动 (支持 Claude Code 实时通信)")

    def pick_font(self, font_names):
        for name in font_names:
            try:
                if pygame.font.match_font(name):
                    return name
            except Exception:
                continue
        return None

    def get_font(self, size, bold=False):
        return pygame.font.SysFont(self.font_zh, size, bold=bold)

    def play_beep(self, freq=2400, duration_ms=40):
        # 实时生成合成方波蜂鸣
        sample_rate = 44100
        n_samples = int(sample_rate * (duration_ms / 1000.0))
        buf = bytearray(n_samples * 2)
        period = sample_rate / freq
        for i in range(n_samples):
            val = 8000 if ((i % period) < (period / 2)) else -8000
            val_bytes = val.to_bytes(2, byteorder='little', signed=True)
            buf[i*2] = val_bytes[0]
            buf[i*2+1] = val_bytes[1]
        sound = pygame.mixer.Sound(buffer=bytes(buf))
        sound.set_volume(0.2)
        sound.play()

    def draw_spark(self, s, cx, cy, r, color):
        r3 = max(1, int(r * 0.3))
        points = [
            (cx, cy - r),
            (cx + r3, cy - r3),
            (cx + r, cy),
            (cx + r3, cy + r3),
            (cx, cy + r),
            (cx - r3, cy + r3),
            (cx - r, cy),
            (cx - r3, cy - r3),
        ]
        pygame.draw.polygon(s, color, points)

    def draw_chevron(self, s, x, y, h, color):
        mid_y = y + h // 2
        pygame.draw.line(s, color, (x, y), (x + 5, mid_y), 2)
        pygame.draw.line(s, color, (x + 5, mid_y), (x, y + h), 2)

    def draw_tactile_button(self, s, x, y, w, h, is_allow, label, sublabel, pressed=False):
        if pressed:
            pygame.draw.rect(s, COLOR_TEXT_WHITE, (x, y, w, h), border_radius=8)
            pygame.draw.rect(s, COLOR_GREEN_HI if is_allow else COLOR_RED_HI, (x, y, w, h), width=2, border_radius=8)
            f_l = self.get_font(15, bold=True)
            f_s = self.get_font(10, bold=True)
            t_l = f_l.render(label, True, (0, 0, 0))
            t_s = f_s.render(sublabel, True, (60, 60, 60))
            s.blit(t_l, (x + w // 2 - t_l.get_width() // 2, y + 16))
            s.blit(t_s, (x + w // 2 - t_s.get_width() // 2, y + 40))
            return

        surf_c = COLOR_GREEN_BTN if is_allow else COLOR_RED_BTN
        hi_c = COLOR_GREEN_HI if is_allow else COLOR_RED_HI
        bd_c = COLOR_GREEN_BORDER if is_allow else COLOR_RED_BORDER
        sub_c = COLOR_GREEN_MINT if is_allow else COLOR_RED_ROSE

        pygame.draw.rect(s, surf_c, (x, y, w, h), border_radius=8)
        pygame.draw.rect(s, bd_c, (x, y, w, h), width=1, border_radius=8)
        pygame.draw.line(s, hi_c, (x + 8, y + 2), (x + w - 8, y + 2), 2)

        cx = x + 24
        cy = y + h // 2
        if is_allow:
            pygame.draw.line(s, hi_c, (cx - 9, cy), (cx - 3, cy + 7), 3)
            pygame.draw.line(s, hi_c, (cx - 3, cy + 7), (cx + 9, cy - 7), 3)
        else:
            pygame.draw.line(s, hi_c, (cx - 7, cy - 7), (cx + 7, cy + 7), 3)
            pygame.draw.line(s, hi_c, (cx - 7, cy + 7), (cx + 7, cy - 7), 3)

        f_lbl = self.get_font(15, bold=True)
        f_sub = self.get_font(10, bold=True)
        t_l = f_lbl.render(label, True, COLOR_TEXT_WHITE)
        t_s = f_sub.render(sublabel, True, sub_c)
        s.blit(t_l, (x + 44, y + 14))
        s.blit(t_s, (x + 44, y + 38))

    def draw_status_bar(self):
        s = self.raw_surface
        pygame.draw.rect(s, COLOR_HEADER_BG, (0, 0, 320, 24))
        pygame.draw.line(s, COLOR_HEADER_LINE, (0, 24), (320, 24), 1)

        # Left brand: Spark + title
        self.draw_spark(s, 13, 12, 5, COLOR_TERRACOTTA)
        f_brand = self.get_font(11, bold=True)
        s.blit(f_brand.render("CLAUDE DECK", True, COLOR_TEXT_WHITE), (23, 5))

        # Center Mode Pill
        mode_text = "[ STANDBY ]"
        mode_color = COLOR_GREEN_HI
        if self.current_state == "PROMPT":
            mode_text = "[ APPROVAL ]"
            mode_color = COLOR_AMBER_GLOW
        elif self.current_state == "THINKING":
            mode_text = "[ REASONING ]"
            mode_color = COLOR_CYAN_HI
        elif self.current_state == "QUESTION":
            mode_text = "[ QUESTION ]"
            mode_color = COLOR_CYAN_HI
        elif "ANSWERED" in self.current_state:
            mode_text = "[ APPROVED ]" if "ALLOW" in self.current_state else "[ STOPPED ]"
            mode_color = COLOR_GREEN_HI if "ALLOW" in self.current_state else COLOR_RED_HI
        elif self.current_state == "EXPIRED":
            mode_text = "[ TIMEOUT ]"
            mode_color = COLOR_RED_HI

        pill_w = 88
        pill_x = 160 - pill_w // 2
        pygame.draw.rect(s, COLOR_CARD_BG, (pill_x, 3, pill_w, 18), border_radius=4)
        pygame.draw.rect(s, COLOR_CARD_BORDER, (pill_x, 3, pill_w, 18), width=1, border_radius=4)
        f_mode = self.get_font(10, bold=True)
        t_mode = f_mode.render(mode_text, True, mode_color)
        s.blit(t_mode, (160 - t_mode.get_width() // 2, 5))

        # Right Telemetry: Signal bars & Battery
        sig_x = 264
        for i in range(4):
            h = 3 + i * 2
            c = COLOR_GREEN_HI
            pygame.draw.rect(s, c, (sig_x + i * 4, 16 - h, 2, h))

        bat_x = 284
        pygame.draw.rect(s, COLOR_TEXT_MUTED, (bat_x, 8, 16, 9), width=1)
        pygame.draw.rect(s, COLOR_TEXT_MUTED, (bat_x + 16, 10, 2, 5))
        pygame.draw.rect(s, COLOR_GREEN_HI, (bat_x + 2, 10, 12, 5))
        f_bat = self.get_font(9, bold=True)
        s.blit(f_bat.render("5V", True, COLOR_GREEN_HI), (bat_x + 20, 6))

    def render(self):
        s = self.raw_surface
        s.fill(COLOR_BG)
        self.draw_status_bar()

        now = time.time()
        sc = self.current_scenario

        if self.current_state == "IDLE":
            # 1. Center Card Container (8, 28, 304, 134)
            cx, cy, cw, ch = 8, 28, 304, 134
            pygame.draw.rect(s, COLOR_CARD_BG, (cx, cy, cw, ch), border_radius=8)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (cx, cy, cw, ch), width=1, border_radius=8)

            # Tech Corner Brackets
            bracket_c = (55, 65, 85)
            blen = 8
            pygame.draw.line(s, bracket_c, (cx + 4, cy + 4), (cx + 4 + blen, cy + 4), 1)
            pygame.draw.line(s, bracket_c, (cx + 4, cy + 4), (cx + 4, cy + 4 + blen), 1)
            pygame.draw.line(s, bracket_c, (cx + cw - 4 - blen, cy + 4), (cx + cw - 4, cy + 4), 1)
            pygame.draw.line(s, bracket_c, (cx + cw - 4, cy + 4), (cx + cw - 4, cy + 4 + blen), 1)
            pygame.draw.line(s, bracket_c, (cx + 4, cy + ch - 4), (cx + 4 + blen, cy + ch - 4), 1)
            pygame.draw.line(s, bracket_c, (cx + 4, cy + ch - 4), (cx + 4, cy + ch - 4 - blen), 1)
            pygame.draw.line(s, bracket_c, (cx + cw - 4 - blen, cy + ch - 4), (cx + cw - 4, cy + ch - 4), 1)
            pygame.draw.line(s, bracket_c, (cx + cw - 4, cy + ch - 4), (cx + cw - 4, cy + ch - 4 - blen), 1)

            # Center Radar Circles & Claude Spark
            ecx, ecy = 160, 70
            pygame.draw.circle(s, (25, 45, 40), (ecx, ecy), 30, width=1)
            pygame.draw.circle(s, COLOR_GREEN_BTN, (ecx, ecy), 22, width=1)
            pygame.draw.line(s, COLOR_GREEN_HI, (ecx, ecy - 34), (ecx, ecy - 28), 1)
            pygame.draw.line(s, COLOR_GREEN_HI, (ecx, ecy + 28), (ecx, ecy + 34), 1)
            pygame.draw.line(s, COLOR_GREEN_HI, (ecx - 34, ecy), (ecx - 28, ecy), 1)
            pygame.draw.line(s, COLOR_GREEN_HI, (ecx + 28, ecy), (ecx + 34, ecy), 1)

            pygame.draw.circle(s, COLOR_GREEN_BTN, (ecx, ecy), 13)
            self.draw_spark(s, ecx, ecy, 8, COLOR_GREEN_HI)

            f_t = self.get_font(15, bold=True)
            t_title = f_t.render("Claude Code 终端待命", True, COLOR_TEXT_WHITE)
            s.blit(t_title, (160 - t_title.get_width() // 2, 108))

            f_sub = self.get_font(11)
            t_sub = f_sub.render("物理安全守护已激活 · 遇高危调用副屏提醒", True, COLOR_TEXT_MUTED)
            s.blit(t_sub, (160 - t_sub.get_width() // 2, 132))

            # Bottom 3-Card Telemetry Dock (Y: 168, H: 64)
            dock_y = 168
            dock_h = 64
            card_w = 96
            metrics = [
                ("BLE LINK", "ONLINE", COLOR_GREEN_HI, COLOR_GREEN_BTN),
                ("POLICY", "FAIL-SAFE", COLOR_AMBER_HI, (69, 26, 3)),
                ("TIMEOUT", "120s AUTO", COLOR_CYAN_HI, COLOR_CYAN_BG),
            ]
            for i, (m_lbl, m_val, m_fg, m_bg) in enumerate(metrics):
                mx = 8 + i * (card_w + 8)
                pygame.draw.rect(s, COLOR_CARD_BG, (mx, dock_y, card_w, dock_h), border_radius=6)
                pygame.draw.rect(s, COLOR_CARD_BORDER, (mx, dock_y, card_w, dock_h), width=1, border_radius=6)
                f_ml = self.get_font(9, bold=True)
                s.blit(f_ml.render(m_lbl, True, COLOR_TEXT_DIM), (mx + 10, dock_y + 8))
                pygame.draw.rect(s, m_bg, (mx + 8, dock_y + 26, card_w - 16, 26), border_radius=4)
                f_mv = self.get_font(11, bold=True)
                t_mv = f_mv.render(m_val, True, m_fg)
                s.blit(t_mv, (mx + card_w // 2 - t_mv.get_width() // 2, dock_y + 31))

        elif self.current_state == "THINKING":
            cx, cy, cw, ch = 8, 28, 304, 198
            pygame.draw.rect(s, COLOR_CARD_BG, (cx, cy, cw, ch), border_radius=8)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (cx, cy, cw, ch), width=1, border_radius=8)

            ecx, ecy = 160, 78
            pygame.draw.circle(s, (15, 60, 85), (ecx, ecy), 36, width=1)
            pygame.draw.circle(s, COLOR_CYAN_BG, (ecx, ecy), 26, width=1)
            pygame.draw.circle(s, COLOR_CYAN_BG, (ecx, ecy), 16)
            pygame.draw.circle(s, COLOR_CYAN_ACCENT, (ecx, ecy), 16, width=1)
            self.draw_spark(s, ecx, ecy, 9, COLOR_CYAN_HI)

            for angle_deg in range(0, 360, 45):
                rad = math.radians(angle_deg)
                ox = ecx + int(math.cos(rad) * 36)
                oy = ecy + int(math.sin(rad) * 36)
                pygame.draw.circle(s, COLOR_CYAN_ACCENT, (ox, oy), 2)

            f_t = self.get_font(15, bold=True)
            t_title = f_t.render("Claude 正在深度思考中...", True, COLOR_TEXT_WHITE)
            s.blit(t_title, (160 - t_title.get_width() // 2, 124))

            # Description box
            tbx, tby, tbw, tbh = cx + 12, 148, cw - 24, 44
            pygame.draw.rect(s, COLOR_CODE_BG, (tbx, tby, tbw, tbh), border_radius=4)
            pygame.draw.rect(s, (25, 45, 65), (tbx, tby, tbw, tbh), width=1, border_radius=4)
            pygame.draw.rect(s, COLOR_CYAN_ACCENT, (tbx, tby, 3, tbh))
            f_d1 = self.get_font(11)
            f_d2 = self.get_font(10)
            s.blit(f_d1.render(sc.get("line1", "分析系统架构拓扑与调用链依赖..."), True, COLOR_TEXT_MUTED), (tbx + 8, tby + 6))
            s.blit(f_d2.render("CoT 阶段: 权衡系统安全策略与文件改动范围", True, COLOR_TEXT_DIM), (tbx + 8, tby + 24))

            bar_y = 202
            f_sc = self.get_font(10, bold=True)
            s.blit(f_sc.render("■ ■ ■ ■ ■ □ □ □", True, COLOR_CYAN_ACCENT), (cx + 12, bar_y))
            f_dim = self.get_font(9, bold=True)
            t_dim = f_dim.render("REASONING IN PROGRESS", True, COLOR_TEXT_DIM)
            s.blit(t_dim, (cx + cw - 12 - t_dim.get_width(), bar_y))

        elif self.current_state == "EXPIRED":
            cx, cy, cw, ch = 8, 28, 304, 198
            pygame.draw.rect(s, COLOR_CARD_BG, (cx, cy, cw, ch), border_radius=8)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (cx, cy, cw, ch), width=1, border_radius=8)

            ecx, ecy = 160, 76
            pygame.draw.circle(s, (69, 10, 10), (ecx, ecy), 36, width=1)
            pygame.draw.circle(s, COLOR_RED_BTN, (ecx, ecy), 26, width=1)
            pygame.draw.circle(s, COLOR_RED_BTN, (ecx, ecy), 18)
            pygame.draw.line(s, COLOR_RED_HI, (ecx - 7, ecy - 7), (ecx + 7, ecy + 7), 3)
            pygame.draw.line(s, COLOR_RED_HI, (ecx - 7, ecy + 7), (ecx + 7, ecy - 7), 3)

            f_t = self.get_font(15, bold=True)
            t_title = f_t.render("请求已超时 · 120s TIMEOUT", True, COLOR_RED_HI)
            s.blit(t_title, (160 - t_title.get_width() // 2, 124))

            f_sub1 = self.get_font(12, bold=True)
            t_sub1 = f_sub1.render("自动触发 Fail-Safe 熔断保护", True, COLOR_TEXT_WHITE)
            s.blit(t_sub1, (160 - t_sub1.get_width() // 2, 148))

            f_sub2 = self.get_font(11)
            t_sub2 = f_sub2.render("终端已安全拦截，代码库受控未受损", True, COLOR_TEXT_MUTED)
            s.blit(t_sub2, (160 - t_sub2.get_width() // 2, 172))

        elif self.current_state == "ANSWERED_ALLOW" or self.current_state == "ANSWERED_DENY":
            is_allow = self.current_state == "ANSWERED_ALLOW"
            cx, cy, cw, ch = 8, 28, 304, 198
            pygame.draw.rect(s, COLOR_CARD_BG, (cx, cy, cw, ch), border_radius=8)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (cx, cy, cw, ch), width=1, border_radius=8)

            ecx, ecy = 160, 76
            ring_c = (25, 45, 40) if is_allow else (69, 10, 10)
            btn_c = COLOR_GREEN_BTN if is_allow else COLOR_RED_BTN
            hi_c = COLOR_GREEN_HI if is_allow else COLOR_RED_HI

            pygame.draw.circle(s, ring_c, (ecx, ecy), 36, width=1)
            pygame.draw.circle(s, btn_c, (ecx, ecy), 26, width=1)
            pygame.draw.circle(s, btn_c, (ecx, ecy), 18)

            if is_allow:
                pygame.draw.line(s, hi_c, (ecx - 8, ecy), (ecx - 2, ecy + 6), 3)
                pygame.draw.line(s, hi_c, (ecx - 2, ecy + 6), (ecx + 8, ecy - 6), 3)
            else:
                pygame.draw.line(s, hi_c, (ecx - 7, ecy - 7), (ecx + 7, ecy + 7), 3)
                pygame.draw.line(s, hi_c, (ecx - 7, ecy + 7), (ecx + 7, ecy - 7), 3)

            f_t = self.get_font(15, bold=True)
            t_title = f_t.render("授权已放行 · APPROVED" if is_allow else "操作已安全拦截 · STOPPED", True, hi_c)
            s.blit(t_title, (160 - t_title.get_width() // 2, 124))

            f_sub1 = self.get_font(12, bold=True)
            t_sub1 = f_sub1.render("反馈已成功回传终端", True, COLOR_TEXT_WHITE)
            s.blit(t_sub1, (160 - t_sub1.get_width() // 2, 148))

            f_sub2 = self.get_font(11)
            t_sub2 = f_sub2.render("指令已回传终端，正在继续执行..." if is_allow else "终端已终止执行，模型重新规划中...", True, COLOR_TEXT_MUTED)
            s.blit(t_sub2, (160 - t_sub2.get_width() // 2, 172))

        elif self.current_state == "ANSWERED_QUESTION":
            cx, cy, cw, ch = 8, 28, 304, 198
            pygame.draw.rect(s, COLOR_CARD_BG, (cx, cy, cw, ch), border_radius=8)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (cx, cy, cw, ch), width=1, border_radius=8)

            ecx, ecy = 160, 76
            pygame.draw.circle(s, (15, 60, 85), (ecx, ecy), 36, width=1)
            pygame.draw.circle(s, COLOR_CYAN_BG, (ecx, ecy), 26, width=1)
            pygame.draw.circle(s, COLOR_CYAN_BG, (ecx, ecy), 18)
            self.draw_spark(s, ecx, ecy, 9, COLOR_CYAN_HI)

            f_t = self.get_font(15, bold=True)
            t_title = f_t.render("已选择，回传终端 · SUBMITTED", True, COLOR_CYAN_HI)
            s.blit(t_title, (160 - t_title.get_width() // 2, 124))

            f_sub1 = self.get_font(12, bold=True)
            t_sub1 = f_sub1.render("选择已确认，Claude 终端继续运行", True, COLOR_TEXT_WHITE)
            s.blit(t_sub1, (160 - t_sub1.get_width() // 2, 148))

            f_sub2 = self.get_font(11)
            chosen = self.current_scenario.get("chosen_label", "")
            t_sub2 = f_sub2.render(f"选项: {chosen[:24]}", True, COLOR_TEXT_MUTED)
            s.blit(t_sub2, (160 - t_sub2.get_width() // 2, 172))

        elif self.current_state == "QUESTION":
            sc = self.current_scenario
            opts = sc.get("options_list", [])

            # Header Card (8, 28, 304, 54)
            cx, cy, cw, ch = 8, 28, 304, 54
            pygame.draw.rect(s, COLOR_CARD_BG, (cx, cy, cw, ch), border_radius=8)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (cx, cy, cw, ch), width=1, border_radius=8)

            self.draw_spark(s, cx + 14, cy + 12, 4, COLOR_CYAN_HI)
            f_hdr = self.get_font(10, bold=True)
            s.blit(f_hdr.render("CLAUDE 交互提问", True, COLOR_CYAN_HI), (cx + 24, cy + 6))

            f_q = self.get_font(12, bold=True)
            t_q = f_q.render(sc.get("title", "Claude 有问题想问你")[:28], True, COLOR_TEXT_WHITE)
            s.blit(t_q, (cx + 12, cy + 26))

            # Options (2 rows)
            btn_y_start = 88
            btn_h_opt = 54
            btn_gap = 6
            n = min(len(opts), 2)
            f_lbl = self.get_font(13, bold=True)
            f_sub = self.get_font(10)

            for i in range(n):
                by = btn_y_start + i * (btn_h_opt + btn_gap)
                flashing = (self.touch_flash_btn == i and now < self.touch_flash_until)
                bg = COLOR_TEXT_WHITE if flashing else COLOR_CARD_BG
                bd = COLOR_CYAN_ACCENT if i == 0 else COLOR_CARD_BORDER
                fg = (0, 0, 0) if flashing else COLOR_TEXT_WHITE

                pygame.draw.rect(s, bg, (8, by, 304, btn_h_opt), border_radius=8)
                pygame.draw.rect(s, bd, (8, by, 304, btn_h_opt), width=1, border_radius=8)

                # Number badge
                nb_bg = COLOR_CYAN_BG if i == 0 else COLOR_CARD_HEADER
                pygame.draw.rect(s, nb_bg, (18, by + 11, 24, 32), border_radius=4)
                pygame.draw.rect(s, bd, (18, by + 11, 24, 32), width=1, border_radius=4)
                f_num = self.get_font(14, bold=True)
                t_num = f_num.render(f"{i+1}", True, COLOR_CYAN_HI if i == 0 else COLOR_TEXT_MUTED)
                s.blit(t_num, (30 - t_num.get_width() // 2, by + 17))

                # Labels
                label = opts[i][:26]
                s.blit(f_lbl.render(label, True, fg), (50, by + 10))
                s.blit(f_sub.render("轻触选择此项回传终端", True, COLOR_TEXT_MUTED), (50, by + 32))
                self.draw_chevron(s, 292, by + 18, 14, bd)

            # Bottom nav
            nav_y = 210
            pygame.draw.rect(s, COLOR_CARD_BG, (8, nav_y, 74, 24), border_radius=6)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (8, nav_y, 74, 24), width=1, border_radius=6)
            f_nav = self.get_font(10, bold=True)
            s.blit(f_nav.render("◀ PREV", True, COLOR_TEXT_DIM), (22, nav_y + 4))

            pygame.draw.rect(s, COLOR_CARD_BG, (238, nav_y, 74, 24), border_radius=6)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (238, nav_y, 74, 24), width=1, border_radius=6)
            s.blit(f_nav.render("NEXT ▶", True, COLOR_TEXT_WHITE), (252, nav_y + 4))

            t_pi = f_nav.render("第 1 / 2 页", True, COLOR_TEXT_MUTED)
            s.blit(t_pi, (160 - t_pi.get_width() // 2, nav_y + 4))

        elif self.current_state == "PROMPT":
            # 1. Main Terminal Window Card (8, 28, 304, 126, r=8)
            cx, cy, cw, ch = 8, 28, 304, 126
            pygame.draw.rect(s, COLOR_CARD_BG, (cx, cy, cw, ch), border_radius=8)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (cx, cy, cw, ch), width=1, border_radius=8)

            # Window Header Bar (macOS style dots + tag)
            pygame.draw.rect(s, COLOR_CARD_HEADER, (cx, cy, cw, 22), border_top_left_radius=8, border_top_right_radius=8)
            pygame.draw.line(s, COLOR_CARD_BORDER, (cx, cy + 22), (cx + cw, cy + 22), 1)

            pygame.draw.circle(s, (255, 95, 86), (cx + 12, cy + 11), 3)
            pygame.draw.circle(s, (255, 189, 46), (cx + 22, cy + 11), 3)
            pygame.draw.circle(s, (39, 201, 63), (cx + 32, cy + 11), 3)

            f_title = self.get_font(11, bold=True)
            t_title = f_title.render(sc["title"], True, COLOR_TEXT_MUTED)
            s.blit(t_title, (cx + 42, cy + 5))

            # Tag Pill on right
            tag_text = sc.get("tag", "CONFIRM")
            f_tag = self.get_font(9, bold=True)
            t_tag = f_tag.render(tag_text, True, COLOR_AMBER_HI)
            tag_w = t_tag.get_width() + 12
            tag_x = cx + cw - tagW if (tagW := tag_w) else cx + cw - 70
            pygame.draw.rect(s, (69, 26, 3), (cx + cw - tag_w - 4, cy + 3, tag_w, 16), border_radius=3)
            pygame.draw.rect(s, COLOR_AMBER_GLOW, (cx + cw - tag_w - 4, cy + 3, tag_w, 16), width=1, border_radius=3)
            s.blit(t_tag, (cx + cw - tag_w + 2, cy + 4))

            # Inner Terminal Codebox (16, 54, 288, 68)
            tbx, tby, tbw, tbh = cx + 8, cy + 28, cw - 16, 68
            pygame.draw.rect(s, COLOR_CODE_BG, (tbx, tby, tbw, tbh), border_radius=4)
            pygame.draw.rect(s, (25, 30, 42), (tbx, tby, tbw, tbh), width=1, border_radius=4)
            pygame.draw.rect(s, sc.get("accent", COLOR_AMBER_GLOW), (tbx, tby, 3, tbh))

            f_mono = self.get_font(12, bold=True)
            t_line1 = f_mono.render(sc.get("line1", ""), True, COLOR_TEXT_WHITE)
            s.blit(t_line1, (tbx + 8, tby + 8))

            f_subline = self.get_font(11)
            t_line2 = f_subline.render(sc.get("line2", ""), True, COLOR_TEXT_MUTED)
            s.blit(t_line2, (tbx + 8, tby + 30))

            f_dim = self.get_font(10)
            s.blit(f_dim.render("影响范围: 当前工作区及关联调用链路", True, COLOR_TEXT_DIM), (tbx + 8, tby + 48))

            # Card Footer: Security notice & Countdown
            self.draw_spark(s, cx + 14, cy + ch - 12, 4, COLOR_TERRACOTTA)
            f_foot = self.get_font(10)
            s.blit(f_foot.render("物理双键审批把关", True, COLOR_TEXT_MUTED), (cx + 24, cy + ch - 18))
            t_to = f_foot.render("⚡ 120s 超时熔断", True, COLOR_TERRACOTTA)
            s.blit(t_to, (cx + cw - 10 - t_to.get_width(), cy + ch - 18))

            # 2. Buttons
            btn1_flash = (self.touch_flash_btn == 0 and now < self.touch_flash_until)
            btn2_flash = (self.touch_flash_btn == 1 and now < self.touch_flash_until)
            self.draw_tactile_button(s, BTN1_X, BTN_Y, BTN1_W, BTN_H, True, sc.get("opt1", "允许 (Y)"), "[ CONFIRM ]", btn1_flash)
            self.draw_tactile_button(s, BTN2_X, BTN_Y, BTN2_W, BTN_H, False, sc.get("opt2", "拒绝 (N)"), "[ ABORT ]", btn2_flash)

        # 绘制最后触控点黄色小标记
        if self.last_touch_pos and (now - self.last_touch_time < 0.4):
            pygame.draw.circle(s, (255, 230, 0), self.last_touch_pos, 4)

        # 缩放至窗口并呈现
        scaled_surface = pygame.transform.scale(s, (self.raw_w * self.scale, self.raw_h * self.scale))
        self.window.blit(scaled_surface, (0, 0))
        pygame.display.flip()

    def handle_touch(self, screen_x, screen_y):
        self.last_touch_pos = (screen_x, screen_y)
        self.last_touch_time = time.time()
        print(f"[SIMULATOR TOUCH] Screen Raw Coord: ({screen_x}, {screen_y})")

        if self.current_state == "QUESTION":
            opts = self.current_scenario.get("options_list", [])
            n = min(len(opts), 4)
            btn_y_start = 92
            btn_h_opt = 30
            btn_gap = 4
            for i in range(n):
                by = btn_y_start + i * (btn_h_opt + btn_gap)
                if 8 <= screen_x <= 312 and by <= screen_y <= by + btn_h_opt:
                    print(f"👉 [QUESTION 命中] 选项 {i+1}: {opts[i]}")
                    self.touch_flash_btn = i
                    self.touch_flash_until = time.time() + 0.15
                    self.play_beep(2000 + i * 200, 50)
                    if self.pending_question:
                        self.pending_question["index"] = i
                        self.pending_question["event"].set()
                        self.pending_question = None
                    self.current_scenario["chosen_label"] = opts[i]
                    self.render()
                    pygame.time.delay(80)
                    self.current_state = "ANSWERED_QUESTION"
                    return
            return  # 点在 QUESTION 布局外，忽略

        if self.current_state != "PROMPT":
            # 允许在反馈状态下点击任意位置重置回当前场景
            if "ANSWERED" in self.current_state:
                self.current_state = "PROMPT"
            return

        # 触控命中判定 (与 main.cpp 严格相同的判定区)
        # BTN1 右边界不扩展 +6，避免与 BTN2 左边界 [158,162] 重叠
        if (BTN1_X - 6) <= screen_x <= (BTN1_X + BTN1_W) and (BTN_Y - 10) <= screen_y <= 238:
            print("👉 [触控命中] 允许 (Y) -> ALLOW")
            if self.pending_request:
                self.pending_request["response"] = "allow"
                self.pending_request["event"].set()
                self.pending_request = None
            self.touch_flash_btn = 0
            self.touch_flash_until = time.time() + 0.12
            self.play_beep(2600, 40)
            self.render()
            pygame.time.delay(60)
            self.play_beep(3200, 50)
            self.current_state = "ANSWERED_ALLOW"

        elif (BTN2_X) <= screen_x <= (BTN2_X + BTN2_W + 6) and (BTN_Y - 10) <= screen_y <= 238:
            print("👉 [触控命中] 拒绝 (N) -> DENY")
            if self.pending_request:
                self.pending_request["response"] = "deny"
                self.pending_request["event"].set()
                self.pending_request = None
            self.touch_flash_btn = 1
            self.touch_flash_until = time.time() + 0.12
            self.play_beep(1200, 100)
            self.render()
            self.current_state = "ANSWERED_DENY"

    def run(self):
        clock = pygame.time.Clock()
        print("=" * 60)
        print("  🚀 Claude Deck 桌面原生仿真器已就绪！")
        print("  快捷键:")
        print("    1-6 : 切换 Bash / Git / Write / Edit / Web / MCP 真实场景")
        print("    7   : 待命态 (Idle)")
        print("    8   : 思考态 (Thinking)")
        print("    9   : 超时态 (Expired)")
        print("    S   : 切换 1X / 2X 窗口缩放")
        print("    Q   : 退出")
        print("=" * 60)

        running = True
        while running:
            # 优先消费由外部 HTTP (Claude Code / Daemon) 注入的实时交互请求
            while not self.ipc_queue.empty():
                try:
                    cmd, payload = self.ipc_queue.get_nowait()
                    if cmd == "prompt":
                        self.pending_request = payload
                        d = payload["data"]
                        self.current_scenario = {
                            "badge": d.get("badge", "[CLAUDE APPROVAL]"),
                            "tag": d.get("tag", "CONFIRM"),
                            "title": d.get("title", "Claude 权限确认"),
                            "line1": d.get("desc", ""),
                            "line2": "通过桌面原生仿真器实时授权",
                            "state": "PROMPT",
                            "accent": COLOR_AMBER_GLOW,
                            "opt1": (d.get("options") or ["允许 (Y)"])[0],
                            "opt2": (d.get("options") or ["", "拒绝 (N)"])[1] if len(d.get("options", [])) > 1 else "拒绝 (N)",
                        }
                        self.current_state = "PROMPT"
                        self.play_beep(2400, 60)
                    elif cmd == "question":
                        self.pending_question = payload
                        d = payload["data"]
                        opts = d.get("options", [])
                        # 把选项列表存入 current_scenario 供渲染用
                        self.current_scenario = {
                            "badge": "[QUESTION]",
                            "tag": d.get("header", "CLAUDE ASK"),
                            "title": d.get("question", "Claude 有问题想问你")[:28],
                            "options_list": [o.get("label", str(o)) for o in opts],
                            "state": "QUESTION",
                            "accent": COLOR_CYAN_ACCENT,
                        }
                        self.current_state = "QUESTION"
                        # 3 声上升蜂鸣提醒 (区别于 PROMPT 的 2 声)
                        self.play_beep(1800, 40)
                        pygame.time.delay(80)
                        self.play_beep(2200, 40)
                        pygame.time.delay(80)
                        self.play_beep(2800, 60)
                    elif cmd == "status":
                        st = payload.get("status", "idle")
                        if st == "thinking":
                            self.current_state = "THINKING"
                        elif st == "expired":
                            self.current_state = "EXPIRED"
                        else:
                            self.current_state = "IDLE"
                except queue.Empty:
                    break
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_q:
                        running = False
                    elif event.key == pygame.K_s:
                        self.scale = 1 if self.scale == 2 else 2
                        self.window = pygame.display.set_mode((self.raw_w * self.scale, self.raw_h * self.scale))
                    elif chr(event.key) in SCENARIOS:
                        k = chr(event.key)
                        self.current_scenario_key = k
                        self.current_scenario = SCENARIOS[k]
                        self.current_state = self.current_scenario["state"]
                        self.play_beep(2000, 30)

                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = event.pos
                    # 转换回 320x240 原生坐标
                    raw_x = int(mx / self.scale)
                    raw_y = int(my / self.scale)
                    self.handle_touch(raw_x, raw_y)

            self.render()
            clock.tick(30)

        pygame.quit()


if __name__ == "__main__":
    sim = VirtualDeckSimulator()
    sim.run()
