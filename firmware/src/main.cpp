#define LGFX_AUTODETECT
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <SPI.h>
#include <XPT2046_Touchscreen.h>
#include <NimBLEDevice.h>
#include <ArduinoJson.h>

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
static bool deviceConnected = false;

// ==========================================
// 3. UI State Machine
// ==========================================
enum UIState {
  STATE_IDLE,
  STATE_THINKING,
  STATE_PROMPT,
  STATE_ANSWERED
};

static UIState currentState = STATE_PROMPT;
static String promptTitle = "Claude Code 待命";
static String promptDesc = "副屏已就绪！请点击下方按钮测试触控反馈：";
static String option1Text = "允许 (Y)";
static String option2Text = "拒绝 (N)";

// Touch button layout (320 x 240)
static const int BTN_Y = 160;
static const int BTN_H = 65;
static const int BTN1_X = 12;
static const int BTN1_W = 142;
static const int BTN2_X = 166;
static const int BTN2_W = 142;

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
  tft.fillRect(0, 0, 320, 24, 0x18E3); // Dark slate header
  tft.setTextColor(TFT_WHITE, 0x18E3);
  tft.setTextSize(1);
  tft.drawString("CLAUDE DECK", 10, 6);

  if (deviceConnected) {
    tft.setTextColor(TFT_GREEN, 0x18E3);
    tft.drawString("BLE: CONNECTED", 200, 6);
  } else {
    tft.setTextColor(TFT_ORANGE, 0x18E3);
    tft.drawString("BLE: PAIRING...", 200, 6);
  }
}

void renderUI() {
  drawStatusBar();

  // Content background
  tft.fillRect(0, 24, 320, 216, 0x0841); // Very dark navy

  if (currentState == STATE_IDLE) {
    setLED(false, true, false); // Green
    tft.setTextColor(TFT_LIGHTGRAY, 0x0841);
    tft.drawCenterString("Claude Code 待命中", 160, 80, 2);
    tft.setTextColor(0x7BEF, 0x0841);
    tft.drawCenterString("终端有交互询问时将显示在此处", 160, 120, 1);
  }
  else if (currentState == STATE_THINKING) {
    setLED(false, false, true); // Blue
    tft.setTextColor(TFT_CYAN, 0x0841);
    tft.drawCenterString("Claude 正在思考中...", 160, 90, 2);
    tft.setTextColor(0x7BEF, 0x0841);
    tft.drawCenterString(promptDesc.c_str(), 160, 130, 1);
  }
  else if (currentState == STATE_PROMPT) {
    setLED(true, true, false); // Yellow / Orange

    // Card background
    tft.fillRoundRect(10, 30, 300, 120, 8, 0x18E3);
    tft.drawRoundRect(10, 30, 300, 120, 8, TFT_GOLD);

    // Title
    tft.setTextColor(TFT_YELLOW, 0x18E3);
    tft.drawString(promptTitle.c_str(), 20, 38, 2);

    // Description text (wrapped)
    tft.setTextColor(TFT_WHITE, 0x18E3);
    tft.setTextSize(1);
    
    int cursorY = 66;
    int maxLen = 34;
    for (unsigned int i = 0; i < promptDesc.length(); i += maxLen) {
      String line = promptDesc.substring(i, min((unsigned int)(i + maxLen), (unsigned int)promptDesc.length()));
      tft.drawString(line.c_str(), 20, cursorY);
      cursorY += 16;
      if (cursorY > 135) break;
    }

    // Button 1 (Allow / Yes) - Emerald Green
    tft.fillRoundRect(BTN1_X, BTN_Y, BTN1_W, BTN_H, 8, 0x0548);
    tft.drawRoundRect(BTN1_X, BTN_Y, BTN1_W, BTN_H, 8, TFT_GREEN);
    tft.setTextColor(TFT_WHITE, 0x0548);
    tft.drawCenterString(option1Text.c_str(), BTN1_X + BTN1_W / 2, BTN_Y + 22, 2);

    // Button 2 (Reject / No) - Coral Red
    tft.fillRoundRect(BTN2_X, BTN_Y, BTN2_W, BTN_H, 8, 0x9800);
    tft.drawRoundRect(BTN2_X, BTN_Y, BTN2_W, BTN_H, 8, TFT_RED);
    tft.setTextColor(TFT_WHITE, 0x9800);
    tft.drawCenterString(option2Text.c_str(), BTN2_X + BTN2_W / 2, BTN_Y + 22, 2);
  }
  else if (currentState == STATE_ANSWERED) {
    tft.setTextColor(TFT_GREEN, 0x0841);
    tft.drawCenterString("反馈已发送到终端", 160, 95, 2);
    tft.setTextColor(TFT_WHITE, 0x0841);
    tft.drawCenterString("请查看 Claude Code 终端执行进度", 160, 125, 1);
  }
}

