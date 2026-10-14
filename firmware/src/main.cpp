#define LGFX_AUTODETECT
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <SPI.h>
#include <XPT2046_Touchscreen.h>
#include <NimBLEDevice.h>
#include <ArduinoJson.h>
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

// Touch Pins
#define XPT2046_IRQ  36
#define XPT2046_MOSI 32
#define XPT2046_MISO 39
#define XPT2046_CLK  25
#define XPT2046_CS   33

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

// ==========================================
// 3. UI State Machine
// ==========================================
enum UIState {
  STATE_IDLE,
  STATE_THINKING,
  STATE_PROMPT,
  STATE_QUESTION,
  STATE_ANSWERED,
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
  char title[64];
  char desc[160];
  char option1[32];
  char option2[32];
  char status[24];
  char header[24];
  char question[160];
  char optionLabels[4][28];
  char optionDescriptions[4][52];
  uint8_t optionCount;
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
static uint8_t questionOptionCount = 0;
static uint8_t questionPage = 0;
static unsigned long lastTouchTime = 0;

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

// Window Dots
#define COLOR_DOT_RED       0xFAD5 // #FF5F56
#define COLOR_DOT_YELLOW    0xFDE5 // #FFBD2E
#define COLOR_DOT_GREEN     0x2647 // #27C93F

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

void setLED(bool r, bool g, bool b) {
  // Active LOW on CYD
  digitalWrite(CYD_LED_R, r ? LOW : HIGH);
  digitalWrite(CYD_LED_G, g ? LOW : HIGH);
  digitalWrite(CYD_LED_B, b ? LOW : HIGH);
}

void beep(int ms = 40) {
  pinMode(CYD_SPEAKER, OUTPUT);
  for (int i = 0; i < ms * 2; i++) {
    digitalWrite(CYD_SPEAKER, HIGH);
    delayMicroseconds(250);
    digitalWrite(CYD_SPEAKER, LOW);
    delayMicroseconds(250);
  }
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
  tft.drawString("CLAUDE DECK", 23, 6);

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
  } else if (currentState == STATE_ANSWERED) {
    modeText = "[ APPROVED ]";
    modeColor = COLOR_GREEN_HI;
  } else if (currentState == STATE_EXPIRED) {
    modeText = "[ TIMEOUT ]";
    modeColor = COLOR_RED_HI;
  }

  int pillW = 88;
  int pillX = 160 - pillW / 2;
  tft.fillRoundRect(pillX, 3, pillW, 18, 4, COLOR_CARD_BG);
  tft.drawRoundRect(pillX, 3, pillW, 18, 4, COLOR_CARD_BORDER);
  tft.setTextColor(modeColor, COLOR_CARD_BG);
  tft.drawCenterString(modeText, 160, 5);

  // Right Telemetry: Signal Bars & Battery
  int sigX = 264;
  for (int i = 0; i < 4; i++) {
    int h = 3 + i * 2;
    uint16_t c = (deviceConnected) ? COLOR_GREEN_HI : COLOR_CARD_BORDER;
    tft.fillRect(sigX + i * 4, 16 - h, 2, h, c);
  }

