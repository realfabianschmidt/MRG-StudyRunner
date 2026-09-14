#include <Arduino.h>
#include <math.h>
#include <Adafruit_NeoPixel.h>
#include "Seeed_Arduino_mmWave.h"

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

//----------------- BLE setup ----------------------------

#define BLE_DEVICE_NAME "MR60_BLE"
#define BLE_SERVICE_UUID "9d6f0001-7d2a-4c6b-9f4e-5c2b1f4a6e10"
#define BLE_SENSOR_CHARACTERISTIC_UUID "9d6f0002-7d2a-4c6b-9f4e-5c2b1f4a6e10"

const uint8_t BLE_PACKET_VERSION = 1;
const size_t BLE_PACKET_SIZE = 20;
const int16_t BLE_MISSING_VALUE = -32768;

const unsigned long BLE_NOTIFY_INTERVAL_MS = 100;  // 10 Hz
const unsigned long DEBUG_INTERVAL_MS = 1000;
const unsigned long STABILIZATION_TIME_MS = 20000;
const unsigned long DATA_TIMEOUT_MS = 3000;

const uint8_t FLAG_VALID = 0x01;
const uint8_t FLAG_STABILIZED = 0x02;
const uint8_t FLAG_PRESENT = 0x04;

BLEServer *bleServer = nullptr;
BLECharacteristic *sensorCharacteristic = nullptr;

volatile bool bleDeviceConnected = false;
bool bleWasConnected = false;

uint16_t bleSequence = 0;
unsigned long startupMs = 0;
unsigned long lastBleNotifyMs = 0;
unsigned long lastDebugMs = 0;
unsigned long lastSensorUpdateMs = 0;

//----------------- mmWave serial setup ------------------

#ifdef ESP32
  #include <HardwareSerial.h>
  // On XIAO ESP32C6 the library examples use UART0 for the radar.
  HardwareSerial mmWaveSerial(0);
#else
  #define mmWaveSerial Serial1
#endif

SEEED_MR60BHA2 mmWave;

//----------------- NeoPixel (on-board WS2812) -----------

// On the XIAO ESP32C6 + MR60 kit the onboard RGB LED is a WS2812 on pin D1.
#define NEOPIXEL_PIN D1
#define NEOPIXEL_COUNT 1

Adafruit_NeoPixel pixels(NEOPIXEL_COUNT, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

//----------------- Breath-phase based BPM ---------------

const float BREATH_PHASE_THRESHOLD = 1.2f;

const int MAX_BREATH_INTERVALS = 4;
unsigned long breathIntervals[MAX_BREATH_INTERVALS];
int breathIntervalCount = 0;
unsigned long lastBreathEventTime = 0;

float breathRatePhaseBPM = 0.0f;
float lastBreathPhase = 0.0f;

//----------------- Sensor data buffers ------------------

float heartRateBPM = 0.0f;
float breathRateBPM_lib = 0.0f;
float distanceCentimeters = 0.0f;

float totalPhase = 0.0f;
float breathPhase = 0.0f;
float heartPhase = 0.0f;

bool hasHeartRate = false;
bool hasBreathRate = false;
bool hasDistance = false;
bool hasTotalPhase = false;
bool hasBreathPhase = false;
bool hasHeartPhase = false;

//----------------- Heartbeat LED (BPM-based) ------------

const unsigned long HEART_LED_FLASH_MS = 30;

unsigned long nextHeartBlinkMs = 0;
unsigned long heartLedOffMs = 0;
bool heartLedEnabled = false;
bool heartLedOn = false;

//----------------- Function prototypes ------------------

void setupBLE();
void updateSensorData();
void handleBleConnectionState();
void sendBlePacketIfDue();
void sendDebugDataIfDue();
void fillSensorPacket(uint8_t *packet, unsigned long now);
uint8_t sensorFlags(unsigned long now);
bool dataIsFresh(unsigned long now);
bool sensorIsStabilized(unsigned long now);
bool personIsPresent();

void updateHeartbeatLedFromBPM();
void setHeartbeatLed(bool on);

void updateBreathRateFromPhase();
void addBreathInterval(unsigned long intervalMs);

int16_t encodeScaled(float value, bool hasValue, float scale);
void putU16LE(uint8_t *packet, size_t offset, uint16_t value);
void putU32LE(uint8_t *packet, size_t offset, uint32_t value);
void putI16LE(uint8_t *packet, size_t offset, int16_t value);

//----------------- BLE callbacks ------------------------

class SensorServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *server) override {
    bleDeviceConnected = true;
    Serial.println("BLE central connected");
  }

  void onDisconnect(BLEServer *server) override {
    bleDeviceConnected = false;
    Serial.println("BLE central disconnected");
  }
};

