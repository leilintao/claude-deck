#define LGFX_AUTODETECT
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <SPI.h>
#include <XPT2046_Touchscreen.h>
#include <NimBLEDevice.h>
#include <ArduinoJson.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

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
  char optionLabels[8][36];
  char optionDescriptions[8][72];
  uint8_t optionCount;
} DeckMessage;

static QueueHandle_t uiQueue = nullptr;

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
static String questionOptionLabels[8];
static String questionOptionDescriptions[8];
static uint8_t questionOptionCount = 0;
static uint8_t questionPage = 0;
static unsigned long lastTouchTime = 0;

#define QUESTION_MAX_OPTIONS 8
#define QUESTION_PER_PAGE 2
#define QUESTION_ROW_X 12
#define QUESTION_ROW_W 296
#define QUESTION_ROW_H 52
#define QUESTION_ROW1_Y 92
#define QUESTION_ROW2_Y 152
#define QUESTION_NAV_Y 210

// ==========================================
// Anthropic Studio Minimalist Design Palette (RGB565)
// ==========================================
#define COLOR_BG            0x10A2 // #121316 Deep obsidian
#define COLOR_HEADER_BG     0x18C3 // #16171B Status bar
#define COLOR_HEADER_LINE   0x2966 // #2A2B33 Divider line
#define COLOR_CARD_BG       0x1CE4 // #1C1D22 Card surface
#define COLOR_CARD_BORDER   0x31A7 // #32343D Subtle card border
#define COLOR_CARD_INNER    0x0861 // #0B0C0E Deep terminal codebox
#define COLOR_TERRACOTTA    0xD52F // #D4A27F Anthropic Terracotta
#define COLOR_AMBER_GLOW    0xFCE0 // #FBBF24 Warning / Active Amber
#define COLOR_TEXT_WHITE    0xFFFF // #FFFFFF Crisp White
#define COLOR_TEXT_MUTED    0x9CF3 // #9CA3AF Slate gray text
#define COLOR_GREEN_BTN     0x04C9 // #059669 Emerald 600
#define COLOR_GREEN_BORDER  0x1571 // #10B981 Emerald 500
#define COLOR_GREEN_MINT    0xA7F3 // #A7F3D0 Light Mint text
#define COLOR_GREEN_BG      0x0A24 // #0A2416 Dark Emerald surface
#define COLOR_RED_BTN       0xB8E3 // #DC2626 Rose 600
#define COLOR_RED_BORDER    0xF9D0 // #F43F5E Rose 500
#define COLOR_RED_ROSE      0xFDEE // #FECDD3 Light Rose text
#define COLOR_RED_BG        0x3000 // Dark Red surface
#define COLOR_CYAN_ACCENT   0x05BD // #06B6D4 Cyan 500
#define COLOR_CYAN_BG       0x01E8 // #062836 Dark Cyan surface

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