  // Battery icon
  int batX = 284;
  tft.drawRect(batX, 8, 16, 9, COLOR_TEXT_MUTED);
  tft.fillRect(batX + 16, 10, 2, 5, COLOR_TEXT_MUTED);
  tft.fillRect(batX + 2, 10, 12, 5, deviceConnected ? COLOR_GREEN_HI : COLOR_AMBER_GLOW);
  tft.setTextColor(deviceConnected ? COLOR_GREEN_HI : COLOR_AMBER_GLOW, COLOR_HEADER_BG);
  tft.drawString("5V", batX + 20, 6);
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

// Draw a UTF-8 aware wrapped string. This intentionally works on the displayed
// pixel width instead of byte count, so Chinese text is not split mid-codepoint.
void drawWrappedText(const String& text, int x, int y, int maxWidth, int lineHeight, int maxLines) {
  unsigned int pos = 0;
  int lines = 0;

  while (pos < text.length() && lines < maxLines) {
    unsigned int lineStart = pos;

    while (pos < text.length()) {
      int charLen = 1;

      // Determine UTF-8 sequence length.
      unsigned char c = text[pos];
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

    String line = text.substring(lineStart, pos);
    tft.drawString(line.c_str(), x, y);
    y += lineHeight;
    lines++;
  }
}

void renderUI() {
  drawStatusBar();

  // Content background (Ultra-deep Obsidian)
  tft.fillRect(0, 25, 320, 215, COLOR_BG);

  if (currentState == STATE_IDLE) {
    setLED(false, true, false); // Green

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
    // 4 radar ticks
    tft.drawFastVLine(ecx, ecy - 34, 6, COLOR_GREEN_HI);
    tft.drawFastVLine(ecx, ecy + 28, 6, COLOR_GREEN_HI);
    tft.drawFastHLine(ecx - 34, ecy, 6, COLOR_GREEN_HI);
    tft.drawFastHLine(ecx + 28, ecy, 6, COLOR_GREEN_HI);

    // Inner glowing core
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

    // Card 2: Policy
    int mx2 = mx1 + cardW + 8;
    tft.fillRoundRect(mx2, dockY, cardW, dockH, 6, COLOR_CARD_BG);
    tft.drawRoundRect(mx2, dockY, cardW, dockH, 6, COLOR_CARD_BORDER);
    tft.setTextColor(COLOR_TEXT_DIM, COLOR_CARD_BG);
    tft.drawString("POLICY", mx2 + 10, dockY + 8);
    tft.fillRoundRect(mx2 + 8, dockY + 26, cardW - 16, 26, 4, 0x4101);
    tft.setTextColor(COLOR_AMBER_HI, 0x4101);
    tft.drawCenterString("FAIL-SAFE", mx2 + cardW / 2, dockY + 32);

    // Card 3: Timeout
    int mx3 = mx2 + cardW + 8;
    tft.fillRoundRect(mx3, dockY, cardW, dockH, 6, COLOR_CARD_BG);
    tft.drawRoundRect(mx3, dockY, cardW, dockH, 6, COLOR_CARD_BORDER);
    tft.setTextColor(COLOR_TEXT_DIM, COLOR_CARD_BG);
    tft.drawString("TIMEOUT", mx3 + 10, dockY + 8);
    tft.fillRoundRect(mx3 + 8, dockY + 26, cardW - 16, 26, 4, COLOR_CYAN_BG);
    tft.setTextColor(COLOR_CYAN_HI, COLOR_CYAN_BG);
    tft.drawCenterString("120s AUTO", mx3 + cardW / 2, dockY + 32);
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

    // 8 orbital points
    const float angles[8] = { 0, 45, 90, 135, 180, 225, 270, 315 };
    for (int i = 0; i < 8; i++) {
      float rad = angles[i] * 3.14159f / 180.0f;
      int ox = ecx + (int)(cos(rad) * 36);
      int oy = ecy + (int)(sin(rad) * 36);
      tft.fillCircle(ox, oy, 2, COLOR_CYAN_ACCENT);
    }

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
    tft.drawCenterString("Claude 正在深度思考中...", 160, 124);

    // Description Box
    int tbx = cx + 12, tby = 148, tbw = cw - 24, tbh = 44;
    tft.fillRoundRect(tbx, tby, tbw, tbh, 4, COLOR_CODE_BG);
    tft.drawRoundRect(tbx, tby, tbw, tbh, 4, 0x09CB);
    tft.fillRect(tbx, tby, 3, tbh, COLOR_CYAN_ACCENT);
    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CODE_BG);
    drawWrappedText(promptDesc, tbx + 8, tby + 6, tbw - 14, 16, 2);

    // Scanning bar
    tft.setTextColor(COLOR_CYAN_ACCENT, COLOR_CARD_BG);
    tft.drawString("■ ■ ■ ■ ■ □ □ □", cx + 12, 202);
    tft.setTextColor(COLOR_TEXT_DIM, COLOR_CARD_BG);
    tft.drawRightString("REASONING IN PROGRESS", cx + cw - 12, 202);
  }
  else if (currentState == STATE_PROMPT) {
    setLED(true, true, false); // Yellow / Amber

    // 1. Middle Card Container (8, 28, 304, 126, r=8)
    int cx = 8, cy = 28, cw = 304, ch = 126;
    tft.fillRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BG);
    tft.drawRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BORDER);