//----------------- Arduino setup / loop -----------------

void setup() {
  startupMs = millis();

  Serial.begin(115200);
  unsigned long serialWaitStart = millis();
  while (!Serial && (millis() - serialWaitStart < 2000)) {
    delay(10);
  }

  Serial.println("MR60BHA2 heart/breath monitor with BLE sensor stream");

  setupBLE();

  // mmWave radar serial
  mmWaveSerial.begin(115200);
  mmWave.begin(&mmWaveSerial);

  // NeoPixel init
  pixels.begin();
  pixels.clear();
  pixels.setBrightness(16);
  pixels.show();

  Serial.println("BLE device name: " BLE_DEVICE_NAME);
  Serial.println("Notify rate: 10 Hz, packet size: 20 bytes");
}

void loop() {
  updateSensorData();
  updateHeartbeatLedFromBPM();
  handleBleConnectionState();
  sendBlePacketIfDue();
  sendDebugDataIfDue();
}

//========================================================
// BLE transport
//========================================================

void setupBLE() {
  BLEDevice::init(BLE_DEVICE_NAME);
  BLEDevice::setMTU(23);  // Default ATT MTU gives exactly 20 bytes payload.

  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new SensorServerCallbacks());
  bleServer->advertiseOnDisconnect(true);

  BLEService *sensorService = bleServer->createService(BLE_SERVICE_UUID);

  sensorCharacteristic = sensorService->createCharacteristic(
    BLE_SENSOR_CHARACTERISTIC_UUID,
    BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY
  );
  sensorCharacteristic->addDescriptor(new BLE2902());

  uint8_t packet[BLE_PACKET_SIZE];
  fillSensorPacket(packet, millis());
  sensorCharacteristic->setValue(packet, BLE_PACKET_SIZE);

  sensorService->start();

  BLEAdvertising *advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(BLE_SERVICE_UUID);
  advertising->setScanResponse(true);
  advertising->setMinPreferred(0x18);  // 30 ms preferred connection interval
  advertising->setMaxPreferred(0x28);  // 50 ms preferred connection interval

  BLEDevice::startAdvertising();
}

void handleBleConnectionState() {
  if (!bleDeviceConnected && bleWasConnected) {
    bleWasConnected = false;
    Serial.println("BLE advertising");
  }

  if (bleDeviceConnected && !bleWasConnected) {
    bleWasConnected = true;
  }
}

void sendBlePacketIfDue() {
  unsigned long now = millis();
  if (now - lastBleNotifyMs < BLE_NOTIFY_INTERVAL_MS) {
    return;
  }
  lastBleNotifyMs = now;

  uint8_t packet[BLE_PACKET_SIZE];
  fillSensorPacket(packet, now);

  if (sensorCharacteristic == nullptr) {
    return;
  }

  sensorCharacteristic->setValue(packet, BLE_PACKET_SIZE);

  if (bleDeviceConnected) {
    sensorCharacteristic->notify();
  }

  bleSequence++;
}

