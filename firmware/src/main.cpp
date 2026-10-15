#define LGFX_AUTODETECT
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <SPI.h>
#include <XPT2046_Touchscreen.h>
#include <NimBLEDevice.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <vector>
// LovyanGFX has full GB2312 Chinese font support (efontCN) built-in
// No external filesystem or massive font arrays required!

// ==========================================
// 1. Hardware Pin Definitions for CYD (HW-458)
// ==========================================
#define CYD_LED_R 4
#define CYD_LED_G 16
#define CYD_LED_B 17
#define CYD_SPEAKER 26
#define CYD_BL_PIN1 21
#define CYD_BL_PIN2 27

// LEDC PWM Channels
#define LEDC_CH_BACKLIGHT 0
#define LEDC_CH_SPEAKER   1

// Touch Pins
#define XPT2046_IRQ  36
#define XPT2046_MOSI 32
#define XPT2046_MISO 39
#define XPT2046_CLK  25
#define XPT2046_CS   33
#define TOUCH_MIN_PRESSURE 350

static LGFX tft;
static SPIClass touchSPI(VSPI);
static XPT2046_Touchscreen ts(XPT2046_CS, XPT2046_IRQ);

// ==========================================
// 2. BLE Configuration
// ==========================================
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHAR_UUID_RX        "beb5483e-36e1-4688-b7f5-ea07361b26a8" // Mac writes to this
#define CHAR_UUID_TX        "beb5483f-36e1-4688-b7f5-ea07361b26a8" // ESP32 notifies to this

static NimBLEServer* pServer = nullptr;
static NimBLECharacteristic* pTxCharacteristic = nullptr;
static volatile bool deviceConnected = false;
static uint16_t peerConnHandle = BLE_HS_CONN_HANDLE_NONE;

// ==========================================
// 3. UI State Machine & Message Queue
// ==========================================
enum UIState {
  STATE_IDLE,
  STATE_PAIRING,
  STATE_THINKING,
  STATE_PROMPT,
  STATE_QUESTION,
  STATE_ANSWERED_ALLOW,
  STATE_ANSWERED_DENY,
  STATE_ANSWERED_QUESTION,
  STATE_EXPIRED
};

enum DeckMessageType {
  DECK_MSG_PROMPT,
  DECK_MSG_QUESTION,
  DECK_MSG_STATUS
};

// BLE NimBLE callbacks run in another task. They only parse JSON and enqueue
// messages. All display/shared-state mutation happens in loop().
typedef struct {
  DeckMessageType type;
  char id[48];
  char badge[32];
  char tag[24];
  char title[96];
  char desc[1024];
  char option1[32];
  char option2[32];
  char status[24];
  char header[32];
  char question[320];
  char optionLabels[4][64];
  char optionDescriptions[4][128];
  uint8_t optionCount;
  bool multiSelect;
  uint16_t timeoutSec;
} DeckMessage;

static QueueHandle_t uiQueue = nullptr;

// The BLE callback owns allocation of these messages. The queue carries only
// pointers; loop() consumes and frees them. This keeps large payloads off the
// NimBLE task stack.
static DeckMessage* allocateDeckMessage() {
  void* memory = pvPortMalloc(sizeof(DeckMessage));
  if (memory == nullptr) return nullptr;
  DeckMessage* msg = static_cast<DeckMessage*>(memory);
  memset(msg, 0, sizeof(DeckMessage));
  return msg;
}

static UIState currentState = STATE_IDLE;
static char pendingRequestId[48] = "";
static String promptBadge = "[CLAUDE APPROVAL]";
static String promptTag = "CONFIRM";
static String promptTitle = "Claude Code 待命";
static String promptDesc = "副屏已就绪，正在监听 Claude Code 编程指令...";
static String option1Text = "允许 (Y)";
static String option2Text = "拒绝 (N)";
static String questionHeaderText = "QUESTION";
static String questionText = "";
static String questionOptionLabels[4];
static String questionOptionDescriptions[4];
static String selectedQuestionLabel = "";
static uint8_t questionOptionCount = 0;
static uint8_t questionPage = 0;
static bool questionMultiSelect = false;
static uint8_t questionSelectedMask = 0;

// Scrolling & lifecycle timers
static int promptScrollLine = 0;
static int promptTotalLines = 0;
static uint16_t activeTimeoutSec = 120;
static int lastRenderedRemainSec = -1;
static unsigned long stateEnteredMillis = 0;
static unsigned long lastActivityMillis = 0;
static bool isScreenDimmed = false;
static bool isMuted = false;
static volatile uint32_t pairingPasskey = 0;
static Preferences pairingPrefs;

// Touch release-lock state (prevents long-press auto-repeat)
static bool touchHeld = false;
static unsigned long lastTouchTime = 0;
static unsigned long lastReleaseTime = 0;

#define IDLE_DIM_TIMEOUT_MS      60000UL
#define ANSWERED_RETURN_IDLE_MS  2500UL
#define EXPIRED_RETURN_IDLE_MS   5000UL

#define QUESTION_MAX_OPTIONS 4
#define QUESTION_PER_PAGE 2
#define QUESTION_ROW_X 8
#define QUESTION_ROW_W 278
#define QUESTION_ROW_H 52
#define QUESTION_ROW1_Y 92
#define QUESTION_ROW2_Y 150
#define QUESTION_NAV_Y 210

// 选项卡右侧加宽滑动条规范 (宽度 18px，舒适好点好识别)
#define SCROLLBAR_X 294
#define SCROLLBAR_W 18
#define SCROLLBAR_Y 92
#define SCROLLBAR_H 110

// ==========================================
// Cyberdeck Studio 2.0 Palette (RGB565)
// ==========================================
#define COLOR_BG            0x0862 // #090A0F Ultra-deep Obsidian
#define COLOR_HEADER_BG     0x10A3 // #10121A Status Bar
#define COLOR_HEADER_LINE   0x2147 // #232A3B Header Divider
#define COLOR_CARD_BG       0x10C4 // #131620 Card Surface
#define COLOR_CARD_BORDER   0x2988 // #283044 Card Edge
#define COLOR_CARD_HEADER   0x18E5 // #1A1E2C Window Titlebar
#define COLOR_CODE_BG       0x0841 // #07080C Terminal Monospace Box
#define COLOR_TERRACOTTA    0xE3EC // #E07A5F Anthropic Terracotta
#define COLOR_AMBER_GLOW    0xFC41 // #F59E0B Warning / Active Amber
#define COLOR_AMBER_HI      0xFE69 // #FCD34D Bright Amber
#define COLOR_TEXT_WHITE    0xFFFF // #FFFFFF Crisp White
#define COLOR_TEXT_MUTED    0x9537 // #94A3B8 Slate Gray
#define COLOR_TEXT_DIM      0x63B1 // #64748B Dark Slate

// Buttons (Emerald & Crimson Bevel)
#define COLOR_GREEN_BTN     0x0267 // #064E3B Deep Emerald Surface
#define COLOR_GREEN_HI      0x36B3 // #34D399 Bright Emerald Highlight
#define COLOR_GREEN_BORDER  0x15D0 // #10B981 Emerald Border
#define COLOR_GREEN_MINT    0xA7F3 // #A7F3D0 Light Mint Text

#define COLOR_RED_BTN       0x4081 // #450A0A Deep Crimson Surface
#define COLOR_RED_HI        0xFB8E // #F87171 Bright Rose Highlight
#define COLOR_RED_BORDER    0xF228 // #EF4444 Crimson Border
#define COLOR_RED_ROSE      0xFDEE // #FECDD3 Light Rose Text

// Accent Cyber Colors
#define COLOR_CYAN_ACCENT   0x05BD // #06B6D4 Electric Cyan
#define COLOR_CYAN_HI       0x677F // #67E8F9 Bright Cyan
#define COLOR_CYAN_BG       0x0989 // #082F49 Deep Cyan Surface
#define COLOR_PURPLE_ACCENT 0x8AEF // #8B5CF6 Violet
#define COLOR_PURPLE_BG     0x2889 // #2E1065 Deep Violet Surface

// Touch button layout (320 x 240) - 68px ergonomic height
static const int BTN_Y = 162;
static const int BTN_H = 68;
static const int BTN1_X = 8;
static const int BTN1_W = 148;
static const int BTN2_X = 164;
static const int BTN2_W = 148;

void inferBadgeAndTag(const String& title, String& badge, String& tag) {
  if (badge.length() > 0 && tag.length() > 0) return;
  if (title.indexOf("Bash") >= 0 || title.indexOf("终端") >= 0 || title.indexOf("命令") >= 0) {
    if (badge.length() == 0) badge = "[BASH EXEC]";
    if (tag.length() == 0) tag = "HIGH RISK";
  } else if (title.indexOf("Write") >= 0 || title.indexOf("写入") >= 0) {
    if (badge.length() == 0) badge = "[WRITE FILE]";
    if (tag.length() == 0) tag = "SRC WRITE";
  } else if (title.indexOf("Edit") >= 0 || title.indexOf("修改") >= 0) {
    if (badge.length() == 0) badge = "[EDIT PATCH]";
    if (tag.length() == 0) tag = "CONFIG EDIT";
  } else if (title.indexOf("搜索") >= 0 || title.indexOf("Search") >= 0) {
    if (badge.length() == 0) badge = "[WEB SEARCH]";
    if (tag.length() == 0) tag = "NETWORK";
  } else if (title.indexOf("抓取") >= 0 || title.indexOf("Fetch") >= 0) {
    if (badge.length() == 0) badge = "[WEB FETCH]";
    if (tag.length() == 0) tag = "NETWORK";
  } else if (title.indexOf("MCP") >= 0) {
    if (badge.length() == 0) badge = "[MCP TOOL]";
    if (tag.length() == 0) tag = "MCP SERVER";
  } else {
    if (badge.length() == 0) badge = "[PERMISSION]";
    if (tag.length() == 0) tag = "CONFIRM";
  }
}