void drawStatusBar() {
  tft.fillRect(0, 0, 320, 26, COLOR_HEADER_BG);
  tft.drawFastHLine(0, 26, 320, COLOR_HEADER_LINE);

  // Status Dot
  uint16_t dotColor;
  if (currentState == STATE_PROMPT) dotColor = COLOR_AMBER_GLOW;
  else if (currentState == STATE_THINKING) dotColor = COLOR_CYAN_ACCENT;
  else if (currentState == STATE_ANSWERED) dotColor = COLOR_GREEN_BTN;
  else if (currentState == STATE_EXPIRED) dotColor = COLOR_RED_BTN;
  else dotColor = deviceConnected ? COLOR_GREEN_BTN : COLOR_AMBER_GLOW;

  tft.fillCircle(14, 13, 3, dotColor);

  // Badge / Title Text
  tft.setTextColor(COLOR_TERRACOTTA, COLOR_HEADER_BG);
  tft.setTextSize(1);
  if (currentState == STATE_PROMPT) {
    tft.drawString(promptBadge.c_str(), 26, 7);
  } else if (currentState == STATE_THINKING) {
    tft.drawString("[THINKING CoT]", 26, 7);
  } else {
    tft.drawString("CLAUDE DECK", 26, 7);
  }

  // Right BLE Status
  if (deviceConnected) {
    tft.setTextColor(COLOR_GREEN_BTN, COLOR_HEADER_BG);
    tft.drawString("BLE 100% | 5V", 228, 7);
  } else {
    tft.setTextColor(COLOR_AMBER_GLOW, COLOR_HEADER_BG);
    tft.drawString("BLE: PAIRING", 232, 7);
  }
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

  // Content background (Deep obsidian warm dark)
  tft.fillRect(0, 27, 320, 213, COLOR_BG);

  if (currentState == STATE_IDLE) {
    setLED(false, true, false); // Green

    // Standby Badge / Icon (54x54)
    tft.fillRoundRect(133, 50, 54, 54, 14, COLOR_GREEN_BG);
    tft.drawRoundRect(133, 50, 54, 54, 14, COLOR_GREEN_BORDER);
    tft.setTextColor(COLOR_GREEN_BORDER, COLOR_GREEN_BG);
    tft.drawCenterString("OK", 160, 64, 4);

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_BG);
    tft.drawCenterString("Claude Code 待命就绪", 160, 116, 2);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
    tft.drawCenterString("把窗口放后台去吧 · 遇到询问副屏会提醒", 160, 144, 1);

    tft.setTextColor(COLOR_CARD_BORDER, COLOR_BG);
    tft.drawCenterString("STANDBY · BLE CONNECTED · FAIL-SAFE DENY", 160, 200, 1);
  }
  else if (currentState == STATE_THINKING) {
    setLED(false, false, true); // Blue

    // Thinking Badge / Icon
    tft.fillRoundRect(133, 50, 54, 54, 14, COLOR_CYAN_BG);
    tft.drawRoundRect(133, 50, 54, 54, 14, COLOR_CYAN_ACCENT);
    tft.setTextColor(COLOR_CYAN_ACCENT, COLOR_CYAN_BG);
    tft.drawCenterString("AI", 160, 64, 4);

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_BG);
    tft.drawCenterString("Claude 正在深度思考中...", 160, 116, 2);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
    drawWrappedText(promptDesc, 20, 144, 280, 16, 2);

    tft.setTextColor(COLOR_CYAN_ACCENT, COLOR_BG);
    tft.drawCenterString("THINKING · PLEASE WAIT", 160, 200, 1);
  }
  else if (currentState == STATE_PROMPT) {
    setLED(true, true, false); // Yellow / Amber

    // Middle Card Container (304 x 124, r=8)
    tft.fillRoundRect(8, 32, 304, 124, 8, COLOR_CARD_BG);
    tft.drawRoundRect(8, 32, 304, 124, 8, COLOR_CARD_BORDER);

    // Prompt Title with Accent
    tft.setTextColor(COLOR_AMBER_GLOW, COLOR_CARD_BG);
    tft.drawString(promptTitle.c_str(), 16, 40, 2);

    // Prompt Category Tag Pill
    tft.fillRoundRect(236, 38, 68, 16, 3, COLOR_HEADER_BG);
    tft.drawRoundRect(236, 38, 68, 16, 3, COLOR_CARD_BORDER);
    tft.setTextColor(COLOR_TERRACOTTA, COLOR_HEADER_BG);
    tft.drawCenterString(promptTag.c_str(), 270, 41, 1);

    // Terminal Inner Codebox (288 x 56, r=4)
    tft.fillRoundRect(16, 58, 288, 56, 4, COLOR_CARD_INNER);
    tft.fillRect(16, 58, 3, 56, COLOR_AMBER_GLOW);

    // Inside Codebox Text (UTF-8 safe wrapping)
    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_INNER);
    drawWrappedText(promptDesc, 26, 63, 272, 16, 3);

    // Card Footer: Sub-hint + Timeout Notice
    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    tft.drawString("轻触按键物理授权", 16, 128, 1);
    tft.setTextColor(COLOR_TERRACOTTA, COLOR_CARD_BG);
    tft.drawRightString("120s 超时熔断", 300, 128, 1);

    // Button 1 (Allow / Left) - Emerald Green 68px
    tft.fillRoundRect(BTN1_X, BTN_Y, BTN1_W, BTN_H, 10, COLOR_GREEN_BTN);
    tft.drawRoundRect(BTN1_X, BTN_Y, BTN1_W, BTN_H, 10, COLOR_GREEN_BORDER);
    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_GREEN_BTN);
    tft.drawCenterString(option1Text.c_str(), BTN1_X + BTN1_W / 2, BTN_Y + 16, 2);
    tft.setTextColor(COLOR_GREEN_MINT, COLOR_GREEN_BTN);
    tft.drawCenterString("ALLOW · TAP", BTN1_X + BTN1_W / 2, BTN_Y + 44, 1);

    // Button 2 (Reject / Right) - Rose Red 68px
    tft.fillRoundRect(BTN2_X, BTN_Y, BTN2_W, BTN_H, 10, COLOR_RED_BTN);
    tft.drawRoundRect(BTN2_X, BTN_Y, BTN2_W, BTN_H, 10, COLOR_RED_BORDER);
    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_RED_BTN);
    tft.drawCenterString(option2Text.c_str(), BTN2_X + BTN2_W / 2, BTN_Y + 16, 2);
    tft.setTextColor(COLOR_RED_ROSE, COLOR_RED_BTN);
    tft.drawCenterString("REJECT · STOP", BTN2_X + BTN2_W / 2, BTN_Y + 44, 1);
  }
  else if (currentState == STATE_QUESTION) {
    setLED(false, false, true); // Blue

    // Header chip: question category / short label.
    tft.fillRoundRect(8, 32, 304, 22, 6, COLOR_CARD_BG);
    tft.drawRoundRect(8, 32, 304, 22, 6, COLOR_CARD_BORDER);
    tft.setTextColor(COLOR_TERRACOTTA, COLOR_CARD_BG);
    tft.drawString(questionHeaderText.c_str(), 16, 36, 1);
    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    tft.drawRightString("CLAUDE QUESTION", 304, 36, 1);

    // Question body.
    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_BG);
    drawWrappedText(questionText, 12, 62, 296, 18, 2);

    // Option rows: two per page, each with label + description.
    uint8_t pageCount = (questionOptionCount + QUESTION_PER_PAGE - 1) / QUESTION_PER_PAGE;
    uint8_t start = questionPage * QUESTION_PER_PAGE;
    for (int row = 0; row < QUESTION_PER_PAGE; ++row) {
      int index = start + row;
      int y = (row == 0) ? QUESTION_ROW1_Y : QUESTION_ROW2_Y;

      if (index >= questionOptionCount) {
        tft.fillRoundRect(QUESTION_ROW_X, y, QUESTION_ROW_W, QUESTION_ROW_H, 10, COLOR_BG);
        continue;
      }

      tft.fillRoundRect(QUESTION_ROW_X, y, QUESTION_ROW_W, QUESTION_ROW_H, 10, COLOR_CARD_BG);
      tft.drawRoundRect(QUESTION_ROW_X, y, QUESTION_ROW_W, QUESTION_ROW_H, 10, COLOR_CYAN_ACCENT);
      tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
      tft.drawString(questionOptionLabels[index].c_str(), QUESTION_ROW_X + 14, y + 8, 2);
      tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
      drawWrappedText(questionOptionDescriptions[index], QUESTION_ROW_X + 14, y + 30, 268, 14, 1);
    }

    // Pagination controls.
    if (pageCount > 1) {
      tft.fillRoundRect(8, QUESTION_NAV_Y, 74, 24, 8, COLOR_CARD_BG);
      tft.drawRoundRect(8, QUESTION_NAV_Y, 74, 24, 8, COLOR_CARD_BORDER);
      tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
      tft.drawCenterString("◀ PREV", 45, QUESTION_NAV_Y + 6, 1);

      tft.fillRoundRect(238, QUESTION_NAV_Y, 74, 24, 8, COLOR_CARD_BG);
      tft.drawRoundRect(238, QUESTION_NAV_Y, 74, 24, 8, COLOR_CARD_BORDER);
      tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
      tft.drawCenterString("NEXT ▶", 275, QUESTION_NAV_Y + 6, 1);

      char pageIndicator[12];
      snprintf(pageIndicator, sizeof(pageIndicator), "%d / %d", questionPage + 1, pageCount);
      tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
      tft.drawCenterString(pageIndicator, 160, QUESTION_NAV_Y + 6, 1);
    }
    else {
      tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
      tft.drawCenterString("轻触选项直接回答", 160, QUESTION_NAV_Y + 6, 1);
    }
  }
  else if (currentState == STATE_ANSWERED) {
    setLED(false, true, false); // Green

    tft.fillRoundRect(133, 50, 54, 54, 14, COLOR_GREEN_BG);
    tft.drawRoundRect(133, 50, 54, 54, 14, COLOR_GREEN_BORDER);
    tft.setTextColor(COLOR_GREEN_BORDER, COLOR_GREEN_BG);
    tft.drawCenterString("OK", 160, 64, 4);

    tft.setTextColor(COLOR_GREEN_BORDER, COLOR_BG);
    tft.drawCenterString("反馈已成功回传终端", 160, 116, 2);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
    tft.drawCenterString("Claude Code 终端已恢复执行", 160, 144, 1);
  }
  else if (currentState == STATE_EXPIRED) {
    setLED(true, false, false); // Red

    tft.fillRoundRect(133, 50, 54, 54, 14, COLOR_RED_BG);
    tft.drawRoundRect(133, 50, 54, 54, 14, COLOR_RED_BORDER);
    tft.setTextColor(COLOR_RED_BORDER, COLOR_RED_BG);
    tft.drawCenterString("X", 160, 64, 4);

    tft.setTextColor(COLOR_RED_BORDER, COLOR_BG);
    tft.drawCenterString("请求已超时 (120s)", 160, 116, 2);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
    tft.drawCenterString("为保护工程安全，终端已自动拦截", 160, 144, 1);
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

    DeckMessage msg;
    memset(&msg, 0, sizeof(msg));
    msg.type = DECK_MSG_STATUS;
    strlcpy(msg.status, "connected", sizeof(msg.status));
    if (uiQueue != nullptr) {
      xQueueSend(uiQueue, &msg, 0);
    }
  }

  void onDisconnect(NimBLEServer* server) override {
    deviceConnected = false;
    NimBLEDevice::startAdvertising();

    DeckMessage msg;
    memset(&msg, 0, sizeof(msg));
    msg.type = DECK_MSG_STATUS;
    strlcpy(msg.status, "idle", sizeof(msg.status));
    if (uiQueue != nullptr) {
      xQueueSend(uiQueue, &msg, 0);
    }
  }
};