void sendResponseToMac(int index, const String& text) {
  if (pTxCharacteristic && deviceConnected) {
    const char* resp = (index == 0) ? "ALLOW" : "DENY";
    pTxCharacteristic->setValue((const uint8_t*)resp, strlen(resp));
    pTxCharacteristic->notify();
    Serial.printf("[BLE] Sent response: %s\n", resp);
  }
}

// ==========================================
// 4. BLE Callbacks
// ==========================================
class ServerCallbacks: public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* pServer) override {
    deviceConnected = true;
    renderUI();
  }

  void onDisconnect(NimBLEServer* pServer) override {
    deviceConnected = false;
    renderUI();
    NimBLEDevice::startAdvertising();
  }
};

class RxCallbacks: public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* pCharacteristic) override {
    std::string rxValue = pCharacteristic->getValue();
    Serial.printf("[BLE] Received %d bytes: %s\n", rxValue.length(), rxValue.c_str());
    if (rxValue.length() > 0) {
      JsonDocument doc;
      DeserializationError error = deserializeJson(doc, rxValue);
      if (!error) {
        String type = doc["type"] | "prompt";
        if (type == "prompt") {
          promptTitle = doc["title"] | "确认请求";
          promptDesc = doc["desc"] | "是否允许执行？";
          JsonArray options = doc["options"];
          if (options.size() >= 2) {
            option1Text = options[0].as<String>();
            option2Text = options[1].as<String>();
          } else {
            option1Text = "允许 (Y)";
            option2Text = "拒绝 (N)";
          }
          currentState = STATE_PROMPT;
          beep(60);
          renderUI();
        } else if (type == "status") {
          String status = doc["status"] | "idle";
          if (status == "thinking") {
            currentState = STATE_THINKING;
            promptDesc = doc["desc"] | "正在思考...";
          } else {
            currentState = STATE_IDLE;
          }
          renderUI();
        }
      } else {
        Serial.printf("[BLE JSON ERROR] %s\n", error.c_str());
      }
    }
  }
};

// ==========================================
// 5. Setup & Loop
// ==========================================
void setup() {
  Serial.begin(115200);

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

unsigned long lastTouchTime = 0;

void loop() {
  if (ts.touched()) {
    TS_Point p = ts.getPoint();

    // Map raw touch (X: 3800~200 inverted, Y: 3800~240 inverted) to screen (0~320, 0~240)
    int screenX = map(p.x, 3800, 200, 0, 320);
    int screenY = map(p.y, 3800, 240, 0, 240);
    screenX = constrain(screenX, 0, 319);
    screenY = constrain(screenY, 0, 239);

    // Draw visual touch point
    tft.fillCircle(screenX, screenY, 3, TFT_YELLOW);

    Serial.printf("[TOUCH] Raw: (%d, %d) -> Screen: (%d, %d)\n", p.x, p.y, screenX, screenY);

    if (currentState == STATE_PROMPT && (millis() - lastTouchTime > 300)) {
      lastTouchTime = millis();

      // Check Button 1 (Allow / Left)
      if (screenX >= (BTN1_X - 10) && screenX <= (BTN1_X + BTN1_W + 10) &&
          screenY >= (BTN_Y - 15) && screenY <= 240) {
        tft.fillRoundRect(BTN1_X, BTN_Y, BTN1_W, BTN_H, 8, TFT_WHITE);
        tft.setTextColor(TFT_BLACK, TFT_WHITE);
        tft.drawCenterString(option1Text.c_str(), BTN1_X + BTN1_W / 2, BTN_Y + 22, 2);
        beep(50);
        delay(100);
        setLED(false, true, false); // Green
        sendResponseToMac(0, option1Text);
        currentState = STATE_ANSWERED;
        renderUI();
      }
      // Check Button 2 (Reject / Right)
      else if (screenX >= (BTN2_X - 10) && screenX <= (BTN2_X + BTN2_W + 10) &&
               screenY >= (BTN_Y - 15) && screenY <= 240) {
        tft.fillRoundRect(BTN2_X, BTN_Y, BTN2_W, BTN_H, 8, TFT_WHITE);
        tft.setTextColor(TFT_BLACK, TFT_WHITE);
        tft.drawCenterString(option2Text.c_str(), BTN2_X + BTN2_W / 2, BTN_Y + 22, 2);
        beep(50);
        delay(100);
        setLED(true, false, false); // Red
        sendResponseToMac(1, option2Text);
        currentState = STATE_ANSWERED;
        renderUI();
      }
    }
  }

  delay(20);
}