void setBacklight(uint8_t brightness) {
  ledcWrite(LEDC_CH_BACKLIGHT, brightness);
}

void wakeScreen() {
  lastActivityMillis = millis();
  if (isScreenDimmed) {
    isScreenDimmed = false;
    setBacklight(255);
  }
}

void setLED(bool r, bool g, bool b) {
  // Active LOW on CYD
  digitalWrite(CYD_LED_R, r ? LOW : HIGH);
  digitalWrite(CYD_LED_G, g ? LOW : HIGH);
  digitalWrite(CYD_LED_B, b ? LOW : HIGH);
}

void playTone(uint32_t freq, uint32_t ms) {
  if (isMuted || ms == 0) return;
  ledcWriteTone(LEDC_CH_SPEAKER, freq);
  delay(ms);
  ledcWriteTone(LEDC_CH_SPEAKER, 0);
  digitalWrite(CYD_SPEAKER, LOW);
}

void playEarconPrompt() {
  if (isMuted) return;
  playTone(1800, 35);
  delay(25);
  playTone(2400, 50);
}

void playEarconQuestion() {
  if (isMuted) return;
  playTone(1600, 30);
  delay(20);
  playTone(2000, 30);
  delay(20);
  playTone(2600, 45);
}

void playEarconAllow() {
  if (isMuted) return;
  playTone(2400, 35);
  delay(20);
  playTone(3200, 50);
}

void playEarconDeny() {
  if (isMuted) return;
  playTone(900, 55);
  delay(25);
  playTone(620, 80);
}

void playEarconTap() {
  if (isMuted) return;
  playTone(2200, 18);
}

int getBleSignalBars() {
  if (!deviceConnected) return 0;
  if (peerConnHandle != BLE_HS_CONN_HANDLE_NONE) {
    int8_t rssi = 0;
    if (ble_gap_conn_rssi(peerConnHandle, &rssi) == 0) {
      if (rssi >= -60) return 4;
      if (rssi >= -72) return 3;
      if (rssi >= -84) return 2;
      return 1;
    }
  }
  return 4;
}

void drawSpark(int cx, int cy, int r, uint16_t color) {
  int r3 = (r * 3) / 10;
  if (r3 < 1) r3 = 1;
  tft.fillTriangle(cx, cy - r, cx - r3, cy, cx + r3, cy, color);
  tft.fillTriangle(cx, cy + r, cx - r3, cy, cx + r3, cy, color);
  tft.fillTriangle(cx - r, cy, cx, cy - r3, cx, cy + r3, color);
  tft.fillTriangle(cx + r, cy, cx, cy - r3, cx, cy + r3, color);
}

void drawChevron(int x, int y, int h, uint16_t color) {
  int midY = y + h / 2;
  tft.drawLine(x, y, x + 5, midY, color);
  tft.drawLine(x, y + 1, x + 5, midY + 1, color);
  tft.drawLine(x + 5, midY, x, y + h, color);
  tft.drawLine(x + 5, midY + 1, x, y + h + 1, color);
}

void drawStatusBar() {
  tft.fillRect(0, 0, 320, 24, COLOR_HEADER_BG);
  tft.drawFastHLine(0, 24, 320, COLOR_HEADER_LINE);

  // Left Brand: Anthropic Spark + Title
  drawSpark(13, 12, 5, COLOR_TERRACOTTA);
  tft.setTextColor(COLOR_TEXT_WHITE, COLOR_HEADER_BG);
  tft.drawString("CLAUDE DECK", 23, 5);

  // Center Mode Pill
  const char* modeText = "[ STANDBY ]";
  uint16_t modeColor = COLOR_GREEN_HI;

  if (currentState == STATE_PROMPT) {
    modeText = "[ APPROVAL ]";
    modeColor = COLOR_AMBER_GLOW;
  } else if (currentState == STATE_THINKING) {
    modeText = "[ REASONING ]";
    modeColor = COLOR_CYAN_HI;
  } else if (currentState == STATE_QUESTION) {
    modeText = "[ QUESTION ]";
    modeColor = COLOR_CYAN_HI;
  } else if (currentState == STATE_ANSWERED_ALLOW) {
    modeText = "[ APPROVED ]";
    modeColor = COLOR_GREEN_HI;
  } else if (currentState == STATE_ANSWERED_DENY) {
    modeText = "[ STOPPED ]";
    modeColor = COLOR_RED_HI;
  } else if (currentState == STATE_ANSWERED_QUESTION) {
    modeText = "[ SUBMITTED ]";
    modeColor = COLOR_CYAN_HI;
  } else if (currentState == STATE_EXPIRED) {
    modeText = "[ TIMEOUT ]";
    modeColor = COLOR_RED_HI;
  }

  int pillW = 88;
  int pillX = 112;
  tft.fillRoundRect(pillX, 3, pillW, 18, 4, COLOR_CARD_BG);
  tft.drawRoundRect(pillX, 3, pillW, 18, 4, COLOR_CARD_BORDER);
  tft.setTextColor(modeColor, COLOR_CARD_BG);
  tft.drawCenterString(modeText, pillX + pillW / 2, 4);

  // Right Telemetry: Interactive Mute Pill, Real RSSI Signal Bars & Power
  uint16_t muteBg = isMuted ? 0x4101 : COLOR_CYAN_BG;
  uint16_t muteBd = isMuted ? COLOR_AMBER_GLOW : COLOR_CYAN_ACCENT;
  uint16_t muteFg = isMuted ? COLOR_AMBER_HI : COLOR_CYAN_HI;
  tft.fillRoundRect(206, 3, 50, 18, 4, muteBg);
  tft.drawRoundRect(206, 3, 50, 18, 4, muteBd);
  tft.setTextColor(muteFg, muteBg);
  tft.drawCenterString(isMuted ? "MUTE" : "SND", 231, 4);

  int bars = getBleSignalBars();
  int sigX = 264;
  for (int i = 0; i < 4; i++) {
    int h = 3 + i * 2;
    uint16_t c = (i < bars) ? COLOR_GREEN_HI : COLOR_CARD_BORDER;
    tft.fillRect(sigX + i * 4, 16 - h, 2, h, c);
  }

  int batX = 284;
  tft.drawRect(batX, 8, 16, 9, COLOR_TEXT_MUTED);
  tft.fillRect(batX + 16, 10, 2, 5, COLOR_TEXT_MUTED);
  tft.fillRect(batX + 2, 10, 12, 5, deviceConnected ? COLOR_GREEN_HI : COLOR_AMBER_GLOW);
  tft.setTextColor(deviceConnected ? COLOR_GREEN_HI : COLOR_AMBER_GLOW, COLOR_HEADER_BG);
  tft.drawString("5V", batX + 20, 5);
}

void drawTactileButton(int x, int y, int w, int h, bool isAllow, const String& label, const char* sublabel, bool pressed = false) {
  if (pressed) {
    tft.fillRoundRect(x, y, w, h, 8, TFT_WHITE);
    tft.drawRoundRect(x, y, w, h, 8, isAllow ? COLOR_GREEN_HI : COLOR_RED_HI);
    tft.setTextColor(TFT_BLACK, TFT_WHITE);
    tft.drawCenterString(label.c_str(), x + w / 2, y + 16);
    tft.drawCenterString(sublabel, x + w / 2, y + 40);
    return;
  }

  uint16_t surfColor = isAllow ? COLOR_GREEN_BTN : COLOR_RED_BTN;
  uint16_t hiColor = isAllow ? COLOR_GREEN_HI : COLOR_RED_HI;
  uint16_t borderColor = isAllow ? COLOR_GREEN_BORDER : COLOR_RED_BORDER;
  uint16_t subColor = isAllow ? COLOR_GREEN_MINT : COLOR_RED_ROSE;

  // Button body
  tft.fillRoundRect(x, y, w, h, 8, surfColor);
  tft.drawRoundRect(x, y, w, h, 8, borderColor);

  // Top tactile bevel line (2px)
  tft.drawFastHLine(x + 8, y + 2, w - 16, hiColor);
  tft.drawFastHLine(x + 10, y + 3, w - 20, hiColor);

  // Vector Icon (Checkmark or Cross)
  int cx = x + 24;
  int cy = y + h / 2;
  if (isAllow) {
    for (int d = -1; d <= 1; d++) {
      tft.drawLine(cx - 9, cy + d, cx - 3, cy + 7 + d, hiColor);
      tft.drawLine(cx - 3, cy + 7 + d, cx + 9, cy - 7 + d, hiColor);
    }
  } else {
    for (int d = -1; d <= 1; d++) {
      tft.drawLine(cx - 7, cy - 7 + d, cx + 7, cy + 7 + d, hiColor);
      tft.drawLine(cx - 7, cy + 7 + d, cx + 7, cy - 7 + d, hiColor);
    }
  }

  // Two-tier text labels
  tft.setTextColor(COLOR_TEXT_WHITE, surfColor);
  tft.drawString(label.c_str(), x + 44, y + 15);
  tft.setTextColor(subColor, surfColor);
  tft.drawString(sublabel, x + 44, y + 39);
}

