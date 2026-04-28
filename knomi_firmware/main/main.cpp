/* MRG Study Runner — KNOMI 2 Display Firmware
 *
 * Hardware: BTT KNOMI 2 (ESP32-S3, 2.1" round 480×480 capacitive touch, WiFi)
 * Framework: ESP-IDF + LVGL
 *
 * Architecture:
 *   - WiFi connects to study network at boot
 *   - Poll task: GET /api/admin/status from Pi Flask server every 2s
 *   - LVGL task: renders home/detail/network screens, handles touch
 *   - Action: tap sensor dot → detail page → [RESTART] → POST /api/display/action
 *   - Network page: shows Pi URL + QR code for easy iPad/Mac connection
 */

#include <cstring>
#include <cstdio>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"

#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"

#include "lvgl.h"

#include "http_client.h"
#include "ui/ui.h"
#include "status_model.h"

// ---------------------------------------------------------------------------
// Config (from sdkconfig.defaults — edit before flashing)
// ---------------------------------------------------------------------------
#ifndef CONFIG_STUDY_RUNNER_WIFI_SSID
#define CONFIG_STUDY_RUNNER_WIFI_SSID     "StudyNet"
#endif
#ifndef CONFIG_STUDY_RUNNER_WIFI_PASSWORD
#define CONFIG_STUDY_RUNNER_WIFI_PASSWORD ""
#endif
#ifndef CONFIG_STUDY_RUNNER_PI_HOST
#define CONFIG_STUDY_RUNNER_PI_HOST       "192.168.1.100"
#endif
#ifndef CONFIG_STUDY_RUNNER_PI_PORT
#define CONFIG_STUDY_RUNNER_PI_PORT       3000
#endif

static const char* TAG = "MAIN";

// Global Pi base URL — accessible from screen_detail.cpp
char g_pi_base_url[64];

// ---------------------------------------------------------------------------
// WiFi event handling
// ---------------------------------------------------------------------------
static EventGroupHandle_t s_wifi_event_group;
static const int WIFI_CONNECTED_BIT = BIT0;
static const int WIFI_FAIL_BIT      = BIT1;
static int s_retry_count = 0;
static const int WIFI_MAX_RETRIES = 10;

static void wifi_event_handler(void* arg, esp_event_base_t base,
                                int32_t event_id, void* event_data) {
    if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_retry_count < WIFI_MAX_RETRIES) {
            esp_wifi_connect();
            s_retry_count++;
            ESP_LOGI(TAG, "WiFi retry %d/%d", s_retry_count, WIFI_MAX_RETRIES);
        } else {
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }
    } else if (base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        s_retry_count = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        ip_event_got_ip_t* event = (ip_event_got_ip_t*)event_data;
        ESP_LOGI(TAG, "WiFi connected — IP: " IPSTR, IP2STR(&event->ip_info.ip));
    }
}

static bool wifi_init_sta(void) {
    s_wifi_event_group = xEventGroupCreate();
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);

    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                        wifi_event_handler, nullptr, nullptr);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                        wifi_event_handler, nullptr, nullptr);

    wifi_config_t wifi_config = {};
    strncpy((char*)wifi_config.sta.ssid,     CONFIG_STUDY_RUNNER_WIFI_SSID,
            sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char*)wifi_config.sta.password, CONFIG_STUDY_RUNNER_WIFI_PASSWORD,
            sizeof(wifi_config.sta.password) - 1);
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_start();

    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_event_group,
        WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
        pdFALSE, pdFALSE,
        pdMS_TO_TICKS(30000)
    );

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "WiFi connected to '%s'", CONFIG_STUDY_RUNNER_WIFI_SSID);
        return true;
    }
    ESP_LOGW(TAG, "WiFi failed — running in offline mode");
    return false;
}

// ---------------------------------------------------------------------------
// Poll task — runs on Core 0, polls Pi every 2s
// ---------------------------------------------------------------------------
static void poll_task(void* pvParams) {
    while (true) {
        StatusModel model = fetch_status(g_pi_base_url);
        if (!model.ok) {
            ESP_LOGW(TAG, "Pi unreachable");
        }
        ui_update(model);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

// ---------------------------------------------------------------------------
// LVGL task — runs on Core 1 (dedicated)
// ---------------------------------------------------------------------------
static void lvgl_task(void* pvParams) {
    while (true) {
        lv_timer_handler();
        vTaskDelay(pdMS_TO_TICKS(5));  // ~200 Hz LVGL tick
    }
}

// ---------------------------------------------------------------------------
// App entry point
// ---------------------------------------------------------------------------
extern "C" void app_main(void) {
    ESP_LOGI(TAG, "MRG Study Runner — KNOMI 2 booting");

    // NVS flash (required for WiFi)
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    // Build Pi base URL
    snprintf(g_pi_base_url, sizeof(g_pi_base_url),
             "http://%s:%d", CONFIG_STUDY_RUNNER_PI_HOST, CONFIG_STUDY_RUNNER_PI_PORT);
    ESP_LOGI(TAG, "Pi URL: %s", g_pi_base_url);

    // WiFi
    wifi_init_sta();

    // LVGL — display + touch driver init (board-specific; KNOMI 2 uses ST7701 + CST816S)
    // The KNOMI 2 BSP handles lv_init(), display registration, and touch input.
    // Include the BSP component in your ESP-IDF project or initialise manually below.
    lv_init();
    // bsp_display_start();   // <- uncomment when using BTT KNOMI 2 BSP component
    // bsp_touch_start();     // <- uncomment when using BTT KNOMI 2 BSP component

    // UI
    ui_init();

    // Start LVGL task on Core 1 (dedicated GPU/display core)
    xTaskCreatePinnedToCore(lvgl_task, "lvgl", 8192, nullptr, 5, nullptr, 1);

    // Start poll task on Core 0
    xTaskCreatePinnedToCore(poll_task, "poll", 8192, nullptr, 4, nullptr, 0);

    ESP_LOGI(TAG, "Tasks started");
    // app_main returns — FreeRTOS scheduler keeps tasks running
}
