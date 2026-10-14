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
  STATE_ANSWERED,
  STATE_EXPIRED
};

enum DeckMessageType {
  DECK_MSG_PROMPT,
  DECK_MSG_STATUS
};

// BLE NimBLE callbacks run in another task. They only parse JSON and enqueue
// messages. All display/shared-state mutation happens in loop().
typedef struct {
  DeckMessageType type;
  char id[48];
  char title[64];
  char desc[160];
  char option1[32];
  char option2[32];
  char status[24];
} DeckMessage;

static QueueHandle_t uiQueue = nullptr;

static UIState currentState = STATE_IDLE;
static char pendingRequestId[48] = "";
static String promptTitle = "Claude Code 待命";
static String promptDesc = "副屏已就绪！";
static String option1Text = "允许 (Y)";
static String option2Text = "拒绝 (N)";
static unsigned long lastTouchTime = 0;

// ==========================================
// Anthropic Studio Minimalist Design Palette (RGB565)
// ==========================================
#define COLOR_BG            0x10A2 // #121214 Deep obsidian
#define COLOR_HEADER_BG     0x18E3 // #18181C Status bar
#define COLOR_HEADER_LINE   0x2945 // #28282E Divider line
#define COLOR_CARD_BG       0x18E4 // #1A1A1E Card surface
#define COLOR_CARD_BORDER   0x39E7 // #383840 Subtle card border
#define COLOR_TERRACOTTA    0xD52F // #D4A27F Anthropic Terracotta
#define COLOR_AMBER_GLOW    0xFCE0 // #FBBF24 Warning / Active Amber
#define COLOR_TEXT_WHITE    0xFFFF // #FFFFFF Crisp White
#define COLOR_TEXT_MUTED    0x9CF3 // #9CA3AF Slate gray text
#define COLOR_GREEN_BTN     0x0DA7 // #10B981 Emerald 500
#define COLOR_GREEN_BORDER  0x3571 // #34D399 Emerald 400
#define COLOR_GREEN_BG      0x0A24 // #0A2416 Dark Emerald surface
#define COLOR_RED_BTN       0xE0E9 // #E11D48 Rose 600
#define COLOR_RED_BORDER    0xF9D0 // #FB7185 Rose 400
#define COLOR_CYAN_ACCENT   0x05BD // #06B6D4 Cyan 500
#define COLOR_CYAN_BG       0x01E8 // #062836 Dark Cyan surface