// UTF-8 & newline-aware text wrapper with scroll offset support.
// Returns the total number of wrapped lines in `text`.
int drawWrappedText(const String& text, int x, int y, int maxWidth, int lineHeight, int maxLines, int startLine = 0) {
  unsigned int pos = 0;
  int totalLines = 0;
  int drawnLines = 0;

  while (pos < text.length()) {
    // Skip leading '\r'
    if (text[pos] == '\r') {
      pos++;
      continue;
    }
    if (text[pos] == '\n') {
      pos++;
      totalLines++;
      continue;
    }

    unsigned int lineStart = pos;
    while (pos < text.length()) {
      unsigned char c = text[pos];
      if (c == '\r' || c == '\n') {
        break;
      }

      int charLen = 1;
      if ((c & 0x80) == 0x00) charLen = 1;
      else if ((c & 0xE0) == 0xC0) charLen = 2;
      else if ((c & 0xF0) == 0xE0) charLen = 3;
      else if ((c & 0xF8) == 0xF0) charLen = 4;

      unsigned int next = pos + charLen;
      if (next > text.length()) {
        next = text.length();
      }

      String candidate = text.substring(lineStart, next);
      if (pos > lineStart && tft.textWidth(candidate.c_str()) > maxWidth) {
        break;
      }

      pos = next;
    }

    if (totalLines >= startLine && drawnLines < maxLines) {
      String line = text.substring(lineStart, pos);
      tft.drawString(line.c_str(), x, y + drawnLines * lineHeight);
      drawnLines++;
    }

    totalLines++;
    if (pos < text.length() && text[pos] == '\r') pos++;
    if (pos < text.length() && text[pos] == '\n') pos++;
  }

  return totalLines;
}

int getRemainingSeconds() {
  unsigned long elapsedSec = (millis() - stateEnteredMillis) / 1000UL;
  if (elapsedSec >= activeTimeoutSec) return 0;
  return (int)(activeTimeoutSec - elapsedSec);
}

void drawPromptFooterCountdown() {
  if (currentState != STATE_PROMPT) return;
  int cx = 8, cy = 28, cw = 304, ch = 126;
  int remain = getRemainingSeconds();
  lastRenderedRemainSec = remain;

  // Clear footer right area inside card
  tft.fillRect(cx + 150, cy + ch - 24, cw - 156, 16, COLOR_CARD_BG);
  char buf[32];
  snprintf(buf, sizeof(buf), "⚡ %ds 超时熔断", remain);
  tft.setTextColor(remain <= 15 ? COLOR_RED_HI : COLOR_AMBER_HI, COLOR_CARD_BG);
  tft.drawRightString(buf, cx + cw - 10, cy + ch - 23);

  // Dynamic Visual Countdown Progress Bar
  int barX = cx + 8;
  int barY = cy + ch - 7;
  int barW = cw - 16;
  tft.fillRoundRect(barX, barY, barW, 4, 2, COLOR_CODE_BG);
  int maxSec = max(1, (int)activeTimeoutSec);
  int fillW = (barW * remain) / maxSec;
  if (fillW > 0) {
    uint16_t barColor = (remain <= 20) ? COLOR_RED_BORDER : COLOR_AMBER_GLOW;
    tft.fillRoundRect(barX, barY, fillW, 4, 2, barColor);
  }
}

void drawQuestionCountdown() {
  if (currentState != STATE_QUESTION) return;
  int remain = getRemainingSeconds();
  lastRenderedRemainSec = remain;
  // Update only the timer field. Repainting the whole question every second
  // visibly flashes the option cards on the physical display.
  tft.fillRect(262, 30, 44, 18, COLOR_CARD_BG);
  char buf[20];
  snprintf(buf, sizeof(buf), "%ds", remain);
  tft.setTextColor(COLOR_TEXT_DIM, COLOR_CARD_BG);
  tft.drawRightString(buf, 304, 31);
}

void drawPromptCodeBox() {
  int cx = 8, cy = 28, cw = 304;
  int tbx = cx + 8, tby = cy + 28, tbw = cw - 16, tbh = 68;
  tft.fillRoundRect(tbx, tby, tbw, tbh, 4, COLOR_CODE_BG);
  tft.drawRoundRect(tbx, tby, tbw, tbh, 4, 0x1946);
  uint16_t accent = (promptTag == "CRITICAL") ? COLOR_RED_BORDER : COLOR_AMBER_GLOW;
  tft.fillRect(tbx, tby, 3, tbh, accent);

  tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CODE_BG);
  promptTotalLines = drawWrappedText(promptDesc, tbx + 8, tby + 6, tbw - 14, 19, 3, promptScrollLine);

  if (promptTotalLines > 3) {
    int totalPages = (promptTotalLines + 2) / 3;
    int curPage = (promptScrollLine / 3) + 1;
    char pageBuf[20];
    snprintf(pageBuf, sizeof(pageBuf), "[▾ %d/%d]", curPage, totalPages);
    tft.fillRoundRect(tbx + tbw - 58, tby + tbh - 17, 56, 15, 3, COLOR_CARD_HEADER);
    tft.setTextColor(COLOR_CYAN_HI, COLOR_CARD_HEADER);
    tft.drawRightString(pageBuf, tbx + tbw - 4, tby + tbh - 16);
  }
}