    // Window Header Bar with macOS dots
    tft.fillRoundRect(cx, cy, cw, 22, 8, COLOR_CARD_HEADER);
    tft.fillRect(cx, cy + 14, cw, 8, COLOR_CARD_HEADER);
    tft.drawFastHLine(cx, cy + 22, cw, COLOR_CARD_BORDER);

    tft.fillCircle(cx + 12, cy + 11, 3, COLOR_DOT_RED);
    tft.fillCircle(cx + 22, cy + 11, 3, COLOR_DOT_YELLOW);
    tft.fillCircle(cx + 32, cy + 11, 3, COLOR_DOT_GREEN);

    // Prompt Header title
    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_HEADER);
    tft.drawString(promptTitle.c_str(), cx + 42, cy + 5);

    // Category Tag Pill on top right
    int tagW = 76;
    int tagX = cx + cw - tagW - 4;
    tft.fillRoundRect(tagX, cy + 3, tagW, 16, 3, 0x4101);
    tft.drawRoundRect(tagX, cy + 3, tagW, 16, 3, COLOR_AMBER_GLOW);
    tft.setTextColor(COLOR_AMBER_HI, 0x4101);
    tft.drawCenterString(promptTag.c_str(), tagX + tagW / 2, cy + 4);

    // Inner Terminal Codebox (16, 54, 288, 68)
    int tbx = cx + 8, tby = cy + 28, tbw = cw - 16, tbh = 68;
    tft.fillRoundRect(tbx, tby, tbw, tbh, 4, COLOR_CODE_BG);
    tft.drawRoundRect(tbx, tby, tbw, tbh, 4, 0x1946);
    tft.fillRect(tbx, tby, 3, tbh, COLOR_AMBER_GLOW);

    // Inside text
    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CODE_BG);
    drawWrappedText(promptDesc, tbx + 8, tby + 8, tbw - 14, 18, 3);

    // Footer notice
    drawSpark(cx + 14, cy + ch - 12, 4, COLOR_TERRACOTTA);
    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    tft.drawString("物理双键审批把关", cx + 24, cy + ch - 18);

    tft.setTextColor(COLOR_TERRACOTTA, COLOR_CARD_BG);
    tft.drawRightString("⚡ 120s 超时熔断", cx + cw - 10, cy + ch - 18);

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
    tft.drawString(questionHeaderText.c_str(), 28, 32);

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
    drawWrappedText(questionText, 16, 52, 288, 16, 2);

    // Option rows
    uint8_t pageCount = (questionOptionCount + QUESTION_PER_PAGE - 1) / QUESTION_PER_PAGE;
    uint8_t start = questionPage * QUESTION_PER_PAGE;
    for (int row = 0; row < QUESTION_PER_PAGE; ++row) {
      int index = start + row;
      int y = (row == 0) ? QUESTION_ROW1_Y : QUESTION_ROW2_Y;

      if (index >= questionOptionCount) {
        tft.fillRoundRect(QUESTION_ROW_X, y, QUESTION_ROW_W, QUESTION_ROW_H, 8, COLOR_BG);
        continue;
      }

      tft.fillRoundRect(QUESTION_ROW_X, y, QUESTION_ROW_W, QUESTION_ROW_H, 8, COLOR_CARD_BG);
      tft.drawRoundRect(QUESTION_ROW_X, y, QUESTION_ROW_W, QUESTION_ROW_H, 8, (index == 0) ? COLOR_CYAN_ACCENT : COLOR_CARD_BORDER);

      // Number badge
      tft.fillRoundRect(QUESTION_ROW_X + 8, y + 10, 24, 32, 4, (index == 0) ? COLOR_CYAN_BG : COLOR_CARD_HEADER);
      tft.drawRoundRect(QUESTION_ROW_X + 8, y + 10, 24, 32, 4, (index == 0) ? COLOR_CYAN_ACCENT : COLOR_CARD_BORDER);
      char numBuf[4];
      snprintf(numBuf, sizeof(numBuf), "%d", index + 1);
      tft.setTextColor((index == 0) ? COLOR_CYAN_HI : COLOR_TEXT_MUTED, (index == 0) ? COLOR_CYAN_BG : COLOR_CARD_HEADER);
      tft.drawCenterString(numBuf, QUESTION_ROW_X + 20, y + 18);

      // Label & Description
      tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
      tft.drawString(questionOptionLabels[index].c_str(), QUESTION_ROW_X + 40, y + 10);
      tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
      drawWrappedText(questionOptionDescriptions[index], QUESTION_ROW_X + 40, y + 30, QUESTION_ROW_W - 65, 14, 1);

      // Right Chevron
      drawChevron(QUESTION_ROW_X + QUESTION_ROW_W - 16, y + 18, 14, (index == 0) ? COLOR_CYAN_ACCENT : COLOR_CARD_BORDER);
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

      char pageIndicator[16];
      snprintf(pageIndicator, sizeof(pageIndicator), "第 %d / %d 页", questionPage + 1, pageCount);
      tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
      tft.drawCenterString(pageIndicator, 147, QUESTION_NAV_Y + 5);
    } else {
      tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
      tft.drawCenterString("轻触选项序号直接作答", 160, QUESTION_NAV_Y + 5);
    }
  }
  else if (currentState == STATE_ANSWERED) {
    setLED(false, true, false); // Green

    int cx = 8, cy = 28, cw = 304, ch = 198;
    tft.fillRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BG);
    tft.drawRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BORDER);

    // Radiant Success Emblem
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
    tft.drawCenterString("反馈已成功回传终端", 160, 148);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    tft.drawCenterString("Claude Code 终端已恢复执行", 160, 172);
  }
  else if (currentState == STATE_EXPIRED) {
    setLED(true, false, false); // Red

    int cx = 8, cy = 28, cw = 304, ch = 198;
    tft.fillRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BG);
    tft.drawRoundRect(cx, cy, cw, ch, 8, COLOR_CARD_BORDER);

    // Radiant Expired Emblem
    int ecx = 160, ecy = 76;
    tft.drawCircle(ecx, ecy, 36, 0x4081);
    tft.drawCircle(ecx, ecy, 26, COLOR_RED_BTN);
    tft.fillCircle(ecx, ecy, 18, COLOR_RED_BTN);
    for (int d = -1; d <= 1; d++) {
      tft.drawLine(ecx - 7, ecy - 7 + d, ecx + 7, ecy + 7 + d, COLOR_RED_HI);
      tft.drawLine(ecx - 7, ecy + 7 + d, ecx + 7, ecy - 7 + d, COLOR_RED_HI);
    }

    tft.setTextColor(COLOR_RED_HI, COLOR_CARD_BG);
    tft.drawCenterString("请求已超时 · 120s TIMEOUT", 160, 124);

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

  // The ID has now been consumed.
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

