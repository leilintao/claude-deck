---
title: Claude Deck - 基于 ESP32-2432S028 的 Claude Code 物理副屏开发与系统架构全解析
tags:
  - ESP32
  - Hardware
  - Claude-Code
  - BLE
  - Embedded
  - Architecture
  - AI-Agent
date: 2026-10-15
author: leilintao
repo: https://github.com/leilintao/claude-deck
status: Build verified; BLE pairing requires physical-device validation
---

# Claude Deck：基于 ESP32-2432S028 的 Claude Code 物理副屏开发与系统架构全解析

> **开源项目地址**：[https://github.com/leilintao/claude-deck](https://github.com/leilintao/claude-deck)  
> **系统宿主环境**：macOS (Apple Silicon / Intel, zsh)  
> **硬件终端**：ESP32-2432S028 (HW-458 / 创客黄板 CYD, 2.8寸 TFT + XPT2046 电阻触摸)

---

## 1. 项目缘起与核心价值 (Project Vision & Core Value)

### 1.1 痛点：CLI 交互时代的注意力与安全断层
随着 Anthropic **Claude Code** 等终端自主编程 Agent 的普及，开发者在日常结对编程时面临三大高频痛点：
- **高危工具权限打断感强**：Claude 在执行 `Bash`（如 `rm -rf`、`git push --force`）、`Write`、`Edit` 等写入工具时，需在终端挂起等待确认。
- **窗口切换割裂思路**：全屏查阅文档或 IDE 时容易错过后台终端的权限提示或 `AskUserQuestion` 提问。
- **误操作风险**：连续敲击键盘时容易盲目按回车放行未审查的高危指令。

### 1.2 解决方案：打造硬件物理把关台 (Claude Deck)
本项目利用 **ESP32-2432S028**（CYD 创客黄板）构建放置在键盘旁的无线物理确认台：
1. **配对并绑定的 BLE 连携**：首次通过屏幕显示的六位码与 Mac 配对；固件保存可信主机身份，Mac 保存设备标识，后续仅连接已绑定设备。
2. **全生命周期 Hook 联动**：
   - `PreToolUse`：安全只读 Bash 命令按严格的命令与参数规则放行；其余非只读工具默认阻塞等待副屏实体审批。`AskUserQuestion` 的单选答案通过 `updatedInput.answers` 回传；多选或副屏装不下的问题交给终端原生 UI。
   - `PermissionRequest`：需要原生权限确认时可在副屏显示 `APPROVAL` 卡片；超出副屏容量的详情保留终端完整审批。
   - `PostToolUse` / `Stop`：操作完成时通知副屏归位；若会话与工具输入指纹匹配当前请求，就取消副屏待决审批。状态更新不会生成允许决定，也不会覆盖其他请求。
3. **声光分级反馈与夜间护眼**：使用硬件 `LEDC` PWM 驱动多音阶语义提示音（支持屏幕点击一键静音 `MUTE`），闲置 60 秒自动将背光平滑降至 14% 并关闭状态灯。
4. **故障安全降级与双端看门狗**：支持 120 秒实时倒计时显示与本地/主机双重超时熔断保护，操作完成后 2.5 秒自动归位待命大盘。

---

## 2. 系统整体架构与交互时序 (System Architecture)

整个系统由四大模块组成：
1. **ESP32 嵌入式固件 (`firmware/src/main.cpp`)**：负责 LovyanGFX 渲染、XPT2046 触控压力过滤与抬起释放锁、NimBLE 多帧粘包解析与 UI 状态机。
2. **Mac 主机守护桥接层 (`host/claude_deck_daemon.py`)**：负责 CoreBluetooth BLE 连接维持、MTU 分片写入、异步事件协调与本地 HTTP IPC 服务（`127.0.0.1:19842`）。
3. **Claude Code 接入层 (`host/claude_hook.py`)**：负责 `PreToolUse`、`PermissionRequest`、`PostToolUse` 事件路由、只读命令安全审计与路径尾部保留缩写。
4. **桌面原生仿真器 (`host/virtual_deck.py`)**：基于 Pygame 的 320×240 像素级桌面模拟器（`127.0.0.1:19843`），支持全场景快捷键切换与自动化测试。

---

## 3. 权威 GPIO 映射表 (Pinout Cheat Sheet)

| 功能分组 | 外设/信号 | 对应 GPIO | 硬件特性与设计注意事项 |
| :--- | :--- | :--- | :--- |
| **显示总线 (SPI2/HSPI)** | TFT_SCK / MOSI / MISO | `GPIO 14 / 13 / 12` | 硬件 SPI 显存传输 |
| | TFT_CS / DC | `GPIO 15 / 2` | 片选与数据/命令切换 |
| | **TFT_BL (背光)** | **`GPIO 21 & 27`** | 绑定 `LEDC_CH_BACKLIGHT` (Channel 0, 5kHz PWM)，支持 100% 唤醒与 14% 休眠调光 |
| **触控总线 (SPI3/VSPI)** | **TOUCH_CLK / MOSI / MISO** | **`GPIO 25 / 32 / 39`** | 独立硬件 VSPI 总线，与屏幕彻底解耦 |
| | **TOUCH_CS / IRQ** | **`GPIO 33 / 36`** | 配合 `p.z >= 350` 压力过滤与抬起释放锁（Release Lock）消除长按连发 |
| **板载交互外设** | **RGB_R / G / B** | **`GPIO 4 / 16 / 17`** | 共阳极设计，`LOW` 点亮，`HIGH` 熄灭 |
| | **SPEAKER** | **`GPIO 26`** | 绑定 `LEDC_CH_SPEAKER` (Channel 1)，硬件 PWM 输出多音阶语义提示音 |

---

## 4. 关键工程设计与可靠性保障

1. **UTF-8 安全边界截断 (`copyJsonString`)**：在固件端解析 JSON 字段时按 UTF-8 码点边界截断并附加 `...`，杜绝中文字符被按字节劈半导致的尾部乱码。
2. **BLE 接收缓冲区按帧消费 (`findCompleteJsonFrame`)**：主机将完整 JSON 帧串行发送，固件按顶层花括号深度逐帧解析；审批请求也在主机端排队，避免多个请求覆盖同一屏幕。
3. **完整详情或终端回退**：副屏审批保留可容纳的完整工具输入；超过容量的操作转到终端展示完整内容，避免用截断摘要作批准依据。
4. **语义一致的状态反馈**：固件严格区分 `STATE_ANSWERED_ALLOW`（绿灯 + `APPROVED`）、`STATE_ANSWERED_DENY`（红灯 + `STOPPED`）与 `STATE_ANSWERED_QUESTION`（青灯 + `SUBMITTED`），展示 2.5 秒后自动归位 `STATE_IDLE`。

---

## 5. 部署与验证指南 (Deployment & Verification)

### 5.1 编译与烧录固件
```bash
cd /Users/leilindao/PyProject/ESP/firmware
pio run --target upload
```

### 5.2 启动守护进程或注册开机自启
```bash
# 前台调试运行
./host/start_deck.sh

# 注册为 macOS launchd 后台常驻服务
./host/install_service.sh
```

### 5.3 运行自动化测试套件与桌面仿真器
```bash
# 运行单元与安全测试套件
python3 tests/test_hmi_simulator.py

# 启动桌面 320x240 交互仿真器
./host/run_simulator.sh
```