void renderUI() {
  drawStatusBar();

  // Content background (Ultra-deep Obsidian)
  tft.fillRect(0, 25, 320, 215, COLOR_BG);

  if (currentState == STATE_PAIRING) {
    setLED(false, false, true);
    tft.fillRoundRect(8, 28, 304, 198, 8, COLOR_CARD_BG);
    tft.drawRoundRect(8, 28, 304, 198, 8, COLOR_CYAN_ACCENT);
    tft.setTextColor(COLOR_CYAN_HI, COLOR_CARD_BG);
    tft.drawCenterString("蓝牙首次配对", 160, 55);
    char code[16];
    snprintf(code, sizeof(code), "%06lu", (unsigned long)pairingPasskey);
    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
    tft.drawCenterString(code, 160, 105);
    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    tft.drawCenterString("请在 Mac 蓝牙提示中输入此号码", 160, 158);
    tft.drawCenterString("仅首次绑定需要", 160, 183);
  }
  else if (currentState == STATE_IDLE) {
    if (isScreenDimmed) {
      setLED(false, false, false);
    } else {
      setLED(false, true, false); // Green
    }

    // 1. Center Card Container (8, 28, 304, 134)
    int cx = 8, cy = 28, cw = 304, ch = 134;
    tft.fillRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BG);
    tft.drawRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BORDER);

    // Tech Corner Brackets
    uint16_t bracketC = 0x31C8;
    int blen = 8;
    tft.drawFastHLine(cx + 4, cy + 4, blen, bracketC);
    tft.drawFastVLine(cx + 4, cy + 4, blen, bracketC);
    tft.drawFastHLine(cx + cw - 4 - blen, cy + 4, blen, bracketC);
    tft.drawFastVLine(cx + cw - 4, cy + 4, blen, bracketC);
    tft.drawFastHLine(cx + 4, cy + ch - 4, blen, bracketC);
    tft.drawFastVLine(cx + 4, cy + ch - 4 - blen, bracketC);
    tft.drawFastHLine(cx + cw - 4 - blen, cy + ch - 4, blen, bracketC);
    tft.drawFastVLine(cx + cw - 4, cy + ch - 4 - blen, bracketC);

    // Center Radar Circles & Claude Spark
    int ecx = 160, ecy = 70;
    tft.drawCircle(ecx, ecy, 30, 0x11E4);
    tft.drawCircle(ecx, ecy, 22, COLOR_GREEN_BTN);
    tft.drawFastVLine(ecx, ecy - 34, 6, COLOR_GREEN_HI);
    tft.drawFastVLine(ecx, ecy + 28, 6, COLOR_GREEN_HI);
    tft.drawFastHLine(ecx - 34, ecy, 6, COLOR_GREEN_HI);
    tft.drawFastHLine(ecx + 28, ecy, 6, COLOR_GREEN_HI);

    tft.fillCircle(ecx, ecy, 13, COLOR_GREEN_BTN);
    drawSpark(ecx, ecy, 8, COLOR_GREEN_HI);

    // Hero title & subtitle
    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
    tft.drawCenterString("Claude Code 终端待命", 160, 108);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    tft.drawCenterString("物理安全守护已激活 · 遇高危调用副屏提醒", 160, 132);

    // 2. Bottom 3-Card Telemetry Dock (Y: 168, H: 64)
    int dockY = 168;
    int dockH = 64;
    int cardW = 96;

    // Card 1: BLE Link
    int mx1 = 8;
    tft.fillRoundRect(mx1, dockY, cardW, dockH, 6, COLOR_CARD_BG);
    tft.drawRoundRect(mx1, dockY, cardW, dockH, 6, COLOR_CARD_BORDER);
    tft.setTextColor(COLOR_TEXT_DIM, COLOR_CARD_BG);
    tft.drawString("BLE LINK", mx1 + 10, dockY + 8);
    tft.fillRoundRect(mx1 + 8, dockY + 26, cardW - 16, 26, 4, COLOR_GREEN_BTN);
    tft.setTextColor(COLOR_GREEN_HI, COLOR_GREEN_BTN);
    tft.drawCenterString(deviceConnected ? "ONLINE" : "WAITING", mx1 + cardW / 2, dockY + 32);

    // Card 2: Audio Mute Toggle (Interactive!)
    int mx2 = mx1 + cardW + 8;
    tft.fillRoundRect(mx2, dockY, cardW, dockH, 6, COLOR_CARD_BG);
    tft.drawRoundRect(mx2, dockY, cardW, dockH, 6, COLOR_CARD_BORDER);
    tft.setTextColor(COLOR_TEXT_DIM, COLOR_CARD_BG);
    tft.drawString("AUDIO TAP", mx2 + 10, dockY + 8);
    uint16_t audioBg = isMuted ? 0x4101 : COLOR_CYAN_BG;
    uint16_t audioFg = isMuted ? COLOR_AMBER_HI : COLOR_CYAN_HI;
    tft.fillRoundRect(mx2 + 8, dockY + 26, cardW - 16, 26, 4, audioBg);
    tft.setTextColor(audioFg, audioBg);
    tft.drawCenterString(isMuted ? "MUTED" : "SOUND ON", mx2 + cardW / 2, dockY + 32);

    // Card 3: Timeout
    int mx3 = mx2 + cardW + 8;
    tft.fillRoundRect(mx3, dockY, cardW, dockH, 6, COLOR_CARD_BG);
    tft.drawRoundRect(mx3, dockY, cardW, dockH, 6, COLOR_CARD_BORDER);
    tft.setTextColor(COLOR_TEXT_DIM, COLOR_CARD_BG);
    tft.drawString("TIMEOUT", mx3 + 10, dockY + 8);
    tft.fillRoundRect(mx3 + 8, dockY + 26, cardW - 16, 26, 4, COLOR_CYAN_BG);
    tft.setTextColor(COLOR_CYAN_HI, COLOR_CYAN_BG);
    char toBuf[20];
    snprintf(toBuf, sizeof(toBuf), "%ds AUTO", activeTimeoutSec);
    tft.drawCenterString(toBuf, mx3 + cardW / 2, dockY + 32);
  }
  else if (currentState == STATE_THINKING) {
    setLED(false, false, true); // Blue

    int cx = 8, cy = 28, cw = 304, ch = 198;
    tft.fillRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BG);
    tft.drawRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BORDER);

    // Center Tech Gyroscope / Neural Rings
    int ecx = 160, ecy = 78;
    tft.drawCircle(ecx, ecy, 36, 0x09CB);
    tft.drawCircle(ecx, ecy, 26, COLOR_CYAN_BG);
    tft.fillCircle(ecx, ecy, 16, COLOR_CYAN_BG);
    tft.drawCircle(ecx, ecy, 16, COLOR_CYAN_ACCENT);
    drawSpark(ecx, ecy, 9, COLOR_CYAN_HI);

    const float angles[8] = { 0, 45, 90, 135, 180, 225, 270, 315 };
    for (int i = 0; i < 8; i++) {
      float rad = angles[i] * 3.14159f / 180.0f;
      int ox = ecx + (int)(cos(rad) * 36);
      int oy = ecy + (int)(sin(rad) * 36);
      tft.fillCircle(ox, oy, 2, COLOR_CYAN_ACCENT);
    }

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
    String thinkHeader = (promptTitle.length() > 0 && promptTitle != "Claude Code 待命")
                           ? promptTitle
                           : String("Claude 正在深度思考中...");
    tft.drawCenterString(thinkHeader.c_str(), 160, 122);

    // Description Box
    int tbx = cx + 12, tby = 146, tbw = cw - 24, tbh = 46;
    tft.fillRoundRect(tbx, tby, tbw, tbh, 4, COLOR_CODE_BG);
    tft.drawRoundRect(tbx, tby, tbw, tbh, 4, 0x09CB);
    tft.fillRect(tbx, tby, 3, tbh, COLOR_CYAN_ACCENT);
    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CODE_BG);
    drawWrappedText(promptDesc, tbx + 8, tby + 6, tbw - 14, 17, 2, 0);

    // Scanning bar
    tft.setTextColor(COLOR_CYAN_ACCENT, COLOR_CARD_BG);
    tft.drawString("■ ■ ■ ■ ■ □ □ □", cx + 12, 202);
    tft.setTextColor(COLOR_TEXT_DIM, COLOR_CARD_BG);
    tft.drawRightString("EXECUTING / REASONING", cx + cw - 12, 202);
  }
  else if (currentState == STATE_PROMPT) {
    setLED(true, true, false); // Yellow / Amber

    // 1. Middle Card Container (8, 28, 304, 126, r=8)
    int cx = 8, cy = 28, cw = 304, ch = 126;
    tft.fillRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BG);
    tft.drawRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BORDER);

    // Window Header Bar (Replaced false-affordance macOS dots with Badge + Title + Tag)
    tft.fillRoundRect(cx, cy, cw, 22, 8, COLOR_CARD_HEADER);
    tft.fillRect(cx, cy + 14, cw, 8, COLOR_CARD_HEADER);
    tft.drawFastHLine(cx, cy + 22, cw, COLOR_CARD_BORDER);

    // Left Tool Badge
    tft.setTextColor(COLOR_CYAN_HI, COLOR_CARD_HEADER);
    tft.drawString(promptBadge.c_str(), cx + 8, cy + 4);
    int badgeW = tft.textWidth(promptBadge.c_str());

    // Prompt Header title
    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_HEADER);
    tft.drawString(promptTitle.c_str(), cx + 14 + badgeW, cy + 4);

    // Category Tag Pill on top right
    int tagW = 76;
    int tagX = cx + cw - tagW - 4;
    bool isCrit = (promptTag == "CRITICAL");
    uint16_t tagBg = isCrit ? COLOR_RED_BTN : 0x4101;
    uint16_t tagBorder = isCrit ? COLOR_RED_BORDER : COLOR_AMBER_GLOW;
    uint16_t tagFg = isCrit ? COLOR_RED_HI : COLOR_AMBER_HI;
    tft.fillRoundRect(tagX, cy + 3, tagW, 16, 3, tagBg);
    tft.drawRoundRect(tagX, cy + 3, tagW, 16, 3, tagBorder);
    tft.setTextColor(tagFg, tagBg);
    tft.drawCenterString(promptTag.c_str(), tagX + tagW / 2, cy + 4);

    // Inner Terminal Codebox (supports tap-to-scroll when > 3 lines)
    drawPromptCodeBox();

    // Footer notice & dynamic countdown
    drawSpark(cx + 14, cy + ch - 12, 4, COLOR_TERRACOTTA);
    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    tft.drawString(promptTotalLines > 3 ? "轻触代码框翻页" : "物理双键审批把关", cx + 24, cy + ch - 19);
    drawPromptFooterCountdown();

    // 2. Buttons
    drawTactileButton(BTN1_X, BTN_Y, BTN1_W, BTN_H, true, option1Text, "[ CONFIRM ]");
    drawTactileButton(BTN2_X, BTN_Y, BTN2_W, BTN_H, false, option2Text, "[ ABORT ]");
  }
  else if (currentState == STATE_QUESTION) {
    setLED(false, false, true); // Blue

    // Header Question Card (8, 28, 304, 54)
    tft.fillRoundRect(8, 28, 304, 54, 8, COLOR_CARD_BG);
    tft.drawRoundRect(8, 28, 304, 54, 8, COLOR_CARD_BORDER);

    drawSpark(18, 38, 4, COLOR_CYAN_HI);
    tft.setTextColor(COLOR_CYAN_HI, COLOR_CARD_BG);
    tft.drawString(questionHeaderText.c_str(), 28, 31);

    drawQuestionCountdown();

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
    drawWrappedText(questionText, 16, 49, 288, 16, 2, 0);

    // Option rows (uniform visual hierarchy so Option 1 doesn't look pre-selected)
    uint8_t pageCount = (questionOptionCount + QUESTION_PER_PAGE - 1) / QUESTION_PER_PAGE;
    uint8_t start = questionPage * QUESTION_PER_PAGE;
    for (int row = 0; row < QUESTION_PER_PAGE; ++row) {
      int index = start + row;
      int y = (row == 0) ? QUESTION_ROW1_Y : QUESTION_ROW2_Y;

      if (index >= questionOptionCount) {
        tft.fillRoundRect(QUESTION_ROW_X, y, QUESTION_ROW_W, QUESTION_ROW_H, 8, COLOR_BG);
        continue;
      }

      bool selected = questionMultiSelect && (questionSelectedMask & (1 << index));
      uint16_t rowBg = selected ? COLOR_CYAN_BG : COLOR_CARD_BG;
      tft.fillRoundRect(QUESTION_ROW_X, y, QUESTION_ROW_W, QUESTION_ROW_H, 8, rowBg);
      tft.drawRoundRect(QUESTION_ROW_X, y, QUESTION_ROW_W, QUESTION_ROW_H, 8, selected ? COLOR_CYAN_ACCENT : COLOR_CARD_BORDER);

      // Number badge (uniform cyan style for all interactive options)
      tft.fillRoundRect(QUESTION_ROW_X + 8, y + 10, 24, 32, 4, COLOR_CYAN_BG);
      tft.drawRoundRect(QUESTION_ROW_X + 8, y + 10, 24, 32, 4, COLOR_CYAN_ACCENT);
      char numBuf[4];
      snprintf(numBuf, sizeof(numBuf), "%d", index + 1);
      tft.setTextColor(COLOR_CYAN_HI, COLOR_CYAN_BG);
      tft.drawCenterString(numBuf, QUESTION_ROW_X + 20, y + 18);

      // Label & Description
      tft.setTextColor(COLOR_TEXT_WHITE, rowBg);
      tft.drawString(questionOptionLabels[index].c_str(), QUESTION_ROW_X + 40, y + 9);
      tft.setTextColor(COLOR_TEXT_MUTED, rowBg);
      drawWrappedText(questionOptionDescriptions[index], QUESTION_ROW_X + 40, y + 29, QUESTION_ROW_W - 65, 14, 1, 0);

      // Right Chevron
      drawChevron(QUESTION_ROW_X + QUESTION_ROW_W - 16, y + 18, 14, COLOR_CYAN_ACCENT);
    }

    // Scrollbar if > 1 page
    if (pageCount > 1) {
      tft.fillRoundRect(SCROLLBAR_X, SCROLLBAR_Y, SCROLLBAR_W, SCROLLBAR_H, 6, COLOR_CARD_BG);
      tft.drawRoundRect(SCROLLBAR_X, SCROLLBAR_Y, SCROLLBAR_W, SCROLLBAR_H, 6, COLOR_CARD_BORDER);
      int thumbH = max(24, SCROLLBAR_H / pageCount);
      int maxThumbY = SCROLLBAR_H - thumbH;
      int thumbY = SCROLLBAR_Y + (questionPage * maxThumbY) / (pageCount - 1);
      tft.fillRoundRect(SCROLLBAR_X + 2, thumbY + 2, SCROLLBAR_W - 4, thumbH - 4, 4, COLOR_CYAN_ACCENT);

      // Nav buttons
      tft.fillRoundRect(8, QUESTION_NAV_Y, 74, 24, 6, COLOR_CARD_BG);
      tft.drawRoundRect(8, QUESTION_NAV_Y, 74, 24, 6, COLOR_CARD_BORDER);
      tft.setTextColor(questionPage > 0 ? COLOR_TEXT_WHITE : COLOR_TEXT_DIM, COLOR_CARD_BG);
      tft.drawCenterString("◀ PREV", 45, QUESTION_NAV_Y + 5);

      tft.fillRoundRect(212, QUESTION_NAV_Y, 74, 24, 6, COLOR_CARD_BG);
      tft.drawRoundRect(212, QUESTION_NAV_Y, 74, 24, 6, COLOR_CARD_BORDER);
      tft.setTextColor(questionPage + 1 < pageCount ? COLOR_TEXT_WHITE : COLOR_TEXT_DIM, COLOR_CARD_BG);
      tft.drawCenterString("NEXT ▶", 249, QUESTION_NAV_Y + 5);

      char pageIndicator[20];
      snprintf(pageIndicator, sizeof(pageIndicator), "第 %d / %d 页", questionPage + 1, pageCount);
      tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
      tft.drawCenterString(pageIndicator, 147, QUESTION_NAV_Y + 5);
      if (questionMultiSelect) {
        tft.fillRoundRect(92, QUESTION_NAV_Y, 110, 24, 6, questionSelectedMask ? COLOR_CYAN_BG : COLOR_CARD_BG);
        tft.drawRoundRect(92, QUESTION_NAV_Y, 110, 24, 6, COLOR_CYAN_ACCENT);
        tft.setTextColor(questionSelectedMask ? COLOR_CYAN_HI : COLOR_TEXT_DIM, questionSelectedMask ? COLOR_CYAN_BG : COLOR_CARD_BG);
        tft.drawCenterString("SUBMIT", 147, QUESTION_NAV_Y + 5);
      }
    } else {
      tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
      tft.drawCenterString(questionMultiSelect ? "选择后点 SUBMIT 提交" : "轻触选项卡片直接作答回传", 160, QUESTION_NAV_Y + 5);
      if (questionMultiSelect) {
        tft.fillRoundRect(103, QUESTION_NAV_Y, 114, 24, 6, questionSelectedMask ? COLOR_CYAN_BG : COLOR_CARD_BG);
        tft.drawRoundRect(103, QUESTION_NAV_Y, 114, 24, 6, COLOR_CYAN_ACCENT);
        tft.setTextColor(questionSelectedMask ? COLOR_CYAN_HI : COLOR_TEXT_DIM, questionSelectedMask ? COLOR_CYAN_BG : COLOR_CARD_BG);
        tft.drawCenterString("SUBMIT", 160, QUESTION_NAV_Y + 5);
      }
    }
  }
  else if (currentState == STATE_ANSWERED_ALLOW) {
    setLED(false, true, false); // Green

    int cx = 8, cy = 28, cw = 304, ch = 198;
    tft.fillRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BG);
    tft.drawRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BORDER);

    int ecx = 160, ecy = 76;
    tft.drawCircle(ecx, ecy, 36, 0x11E4);
    tft.drawCircle(ecx, ecy, 26, COLOR_GREEN_BTN);
    tft.fillCircle(ecx, ecy, 18, COLOR_GREEN_BTN);
    for (int d = -1; d <= 1; d++) {
      tft.drawLine(ecx - 8, ecy + d, ecx - 2, ecy + 6 + d, COLOR_GREEN_HI);
      tft.drawLine(ecx - 2, ecy + 6 + d, ecx + 8, ecy - 6 + d, COLOR_GREEN_HI);
    }

    tft.setTextColor(COLOR_GREEN_HI, COLOR_CARD_BG);
    tft.drawCenterString("授权已放行 · APPROVED", 160, 124);

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
    tft.drawCenterString("允许指令已成功回传终端", 160, 148);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    tft.drawCenterString("Claude Code 终端已恢复执行", 160, 172);
  }
  else if (currentState == STATE_ANSWERED_DENY) {
    setLED(true, false, false); // Red

    int cx = 8, cy = 28, cw = 304, ch = 198;
    tft.fillRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BG);
    tft.drawRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BORDER);

    int ecx = 160, ecy = 76;
    tft.drawCircle(ecx, ecy, 36, 0x4081);
    tft.drawCircle(ecx, ecy, 26, COLOR_RED_BTN);
    tft.fillCircle(ecx, ecy, 18, COLOR_RED_BTN);
    for (int d = -1; d <= 1; d++) {
      tft.drawLine(ecx - 7, ecy - 7 + d, ecx + 7, ecy + 7 + d, COLOR_RED_HI);
      tft.drawLine(ecx - 7, ecy + 7 + d, ecx + 7, ecy - 7 + d, COLOR_RED_HI);
    }

    tft.setTextColor(COLOR_RED_HI, COLOR_CARD_BG);
    tft.drawCenterString("操作已安全拦截 · STOPPED", 160, 124);

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
    tft.drawCenterString("拒绝指令已回传终端", 160, 148);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    tft.drawCenterString("高危调用已中止，模型重新规划中", 160, 172);
  }
  else if (currentState == STATE_ANSWERED_QUESTION) {
    setLED(false, false, true); // Cyan / Blue

    int cx = 8, cy = 28, cw = 304, ch = 198;
    tft.fillRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BG);
    tft.drawRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BORDER);

    int ecx = 160, ecy = 76;
    tft.drawCircle(ecx, ecy, 36, 0x09CB);
    tft.drawCircle(ecx, ecy, 26, COLOR_CYAN_BG);
    tft.fillCircle(ecx, ecy, 18, COLOR_CYAN_BG);
    drawSpark(ecx, ecy, 9, COLOR_CYAN_HI);

    tft.setTextColor(COLOR_CYAN_HI, COLOR_CARD_BG);
    tft.drawCenterString("已选择并回传 · SUBMITTED", 160, 124);

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
    tft.drawCenterString("选择已确认，Claude 终端继续运行", 160, 148);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    String chosenLine = "已选: " + selectedQuestionLabel;
    tft.drawCenterString(chosenLine.c_str(), 160, 172);
  }
  else if (currentState == STATE_EXPIRED) {
    setLED(true, false, false); // Red

    int cx = 8, cy = 28, cw = 304, ch = 198;
    tft.fillRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BG);
    tft.drawRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BORDER);

    int ecx = 160, ecy = 76;
    tft.drawCircle(ecx, ecy, 36, 0x4081);
    tft.drawCircle(ecx, ecy, 26, COLOR_RED_BTN);
    tft.fillCircle(ecx, ecy, 18, COLOR_RED_BTN);
    for (int d = -1; d <= 1; d++) {
      tft.drawLine(ecx - 7, ecy - 7 + d, ecx + 7, ecy + 7 + d, COLOR_RED_HI);
      tft.drawLine(ecx - 7, ecy + 7 + d, ecx + 7, ecy - 7 + d, COLOR_RED_HI);
    }

    char expTitle[48];
    snprintf(expTitle, sizeof(expTitle), "请求已超时 · %ds TIMEOUT", activeTimeoutSec);
    tft.setTextColor(COLOR_RED_HI, COLOR_CARD_BG);
    tft.drawCenterString(expTitle, 160, 124);

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
    tft.drawCenterString("自动触发 Fail-Safe 熔断保护", 160, 148);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    tft.drawCenterString("终端已安全拦截，代码库受控未受损", 160, 172);
  }
}