void copyJsonString(JsonVariant source, char* dest, size_t destSize) {
  String value = source | "";
  value.toCharArray(dest, destSize);
}

class RxCallbacks: public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* pCharacteristic) override {
    std::string rxValue = pCharacteristic->getValue();
    Serial.printf("[BLE] Received %d bytes: %s\n", rxValue.length(), rxValue.c_str());

    if (rxValue.length() == 0 || uiQueue == nullptr) {
      return;
    }

    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, rxValue);
    if (error) {
      Serial.printf("[BLE JSON ERROR] %s\n", error.c_str());
      return;
    }

    DeckMessage msg;
    memset(&msg, 0, sizeof(msg));

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
      return;
    }

    xQueueSend(uiQueue, &msg, 0);
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

    for (uint8_t i = 0; i < 8; ++i) {
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

  uiQueue = xQueueCreate(4, sizeof(DeckMessage));

  // Initialize LEDs
  pinMode(CYD_LED_R, OUTPUT);
  pinMode(CYD_LED_G, OUTPUT);
  pinMode(CYD_LED_B, OUTPUT);
  setLED(false, false, false);

  // Initialize Display
  tft.init();
  tft.setRotation(1); // Landscape 320x240

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
  DeckMessage msg;
  while (xQueueReceive(uiQueue, &msg, 0) == pdTRUE) {
    handleQueuedMessage(msg);
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

      if (pageCount > 1 && screenY >= QUESTION_NAV_Y && screenY <= 238) {
        if (screenX >= 8 && screenX <= 82 && questionPage > 0) {
          questionPage--;
          renderUI();
        }
        else if (screenX >= 238 && screenX <= 312 && questionPage + 1 < pageCount) {
          questionPage++;
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
        tft.drawString(questionOptionLabels[selectedOption].c_str(), QUESTION_ROW_X + 14, y + 8, 2);
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
      if (screenX >= (BTN1_X - 6) && screenX <= (BTN1_X + BTN1_W + 6) &&
          screenY >= (BTN_Y - 10) && screenY <= 238) {
        allowed = true;
        matched = true;
      }
      // Check Button 2 (Reject / Right)
      else if (screenX >= (BTN2_X - 6) && screenX <= (BTN2_X + BTN2_W + 6) &&
               screenY >= (BTN_Y - 10) && screenY <= 238) {
        allowed = false;
        matched = true;
      }

      if (matched) {
        // Visual press flash feedback
        if (allowed) {
          tft.fillRoundRect(BTN1_X, BTN_Y, BTN1_W, BTN_H, 10, TFT_WHITE);
          tft.setTextColor(TFT_BLACK, TFT_WHITE);
          tft.drawCenterString(option1Text.c_str(), BTN1_X + BTN1_W / 2, BTN_Y + 16, 2);
          beep(40);
          delay(40);
          beep(50);
        } else {
          tft.fillRoundRect(BTN2_X, BTN_Y, BTN2_W, BTN_H, 10, TFT_WHITE);
          tft.setTextColor(TFT_BLACK, TFT_WHITE);
          tft.drawCenterString(option2Text.c_str(), BTN2_X + BTN2_W / 2, BTN_Y + 16, 2);
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