// ==========================================
// 4. BLE Callbacks
// ==========================================
class ServerCallbacks: public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* server) override {
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

void copyJsonString(JsonVariant source, char* dest, size_t destSize) {
  String value = source | "";
  value.toCharArray(dest, destSize);
}

// Writes larger than the negotiated ATT MTU arrive from the host as several
// sequential writes. Accumulate them here and parse once the JSON is complete.
#define RX_BUFFER_MAX 2048
#define RX_CHUNK_GAP_MS 200
static std::vector<uint8_t> rxBuffer;
static unsigned long lastRxChunkMillis = 0;

class RxCallbacks: public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* pCharacteristic) override {
    std::string rxValue = pCharacteristic->getValue();
    Serial.printf("[BLE] Received %d bytes\n", rxValue.length());

    if (rxValue.length() == 0 || uiQueue == nullptr) {
      return;
    }

    // A long quiet gap after a partial frame means that burst is dead (a
    // dropped chunk, or host restart); it can never complete, so drop it.
    unsigned long now = millis();
    if (!rxBuffer.empty() && (now - lastRxChunkMillis > RX_CHUNK_GAP_MS)) {
      Serial.printf("[BLE RX] stale partial frame (%d bytes) discarded\n", rxBuffer.size());
      rxBuffer.clear();
    }
    lastRxChunkMillis = now;

