#!/usr/bin/env python3
"""
Claude Deck 端到端仿真与全场景关键 HMI 自动化截图捕获引擎
==========================================================
覆盖 Claude Deck 完整交互生命周期的所有任务触发点与关键 HMI：
  1. 待命大盘 (IDLE 正常态 / SND 开启)
  2. 静音待命大盘 (IDLE 静音态 / MUTE 开启)
  3. PreToolUse 深度思考态 (THINKING / Reasoning，非阻塞无假按钮)
  4. 高危 Bash 命令审批 (PROMPT / rm -rf dist && npm run build) - Page 1
  5. 高危 Bash 命令审批 (PROMPT) - Page 2 代码框翻页后
  6. 高危 Bash 命令审批倒计时告警 (PROMPT - 倒计时 < 15s 变红警报)
  7. Git 强推高危审批 (PROMPT / git push --force-with-lease / CRITICAL 绯红预警)
  8. 新建核心代码文件写入审批 (PROMPT / Write / 智能路径尾部缩写)
  9. 配置文件补丁审批 (PROMPT / Edit Patch / production.json 参数修改)
  10. MCP 数据库高危调用审批 (PROMPT / MCP Postgres / 级联删除拦截)
  11. AskUserQuestion 问答任务 (QUESTION / Schema 迁移策略) - Page 1 (选项 1, 2)
  12. AskUserQuestion 问答任务 (QUESTION) - Page 2 (选项 3, 4，滑动条下移)
  13. 审批动作反馈 - 允许放行态 (ANSWERED_ALLOW / APPROVED 翠绿环形勾)
  14. 审批动作反馈 - 拦截拒绝态 (ANSWERED_DENY / STOPPED 绯红环形叉，区分于绿色)
  15. 问答动作反馈 - 选项已提交态 (ANSWERED_QUESTION / SUBMITTED 电光青)
  16. 超时熔断异常态 (EXPIRED / 120s TIMEOUT 红色熔断卡片)
"""

import os
import sys
import time
import json

# 加入 host 路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from host.virtual_deck import (
    VirtualDeckSimulator,
    SCENARIOS,
    COLOR_AMBER_GLOW,
    COLOR_CYAN_ACCENT,
    COLOR_GREEN_BTN,
    COLOR_RED_BTN,
)

SCREENSHOTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "screenshots"))
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def run_simulation_and_capture():
    print("=" * 70)
    print("🚀 启动 Claude Deck 仿真任务流并捕获关键 HMI 界面...")
    print(f"📁 截图保存目录: {SCREENSHOTS_DIR}")
    print("=" * 70)

    sim = VirtualDeckSimulator(headless=True)
    sim.scale = 2
    captures = []

    # ----------------------------------------------------
    # 任务 1: 待命状态 (IDLE / Standby - Sound ON)
    # ----------------------------------------------------
    sim.current_scenario_key = "7"
    sim.current_scenario = dict(SCENARIOS["7"])
    sim.current_state = "IDLE"
    sim.is_muted = False
    sim.state_entered_time = time.time()
    sim.render()
    fn = "01_task_idle_standby.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-01",
        "file": fn,
        "title": "待命大盘 (IDLE Standby - 声音开启)",
        "trigger": "设备上电或任务执行完毕自动归位",
        "state": "IDLE",
        "hmi_elements": ["顶部状态栏 [ STANDBY ] 绿色胶囊", "🔊SND 提示音开启", "5V 电源与 BLE 信号", "中央科技同心圆与状态摘要", "底部 BLE / AUDIO / TIMEOUT 状态卡片"]
    })
    print(f"📸 捕获 [TASK-01]: {fn}")

    # ----------------------------------------------------
    # 任务 2: 静音待命状态 (IDLE / Muted)
    # ----------------------------------------------------
    sim.is_muted = True
    sim.render()
    fn = "02_task_idle_muted.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-02",
        "file": fn,
        "title": "待命大盘 (IDLE Muted - 静音模式)",
        "trigger": "轻触顶栏 SND 胶囊或底部 AUDIO TAP 卡片",
        "state": "IDLE",
        "hmi_elements": ["顶部 🔇MUTE 琥珀色高亮胶囊", "底部 AUDIO TAP 显示 MUTED 琥珀色背景", "蜂鸣器提示音物理关闭"]
    })
    print(f"📸 捕获 [TASK-02]: {fn}")

    # ----------------------------------------------------
    # 任务 3: PreToolUse 深度思考与只读推理态 (THINKING / Reasoning)
    # ----------------------------------------------------
    sim.current_scenario_key = "8"
    sim.current_scenario = dict(SCENARIOS["8"])
    sim.current_state = "THINKING"
    sim.state_entered_time = time.time()
    sim.render()
    fn = "03_task_thinking_pretooluse.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-03",
        "file": fn,
        "title": "只读分析/思考态 (THINKING / Reasoning)",
        "trigger": "PreToolUse 钩子捕获只读分析、架构依赖扫描等非阻塞任务",
        "state": "THINKING",
        "hmi_elements": ["顶部状态栏 [ REASONING ] 电光青胶囊", "中央旋转科技粒子环 (Cyan Accent)", "代码框展示只读扫描详情", "底部进度方块 ■ ■ ■ ■ ■ □ □ □", "无假确认按钮 (避免误导用户)"]
    })
    print(f"📸 捕获 [TASK-03]: {fn}")

    # ----------------------------------------------------
    # 任务 4: 高危 Bash 命令审批 (PROMPT / Bash Approval) - Page 1
    # ----------------------------------------------------
    sim.current_scenario_key = "1"
    sim.current_scenario = dict(SCENARIOS["1"])
    sim.current_state = "PROMPT"
    sim.prompt_scroll_page = 0
    sim.active_timeout_sec = 120
    sim.state_entered_time = time.time() - 10  # 剩余 110s
    sim.render()
    fn = "04_task_prompt_bash_page1.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-04",
        "file": fn,
        "title": "终端命令审批 (PROMPT Bash - Page 1)",
        "trigger": "PermissionRequest 钩子捕获高危命令 rm -rf dist && npm run build",
        "state": "PROMPT",
        "hmi_elements": ["顶部状态栏 [ APPROVAL ] 琥珀色", "窗口头部 [BASH EXEC] 青色胶囊 + HIGH RISK 琥珀色预警标签", "深黑终端代码框第 1 行命令", "右下角 ▾ 点击翻页 1/2 青色提示", "底部 110s 超时倒计时与动态进度条", "底部 68px 物理大双键: 允许 (Y) 翠绿 / 拒绝 (N) 绯红"]
    })
    print(f"📸 捕获 [TASK-04]: {fn}")

    # ----------------------------------------------------
    # 任务 5: 高危 Bash 命令代码框翻页 (PROMPT / Bash Scrolled) - Page 2
    # ----------------------------------------------------
    sim.prompt_scroll_page = 1
    sim.render()
    fn = "05_task_prompt_bash_page2_scrolled.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-05",
        "file": fn,
        "title": "终端命令审批翻页 (PROMPT Bash - Page 2)",
        "trigger": "轻触终端代码框翻页查看超长命令细节",
        "state": "PROMPT",
        "hmi_elements": ["代码框平滑切换至第 2 页", "显示工作目录 (.../apps/web-dashboard) 与构建产物影响说明 (共 42 个模块)", "翻页指示器更新为 ▾ 点击翻页 2/2", "底部双键热区与倒计时条保持稳定"]
    })
    print(f"📸 捕获 [TASK-05]: {fn}")

    # ----------------------------------------------------
    # 任务 6: 审批倒计时即将耗尽告警 (PROMPT - Countdown Warning < 15s)
    # ----------------------------------------------------
    sim.prompt_scroll_page = 0
    sim.state_entered_time = time.time() - 110  # 剩余 10s
    sim.render()
    fn = "06_task_prompt_countdown_warning.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-06",
        "file": fn,
        "title": "审批倒计时告警 (PROMPT - 临近超时 <15s)",
        "trigger": "审批请求挂起超过 105 秒，剩余时间 <= 15 秒",
        "state": "PROMPT",
        "hmi_elements": ["⚡ 10s 超时熔断 文字切换为红色高亮警告", "底部动态进度条收缩至临界区并变红 (COLOR_RED_BORDER)", "提醒用户即将触发 Fail-Safe 自动拦截"]
    })
    print(f"📸 捕获 [TASK-06]: {fn}")

    # ----------------------------------------------------
    # 任务 7: Git 强推高危审批 (PROMPT / Critical Git Push)
    # ----------------------------------------------------
    sim.current_scenario_key = "2"
    sim.current_scenario = dict(SCENARIOS["2"])
    sim.current_state = "PROMPT"
    sim.prompt_scroll_page = 0
    sim.state_entered_time = time.time()
    sim.render()
    fn = "07_task_prompt_git_push_critical.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-07",
        "file": fn,
        "title": "Git 强推高危审批 (PROMPT Git Push)",
        "trigger": "PermissionRequest 钩子捕获 git push origin main --force-with-lease",
        "state": "PROMPT",
        "hmi_elements": ["顶部状态栏 [ APPROVAL ]", "[GIT PUSH] 胶囊搭配 CRITICAL 鲜红预警标签", "代码框红色警示前缀条", "强推 (Y) / 取消 (N) 语义化定制按钮文案", "高危覆盖主干风险说明"]
    })
    print(f"📸 捕获 [TASK-07]: {fn}")

    # ----------------------------------------------------
    # 任务 8: 新建核心代码文件写入审批 (PROMPT / Write File)
    # ----------------------------------------------------
    sim.current_scenario_key = "3"
    sim.current_scenario = dict(SCENARIOS["3"])
    sim.current_state = "PROMPT"
    sim.prompt_scroll_page = 0
    sim.state_entered_time = time.time()
    sim.render()
    fn = "08_task_prompt_write_file.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-08",
        "file": fn,
        "title": "新建核心文件写入审批 (PROMPT Write File)",
        "trigger": "PermissionRequest 捕获 Write 工具创建关键鉴权文件",
        "state": "PROMPT",
        "hmi_elements": ["[WRITE FILE] 胶囊搭配 SRC WRITE 紫色标签", "智能尾部优先路径缩写: 目标: .../Backend/src/auth/jwt_provider.py", "写入 (Y) / 放弃 (N) 语义化按钮", "变更行数 (+148 行) 与 RS256 模块说明"]
    })
    print(f"📸 捕获 [TASK-08]: {fn}")

    # ----------------------------------------------------
    # 任务 9: 配置文件代码补丁审批 (PROMPT / Edit Patch)
    # ----------------------------------------------------
    sim.current_scenario_key = "4"
    sim.current_scenario = dict(SCENARIOS["4"])
    sim.current_state = "PROMPT"
    sim.prompt_scroll_page = 0
    sim.state_entered_time = time.time()
    sim.render()
    fn = "09_task_prompt_edit_patch.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-09",
        "file": fn,
        "title": "配置文件代码补丁审批 (PROMPT Edit Patch)",
        "trigger": "PermissionRequest 捕获 Edit 工具修改生产配置",
        "state": "PROMPT",
        "hmi_elements": ["[EDIT PATCH] 胶囊搭配 CONFIG EDIT 标签", "目标: .../deploy/config/production.json", "代码框清晰显示 diff 变更 (- db_pool_max: 20 + 64)", "应用 (Y) / 撤销 (N) 语义化按钮"]
    })
    print(f"📸 捕获 [TASK-09]: {fn}")

    # ----------------------------------------------------
    # 任务 10: MCP 扩展数据库高危调用 (PROMPT / MCP Postgres)
    # ----------------------------------------------------
    sim.current_scenario_key = "6"
    sim.current_scenario = dict(SCENARIOS["6"])
    sim.current_state = "PROMPT"
    sim.prompt_scroll_page = 0
    sim.state_entered_time = time.time()
    sim.render()
    fn = "10_task_prompt_mcp_postgres.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-10",
        "file": fn,
        "title": "MCP 数据库调用审批 (PROMPT MCP Tool)",
        "trigger": "PermissionRequest 捕获 mcp__postgres 执行级联删除",
        "state": "PROMPT",
        "hmi_elements": ["[MCP TOOL] 胶囊搭配 MCP POSTGRES 标签", "展示服务实例名 (db-prod-cluster-01)", "DROP TABLE staging_users CASCADE 高危 SQL 警示", "放行 (Y) / 拦截 (N) 按钮"]
    })
    print(f"📸 捕获 [TASK-10]: {fn}")

    # ----------------------------------------------------
    # 任务 11: AskUserQuestion 问答多选任务 - Page 1
    # ----------------------------------------------------
    sim.current_scenario_key = "5"
    sim.current_scenario = dict(SCENARIOS["5"])
    sim.current_state = "QUESTION"
    sim.question_page = 0
    sim.state_entered_time = time.time()
    sim.render()
    fn = "11_task_question_options_page1.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-11",
        "file": fn,
        "title": "问答选项选择 (QUESTION - Page 1)",
        "trigger": "Claude Code 触发 AskUserQuestion 工具请求用户决策",
        "state": "QUESTION",
        "hmi_elements": ["顶部卡片展示问答标题与 ⏱ 120s 倒计时", "2 行大卡片选项: 1. 自动迁移 (Auto) / 2. 仅生成 SQL (Dry-run)", "选项卡带青色编号徽章与 Chevron 右箭头", "右侧 18px 物理滑轨 (滑块在上部)", "底部分页指示 '第 1 / 2 页'，PREV(暗色置灰)/NEXT ▶(高亮可用)"]
    })
    print(f"📸 捕获 [TASK-11]: {fn}")

    # ----------------------------------------------------
    # 任务 12: AskUserQuestion 问答多选任务翻页 - Page 2
    # ----------------------------------------------------
    sim.question_page = 1
    sim.render()
    fn = "12_task_question_options_page2.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-12",
        "file": fn,
        "title": "问答选项选择翻页 (QUESTION - Page 2)",
        "trigger": "轻触 NEXT ▶ 按钮或右侧滑轨下半部翻页",
        "state": "QUESTION",
        "hmi_elements": ["卡片平滑切换显示: 3. 备份后迁移 (Safe) / 4. 取消操作 (Abort)", "右侧滑轨滑块跟随移至下半部", "底部 '第 2 / 2 页'，◀ PREV(高亮可用)/NEXT ▶(暗色置灰)", "每个选项卡片保留独立点击热区"]
    })
    print(f"📸 捕获 [TASK-12]: {fn}")

    # ----------------------------------------------------
    # 任务 13: 审批动作反馈 - 允许放行态 (ANSWERED_ALLOW / APPROVED)
    # ----------------------------------------------------
    sim.current_state = "ANSWERED_ALLOW"
    sim.state_entered_time = time.time()
    sim.render()
    fn = "13_task_feedback_answered_allow.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-13",
        "file": fn,
        "title": "审批放行反馈态 (ANSWERED_ALLOW / APPROVED)",
        "trigger": "轻触物理左键 [允许 (Y)]",
        "state": "ANSWERED_ALLOW",
        "hmi_elements": ["状态栏 [ APPROVED ] 翠绿高亮", "中央大尺寸翠绿环形勾图标", "标题: 授权已放行 · APPROVED (翠绿)", "允许指令已成功回传终端", "提示 2.5s 自动归位待命"]
    })
    print(f"📸 捕获 [TASK-13]: {fn}")

    # ----------------------------------------------------
    # 任务 14: 审批动作反馈 - 拦截拒绝态 (ANSWERED_DENY / STOPPED)
    # ----------------------------------------------------
    sim.current_state = "ANSWERED_DENY"
    sim.state_entered_time = time.time()
    sim.render()
    fn = "14_task_feedback_answered_deny.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-14",
        "file": fn,
        "title": "审批拦截反馈态 (ANSWERED_DENY / STOPPED)",
        "trigger": "轻触物理右键 [拒绝 (N)]",
        "state": "ANSWERED_DENY",
        "hmi_elements": ["状态栏 [ STOPPED ] 绯红高亮", "中央大尺寸绯红环形大叉图标 (清晰区别于绿色勾)", "标题: 操作已安全拦截 · STOPPED (绯红)", "拒绝指令已回传终端", "提示 高危调用已中止 (2.5s 自动归位)"]
    })
    print(f"📸 捕获 [TASK-14]: {fn}")

    # ----------------------------------------------------
    # 任务 15: 问答动作反馈 - 选项已提交态 (ANSWERED_QUESTION / SUBMITTED)
    # ----------------------------------------------------
    sim.current_state = "ANSWERED_QUESTION"
    sim.current_scenario["chosen_label"] = "备份后迁移 (Safe)"
    sim.state_entered_time = time.time()
    sim.render()
    fn = "15_task_feedback_answered_question.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-15",
        "file": fn,
        "title": "问答提交反馈态 (ANSWERED_QUESTION / SUBMITTED)",
        "trigger": "轻触选中某一问答选项卡片",
        "state": "ANSWERED_QUESTION",
        "hmi_elements": ["状态栏 [ SUBMITTED ] 电光青高亮", "中央电光青粒子环图标", "标题: 已选择并回传 · SUBMITTED", "副标题: 已选: 备份后迁移 (Safe)", "提示 Claude 终端继续运行"]
    })
    print(f"📸 捕获 [TASK-15]: {fn}")

    # ----------------------------------------------------
    # 任务 16: 超时熔断异常态 (EXPIRED / Timeout Fail-Safe)
    # ----------------------------------------------------
    sim.current_state = "EXPIRED"
    sim.state_entered_time = time.time()
    sim.render()
    fn = "16_task_expired_timeout.png"
    fp = os.path.join(SCREENSHOTS_DIR, fn)
    sim.save_screenshot(fp, scale=2)
    captures.append({
        "id": "TASK-16",
        "file": fn,
        "title": "超时熔断异常态 (EXPIRED / TIMEOUT)",
        "trigger": "超过 120 秒未收到物理触控响应，触发 Fail-Safe 熔断保护",
        "state": "EXPIRED",
        "hmi_elements": ["状态栏 [ TIMEOUT ] 绯红高亮", "中央绯红环形大叉图标", "标题: 请求已超时 · 120s TIMEOUT", "副标题: 自动触发 Fail-Safe 熔断保护", "提示 终端已安全拦截 · 5秒后自动归位待命"]
    })
    print(f"📸 捕获 [TASK-16]: {fn}")

    # 保存元数据清单
    manifest_path = os.path.join(SCREENSHOTS_DIR, "hmi_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(captures, f, ensure_ascii=False, indent=2)

    print("=" * 70)
    print(f"✅ 成功完成全生命周期 16 大关键 HMI 仿真截图并导出元数据!")
    print(f"📄 清单文件: {manifest_path}")
    print("=" * 70)

if __name__ == "__main__":
    run_simulation_and_capture()