bool sendResponseToMac(int index) {
  if (!pTxCharacteristic || !deviceConnected) {
    return false;
  }

  const char* token = (index == 0) ? "ALLOW" : "DENY";
  char response[64];
  if (pendingRequestId[0] != '\0') {
    snprintf(response, sizeof(response), "%s:%s", token, pendingRequestId);
  } else {
    snprintf(response, sizeof(response), "%s", token);
  }

  pTxCharacteristic->setValue((const uint8_t*)response, strlen(response));
  pTxCharacteristic->notify();
  Serial.printf("[BLE] Sent response: %s\n", response);

  pendingRequestId[0] = '\0';
  return true;
}

bool sendSelectionToMac(int optionIndex) {
  if (!pTxCharacteristic || !deviceConnected) {
    return false;
  }

  char response[80];
  snprintf(response, sizeof(response), "SELECT:%s:%d", pendingRequestId, optionIndex);
  pTxCharacteristic->setValue((const uint8_t*)response, strlen(response));
  pTxCharacteristic->notify();
  Serial.printf("[BLE] Sent selection: %s\n", response);

  pendingRequestId[0] = '\0';
  return true;
}

bool sendMultiSelectionToMac(uint8_t selectedMask) {
  if (!pTxCharacteristic || strlen(pendingRequestId) == 0 || selectedMask == 0) return false;
  String indexes = "";
  for (uint8_t index = 0; index < questionOptionCount; ++index) {
    if (selectedMask & (1 << index)) {
      if (indexes.length()) indexes += ",";
      indexes += String(index);
    }
  }
  String response = "SELECTM:" + String(pendingRequestId) + ":" + indexes;
  pTxCharacteristic->setValue(response.c_str());
  pTxCharacteristic->notify();
  return true;
}