void fillSensorPacket(uint8_t *packet, unsigned long now) {
  packet[0] = BLE_PACKET_VERSION;
  packet[1] = sensorFlags(now);
  putU16LE(packet, 2, bleSequence);
  putU32LE(packet, 4, (uint32_t)now);
  putI16LE(packet, 8, encodeScaled(heartRateBPM, hasHeartRate, 10.0f));
  putI16LE(packet, 10, encodeScaled(breathRateBPM_lib, hasBreathRate, 10.0f));
  putI16LE(packet, 12, encodeScaled(distanceCentimeters, hasDistance, 10.0f));
  putI16LE(packet, 14, encodeScaled(heartPhase, hasHeartPhase, 100.0f));
  putI16LE(packet, 16, encodeScaled(breathPhase, hasBreathPhase, 100.0f));
  putI16LE(packet, 18, encodeScaled(totalPhase, hasTotalPhase, 100.0f));
}

uint8_t sensorFlags(unsigned long now) {
  uint8_t flags = 0;

  if (dataIsFresh(now)) {
    flags |= FLAG_VALID;
  }
  if (sensorIsStabilized(now)) {
    flags |= FLAG_STABILIZED;
  }
  if (personIsPresent()) {
    flags |= FLAG_PRESENT;
  }

  return flags;
}

bool dataIsFresh(unsigned long now) {
  return lastSensorUpdateMs != 0 && (now - lastSensorUpdateMs <= DATA_TIMEOUT_MS);
}

bool sensorIsStabilized(unsigned long now) {
  return now - startupMs >= STABILIZATION_TIME_MS;
}

bool personIsPresent() {
  return (hasHeartRate && heartRateBPM > 0.0f) ||
         (hasBreathRate && breathRateBPM_lib > 0.0f) ||
         (hasDistance && distanceCentimeters > 0.0f);
}

//========================================================
// Sensor update
//========================================================

void updateSensorData() {
  bool dataUpdated = false;

  // Short timeout keeps BLE timing regular while still polling fresh radar data.
  if (!mmWave.update(20)) {
    return;
  }

  float t, b, h;
  if (mmWave.getHeartBreathPhases(t, b, h)) {
    totalPhase = t;
    breathPhase = b;
    heartPhase = h;
    hasTotalPhase = true;
    hasBreathPhase = true;
    hasHeartPhase = true;
    dataUpdated = true;

    updateBreathRateFromPhase();
  }

  float tmp;
  if (mmWave.getBreathRate(tmp)) {
    breathRateBPM_lib = tmp;
    hasBreathRate = true;
    dataUpdated = true;
  }
  if (mmWave.getHeartRate(tmp)) {
    heartRateBPM = tmp;
    hasHeartRate = true;
    dataUpdated = true;
  }

  float dist;
  if (mmWave.getDistance(dist)) {
    distanceCentimeters = dist;
    hasDistance = true;
    dataUpdated = true;
  }

  if (dataUpdated) {
    lastSensorUpdateMs = millis();
  }
}

void sendDebugDataIfDue() {
  if (!Serial) {
    return;
  }

  unsigned long now = millis();
  if (now - lastDebugMs < DEBUG_INTERVAL_MS) {
    return;
  }
  lastDebugMs = now;

  Serial.print("seq=");
  Serial.print(bleSequence);
  Serial.print(",flags=");
  Serial.print(sensorFlags(now));
  Serial.print(",hr=");
  Serial.print(hasHeartRate ? heartRateBPM : NAN);
  Serial.print(",br=");
  Serial.print(hasBreathRate ? breathRateBPM_lib : NAN);
  Serial.print(",calc_br=");
  Serial.print(breathRatePhaseBPM);
  Serial.print(",dist_cm=");
  Serial.print(hasDistance ? distanceCentimeters : NAN);
  Serial.print(",heart_phase=");
  Serial.print(hasHeartPhase ? heartPhase : NAN);
  Serial.print(",breath_phase=");
  Serial.print(hasBreathPhase ? breathPhase : NAN);
  Serial.print(",total_phase=");
  Serial.println(hasTotalPhase ? totalPhase : NAN);
}

