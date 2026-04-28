#include "ui.h"
#include "screen_home.h"
#include "screen_detail.h"
#include "screen_network.h"
#include "esp_log.h"
#include <mutex>

static const char* TAG = "UI";

// Shared model protected by a mutex — written by poll task, read by LVGL task.
static StatusModel g_model;
static std::mutex  g_model_mutex;
static bool        g_model_dirty = false;

// Screen pointers
static lv_obj_t* g_scr_home    = nullptr;
static lv_obj_t* g_scr_detail  = nullptr;
static lv_obj_t* g_scr_network = nullptr;

// ---------------------------------------------------------------------------
// Colour mapping
// ---------------------------------------------------------------------------

lv_color_t sensor_state_color(SensorState state) {
    switch (state) {
        case SensorState::OK:       return lv_color_make(0x22, 0xC5, 0x5E);  // green
        case SensorState::WARNING:  return lv_color_make(0xFF, 0xC1, 0x07);  // amber
        case SensorState::ERROR:    return lv_color_make(0xEF, 0x44, 0x44);  // red
        case SensorState::DISABLED: return lv_color_make(0x6B, 0x72, 0x80);  // grey
        default:                    return lv_color_make(0x4B, 0x55, 0x63);  // dark grey
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

void ui_init(void) {
    // Create all screens once; they will be hidden/shown as needed.
    g_scr_home    = screen_home_create();
    g_scr_detail  = screen_detail_create();
    g_scr_network = screen_network_create();

    // Start on the home screen
    lv_scr_load(g_scr_home);
    ESP_LOGI(TAG, "UI initialised");
}

void ui_update(const StatusModel& model) {
    {
        std::lock_guard<std::mutex> lock(g_model_mutex);
        g_model = model;
        g_model_dirty = true;
    }
    // LVGL is not thread-safe: request a re-render via a timer callback
    // that fires on the LVGL task.
    lv_async_call([](void*) {
        StatusModel local;
        {
            std::lock_guard<std::mutex> lock(g_model_mutex);
            if (!g_model_dirty) return;
            local = g_model;
            g_model_dirty = false;
        }
        lv_obj_t* active = lv_scr_act();
        if (active == g_scr_home)    screen_home_update(local);
        if (active == g_scr_detail)  screen_detail_update(local);
        if (active == g_scr_network) screen_network_update(local);
    }, nullptr);
}

void ui_show_home(void) {
    lv_scr_load_anim(g_scr_home, LV_SCR_LOAD_ANIM_FADE_IN, 200, 0, false);
}

void ui_show_detail(const char* sensor_key) {
    screen_detail_set_sensor(sensor_key);
    lv_scr_load_anim(g_scr_detail, LV_SCR_LOAD_ANIM_MOVE_LEFT, 200, 0, false);
}

void ui_show_network(void) {
    lv_scr_load_anim(g_scr_network, LV_SCR_LOAD_ANIM_MOVE_LEFT, 200, 0, false);
}