// ==========================================
// 4. BLE Callbacks & UTF-8 Safe Frame Parser
// ==========================================
class ServerCallbacks: public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* server) override {
    deviceConnected = false;
  }

  void onConnect(NimBLEServer* server, ble_gap_conn_desc* desc) override {
    deviceConnected = false;
    peerConnHandle = desc ? desc->conn_handle : BLE_HS_CONN_HANDLE_NONE;
  }

  uint32_t onPassKeyRequest() override {
    pairingPasskey = 100000UL + (esp_random() % 900000UL);
    return pairingPasskey;
  }

  void onAuthenticationComplete(ble_gap_conn_desc* desc) override {
    pairingPasskey = 0;
    if (desc == nullptr || !desc->sec_state.encrypted || !desc->sec_state.authenticated) {
      if (desc != nullptr) pServer->disconnect(desc->conn_handle);
      return;
    }

    NimBLEAddress peer(desc->peer_id_addr);
    String peerId = peer.toString().c_str();
    String trustedId = pairingPrefs.getString("trustedPeer", "");
    if (trustedId.length() > 0 && trustedId != peerId) {
      NimBLEDevice::deleteBond(peer);
      pServer->disconnect(desc->conn_handle);
      return;
    }
    if (trustedId.length() == 0 && pairingPrefs.putString("trustedPeer", peerId) == 0) {
      pServer->disconnect(desc->conn_handle);
      return;
    }
    deviceConnected = true;
    DeckMessage* msg = allocateDeckMessage();
    if (msg != nullptr && uiQueue != nullptr) {
      msg->type = DECK_MSG_STATUS;
      strlcpy(msg->status, "connected", sizeof(msg->status));
      if (xQueueSend(uiQueue, &msg, 0) != pdTRUE) {
        vPortFree(msg);
      }
    } else if (msg != nullptr) {
      vPortFree(msg);
    }
  }

  void onDisconnect(NimBLEServer* server) override {
    deviceConnected = false;
    pairingPasskey = 0;
    peerConnHandle = BLE_HS_CONN_HANDLE_NONE;
    NimBLEDevice::startAdvertising();

    DeckMessage* msg = allocateDeckMessage();
    if (msg != nullptr && uiQueue != nullptr) {
      msg->type = DECK_MSG_STATUS;
      strlcpy(msg->status, "idle", sizeof(msg->status));
      if (xQueueSend(uiQueue, &msg, 0) != pdTRUE) {
        vPortFree(msg);
      }
    } else if (msg != nullptr) {
      vPortFree(msg);
    }
  }
};

// Copies a UTF-8 string into `dest` without splitting multi-byte codepoints at the boundary.
void copyJsonString(JsonVariant source, char* dest, size_t destSize) {
  if (destSize == 0) return;
  const char* src = source.as<const char*>();
  if (src == nullptr) {
    dest[0] = '\0';
    return;
  }

  size_t srcLen = strlen(src);
  if (srcLen < destSize) {
    memcpy(dest, src, srcLen + 1);
    return;
  }

  // Need to truncate safely on a UTF-8 codepoint boundary, leaving room for "..."
  size_t reserveEllipsis = (destSize >= 8) ? 3 : 0;
  size_t limit = destSize - 1 - reserveEllipsis;
  size_t pos = 0;

  while (pos < limit) {
    unsigned char c = (unsigned char)src[pos];
    size_t charLen = 1;
    if ((c & 0x80) == 0x00) charLen = 1;
    else if ((c & 0xE0) == 0xC0) charLen = 2;
    else if ((c & 0xF0) == 0xE0) charLen = 3;
    else if ((c & 0xF8) == 0xF0) charLen = 4;

    if (pos + charLen > limit) {
      break;
    }
    pos += charLen;
  }

  memcpy(dest, src, pos);
  if (reserveEllipsis == 3) {
    memcpy(dest + pos, "...", 4);
  } else {
    dest[pos] = '\0';
  }
}

// Finds the byte length of the first complete top-level JSON object `{...}` in `buf`.
// Returns 0 if incomplete.
size_t findCompleteJsonFrame(const std::vector<uint8_t>& buf, size_t& startIdx) {
  startIdx = 0;
  while (startIdx < buf.size() && buf[startIdx] != '{') {
    startIdx++;
  }
  if (startIdx >= buf.size()) {
    return 0;
  }

  int depth = 0;
  bool inString = false;
  bool escaped = false;

  for (size_t i = startIdx; i < buf.size(); ++i) {
    uint8_t ch = buf[i];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (ch == '\\') {
        escaped = true;
      } else if (ch == '"') {
        inString = false;
      }
    } else {
      if (ch == '"') {
        inString = true;
      } else if (ch == '{') {
        depth++;
      } else if (ch == '}') {
        depth--;
        if (depth == 0) {
          return (i - startIdx) + 1;
        }
      }
    }
  }
  return 0;
}

#define RX_BUFFER_MAX 4096
#define RX_CHUNK_GAP_MS 3000
static std::vector<uint8_t> rxBuffer;
static unsigned long lastRxChunkMillis = 0;

class RxCallbacks: public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* pCharacteristic) override {
    std::string rxValue = pCharacteristic->getValue();
    Serial.printf("[BLE] Received %d bytes\n", rxValue.length());

    if (rxValue.length() == 0 || uiQueue == nullptr) {
      return;
    }

    unsigned long now = millis();
    if (!rxBuffer.empty() && (now - lastRxChunkMillis > RX_CHUNK_GAP_MS)) {
      Serial.printf("[BLE RX] stale partial frame (%d bytes) discarded\n", (int)rxBuffer.size());
      rxBuffer.clear();
    }
    lastRxChunkMillis = now;

    rxBuffer.insert(rxBuffer.end(), rxValue.begin(), rxValue.end());
    if (rxBuffer.size() > RX_BUFFER_MAX) {
      Serial.printf("[BLE RX] buffer overflow (%d bytes), dropping\n", (int)rxBuffer.size());
      rxBuffer.clear();
      return;
    }