// Touch button layout (320 x 240)
static const int BTN_Y = 164;
static const int BTN_H = 64;
static const int BTN1_X = 10;
static const int BTN1_W = 144;
static const int BTN2_X = 166;
static const int BTN2_W = 144;

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
  uint16_t dotColor = (currentState == STATE_PROMPT) ? COLOR_AMBER_GLOW : (deviceConnected ? COLOR_GREEN_BTN : COLOR_AMBER_GLOW);
  tft.fillCircle(14, 13, 3, dotColor);

  // Title / Badge Text
  tft.setTextColor(COLOR_TERRACOTTA, COLOR_HEADER_BG);
  tft.setTextSize(1);
  if (currentState == STATE_PROMPT) {
    tft.drawString("[CLAUDE APPROVAL]", 26, 7);
  } else {
    tft.drawString("CLAUDE DECK", 26, 7);
  }

  // Right BLE Status
  if (deviceConnected) {
    tft.setTextColor(COLOR_GREEN_BTN, COLOR_HEADER_BG);
    tft.drawString("BLE: 100%", 252, 7);
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

    // Standby Badge / Icon
    tft.fillRoundRect(135, 52, 50, 50, 14, COLOR_GREEN_BG);
    tft.drawRoundRect(135, 52, 50, 50, 14, COLOR_GREEN_BORDER);
    tft.setTextColor(COLOR_GREEN_BORDER, COLOR_GREEN_BG);
    tft.drawCenterString("OK", 160, 65, 4);

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_BG);
    tft.drawCenterString("Claude Code 待命就绪", 160, 118, 2);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
    tft.drawCenterString("把窗口放后台去吧 · 遇到询问副屏会提醒", 160, 146, 1);

    tft.setTextColor(COLOR_CARD_BORDER, COLOR_BG);
    tft.drawCenterString("STANDBY · BLUETOOTH CONNECTED", 160, 198, 1);
  }
  else if (currentState == STATE_THINKING) {
    setLED(false, false, true); // Blue

    // Thinking Badge / Icon
    tft.fillRoundRect(135, 52, 50, 50, 14, COLOR_CYAN_BG);
    tft.drawRoundRect(135, 52, 50, 50, 14, COLOR_CYAN_ACCENT);
    tft.setTextColor(COLOR_CYAN_ACCENT, COLOR_CYAN_BG);
    tft.drawCenterString("AI", 160, 65, 4);

    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_BG);
    tft.drawCenterString("Claude 正在深度思考中...", 160, 118, 2);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
    drawWrappedText(promptDesc, 20, 146, 280, 16, 2);

    tft.setTextColor(COLOR_CYAN_ACCENT, COLOR_BG);
    tft.drawCenterString("THINKING · PLEASE WAIT", 160, 198, 1);
  }
  else if (currentState == STATE_PROMPT) {
    setLED(true, true, false); // Yellow / Amber

    // Middle Card Container (300 x 122)
    tft.fillRoundRect(10, 34, 300, 122, 10, COLOR_CARD_BG);
    tft.drawRoundRect(10, 34, 300, 122, 10, COLOR_CARD_BORDER);

    // Prompt Title with Accent
    tft.setTextColor(COLOR_AMBER_GLOW, COLOR_CARD_BG);
    tft.drawString(promptTitle.c_str(), 20, 42, 2);

    // Prompt Description (UTF-8 safe wrapping)
    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_CARD_BG);
    drawWrappedText(promptDesc, 20, 68, 276, 18, 3);

    // Sub-hint
    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_CARD_BG);
    tft.drawString("请轻触下方物理按键进行快速授权：", 20, 134, 1);

    // Button 1 (Allow / Left) - Emerald Green with sublabel
    tft.fillRoundRect(BTN1_X, BTN_Y, BTN1_W, BTN_H, 12, COLOR_GREEN_BTN);
    tft.drawRoundRect(BTN1_X, BTN_Y, BTN1_W, BTN_H, 12, COLOR_GREEN_BORDER);
    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_GREEN_BTN);
    tft.drawCenterString(option1Text.c_str(), BTN1_X + 72, BTN_Y + 14, 2);
    tft.setTextColor(tft.color565(200, 255, 220), COLOR_GREEN_BTN);
    tft.drawCenterString("ALLOW ACTION", BTN1_X + 72, BTN_Y + 38, 1);

    // Button 2 (Reject / Right) - Rose Red with sublabel
    tft.fillRoundRect(BTN2_X, BTN_Y, BTN2_W, BTN_H, 12, COLOR_RED_BTN);
    tft.drawRoundRect(BTN2_X, BTN_Y, BTN2_W, BTN_H, 12, COLOR_RED_BORDER);
    tft.setTextColor(COLOR_TEXT_WHITE, COLOR_RED_BTN);
    tft.drawCenterString(option2Text.c_str(), BTN2_X + 72, BTN_Y + 14, 2);
    tft.setTextColor(tft.color565(255, 200, 210), COLOR_RED_BTN);
    tft.drawCenterString("REJECT STOP", BTN2_X + 72, BTN_Y + 38, 1);
  }
  else if (currentState == STATE_ANSWERED) {
    setLED(false, true, false); // Green

    tft.fillRoundRect(135, 52, 50, 50, 14, COLOR_GREEN_BG);
    tft.drawRoundRect(135, 52, 50, 50, 14, COLOR_GREEN_BORDER);
    tft.setTextColor(COLOR_GREEN_BORDER, COLOR_GREEN_BG);
    tft.drawCenterString("OK", 160, 65, 4);

    tft.setTextColor(COLOR_GREEN_BORDER, COLOR_BG);
    tft.drawCenterString("反馈已成功回传终端", 160, 118, 2);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
    tft.drawCenterString("Claude Code 终端已恢复执行", 160, 146, 1);
  }
  else if (currentState == STATE_EXPIRED) {
    setLED(true, false, false); // Red

    tft.fillRoundRect(135, 52, 50, 50, 14, 0x3000);
    tft.drawRoundRect(135, 52, 50, 50, 14, COLOR_RED_BORDER);
    tft.setTextColor(COLOR_RED_BORDER, 0x3000);
    tft.drawCenterString("X", 160, 65, 4);

    tft.setTextColor(COLOR_RED_BORDER, COLOR_BG);
    tft.drawCenterString("请求已超时", 160, 118, 2);

    tft.setTextColor(COLOR_TEXT_MUTED, COLOR_BG);
    tft.drawCenterString("终端已自动拒绝该操作", 160, 146, 1);
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
    option1Text = msg.option1;
    option2Text = msg.option2;
    currentState = STATE_PROMPT;
    lastTouchTime = millis(); // Avoid a stale touch immediately answering a new prompt.
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

    if (currentState == STATE_PROMPT && (millis() - lastTouchTime > 300)) {
      // Draw visual touch point only when a physical decision is possible.
      tft.fillCircle(screenX, screenY, 3, TFT_YELLOW);

      lastTouchTime = millis();

      bool allowed = false;
      bool matched = false;

      // Check Button 1 (Allow / Left)
      if (screenX >= (BTN1_X - 6) && screenX <= (BTN1_X + BTN1_W + 6) &&
          screenY >= (BTN_Y - 12) && screenY <= 235) {
        allowed = true;
        matched = true;
      }
      // Check Button 2 (Reject / Right)
      else if (screenX >= (BTN2_X - 6) && screenX <= (BTN2_X + BTN2_W + 6) &&
               screenY >= (BTN_Y - 12) && screenY <= 235) {
        allowed = false;
        matched = true;
      }

      if (matched) {
        beep(50);
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

  delay(20);
}