//========================================================
// Heartbeat LED helpers
//========================================================

void setHeartbeatLed(bool on) {
  if (heartLedOn == on) {
    return;
  }

  heartLedOn = on;
  if (on) {
    pixels.setPixelColor(0, pixels.Color(255, 80, 0));
  } else {
    pixels.setPixelColor(0, 0, 0, 0);
  }
  pixels.show();
}

void updateHeartbeatLedFromBPM() {
  unsigned long now = millis();

  if (!hasHeartRate || heartRateBPM <= 0.0f) {
    heartLedEnabled = false;
    setHeartbeatLed(false);
    return;
  }

  if (heartLedOn && now >= heartLedOffMs) {
    setHeartbeatLed(false);
  }

  float intervalMsF = 60000.0f / heartRateBPM;
  unsigned long intervalMs = (unsigned long)(intervalMsF + 0.5f);
  if (intervalMs < 250) {
    intervalMs = 250;
  }

  if (!heartLedEnabled) {
    heartLedEnabled = true;
    nextHeartBlinkMs = now;
  }

  if (!heartLedOn && now >= nextHeartBlinkMs) {
    setHeartbeatLed(true);
    heartLedOffMs = now + HEART_LED_FLASH_MS;
    nextHeartBlinkMs = now + intervalMs;
  }
}

//========================================================
// Breath BPM helpers
//========================================================

void addBreathInterval(unsigned long intervalMs) {
  if (breathIntervalCount < MAX_BREATH_INTERVALS) {
    breathIntervals[breathIntervalCount++] = intervalMs;
  } else {
    for (int i = 1; i < MAX_BREATH_INTERVALS; i++) {
      breathIntervals[i - 1] = breathIntervals[i];
    }
    breathIntervals[MAX_BREATH_INTERVALS - 1] = intervalMs;
  }

  unsigned long sum = 0;
  for (int i = 0; i < breathIntervalCount; i++) {
    sum += breathIntervals[i];
  }

  if (sum > 0) {
    float avgIntervalMs = (float)sum / (float)breathIntervalCount;
    breathRatePhaseBPM = 60000.0f / avgIntervalMs;
  }
}

void updateBreathRateFromPhase() {
  unsigned long nowMs = millis();

  bool risingThroughThreshold =
    (lastBreathPhase < BREATH_PHASE_THRESHOLD &&
     breathPhase >= BREATH_PHASE_THRESHOLD);

  if (risingThroughThreshold) {
    if (lastBreathEventTime != 0) {
      unsigned long intervalMs = nowMs - lastBreathEventTime;

      if (intervalMs > 500 && intervalMs < 20000) {
        addBreathInterval(intervalMs);
      }
    }
    lastBreathEventTime = nowMs;
  }

  lastBreathPhase = breathPhase;
}

//========================================================
// Packet helpers
//========================================================

int16_t encodeScaled(float value, bool hasValue, float scale) {
  if (!hasValue || isnan(value) || isinf(value)) {
    return BLE_MISSING_VALUE;
  }

  long scaled = lroundf(value * scale);
  if (scaled < -32767L) {
    return -32767;
  }
  if (scaled > 32767L) {
    return 32767;
  }

  return (int16_t)scaled;
}

void putU16LE(uint8_t *packet, size_t offset, uint16_t value) {
  packet[offset] = (uint8_t)(value & 0xFF);
  packet[offset + 1] = (uint8_t)((value >> 8) & 0xFF);
}

void putU32LE(uint8_t *packet, size_t offset, uint32_t value) {
  packet[offset] = (uint8_t)(value & 0xFF);
  packet[offset + 1] = (uint8_t)((value >> 8) & 0xFF);
  packet[offset + 2] = (uint8_t)((value >> 16) & 0xFF);
  packet[offset + 3] = (uint8_t)((value >> 24) & 0xFF);
}

void putI16LE(uint8_t *packet, size_t offset, int16_t value) {
  putU16LE(packet, offset, (uint16_t)value);
}