    // Consume all complete JSON frames in rxBuffer (prevents losing back-to-back packets)
    while (!rxBuffer.empty()) {
      size_t startIdx = 0;
      size_t frameLen = findCompleteJsonFrame(rxBuffer, startIdx);
      if (startIdx > 0) {
        rxBuffer.erase(rxBuffer.begin(), rxBuffer.begin() + startIdx);
      }
      if (frameLen == 0) {
        // Incomplete JSON object; wait for remaining chunks
        Serial.printf("[BLE RX] waiting for remaining chunks (%d bytes buffered)\n", (int)rxBuffer.size());
        return;
      }

      JsonDocument doc;
      DeserializationError error = deserializeJson(doc, rxBuffer.data(), frameLen);
      rxBuffer.erase(rxBuffer.begin(), rxBuffer.begin() + frameLen);

      if (error) {
        Serial.printf("[BLE JSON ERROR] %s (dropped frame of %d bytes)\n", error.c_str(), (int)frameLen);
        continue;
      }

      DeckMessage* pMsg = allocateDeckMessage();
      if (pMsg == nullptr) continue;
      DeckMessage& msg = *pMsg;

      msg.timeoutSec = (uint16_t)(doc["timeout"] | 120);
      if (msg.timeoutSec < 5) msg.timeoutSec = 120;

      String type = doc["type"] | "prompt";
      if (type == "prompt") {
        msg.type = DECK_MSG_PROMPT;
        copyJsonString(doc["id"], msg.id, sizeof(msg.id));
        copyJsonString(doc["badge"], msg.badge, sizeof(msg.badge));
        copyJsonString(doc["tag"], msg.tag, sizeof(msg.tag));
        copyJsonString(doc["title"], msg.title, sizeof(msg.title));
        copyJsonString(doc["desc"], msg.desc, sizeof(msg.desc));

        JsonArray options = doc["options"];
        if (options.size() >= 2) {
          copyJsonString(options[0], msg.option1, sizeof(msg.option1));
          copyJsonString(options[1], msg.option2, sizeof(msg.option2));
        } else {
          strlcpy(msg.option1, "允许 (Y)", sizeof(msg.option1));
          strlcpy(msg.option2, "拒绝 (N)", sizeof(msg.option2));
        }
      } else if (type == "question") {
        msg.type = DECK_MSG_QUESTION;
        copyJsonString(doc["id"], msg.id, sizeof(msg.id));
        copyJsonString(doc["header"], msg.header, sizeof(msg.header));
        copyJsonString(doc["question"], msg.question, sizeof(msg.question));

        JsonArray options = doc["options"];
        msg.optionCount = (uint8_t)min((size_t)QUESTION_MAX_OPTIONS, options.size());
        msg.multiSelect = doc["multiSelect"] | false;
        for (uint8_t i = 0; i < msg.optionCount; ++i) {
          copyJsonString(options[i]["label"], msg.optionLabels[i], sizeof(msg.optionLabels[i]));
          copyJsonString(options[i]["description"], msg.optionDescriptions[i], sizeof(msg.optionDescriptions[i]));
        }
      } else if (type == "status") {
        msg.type = DECK_MSG_STATUS;
        copyJsonString(doc["status"], msg.status, sizeof(msg.status));
        copyJsonString(doc["badge"], msg.badge, sizeof(msg.badge));
        copyJsonString(doc["tag"], msg.tag, sizeof(msg.tag));
        copyJsonString(doc["title"], msg.title, sizeof(msg.title));
        copyJsonString(doc["desc"], msg.desc, sizeof(msg.desc));
      } else {
        vPortFree(pMsg);
        continue;
      }

      if (uiQueue == nullptr || xQueueSend(uiQueue, &pMsg, 0) != pdTRUE) {
        vPortFree(pMsg);
      }
    }
  }
};

void handleQueuedMessage(DeckMessage& msg) {
  wakeScreen();

  if (msg.type == DECK_MSG_PROMPT) {
    strlcpy(pendingRequestId, msg.id, sizeof(pendingRequestId));
    promptTitle = msg.title;
    promptDesc = msg.desc;
    promptBadge = msg.badge;
    promptTag = msg.tag;
    inferBadgeAndTag(promptTitle, promptBadge, promptTag);
    option1Text = msg.option1;
    option2Text = msg.option2;
    activeTimeoutSec = msg.timeoutSec > 0 ? msg.timeoutSec : 120;
    promptScrollLine = 0;
    currentState = STATE_PROMPT;
    stateEnteredMillis = millis();
    lastRenderedRemainSec = -1;
    lastTouchTime = millis();
    playEarconPrompt();
    renderUI();
    return;
  }

  if (msg.type == DECK_MSG_QUESTION) {
    strlcpy(pendingRequestId, msg.id, sizeof(pendingRequestId));
    questionHeaderText = msg.header;
    if (questionHeaderText.length() == 0) {
      questionHeaderText = "QUESTION";
    }
    questionText = msg.question;
    questionOptionCount = msg.optionCount;
    questionPage = 0;
    questionMultiSelect = msg.multiSelect;
    questionSelectedMask = 0;
    activeTimeoutSec = msg.timeoutSec > 0 ? msg.timeoutSec : 120;

    for (uint8_t i = 0; i < QUESTION_MAX_OPTIONS; ++i) {
      if (i < questionOptionCount) {
        questionOptionLabels[i] = msg.optionLabels[i];
        questionOptionDescriptions[i] = msg.optionDescriptions[i];
      } else {
        questionOptionLabels[i] = "";
        questionOptionDescriptions[i] = "";
      }
    }

    currentState = STATE_QUESTION;
    stateEnteredMillis = millis();
    lastRenderedRemainSec = -1;
    lastTouchTime = millis();
    playEarconQuestion();
    renderUI();
    return;
  }

  if (msg.type == DECK_MSG_STATUS) {
    String status = msg.status;
    if (strlen(msg.title) > 0) promptTitle = msg.title;
    if (strlen(msg.desc) > 0) promptDesc = msg.desc;
    if (strlen(msg.badge) > 0) promptBadge = msg.badge;
    if (strlen(msg.tag) > 0) promptTag = msg.tag;

    stateEnteredMillis = millis();
    if (status == "expired") {
      currentState = STATE_EXPIRED;
      playEarconDeny();
    } else if (status == "thinking") {
      currentState = STATE_THINKING;
    } else {
      currentState = STATE_IDLE;
    }

    renderUI();
  }
}

// ==========================================
// 5. Setup & Loop
// ==========================================
void setup() {
  Serial.begin(115200);

  uiQueue = xQueueCreate(8, sizeof(DeckMessage*));

  // Initialize LEDs
  pinMode(CYD_LED_R, OUTPUT);
  pinMode(CYD_LED_G, OUTPUT);
  pinMode(CYD_LED_B, OUTPUT);
  setLED(false, false, false);

  // Initialize Speaker PWM channel
  pinMode(CYD_SPEAKER, OUTPUT);
  digitalWrite(CYD_SPEAKER, LOW);
  ledcSetup(LEDC_CH_SPEAKER, 2000, 8);
  ledcAttachPin(CYD_SPEAKER, LEDC_CH_SPEAKER);

  // Initialize Display
  tft.init();
  tft.setRotation(1); // Landscape 320x240

  // Set Full GB2312 Chinese Font (6,763 characters, 0-byte RAM overhead, runs from Flash)
  tft.setFont(&fonts::efontCN_14_b);
  tft.setTextSize(1);
  Serial.println("CJK font loaded: efontCN_14_b (Full GB2312 Bold)");

  // Initialize Backlight with PWM dimming support
  ledcSetup(LEDC_CH_BACKLIGHT, 5000, 8);
  ledcAttachPin(CYD_BL_PIN1, LEDC_CH_BACKLIGHT);
  ledcAttachPin(CYD_BL_PIN2, LEDC_CH_BACKLIGHT);
  setBacklight(255);
  lastActivityMillis = millis();

  // Initialize Touch Screen with dedicated SPI
  touchSPI.begin(XPT2046_CLK, XPT2046_MISO, XPT2046_MOSI, XPT2046_CS);
  ts.begin(touchSPI);
  ts.setRotation(1);

  // Initial UI
  renderUI();
  playEarconTap();

  // Initialize NimBLE with large MTU
  pairingPrefs.begin("claude-deck", false);
  NimBLEDevice::init("Claude-Deck");
  NimBLEDevice::setMTU(517);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9); // Max TX power
  NimBLEDevice::setSecurityAuth(true, true, true);
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_DISPLAY_ONLY);

  pServer = NimBLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  NimBLEService* pService = pServer->createService(SERVICE_UUID);

  NimBLECharacteristic* pRxCharacteristic = pService->createCharacteristic(
    CHAR_UUID_RX,
    NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_ENC | NIMBLE_PROPERTY::WRITE_AUTHEN
  );
  pRxCharacteristic->setCallbacks(new RxCallbacks());

  pTxCharacteristic = pService->createCharacteristic(
    CHAR_UUID_TX,
    NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY |
    NIMBLE_PROPERTY::READ_ENC | NIMBLE_PROPERTY::READ_AUTHEN
  );

  pService->start();

  NimBLEAdvertising* pAdvertising = NimBLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  pAdvertising->start();

  Serial.println("Claude Deck Initialized & Ready!");
}

