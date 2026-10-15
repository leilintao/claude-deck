#!/usr/bin/env python3
"""
Claude Deck 桌面原生交互模拟器 (Virtual Deck Simulator — HMI v2.2)
==================================================================
专为 ESP32-2432S028 (320x240) 打造的原生桌面仿真器。
无需烧录开发板，直接在 Mac 桌面上验证所有 UI 场景、触控热区、代码框翻页、动态倒计时条与业务闭环。

运行方法:
  ./host/run_simulator.sh
快捷键:
  1-4 : 切换真实授权场景 (Bash 长命令翻页 / Git 强推 / Write 路径尾部缩写 / Edit 补丁)
  5/0 : 切换到多选分页问答态 (AskUserQuestion 2行卡片 + 右侧滑轨 + PREV/NEXT)
  6   : 切换到 MCP 数据库高危调用场景
  7   : 切换到待命就绪大盘 (Idle Standby)
  8   : 切换到深度思考/执行态 (Thinking / Executing)
  9   : 切换到超时熔断态 (Expired)
  M   : 切换静音 (或直接用鼠标点击顶栏 [SND/MUTE] 胶囊按钮)
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
    pygame = None


class _HeadlessSurface:
    def __init__(self, size=(320, 240)):
        self._w, self._h = size

    def fill(self, color):
        pass

    def blit(self, source, pos):
        pass

    def get_width(self):
        return self._w

    def get_height(self):
        return self._h


class _HeadlessFont:
    def __init__(self, size=12):
        self._size = size

    def size(self, text):
        width = 0
        for ch in str(text):
            width += self._size if ord(ch) > 127 else max(4, int(self._size * 0.6))
        return (width, self._size)

    def render(self, text, antialias, color):
        w, h = self.size(text)
        return _HeadlessSurface((w, h))


class _HeadlessDraw:
    @staticmethod
    def rect(*args, **kwargs):
        pass

    @staticmethod
    def line(*args, **kwargs):
        pass

    @staticmethod
    def circle(*args, **kwargs):
        pass

    @staticmethod
    def polygon(*args, **kwargs):
        pass


class _HeadlessDisplay:
    @staticmethod
    def set_mode(size):
        return _HeadlessSurface(size)

    @staticmethod
    def set_caption(title):
        pass

    @staticmethod
    def flip():
        pass


class _HeadlessTransform:
    @staticmethod
    def scale(surface, size):
        return _HeadlessSurface(size)


class _HeadlessTime:
    @staticmethod
    def delay(ms):
        time.sleep(ms / 1000.0)

    class Clock:
        def tick(self, fps):
            time.sleep(1.0 / max(1, fps))


class _HeadlessPygame:
    QUIT = 256
    KEYDOWN = 768
    MOUSEBUTTONDOWN = 1025
    K_q = ord("q")
    K_m = ord("m")
    K_s = ord("s")

    Surface = _HeadlessSurface
    draw = _HeadlessDraw
    display = _HeadlessDisplay
    transform = _HeadlessTransform
    time = _HeadlessTime

    class font:
        @staticmethod
        def init():
            pass

        @staticmethod
        def match_font(name):
            return name

        @staticmethod
        def SysFont(name, size, bold=False):
            return _HeadlessFont(size)

    class mixer:
        @staticmethod
        def init(**kwargs):
            pass

        class Sound:
            def __init__(self, buffer=b""):
                pass

            def set_volume(self, v):
                pass

            def play(self):
                pass

    class event:
        @staticmethod
        def get():
            return []

    @staticmethod
    def init():
        pass

    @staticmethod
    def quit():
        pass


if pygame is None:
    pygame = _HeadlessPygame()

# ==========================================
# 1. 颜色规范 (Cyberdeck Studio 2.2 RGB888)
# ==========================================
COLOR_BG = (9, 10, 15)             # #090A0F Ultra-deep Obsidian
COLOR_HEADER_BG = (16, 18, 26)     # #10121A Status Bar
COLOR_HEADER_LINE = (35, 42, 59)   # #232A3B Header Divider
COLOR_CARD_BG = (19, 22, 32)       # #131620 Card Surface
COLOR_CARD_BORDER = (40, 48, 68)   # #283044 Card Edge
COLOR_CARD_HEADER = (26, 30, 44)   # #1A1E2C Window Titlebar
COLOR_CODE_BG = (7, 8, 12)         # #07080C Terminal Codebox
COLOR_TERRACOTTA = (224, 122, 95)  # #E07A5F Anthropic Terracotta
COLOR_AMBER_GLOW = (245, 158, 11)  # #F59E0B Warning / Active Amber
COLOR_AMBER_HI = (252, 211, 77)    # #FCD34D Bright Amber
COLOR_TEXT_WHITE = (255, 255, 255) # #FFFFFF Crisp White
COLOR_TEXT_MUTED = (148, 163, 184) # #94A3B8 Slate Gray
COLOR_TEXT_DIM = (100, 116, 139)   # #64748B Dark Slate

COLOR_GREEN_BTN = (6, 78, 59)      # #064E3B Deep Emerald Surface
COLOR_GREEN_HI = (52, 211, 153)    # #34D399 Bright Emerald Highlight
COLOR_GREEN_BORDER = (16, 185, 129)# #10B981 Emerald Border
COLOR_GREEN_MINT = (167, 243, 208) # #A7F3D0 Light Mint

COLOR_RED_BTN = (69, 10, 10)       # #450A0A Deep Crimson Surface
COLOR_RED_HI = (248, 113, 113)     # #F87171 Bright Rose Highlight
COLOR_RED_BORDER = (239, 68, 68)   # #EF4444 Crimson Border
COLOR_RED_ROSE = (254, 205, 211)   # #FECDD3 Light Rose

COLOR_CYAN_ACCENT = (6, 182, 212)  # #06B6D4 Electric Cyan
COLOR_CYAN_HI = (103, 232, 249)    # #67E8F9 Bright Cyan
COLOR_CYAN_BG = (8, 47, 73)        # #082F49 Deep Cyan Surface

# 物理按键坐标 (320x240 原生坐标系)
BTN_Y = 162
BTN_H = 68
BTN1_X = 8
BTN1_W = 148
BTN2_X = 164
BTN2_W = 148

# 问答页几何常量 (与 main.cpp 严格对齐)
QUESTION_PER_PAGE = 2
QUESTION_ROW_X = 8
QUESTION_ROW_W = 278
QUESTION_ROW_H = 52
QUESTION_ROW1_Y = 92
QUESTION_ROW2_Y = 150
QUESTION_NAV_Y = 210
SCROLLBAR_X = 294
SCROLLBAR_W = 18
SCROLLBAR_Y = 92
SCROLLBAR_H = 110

ANSWERED_RETURN_IDLE_SEC = 2.5
EXPIRED_RETURN_IDLE_SEC = 5.0

SCENARIOS = {
    "1": {
        "badge": "[BASH EXEC]",
        "tag": "HIGH RISK",
        "title": "终端命令执行审批",
        "line1": "$ rm -rf dist && npm run build -- --mode production --sourcemap false",
        "line2": "工作目录: .../apps/web-dashboard\n影响说明: 清理旧构建产物并重新打包生产环境静态资源 (共 42 个模块)",
        "state": "PROMPT",
        "accent": COLOR_AMBER_GLOW,
        "opt1": "允许 (Y)",
        "opt2": "拒绝 (N)",
    },
    "2": {
        "badge": "[GIT PUSH]",
        "tag": "CRITICAL",
        "title": "远端分支强制推送",
        "line1": "$ git push origin main --force-with-lease --prune",
        "line2": "仓库分支: origin/main (重写最近 3 次提交历史)\n高危预警: 强制覆盖远端主干可能影响协作成员代码，请仔细核对",
        "state": "PROMPT",
        "accent": (239, 68, 68),
        "opt1": "强推 (Y)",
        "opt2": "取消 (N)",
    },
    "3": {
        "badge": "[WRITE FILE]",
        "tag": "SRC WRITE",
        "title": "新建核心代码文件",
        "line1": "目标: .../Backend/src/auth/jwt_provider.py",
        "line2": "变更摘要: 新建用户认证核心鉴权模块 (+148 行)\n包含 RS256 密钥轮换、Token 吊销列表与过期校验逻辑",
        "state": "PROMPT",
        "accent": (168, 85, 247),
        "opt1": "写入 (Y)",
        "opt2": "放弃 (N)",
    },
    "4": {
        "badge": "[EDIT PATCH]",
        "tag": "CONFIG EDIT",
        "title": "配置文件代码补丁",
        "line1": "目标: .../deploy/config/production.json",
        "line2": "变更行: - \"db_pool_max\": 20  + \"db_pool_max\": 64\n同时更新生产环境 Redis 哨兵超时阈值与 API 限流配额",
        "state": "PROMPT",
        "accent": (168, 85, 247),
        "opt1": "应用 (Y)",
        "opt2": "撤销 (N)",
    },
    "5": {
        "badge": "[QUESTION]",
        "tag": "CLAUDE ASK",
        "title": "请选择数据库迁移的执行策略",
        "line1": "请选择数据库迁移的执行策略",
        "line2": "支持右侧滑动条与底部翻页键",
        "state": "QUESTION",
        "accent": COLOR_CYAN_ACCENT,
        "options_list": [
            {"label": "自动迁移 (Auto)", "description": "立即应用所有增量 Schema 变更"},
            {"label": "仅生成 SQL (Dry-run)", "description": "输出变更脚本供人工二次审查"},
            {"label": "备份后迁移 (Safe)", "description": "先创建快照再执行迁移操作"},
            {"label": "取消操作 (Abort)", "description": "保持现有数据库结构不变"},
        ],
    },
    "6": {
        "badge": "[MCP TOOL]",
        "tag": "MCP POSTGRES",
        "title": "MCP 扩展工具调用",
        "line1": "服务: mcp__postgres (db-prod-cluster-01)",
        "line2": "SQL: DROP TABLE staging_users CASCADE;\n高危操作: 将级联删除关联视图与索引，请确认是否继续",
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
        "title": "Claude 正在执行只读分析...",
        "line1": "正在扫描工作区调用链依赖与单元测试覆盖率...",
        "line2": "PreToolUse 非阻塞状态展示 · 无需手动按键确认",
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
    "0": {
        "badge": "[QUESTION]",
        "tag": "CLAUDE ASK",
        "title": "请选择数据库迁移的执行策略",
        "line1": "请选择数据库迁移的执行策略",
        "line2": "支持右侧滑动条与底部翻页键",
        "state": "QUESTION",
        "accent": COLOR_CYAN_ACCENT,
        "options_list": [
            {"label": "自动迁移 (Auto)", "description": "立即应用所有增量 Schema 变更"},
            {"label": "仅生成 SQL (Dry-run)", "description": "输出变更脚本供人工二次审查"},
            {"label": "备份后迁移 (Safe)", "description": "先创建快照再执行迁移操作"},
            {"label": "取消操作 (Abort)", "description": "保持现有数据库结构不变"},
        ],
    },
}


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class SimHTTPHandler(BaseHTTPRequestHandler):
    simulator = None

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        if self.path == "/prompt":
            try:
                data = json.loads(body)
                res = SimHTTPHandler.simulator.inject_ipc("/prompt", data)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
        elif self.path in ("/status", "/notify_prompt", "/notify_question", "/question"):
            try:
                data = json.loads(body)
                res = SimHTTPHandler.simulator.inject_ipc(self.path, data)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
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
            self.wfile.write(json.dumps({"simulator": "active", "version": "2.2"}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class VirtualDeckSimulator:
    def __init__(self, headless=False):
        self.headless = headless
        pygame.init()
        pygame.font.init()
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=1)
        except Exception:
            pass

        self.scale = 2  # 默认 2X 高清缩放 (640x480)
        self.raw_w = 320
        self.raw_h = 240
        if not self.headless:
            self.window = pygame.display.set_mode((self.raw_w * self.scale, self.raw_h * self.scale))
            pygame.display.set_caption("Claude Deck HMI v2.2 仿真器 (点击代码框翻页 | 顶栏静音按钮 | 键5问答分页)")
        else:
            self.window = None

        self.raw_surface = pygame.Surface((self.raw_w, self.raw_h))
        self.render_lock = threading.RLock()

        self.font_zh = self.pick_font(
            [
                "Hiragino Sans GB",
                "STHeiti Medium",
                "Arial Unicode MS",
                "Helvetica",
                "Arial",
            ]
        )

        self.current_scenario_key = "1"
        self.current_scenario = dict(SCENARIOS[self.current_scenario_key])
        self.current_state = self.current_scenario["state"]
        self.state_entered_time = time.time()
        self.active_timeout_sec = 120
        self.question_page = 0
        self.prompt_scroll_page = 0
        self.is_muted = False

        self.touch_flash_btn = None
        self.touch_flash_until = 0
        self.last_touch_pos = None
        self.last_touch_time = 0

        # HTTP IPC 实时桥接支持
        self.ipc_queue = queue.Queue()
        self.pending_request = None
        self.pending_question = None
        SimHTTPHandler.simulator = self
        self.httpd = None
        self.http_thread = None
        try:
            self.httpd = ReusableThreadingHTTPServer(("127.0.0.1", 19843), SimHTTPHandler)
            self.http_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.http_thread.start()
            print("  🌐 虚拟副屏 IPC 桥接已在 http://127.0.0.1:19843 启动 (支持 Claude Code 实时通信)")
        except OSError:
            print("  🌐 虚拟副屏已启用进程内队列 IPC 仿真模式 (In-Memory IPC Queue)")

    def inject_ipc(self, path, data):
        """进程内直接执行与 HTTP 端点完全一致的 IPC 投递与阻塞等待逻辑"""
        if path == "/prompt":
            req_timeout = int(data.get("timeout", 120))
            ev = threading.Event()
            req = {
                "data": data,
                "event": ev,
                "response": "deny",
                "timeout": req_timeout,
            }
            self.ipc_queue.put(("prompt", req))
            ev.wait(timeout=req_timeout)
            return {"decision": req["response"]}
        elif path in ("/status", "/notify_prompt"):
            payload = dict(data)
            if path == "/notify_prompt":
                payload["status"] = "thinking"
            self.ipc_queue.put(("status", payload))
            return {"status": "ok"}
        elif path == "/notify_question":
            req = {
                "data": data,
                "event": threading.Event(),
                "index": -1,
            }
            self.ipc_queue.put(("question", req))
            return {"status": "ok"}
        elif path == "/question":
            req_timeout = int(data.get("timeout", 120))
            ev = threading.Event()
            req = {
                "data": data,
                "event": ev,
                "index": -1,
                "timeout": req_timeout,
            }
            self.ipc_queue.put(("question", req))
            answered = ev.wait(timeout=req_timeout)
            idx = req["index"]
            return {
                "answered": answered and idx >= 0,
                "index": idx if idx >= 0 else None,
            }
        raise ValueError(f"Unknown IPC path: {path}")

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
        if self.is_muted or duration_ms <= 0:
            return
        sample_rate = 44100
        n_samples = int(sample_rate * (duration_ms / 1000.0))
        buf = bytearray(n_samples * 2)
        period = sample_rate / freq
        for i in range(n_samples):
            val = 8000 if ((i % period) < (period / 2)) else -8000
            val_bytes = val.to_bytes(2, byteorder="little", signed=True)
            buf[i * 2] = val_bytes[0]
            buf[i * 2 + 1] = val_bytes[1]
        sound = pygame.mixer.Sound(buffer=bytes(buf))
        sound.set_volume(0.2)
        sound.play()

    def get_remaining_seconds(self):
        elapsed = int(time.time() - self.state_entered_time)
        return max(0, self.active_timeout_sec - elapsed)

    def wrap_text_lines(self, text, font, max_width):
        lines = []
        for raw_line in str(text or "").splitlines() or [""]:
            cur = ""
            for ch in raw_line:
                cand = cur + ch
                if cur and font.size(cand)[0] > max_width:
                    lines.append(cur)
                    cur = ch
                else:
                    cur = cand
            lines.append(cur)
        return lines

    def wrap_code_pages(self, text, font, full_width, page_size=3, reserve_last_line=62):
        """代码框分页智能折行：当需多页展示时，对每页第 3 行自动预留右侧翻页按钮宽度，彻底避免文字被遮挡"""
        raw_lines = str(text or "").splitlines() or [""]
        trial_lines = self.wrap_text_lines(text, font, full_width)
        if len(trial_lines) <= page_size:
            return trial_lines

        lines = []
        for raw_line in raw_lines:
            cur = ""
            for ch in raw_line:
                cand = cur + ch
                line_in_page = len(lines) % page_size
                limit = (full_width - reserve_last_line) if (line_in_page == page_size - 1) else full_width
                if cur and font.size(cand)[0] > limit:
                    lines.append(cur)
                    cur = ch
                else:
                    cur = cand
            lines.append(cur)
        return lines

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

        # Left brand: Spark + title (确保与中间状态胶囊保持足够安全距离)
        self.draw_spark(s, 11, 12, 4, COLOR_TERRACOTTA)
        f_brand = self.get_font(10, bold=True)
        s.blit(f_brand.render("CLAUDE DECK", True, COLOR_TEXT_WHITE), (18, 5))

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
        elif self.current_state == "ANSWERED_ALLOW":
            mode_text = "[ APPROVED ]"
            mode_color = COLOR_GREEN_HI
        elif self.current_state == "ANSWERED_DENY":
            mode_text = "[ STOPPED ]"
            mode_color = COLOR_RED_HI
        elif self.current_state == "ANSWERED_QUESTION":
            mode_text = "[ SUBMITTED ]"
            mode_color = COLOR_CYAN_HI
        elif self.current_state == "EXPIRED":
            mode_text = "[ TIMEOUT ]"
            mode_color = COLOR_RED_HI

        pill_w = 88
        pill_x = 114
        pygame.draw.rect(s, COLOR_CARD_BG, (pill_x, 3, pill_w, 18), border_radius=4)
        pygame.draw.rect(s, COLOR_CARD_BORDER, (pill_x, 3, pill_w, 18), width=1, border_radius=4)
        f_mode = self.get_font(10, bold=True)
        t_mode = f_mode.render(mode_text, True, mode_color)
        s.blit(t_mode, (pill_x + pill_w // 2 - t_mode.get_width() // 2, 5))

        # Top-Right Interactive Mute Pill Button (X: 206..256, Y: 3..21) — 采用矢量微图标代替 Emoji 杜绝乱码
        mute_bg = (69, 26, 3) if self.is_muted else COLOR_CYAN_BG
        mute_bd = COLOR_AMBER_GLOW if self.is_muted else COLOR_CYAN_ACCENT
        mute_fg = COLOR_AMBER_HI if self.is_muted else COLOR_CYAN_HI
        pygame.draw.rect(s, mute_bg, (206, 3, 50, 18), border_radius=4)
        pygame.draw.rect(s, mute_bd, (206, 3, 50, 18), width=1, border_radius=4)

        # 绘制小喇叭矢量图形
        spk_x, spk_y = 209, 12
        pygame.draw.polygon(
            s,
            mute_fg,
            [
                (spk_x, spk_y - 2),
                (spk_x + 3, spk_y - 2),
                (spk_x + 5, spk_y - 4),
                (spk_x + 5, spk_y + 4),
                (spk_x + 3, spk_y + 2),
                (spk_x, spk_y + 2),
            ],
        )
        if self.is_muted:
            pygame.draw.line(s, COLOR_AMBER_HI, (spk_x + 8, spk_y - 3), (spk_x + 12, spk_y + 3), 1)
            pygame.draw.line(s, COLOR_AMBER_HI, (spk_x + 12, spk_y - 3), (spk_x + 8, spk_y + 3), 1)
        else:
            pygame.draw.line(s, COLOR_CYAN_HI, (spk_x + 8, spk_y - 2), (spk_x + 8, spk_y + 2), 1)
            pygame.draw.line(s, COLOR_CYAN_HI, (spk_x + 10, spk_y - 4), (spk_x + 10, spk_y + 4), 1)

        f_snd = self.get_font(9, bold=True)
        t_snd = f_snd.render("MUTE" if self.is_muted else "SND", True, mute_fg)
        s.blit(t_snd, (226, 5))

        # Signal bars & Battery
        sig_x = 263
        for i in range(4):
            h = 3 + i * 2
            pygame.draw.rect(s, COLOR_GREEN_HI, (sig_x + i * 4, 16 - h, 2, h))

        bat_x = 283
        pygame.draw.rect(s, COLOR_TEXT_MUTED, (bat_x, 8, 16, 9), width=1)
        pygame.draw.rect(s, COLOR_TEXT_MUTED, (bat_x + 16, 10, 2, 5))
        pygame.draw.rect(s, COLOR_GREEN_HI, (bat_x + 2, 10, 12, 5))
        f_bat = self.get_font(9, bold=True)
        s.blit(f_bat.render("5V", True, COLOR_GREEN_HI), (bat_x + 20, 6))

    def normalize_question_options(self):
        raw_opts = self.current_scenario.get("options_list", [])
        normalized = []
        for item in raw_opts[:4]:
            if isinstance(item, dict):
                normalized.append(
                    {
                        "label": str(item.get("label", item.get("text", "")))[:36],
                        "description": str(item.get("description", "轻触选择此项回传终端"))[:60],
                    }
                )
            else:
                normalized.append(
                    {
                        "label": str(item)[:36],
                        "description": "轻触选择此项回传终端",
                    }
                )
        return normalized

    def render(self):
        with self.render_lock:
            self._render_locked()

    def _render_locked(self):
        s = self.raw_surface
        s.fill(COLOR_BG)
        self.draw_status_bar()

        now = time.time()
        sc = self.current_scenario

        if self.current_state == "IDLE":
            cx, cy, cw, ch = 8, 28, 304, 134
            pygame.draw.rect(s, COLOR_CARD_BG, (cx, cy, cw, ch), border_radius=8)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (cx, cy, cw, ch), width=1, border_radius=8)

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

            dock_y = 168
            dock_h = 64
            card_w = 96
            metrics = [
                ("BLE LINK", "ONLINE", COLOR_GREEN_HI, COLOR_GREEN_BTN),
                (
                    "AUDIO TAP",
                    "MUTED" if self.is_muted else "SOUND ON",
                    COLOR_AMBER_HI if self.is_muted else COLOR_CYAN_HI,
                    (69, 26, 3) if self.is_muted else COLOR_CYAN_BG,
                ),
                ("TIMEOUT", f"{self.active_timeout_sec}s AUTO", COLOR_CYAN_HI, COLOR_CYAN_BG),
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
            t_title = f_t.render(sc.get("title", "Claude 正在深度思考中..."), True, COLOR_TEXT_WHITE)
            s.blit(t_title, (160 - t_title.get_width() // 2, 122))

            tbx, tby, tbw, tbh = cx + 12, 146, cw - 24, 46
            pygame.draw.rect(s, COLOR_CODE_BG, (tbx, tby, tbw, tbh), border_radius=4)
            pygame.draw.rect(s, (25, 45, 65), (tbx, tby, tbw, tbh), width=1, border_radius=4)
            pygame.draw.rect(s, COLOR_CYAN_ACCENT, (tbx, tby, 3, tbh))
            f_d1 = self.get_font(11)
            lines = self.wrap_text_lines(sc.get("line1", "分析系统架构拓扑与调用链依赖..."), f_d1, tbw - 16)
            for idx, ln in enumerate(lines[:2]):
                s.blit(f_d1.render(ln, True, COLOR_TEXT_MUTED), (tbx + 8, tby + 6 + idx * 18))

            bar_y = 202
            f_sc = self.get_font(10, bold=True)
            s.blit(f_sc.render("■ ■ ■ ■ ■ □ □ □", True, COLOR_CYAN_ACCENT), (cx + 12, bar_y))
            f_dim = self.get_font(9, bold=True)
            t_dim = f_dim.render("EXECUTING / REASONING", True, COLOR_TEXT_DIM)
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
            t_title = f_t.render(f"请求已超时 · {self.active_timeout_sec}s TIMEOUT", True, COLOR_RED_HI)
            s.blit(t_title, (160 - t_title.get_width() // 2, 124))

            f_sub1 = self.get_font(12, bold=True)
            t_sub1 = f_sub1.render("自动触发 Fail-Safe 熔断保护", True, COLOR_TEXT_WHITE)
            s.blit(t_sub1, (160 - t_sub1.get_width() // 2, 148))

            f_sub2 = self.get_font(11)
            t_sub2 = f_sub2.render("终端已安全拦截 · 5秒后自动归位待命", True, COLOR_TEXT_MUTED)
            s.blit(t_sub2, (160 - t_sub2.get_width() // 2, 172))

        elif self.current_state in ("ANSWERED_ALLOW", "ANSWERED_DENY"):
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
            t_sub1 = f_sub1.render("允许指令已成功回传终端" if is_allow else "拒绝指令已回传终端", True, COLOR_TEXT_WHITE)
            s.blit(t_sub1, (160 - t_sub1.get_width() // 2, 148))

            f_sub2 = self.get_font(11)
            t_sub2 = f_sub2.render(
                "Claude 终端继续执行 (2.5s 自动归位)" if is_allow else "高危调用已中止 (2.5s 自动归位)",
                True,
                COLOR_TEXT_MUTED,
            )
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
            t_title = f_t.render("已选择并回传 · SUBMITTED", True, COLOR_CYAN_HI)
            s.blit(t_title, (160 - t_title.get_width() // 2, 124))

            f_sub1 = self.get_font(12, bold=True)
            t_sub1 = f_sub1.render("选择已确认，Claude 终端继续运行", True, COLOR_TEXT_WHITE)
            s.blit(t_sub1, (160 - t_sub1.get_width() // 2, 148))

            f_sub2 = self.get_font(11)
            chosen = self.current_scenario.get("chosen_label", "")
            t_sub2 = f_sub2.render(f"已选: {chosen[:24]}", True, COLOR_TEXT_MUTED)
            s.blit(t_sub2, (160 - t_sub2.get_width() // 2, 172))

        elif self.current_state == "QUESTION":
            opts = self.normalize_question_options()
            total_opts = len(opts)
            page_count = max(1, (total_opts + QUESTION_PER_PAGE - 1) // QUESTION_PER_PAGE)
            if self.question_page >= page_count:
                self.question_page = 0
            start_idx = self.question_page * QUESTION_PER_PAGE

            # Header Card (8, 28, 304, 54)
            cx, cy, cw, ch = 8, 28, 304, 54
            pygame.draw.rect(s, COLOR_CARD_BG, (cx, cy, cw, ch), border_radius=8)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (cx, cy, cw, ch), width=1, border_radius=8)

            self.draw_spark(s, cx + 10, cy + 10, 4, COLOR_CYAN_HI)
            f_hdr = self.get_font(10, bold=True)
            s.blit(f_hdr.render(sc.get("tag", "QUESTION"), True, COLOR_CYAN_HI), (cx + 20, cy + 4))

            t_rem = f_hdr.render(f"{self.get_remaining_seconds()}s", True, COLOR_AMBER_HI)
            rem_x = cx + cw - 8 - t_rem.get_width()
            # 绘制小秒表/时钟图标 (避免 Emoji ⏱ 乱码)
            clk_x = rem_x - 8
            clk_y = cy + 10
            pygame.draw.circle(s, COLOR_AMBER_HI, (clk_x, clk_y), 4, width=1)
            pygame.draw.line(s, COLOR_AMBER_HI, (clk_x, clk_y), (clk_x, clk_y - 2), 1)
            pygame.draw.line(s, COLOR_AMBER_HI, (clk_x, clk_y), (clk_x + 2, clk_y), 1)
            s.blit(t_rem, (rem_x, cy + 4))

            f_q = self.get_font(12, bold=True)
            q_lines = self.wrap_text_lines(sc.get("title", "Claude 有问题想问你"), f_q, 284)
            for idx, ln in enumerate(q_lines[:2]):
                s.blit(f_q.render(ln, True, COLOR_TEXT_WHITE), (cx + 8, cy + 21 + idx * 15))

            # Option rows (2 per page, identical coordinates to main.cpp)
            f_lbl = self.get_font(13, bold=True)
            f_sub = self.get_font(10)
            f_num = self.get_font(14, bold=True)

            for row in range(QUESTION_PER_PAGE):
                opt_idx = start_idx + row
                by = QUESTION_ROW1_Y if row == 0 else QUESTION_ROW2_Y
                if opt_idx >= total_opts:
                    continue

                flashing = self.touch_flash_btn == opt_idx and now < self.touch_flash_until
                bg = COLOR_TEXT_WHITE if flashing else COLOR_CARD_BG
                fg = (0, 0, 0) if flashing else COLOR_TEXT_WHITE

                pygame.draw.rect(s, bg, (QUESTION_ROW_X, by, QUESTION_ROW_W, QUESTION_ROW_H), border_radius=8)
                pygame.draw.rect(s, COLOR_CARD_BORDER, (QUESTION_ROW_X, by, QUESTION_ROW_W, QUESTION_ROW_H), width=1, border_radius=8)

                # Uniform Cyan Number Badge
                pygame.draw.rect(s, COLOR_CYAN_BG, (QUESTION_ROW_X + 8, by + 10, 24, 32), border_radius=4)
                pygame.draw.rect(s, COLOR_CYAN_ACCENT, (QUESTION_ROW_X + 8, by + 10, 24, 32), width=1, border_radius=4)
                t_num = f_num.render(f"{opt_idx + 1}", True, COLOR_CYAN_HI)
                s.blit(t_num, (QUESTION_ROW_X + 20 - t_num.get_width() // 2, by + 17))

                opt_item = opts[opt_idx]
                s.blit(f_lbl.render(opt_item["label"][:24], True, fg), (QUESTION_ROW_X + 40, by + 9))
                s.blit(f_sub.render(opt_item["description"][:30], True, COLOR_TEXT_MUTED), (QUESTION_ROW_X + 40, by + 30))
                self.draw_chevron(s, QUESTION_ROW_X + QUESTION_ROW_W - 16, by + 18, 14, COLOR_CYAN_ACCENT)

            if page_count > 1:
                pygame.draw.rect(s, COLOR_CARD_BG, (SCROLLBAR_X, SCROLLBAR_Y, SCROLLBAR_W, SCROLLBAR_H), border_radius=6)
                pygame.draw.rect(s, COLOR_CARD_BORDER, (SCROLLBAR_X, SCROLLBAR_Y, SCROLLBAR_W, SCROLLBAR_H), width=1, border_radius=6)
                thumb_h = max(24, SCROLLBAR_H // page_count)
                max_thumb_y = SCROLLBAR_H - thumb_h
                thumb_y = SCROLLBAR_Y + (self.question_page * max_thumb_y) // (page_count - 1)
                pygame.draw.rect(s, COLOR_CYAN_ACCENT, (SCROLLBAR_X + 2, thumb_y + 2, SCROLLBAR_W - 4, thumb_h - 4), border_radius=4)

                f_nav = self.get_font(10, bold=True)
                pygame.draw.rect(s, COLOR_CARD_BG, (8, QUESTION_NAV_Y, 74, 24), border_radius=6)
                pygame.draw.rect(s, COLOR_CARD_BORDER, (8, QUESTION_NAV_Y, 74, 24), width=1, border_radius=6)
                s.blit(f_nav.render("< PREV", True, COLOR_TEXT_WHITE if self.question_page > 0 else COLOR_TEXT_DIM), (22, QUESTION_NAV_Y + 5))

                pygame.draw.rect(s, COLOR_CARD_BG, (212, QUESTION_NAV_Y, 74, 24), border_radius=6)
                pygame.draw.rect(s, COLOR_CARD_BORDER, (212, QUESTION_NAV_Y, 74, 24), width=1, border_radius=6)
                s.blit(
                    f_nav.render("NEXT >", True, COLOR_TEXT_WHITE if self.question_page + 1 < page_count else COLOR_TEXT_DIM),
                    (226, QUESTION_NAV_Y + 5),
                )

                t_pi = f_nav.render(f"第 {self.question_page + 1} / {page_count} 页", True, COLOR_TEXT_MUTED)
                s.blit(t_pi, (147 - t_pi.get_width() // 2, QUESTION_NAV_Y + 5))
            else:
                f_nav = self.get_font(10)
                t_pi = f_nav.render("轻触选项卡片直接作答回传", True, COLOR_TEXT_MUTED)
                s.blit(t_pi, (160 - t_pi.get_width() // 2, QUESTION_NAV_Y + 5))

        elif self.current_state == "PROMPT":
            cx, cy, cw, ch = 8, 28, 304, 126
            pygame.draw.rect(s, COLOR_CARD_BG, (cx, cy, cw, ch), border_radius=8)
            pygame.draw.rect(s, COLOR_CARD_BORDER, (cx, cy, cw, ch), width=1, border_radius=8)

            # Header Bar (Cyan Badge Pill + Title + Right Risk Tag — 彻底移除旧版红黄绿假圆点)
            pygame.draw.rect(s, COLOR_CARD_HEADER, (cx, cy, cw, 22), border_top_left_radius=8, border_top_right_radius=8)
            pygame.draw.line(s, COLOR_CARD_BORDER, (cx, cy + 22), (cx + cw, cy + 22), 1)

            f_badge = self.get_font(9, bold=True)
            badge_str = sc.get("badge", "[APPROVAL]")
            t_badge = f_badge.render(badge_str, True, COLOR_CYAN_HI)
            bw = t_badge.get_width() + 10
            pygame.draw.rect(s, COLOR_CYAN_BG, (cx + 6, cy + 3, bw, 16), border_radius=3)
            pygame.draw.rect(s, COLOR_CYAN_ACCENT, (cx + 6, cy + 3, bw, 16), width=1, border_radius=3)
            s.blit(t_badge, (cx + 11, cy + 4))

            f_title = self.get_font(11, bold=True)
            t_title = f_title.render(sc.get("title", ""), True, COLOR_TEXT_WHITE)
            s.blit(t_title, (cx + 12 + bw, cy + 4))

            tag_text = sc.get("tag", "CONFIRM")
            is_crit = tag_text == "CRITICAL"
            f_tag = self.get_font(9, bold=True)
            t_tag = f_tag.render(tag_text, True, COLOR_RED_HI if is_crit else COLOR_AMBER_HI)
            tag_w = t_tag.get_width() + 12
            pygame.draw.rect(s, COLOR_RED_BTN if is_crit else (69, 26, 3), (cx + cw - tag_w - 4, cy + 3, tag_w, 16), border_radius=3)
            pygame.draw.rect(
                s,
                COLOR_RED_BORDER if is_crit else COLOR_AMBER_GLOW,
                (cx + cw - tag_w - 4, cy + 3, tag_w, 16),
                width=1,
                border_radius=3,
            )
            s.blit(t_tag, (cx + cw - tag_w + 2, cy + 4))

            # Inner Terminal Codebox (16, 54, 288, 68)
            tbx, tby, tbw, tbh = cx + 8, cy + 26, cw - 16, 68
            pygame.draw.rect(s, COLOR_CODE_BG, (tbx, tby, tbw, tbh), border_radius=4)
            pygame.draw.rect(s, (25, 30, 42), (tbx, tby, tbw, tbh), width=1, border_radius=4)
            pygame.draw.rect(s, sc.get("accent", COLOR_AMBER_GLOW), (tbx, tby, 3, tbh))

            f_mono = self.get_font(11, bold=True)
            full_code_text = sc.get("line1", "")
            if sc.get("line2"):
                full_code_text += "\n" + sc.get("line2", "")
            wrapped_lines = self.wrap_code_pages(full_code_text, f_mono, tbw - 16, page_size=3, reserve_last_line=54)
            total_pages = max(1, (len(wrapped_lines) + 2) // 3)
            if self.prompt_scroll_page >= total_pages:
                self.prompt_scroll_page = 0
            start_ln = self.prompt_scroll_page * 3
            for idx, ln in enumerate(wrapped_lines[start_ln : start_ln + 3]):
                s.blit(f_mono.render(ln, True, COLOR_TEXT_WHITE if idx == 0 else COLOR_TEXT_MUTED), (tbx + 8, tby + 6 + idx * 19))

            if total_pages > 1:
                # 紧凑翻页指示胶囊，且第3行文本已自动留白，彻底解决重叠遮挡问题
                page_str = f" {self.prompt_scroll_page + 1}/{total_pages}"
                f_pg = self.get_font(9, bold=True)
                t_pg = f_pg.render(page_str, True, COLOR_CYAN_HI)
                pw = t_pg.get_width() + 18
                px = tbx + tbw - pw - 4
                py = tby + tbh - 18
                pygame.draw.rect(s, COLOR_CYAN_BG, (px, py, pw, 15), border_radius=3)
                pygame.draw.rect(s, COLOR_CYAN_ACCENT, (px, py, pw, 15), width=1, border_radius=3)
                # 绘制小下三角矢量图标替代 Emoji ▾
                pygame.draw.polygon(s, COLOR_CYAN_HI, [(px + 4, py + 5), (px + 10, py + 5), (px + 7, py + 9)])
                s.blit(t_pg, (px + 11, py + 1))

            # Card Footer: Security notice + Live Countdown Text + Dynamic Progress Bar
            self.draw_spark(s, cx + 14, cy + ch - 16, 4, COLOR_TERRACOTTA)
            f_foot = self.get_font(10)
            foot_msg = f"轻触代码框翻页 ({self.prompt_scroll_page + 1}/{total_pages})" if total_pages > 1 else "物理双键审批把关"
            s.blit(f_foot.render(foot_msg, True, COLOR_TEXT_MUTED), (cx + 24, cy + ch - 23))

            remain_sec = self.get_remaining_seconds()
            cd_color = COLOR_RED_HI if remain_sec <= 15 else COLOR_AMBER_HI
            t_to = f_foot.render(f"{remain_sec}s 超时熔断", True, cd_color)
            to_x = cx + cw - 10 - t_to.get_width()
            to_y = cy + ch - 23

            # 绘制小闪电警示图形替代 Emoji ⚡
            lx = to_x - 9
            ly = to_y + 7
            pygame.draw.polygon(
                s,
                cd_color,
                [
                    (lx + 2, ly - 5),
                    (lx - 1, ly),
                    (lx + 1, ly),
                    (lx - 2, ly + 5),
                    (lx + 3, ly - 1),
                    (lx + 1, ly - 1),
                ],
            )
            s.blit(t_to, (to_x, to_y))

            # Dynamic Visual Countdown Progress Bar (Y: cy + ch - 7, H: 4)
            bar_x = cx + 8
            bar_y = cy + ch - 7
            bar_w = cw - 16
            pygame.draw.rect(s, COLOR_CODE_BG, (bar_x, bar_y, bar_w, 4), border_radius=2)
            ratio = max(0.0, min(1.0, remain_sec / float(max(1, self.active_timeout_sec))))
            fill_w = int(bar_w * ratio)
            if fill_w > 0:
                bar_color = COLOR_RED_BORDER if remain_sec <= 20 else COLOR_AMBER_GLOW
                pygame.draw.rect(s, bar_color, (bar_x, bar_y, fill_w, 4), border_radius=2)

            # 2. Buttons
            btn1_flash = self.touch_flash_btn == 0 and now < self.touch_flash_until
            btn2_flash = self.touch_flash_btn == 1 and now < self.touch_flash_until
            self.draw_tactile_button(s, BTN1_X, BTN_Y, BTN1_W, BTN_H, True, sc.get("opt1", "允许 (Y)"), "[ CONFIRM ]", btn1_flash)
            self.draw_tactile_button(s, BTN2_X, BTN_Y, BTN2_W, BTN_H, False, sc.get("opt2", "拒绝 (N)"), "[ ABORT ]", btn2_flash)

        # 绘制最后触控点黄色小标记
        if self.last_touch_pos and (now - self.last_touch_time < 0.4):
            pygame.draw.circle(s, (255, 230, 0), self.last_touch_pos, 4)

        scaled_surface = pygame.transform.scale(s, (self.raw_w * self.scale, self.raw_h * self.scale))
        if self.window is not None:
            try:
                self.window.blit(scaled_surface, (0, 0))
                pygame.display.flip()
            except Exception:
                pass

    def save_screenshot(self, filepath, scale=2):
        """保存当前渲染画面的高质量截图 (默认 2X: 640x480, 支持 1X: 320x240)"""
        import os
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        if scale == 1:
            pygame.image.save(self.raw_surface, filepath)
        else:
            scaled = pygame.transform.scale(self.raw_surface, (self.raw_w * scale, self.raw_h * scale))
            pygame.image.save(scaled, filepath)

    def handle_touch(self, screen_x, screen_y):
        self.last_touch_pos = (screen_x, screen_y)
        self.last_touch_time = time.time()
        print(f"[SIMULATOR TOUCH] Screen Raw Coord: ({screen_x}, {screen_y})")

        # 全局顶栏静音胶囊按钮 (X: 204..260, Y: 0..24) — 任意状态下均可点击切换静音！
        if 204 <= screen_x <= 260 and 0 <= screen_y <= 24:
            self.is_muted = not self.is_muted
            print(f"👉 [全局静音切换] {'🔇 已静音 (MUTED)' if self.is_muted else '🔊 提示音已开启 (SOUND ON)'}")
            if not self.is_muted:
                self.play_beep(2200, 20)
            return

        # IDLE 态点击底部中间 AUDIO TAP 卡片切换静音
        if self.current_state == "IDLE":
            if 112 <= screen_x <= 208 and 168 <= screen_y <= 232:
                self.is_muted = not self.is_muted
                if not self.is_muted:
                    self.play_beep(2200, 20)
            return

        if self.current_state == "QUESTION":
            opts = self.normalize_question_options()
            total_opts = len(opts)
            page_count = max(1, (total_opts + QUESTION_PER_PAGE - 1) // QUESTION_PER_PAGE)
            start_idx = self.question_page * QUESTION_PER_PAGE

            # 1. 点击右侧滑动条翻页
            if page_count > 1 and (SCROLLBAR_X - 6) <= screen_x <= 318 and SCROLLBAR_Y <= screen_y <= (SCROLLBAR_Y + SCROLLBAR_H):
                mid_y = SCROLLBAR_Y + SCROLLBAR_H // 2
                if screen_y < mid_y and self.question_page > 0:
                    self.question_page -= 1
                    self.play_beep(2200, 20)
                elif screen_y >= mid_y and self.question_page + 1 < page_count:
                    self.question_page += 1
                    self.play_beep(2200, 20)
                return

            # 2. 点击底部 PREV / NEXT 导航翻页
            if page_count > 1 and QUESTION_NAV_Y <= screen_y <= 238:
                if 8 <= screen_x <= 82 and self.question_page > 0:
                    self.question_page -= 1
                    self.play_beep(2200, 20)
                elif 212 <= screen_x <= 286 and self.question_page + 1 < page_count:
                    self.question_page += 1
                    self.play_beep(2200, 20)
                return

            # 3. 点击当前页的 2 个选项卡片 (Y: 92..144 或 150..202)
            selected_idx = -1
            if QUESTION_ROW_X <= screen_x <= (QUESTION_ROW_X + QUESTION_ROW_W):
                if QUESTION_ROW1_Y <= screen_y <= (QUESTION_ROW1_Y + QUESTION_ROW_H):
                    selected_idx = start_idx
                elif QUESTION_ROW2_Y <= screen_y <= (QUESTION_ROW2_Y + QUESTION_ROW_H):
                    selected_idx = start_idx + 1

            if 0 <= selected_idx < total_opts:
                chosen_label = opts[selected_idx]["label"]
                print(f"👉 [QUESTION 命中] 选项 {selected_idx + 1}: {chosen_label}")
                self.touch_flash_btn = selected_idx
                self.touch_flash_until = time.time() + 0.15
                self.play_beep(2400, 35)
                if self.pending_question:
                    self.pending_question["index"] = selected_idx
                    self.pending_question["event"].set()
                    self.pending_question = None
                self.current_scenario["chosen_label"] = chosen_label
                self.render()
                pygame.time.delay(60)
                self.play_beep(3200, 50)
                self.current_state = "ANSWERED_QUESTION"
                self.state_entered_time = time.time()
            return

        if self.current_state != "PROMPT":
            if "ANSWERED" in self.current_state or self.current_state == "EXPIRED":
                self.current_state = "IDLE"
                self.state_entered_time = time.time()
            return

        # PROMPT 态点击中央代码框翻页浏览长命令
        if 16 <= screen_x <= 304 and 54 <= screen_y <= 128:
            self.prompt_scroll_page += 1
            print(f"👉 [代码框翻页] 切换到第 {self.prompt_scroll_page + 1} 页")
            self.play_beep(2200, 20)
            return

        # 触控命中判定 (与 main.cpp 严格相同的判定区)
        if (BTN1_X - 6) <= screen_x <= (BTN1_X + BTN1_W) and (BTN_Y - 10) <= screen_y <= 238:
            print("👉 [触控命中] 允许 (Y) -> ALLOW")
            if self.pending_request:
                self.pending_request["response"] = "allow"
                self.pending_request["event"].set()
                self.pending_request = None
            self.touch_flash_btn = 0
            self.touch_flash_until = time.time() + 0.12
            self.play_beep(2400, 35)
            self.render()
            pygame.time.delay(50)
            self.play_beep(3200, 50)
            self.current_state = "ANSWERED_ALLOW"
            self.state_entered_time = time.time()

        elif (BTN2_X) <= screen_x <= (BTN2_X + BTN2_W + 6) and (BTN_Y - 10) <= screen_y <= 238:
            print("👉 [触控命中] 拒绝 (N) -> DENY")
            if self.pending_request:
                self.pending_request["response"] = "deny"
                self.pending_request["event"].set()
                self.pending_request = None
            self.touch_flash_btn = 1
            self.touch_flash_until = time.time() + 0.12
            self.play_beep(900, 60)
            self.render()
            pygame.time.delay(40)
            self.play_beep(620, 80)
            self.current_state = "ANSWERED_DENY"
            self.state_entered_time = time.time()

    def run(self):
        clock = pygame.time.Clock()
        print("=" * 64)
        print("  🚀 Claude Deck 桌面原生仿真器 (HMI v2.2) 已就绪！")
        print("  交互亮点:")
        print("    • 点击中央黑色代码框 ➡️ 翻页查看被折叠的多行长命令 ([▾ 点击翻页 1/2])")
        print("    • 点击顶栏右上角 [🔊SND] ➡️ 任意界面一键切换静音")
        print("    • 卡片底部新增实时缩短的动态倒计时进度条")
        print("    • 点击 [拒绝 (N)] 呈现红色 STOPPED 拦截页，2.5s 后自动归位待命页")
        print("  快捷键:")
        print("    1-4 : 切换 Bash / Git / Write / Edit 长命令授权场景")
        print("    5/0 : 多选分页问答页 (AskUserQuestion 2行卡片 + 右侧滑轨 + PREV/NEXT)")
        print("    6   : MCP 数据库高危调用场景")
        print("    7   : 待命大盘 (Idle)")
        print("    8   : 思考/执行态 (Thinking)")
        print("    9   : 超时熔断态 (Expired)")
        print("    M   : 切换静音 (Mute)")
        print("    S   : 切换 1X / 2X 窗口缩放")
        print("    Q   : 退出")
        print("=" * 64)

        running = True
        while running:
            now = time.time()

            # 自动归位与本地超时熔断看门狗
            if self.current_state in ("ANSWERED_ALLOW", "ANSWERED_DENY", "ANSWERED_QUESTION"):
                if now - self.state_entered_time >= ANSWERED_RETURN_IDLE_SEC:
                    self.current_state = "IDLE"
                    self.state_entered_time = now
            elif self.current_state == "EXPIRED":
                if now - self.state_entered_time >= EXPIRED_RETURN_IDLE_SEC:
                    self.current_state = "IDLE"
                    self.state_entered_time = now
            elif self.current_state in ("PROMPT", "QUESTION"):
                if self.get_remaining_seconds() <= 0:
                    self.current_state = "EXPIRED"
                    self.state_entered_time = now
                    self.play_beep(620, 90)

            while not self.ipc_queue.empty():
                try:
                    cmd, payload = self.ipc_queue.get_nowait()
                    if cmd == "prompt":
                        self.pending_request = payload
                        d = payload["data"]
                        self.active_timeout_sec = int(payload.get("timeout", 120))
                        self.prompt_scroll_page = 0
                        self.current_scenario = {
                            "badge": d.get("badge", "[CLAUDE APPROVAL]"),
                            "tag": d.get("tag", "CONFIRM"),
                            "title": d.get("title", "Claude 权限确认"),
                            "line1": d.get("desc", ""),
                            "line2": "",
                            "state": "PROMPT",
                            "accent": COLOR_RED_BORDER if d.get("tag") == "CRITICAL" else COLOR_AMBER_GLOW,
                            "opt1": (d.get("options") or ["允许 (Y)"])[0],
                            "opt2": (d.get("options") or ["", "拒绝 (N)"])[1] if len(d.get("options", [])) > 1 else "拒绝 (N)",
                        }
                        self.current_state = "PROMPT"
                        self.state_entered_time = time.time()
                        self.play_beep(1800, 35)
                        pygame.time.delay(25)
                        self.play_beep(2400, 50)
                    elif cmd == "question":
                        self.pending_question = payload
                        d = payload["data"]
                        self.active_timeout_sec = int(payload.get("timeout", 120))
                        self.question_page = 0
                        opts = d.get("options", [])
                        self.current_scenario = {
                            "badge": "[QUESTION]",
                            "tag": d.get("header", "CLAUDE ASK"),
                            "title": d.get("question", "Claude 有问题想问你"),
                            "options_list": opts,
                            "state": "QUESTION",
                            "accent": COLOR_CYAN_ACCENT,
                        }
                        self.current_state = "QUESTION"
                        self.state_entered_time = time.time()
                        self.play_beep(1600, 30)
                        pygame.time.delay(20)
                        self.play_beep(2000, 30)
                        pygame.time.delay(20)
                        self.play_beep(2600, 45)
                    elif cmd == "status":
                        st = payload.get("status", "idle")
                        if payload.get("title"):
                            self.current_scenario["title"] = payload["title"]
                        if payload.get("desc"):
                            self.current_scenario["line1"] = payload["desc"]
                        if st == "thinking":
                            self.current_state = "THINKING"
                        elif st == "expired":
                            self.current_state = "EXPIRED"
                        else:
                            self.current_state = "IDLE"
                        self.state_entered_time = time.time()
                except queue.Empty:
                    break

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_q:
                        running = False
                    elif event.key == pygame.K_m:
                        self.is_muted = not self.is_muted
                        if not self.is_muted:
                            self.play_beep(2200, 20)
                    elif event.key == pygame.K_s:
                        self.scale = 1 if self.scale == 2 else 2
                        self.window = pygame.display.set_mode((self.raw_w * self.scale, self.raw_h * self.scale))
                    elif chr(event.key) in SCENARIOS:
                        k = chr(event.key)
                        self.current_scenario_key = k
                        self.current_scenario = dict(SCENARIOS[k])
                        self.current_state = self.current_scenario["state"]
                        self.state_entered_time = time.time()
                        self.question_page = 0
                        self.prompt_scroll_page = 0
                        self.play_beep(2000, 25)

                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = event.pos
                    raw_x = int(mx / self.scale)
                    raw_y = int(my / self.scale)
                    self.handle_touch(raw_x, raw_y)

            self.render()
            clock.tick(30)

        pygame.quit()


if __name__ == "__main__":
    sim = VirtualDeckSimulator()
    sim.run()