    rxBuffer.insert(rxBuffer.end(), rxValue.begin(), rxValue.end());
    if (rxBuffer.size() > RX_BUFFER_MAX) {
      Serial.printf("[BLE RX] buffer overflow (%d bytes), dropping\n", rxBuffer.size());
      rxBuffer.clear();
      return;
    }

    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, rxBuffer.data(), rxBuffer.size());
    if (error == DeserializationError::IncompleteInput) {
      // Chunked write in progress; wait for the remaining chunks.
      Serial.printf("[BLE RX] incomplete JSON, waiting for remaining chunks (%d bytes so far)\n",
                    rxBuffer.size());
      return;
    }
    if (error) {
      Serial.printf("[BLE JSON ERROR] %s (dropping %d bytes)\n", error.c_str(), rxBuffer.size());
      rxBuffer.clear();
      return;
    }
    rxBuffer.clear();

    DeckMessage* pMsg = allocateDeckMessage();
    if (pMsg == nullptr) return;
    DeckMessage& msg = *pMsg;

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
      for (uint8_t i = 0; i < msg.optionCount; ++i) {
        copyJsonString(options[i]["label"], msg.optionLabels[i], sizeof(msg.optionLabels[i]));
        copyJsonString(options[i]["description"], msg.optionDescriptions[i], sizeof(msg.optionDescriptions[i]));
      }
    } else if (type == "status") {
      msg.type = DECK_MSG_STATUS;
      copyJsonString(doc["status"], msg.status, sizeof(msg.status));
      copyJsonString(doc["desc"], msg.desc, sizeof(msg.desc));
    } else {
      vPortFree(pMsg);
      return;
    }

    if (uiQueue == nullptr || xQueueSend(uiQueue, &pMsg, 0) != pdTRUE) {
      vPortFree(pMsg);
    }
  }
};