void loop() {
  DeckMessage* pMsg = nullptr;
  while (uiQueue != nullptr && xQueueReceive(uiQueue, &pMsg, 0) == pdTRUE) {
    if (pMsg != nullptr) {
      handleQueuedMessage(*pMsg);
      vPortFree(pMsg);
    }
  }

  unsigned long now = millis();
  if (pairingPasskey != 0 && currentState != STATE_PAIRING) {
    currentState = STATE_PAIRING;
    renderUI();
  }

  // 1. Auto-return to STATE_IDLE after displaying decision or timeout result
  if ((currentState == STATE_ANSWERED_ALLOW ||
       currentState == STATE_ANSWERED_DENY ||
       currentState == STATE_ANSWERED_QUESTION) &&
      (now - stateEnteredMillis >= ANSWERED_RETURN_IDLE_MS)) {
    currentState = STATE_IDLE;
    stateEnteredMillis = now;
    renderUI();
  } else if (currentState == STATE_EXPIRED &&
             (now - stateEnteredMillis >= EXPIRED_RETURN_IDLE_MS)) {
    currentState = STATE_IDLE;
    stateEnteredMillis = now;
    renderUI();
  }

  // 2. Local countdown & fail-safe timeout watchdog for STATE_PROMPT / STATE_QUESTION
  if (currentState == STATE_PROMPT || currentState == STATE_QUESTION) {
    int remain = getRemainingSeconds();
    if (remain <= 0) {
      pendingRequestId[0] = '\0';
      currentState = STATE_EXPIRED;
      stateEnteredMillis = now;
      playEarconDeny();
      renderUI();
    } else if (remain != lastRenderedRemainSec) {
      if (currentState == STATE_PROMPT) {
        drawPromptFooterCountdown();
      } else {
        drawQuestionCountdown();
      }
    }
  }

  // 3. Auto-dim backlight in STATE_IDLE after 60s of inactivity
  if (currentState == STATE_IDLE && !isScreenDimmed &&
      (now - lastActivityMillis >= IDLE_DIM_TIMEOUT_MS)) {
    isScreenDimmed = true;
    setBacklight(35); // ~14% soft night-friendly brightness
    setLED(false, false, false);
  }

  // 4. Touch controller with pressure filter & release lock
  if (ts.touched()) {
    TS_Point p = ts.getPoint();
    if (p.z < TOUCH_MIN_PRESSURE) {
      goto endTouch;
    }

    // Require finger release between distinct taps to prevent auto-repeat
    if (touchHeld || (now - lastReleaseTime < 60) || (now - lastTouchTime < 220)) {
      touchHeld = true;
      goto endTouch;
    }
    touchHeld = true;
    lastTouchTime = now;

    // If screen was dimmed, wake it up on first tap without firing buttons
    if (isScreenDimmed) {
      wakeScreen();
      setLED(false, true, false);
      playEarconTap();
      goto endTouch;
    }
    wakeScreen();

    // Map raw touch (X: 3800~200 inverted, Y: 3800~240 inverted) to screen.
    int screenX = map(p.x, 3800, 200, 0, 320);
    int screenY = map(p.y, 3800, 240, 0, 240);
    screenX = constrain(screenX, 0, 319);
    screenY = constrain(screenY, 0, 239);

    Serial.printf("[TOUCH] Raw: (%d, %d, z=%d) -> Screen: (%d, %d)\n", p.x, p.y, p.z, screenX, screenY);

    // Global Top-Bar Mute Pill Button (X: 204..260, Y: 0..24) — Clickable in ALL states!
    if (screenX >= 204 && screenX <= 260 && screenY <= 24) {
      isMuted = !isMuted;
      if (!isMuted) playEarconTap();
      drawStatusBar();
      if (currentState == STATE_IDLE) renderUI();
      goto endTouch;
    }

    // IDLE State: Tap middle dock card (AUDIO TAP) to toggle Mute
    if (currentState == STATE_IDLE) {
      if (screenX >= 112 && screenX <= 208 && screenY >= 168 && screenY <= 232) {
        isMuted = !isMuted;
        if (!isMuted) playEarconTap();
        renderUI();
      }
      goto endTouch;
    }

    if (currentState == STATE_QUESTION) {
      uint8_t pageCount = (questionOptionCount + QUESTION_PER_PAGE - 1) / QUESTION_PER_PAGE;
      uint8_t start = questionPage * QUESTION_PER_PAGE;

      // 1. 点击滚动条轨道直接翻页
      if (pageCount > 1 && screenX >= (SCROLLBAR_X - 6) && screenX <= 318 &&
          screenY >= SCROLLBAR_Y && screenY <= (SCROLLBAR_Y + SCROLLBAR_H)) {
        int midY = SCROLLBAR_Y + SCROLLBAR_H / 2;
        if (screenY < midY && questionPage > 0) {
          questionPage--;
          playEarconTap();
          renderUI();
        } else if (screenY >= midY && questionPage + 1 < pageCount) {
          questionPage++;
          playEarconTap();
          renderUI();
        }
        goto endTouch;
      }

      // 2. 点击底部导航翻页按键
      if (pageCount > 1 && screenY >= QUESTION_NAV_Y && screenY <= 238) {
        if (screenX >= 8 && screenX <= 82 && questionPage > 0) {
          questionPage--;
          playEarconTap();
          renderUI();
        } else if (screenX >= 212 && screenX <= 286 && questionPage + 1 < pageCount) {
          questionPage++;
          playEarconTap();
          renderUI();
        }
        if (questionMultiSelect && screenX >= 92 && screenX <= 202 && questionSelectedMask) {
          if (sendMultiSelectionToMac(questionSelectedMask)) {
            selectedQuestionLabel = "已提交多个选择";
            currentState = STATE_ANSWERED_QUESTION;
            stateEnteredMillis = millis();
            renderUI();
          }
        }
        goto endTouch;
      }

      if (questionMultiSelect && pageCount == 1 && screenY >= QUESTION_NAV_Y && screenY <= 238 && screenX >= 103 && screenX <= 217 && questionSelectedMask) {
        if (sendMultiSelectionToMac(questionSelectedMask)) {
          selectedQuestionLabel = "已提交多个选择";
          currentState = STATE_ANSWERED_QUESTION;
          stateEnteredMillis = millis();
          renderUI();
        }
        goto endTouch;
      }

      int selectedOption = -1;
      if (screenX >= QUESTION_ROW_X && screenX <= QUESTION_ROW_X + QUESTION_ROW_W) {
        if (screenY >= QUESTION_ROW1_Y && screenY <= QUESTION_ROW1_Y + QUESTION_ROW_H) {
          selectedOption = start;
        } else if (screenY >= QUESTION_ROW2_Y && screenY <= QUESTION_ROW2_Y + QUESTION_ROW_H) {
          selectedOption = start + 1;
        }
      }

      if (selectedOption >= 0 && selectedOption < questionOptionCount) {
        if (questionMultiSelect) {
          questionSelectedMask ^= (1 << selectedOption);
          playEarconTap();
          renderUI();
          goto endTouch;
        }
        int row = selectedOption - start;
        int y = (row == 0) ? QUESTION_ROW1_Y : QUESTION_ROW2_Y;
        tft.fillRoundRect(QUESTION_ROW_X, y, QUESTION_ROW_W, QUESTION_ROW_H, 8, TFT_WHITE);
        tft.setTextColor(TFT_BLACK, TFT_WHITE);
        tft.drawString(questionOptionLabels[selectedOption].c_str(), QUESTION_ROW_X + 14, y + 16);
        playEarconAllow();

        if (sendSelectionToMac(selectedOption)) {
          selectedQuestionLabel = questionOptionLabels[selectedOption];
          currentState = STATE_ANSWERED_QUESTION;
          stateEnteredMillis = millis();
          renderUI();
        } else {
          setLED(true, false, false);
          renderUI();
        }
      }

      goto endTouch;
    }

    if (currentState == STATE_PROMPT) {
      // Tap inside codebox (Y: 54..126) to scroll long commands!
      if (promptTotalLines > 3 && screenX >= 16 && screenX <= 304 &&
          screenY >= 54 && screenY <= 128) {
        promptScrollLine += 3;
        if (promptScrollLine >= promptTotalLines) {
          promptScrollLine = 0;
        }
        playEarconTap();
        drawPromptCodeBox();
        goto endTouch;
      }

      // Draw visual touch point
      tft.fillCircle(screenX, screenY, 3, TFT_YELLOW);

      bool allowed = false;
      bool matched = false;

      // Check Button 1 (Allow / Left)
      if (screenX >= (BTN1_X - 6) && screenX <= (BTN1_X + BTN1_W) &&
          screenY >= (BTN_Y - 10) && screenY <= 238) {
        allowed = true;
        matched = true;
      }
      // Check Button 2 (Reject / Right)
      else if (screenX >= (BTN2_X) && screenX <= (BTN2_X + BTN2_W + 6) &&
               screenY >= (BTN_Y - 10) && screenY <= 238) {
        allowed = false;
        matched = true;
      }

      if (matched) {
        if (allowed) {
          drawTactileButton(BTN1_X, BTN_Y, BTN1_W, BTN_H, true, option1Text, "[ CONFIRM ]", true);
          playEarconAllow();
        } else {
          drawTactileButton(BTN2_X, BTN_Y, BTN2_W, BTN_H, false, option2Text, "[ ABORT ]", true);
          playEarconDeny();
        }

        if (sendResponseToMac(allowed ? 0 : 1)) {
          currentState = allowed ? STATE_ANSWERED_ALLOW : STATE_ANSWERED_DENY;
          stateEnteredMillis = millis();
        } else {
          setLED(true, false, false); // Red on send error, stay in PROMPT to allow retry
        }
        renderUI();
      }
    }
  } else {
    if (touchHeld) {
      touchHeld = false;
      lastReleaseTime = now;
    }
  }

endTouch:;
  delay(20);
}