void handleQueuedMessage(DeckMessage& msg) {
  if (msg.type == DECK_MSG_PROMPT) {
    strlcpy(pendingRequestId, msg.id, sizeof(pendingRequestId));
    promptTitle = msg.title;
    promptDesc = msg.desc;
    promptBadge = msg.badge;
    promptTag = msg.tag;
    inferBadgeAndTag(promptTitle, promptBadge, promptTag);
    option1Text = msg.option1;
    option2Text = msg.option2;
    currentState = STATE_PROMPT;
    lastTouchTime = millis(); // Avoid a stale touch immediately answering a new prompt.
    beep(60);
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
    lastTouchTime = millis();
    beep(60);
    renderUI();
    return;
  }

  if (msg.type == DECK_MSG_STATUS) {
    String status = msg.status;
    promptDesc = msg.desc;

    if (status == "expired") {
      currentState = STATE_EXPIRED;
      beep(80);
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

  // Initialize Display
  tft.init();
  tft.setRotation(1); // Landscape 320x240

  // Set Full GB2312 Chinese Font (6,763 characters, 0-byte RAM overhead, runs from Flash)
  tft.setFont(&fonts::efontCN_14_b);
  tft.setTextSize(1);
  Serial.println("CJK font loaded: efontCN_14_b (Full GB2312 Bold)");

  // Force backlight ON
  pinMode(21, OUTPUT);
  digitalWrite(21, HIGH);
  pinMode(27, OUTPUT);
  digitalWrite(27, HIGH);

  // Initialize Touch Screen with dedicated SPI
  touchSPI.begin(XPT2046_CLK, XPT2046_MISO, XPT2046_MOSI, XPT2046_CS);
  ts.begin(touchSPI);
  ts.setRotation(1);

  // Initial UI
  renderUI();
  beep(40);

  // Initialize NimBLE with large MTU
  NimBLEDevice::init("Claude-Deck");
  NimBLEDevice::setMTU(517);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9); // Max TX power

  pServer = NimBLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  NimBLEService* pService = pServer->createService(SERVICE_UUID);

  NimBLECharacteristic* pRxCharacteristic = pService->createCharacteristic(
    CHAR_UUID_RX,
    NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR
  );
  pRxCharacteristic->setCallbacks(new RxCallbacks());

  pTxCharacteristic = pService->createCharacteristic(
    CHAR_UUID_TX,
    NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY
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

  if (ts.touched()) {
    TS_Point p = ts.getPoint();

    // Map raw touch (X: 3800~200 inverted, Y: 3800~240 inverted) to screen.
    int screenX = map(p.x, 3800, 200, 0, 320);
    int screenY = map(p.y, 3800, 240, 0, 240);
    screenX = constrain(screenX, 0, 319);
    screenY = constrain(screenY, 0, 239);

    Serial.printf("[TOUCH] Raw: (%d, %d) -> Screen: (%d, %d)\n", p.x, p.y, screenX, screenY);

    if (currentState == STATE_QUESTION && (millis() - lastTouchTime > 300)) {
      lastTouchTime = millis();
      uint8_t pageCount = (questionOptionCount + QUESTION_PER_PAGE - 1) / QUESTION_PER_PAGE;
      uint8_t start = questionPage * QUESTION_PER_PAGE;

      // 1. 点击滚动条轨道直接翻页 (宽度18px，允许左右各扩容6px触摸热区)
      if (pageCount > 1 && screenX >= (SCROLLBAR_X - 6) && screenX <= 318 && screenY >= SCROLLBAR_Y && screenY <= (SCROLLBAR_Y + SCROLLBAR_H)) {
        int midY = SCROLLBAR_Y + SCROLLBAR_H / 2;
        if (screenY < midY && questionPage > 0) {
          questionPage--;
          beep(30);
          renderUI();
        } else if (screenY >= midY && questionPage + 1 < pageCount) {
          questionPage++;
          beep(30);
          renderUI();
        }
        goto endTouch;
      }

      // 2. 点击底部导航翻页按键
      if (pageCount > 1 && screenY >= QUESTION_NAV_Y && screenY <= 238) {
        if (screenX >= 8 && screenX <= 82 && questionPage > 0) {
          questionPage--;
          beep(30);
          renderUI();
        }
        else if (screenX >= 212 && screenX <= 286 && questionPage + 1 < pageCount) {
          questionPage++;
          beep(30);
          renderUI();
        }
        goto endTouch;
      }

      int selectedOption = -1;
      if (screenX >= QUESTION_ROW_X && screenX <= QUESTION_ROW_X + QUESTION_ROW_W) {
        if (screenY >= QUESTION_ROW1_Y && screenY <= QUESTION_ROW1_Y + QUESTION_ROW_H) {
          selectedOption = start;
        }
        else if (screenY >= QUESTION_ROW2_Y && screenY <= QUESTION_ROW2_Y + QUESTION_ROW_H) {
          selectedOption = start + 1;
        }
      }

      if (selectedOption >= 0 && selectedOption < questionOptionCount) {
        int row = selectedOption - start;
        int y = (row == 0) ? QUESTION_ROW1_Y : QUESTION_ROW2_Y;
        tft.fillRoundRect(QUESTION_ROW_X, y, QUESTION_ROW_W, QUESTION_ROW_H, 10, TFT_WHITE);
        tft.setTextColor(TFT_BLACK, TFT_WHITE);
        tft.drawString(questionOptionLabels[selectedOption].c_str(), QUESTION_ROW_X + 14, y + 8);
        beep(60);

        if (sendSelectionToMac(selectedOption)) {
          setLED(false, true, false);
          currentState = STATE_ANSWERED;
          renderUI();
        } else {
          setLED(true, false, false);
          renderUI();
        }
      }

      goto endTouch;
    }

    if (currentState == STATE_PROMPT && (millis() - lastTouchTime > 300)) {
      // Draw visual touch point only when a physical decision is possible.
      tft.fillCircle(screenX, screenY, 3, TFT_YELLOW);

      lastTouchTime = millis();

      bool allowed = false;
      bool matched = false;

      // Check Button 1 (Allow / Left)
      // 右边界不扩展 +6，避免与 BTN2 左边界 [158,162] 4px 重叠
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
        // Visual press flash feedback
        if (allowed) {
          drawTactileButton(BTN1_X, BTN_Y, BTN1_W, BTN_H, true, option1Text, "[ CONFIRM ]", true);
          beep(40);
          delay(40);
          beep(50);
        } else {
          drawTactileButton(BTN2_X, BTN_Y, BTN2_W, BTN_H, false, option2Text, "[ ABORT ]", true);
          beep(90);
        }

        if (sendResponseToMac(allowed ? 0 : 1)) {
          setLED(false, true, false); // Green
          currentState = STATE_ANSWERED;
        } else {
          setLED(true, false, false); // Red
          // Stay in prompt mode so the user can retry if BLE briefly recovers.
        }
        renderUI();
      }
    }
  }

endTouch:;
  delay(20);
}
