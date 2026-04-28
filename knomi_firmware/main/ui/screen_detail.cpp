/* Detail screen — shown when user taps a status dot on home screen.
 *
 * Layout:
 *   ← back button (top-left)
 *   [Sensor name — top centre]
 *   [Big status colour dot — centre]
 *   [Status text]
 *   [Detail text (HR/BR, battery, …)]
 *   [RESTART / RECONNECT button — bottom]
 */
#include "screen_detail.h"
#include "ui.h"
#include "http_client.h"
#include "esp_log.h"
#include <cstring>
#include <cstdio>

// Must be set before we load the screen (from sdkconfig / NVS at runtime)
extern const char* g_pi_base_url;

static const char* TAG = "SCR_DETAIL";

static lv_obj_t* g_scr          = nullptr;
static lv_obj_t* g_title_label  = nullptr;
static lv_obj_t* g_dot          = nullptr;
static lv_obj_t* g_status_label = nullptr;
static lv_obj_t* g_detail_label = nullptr;
static lv_obj_t* g_action_btn   = nullptr;
static lv_obj_t* g_action_label = nullptr;

static char g_sensor_key[32] = "brainbit";

// Human-readable names for each sensor key
static const char* sensor_title(const char* key) {
    if (strcmp(key, "brainbit") == 0) return "BrainBit EEG";
    if (strcmp(key, "radar")    == 0) return "Mini Radar";
    if (strcmp(key, "emg")      == 0) return "EMG";
    if (strcmp(key, "emotion")  == 0) return "Emotion Worker";
    if (strcmp(key, "ipad")     == 0) return "iPad / Clients";
    return key;
}

// Action verb for the action button
static const char* sensor_action(const char* key) {
    if (strcmp(key, "ipad") == 0) return nullptr;  // no button for iPad
    if (strcmp(key, "emotion") == 0) return "RECONNECT";
    return "RESTART";
}

// The action string sent to /api/display/action
static const char* sensor_action_verb(const char* key) {
    if (strcmp(key, "emotion") == 0) return "reconnect";
    return "restart";
}

// ---------------------------------------------------------------------------
// Touch callbacks
// ---------------------------------------------------------------------------
static void back_clicked(lv_event_t*) {
    ui_show_home();
}

static void action_clicked(lv_event_t*) {
    const char* action = sensor_action_verb(g_sensor_key);
    ESP_LOGI(TAG, "Action: %s %s", g_sensor_key, action);
    post_display_action(g_pi_base_url, g_sensor_key, action);
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------
lv_obj_t* screen_detail_create(void) {
    g_scr = lv_obj_create(nullptr);
    lv_obj_set_size(g_scr, 480, 480);
    lv_obj_set_style_bg_color(g_scr, lv_color_black(), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(g_scr, LV_OPA_COVER, LV_PART_MAIN);

    // Back button ←
    lv_obj_t* back_btn = lv_btn_create(g_scr);
    lv_obj_set_size(back_btn, 60, 40);
    lv_obj_align(back_btn, LV_ALIGN_TOP_LEFT, 20, 20);
    lv_obj_set_style_bg_color(back_btn, lv_color_make(0x1F, 0x2A, 0x37), LV_PART_MAIN);
    lv_obj_set_style_border_width(back_btn, 0, LV_PART_MAIN);
    lv_obj_add_event_cb(back_btn, back_clicked, LV_EVENT_CLICKED, nullptr);
    lv_obj_t* back_lbl = lv_label_create(back_btn);
    lv_label_set_text(back_lbl, LV_SYMBOL_LEFT);
    lv_obj_center(back_lbl);

    // Title
    g_title_label = lv_label_create(g_scr);
    lv_obj_set_style_text_color(g_title_label, lv_color_white(), LV_PART_MAIN);
    lv_obj_set_style_text_font(g_title_label, &lv_font_montserrat_20, LV_PART_MAIN);
    lv_obj_align(g_title_label, LV_ALIGN_TOP_MID, 0, 30);

    // Status dot
    g_dot = lv_obj_create(g_scr);
    lv_obj_set_size(g_dot, 80, 80);
    lv_obj_align(g_dot, LV_ALIGN_CENTER, 0, -60);
    lv_obj_set_style_radius(g_dot, LV_RADIUS_CIRCLE, LV_PART_MAIN);
    lv_obj_set_style_bg_color(g_dot, lv_color_make(0x4B, 0x55, 0x63), LV_PART_MAIN);
    lv_obj_set_style_border_width(g_dot, 0, LV_PART_MAIN);

    // Status text
    g_status_label = lv_label_create(g_scr);
    lv_obj_set_style_text_color(g_status_label, lv_color_make(0x9C, 0xA3, 0xAF), LV_PART_MAIN);
    lv_obj_set_style_text_font(g_status_label, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_align(g_status_label, LV_ALIGN_CENTER, 0, 20);

    // Detail text
    g_detail_label = lv_label_create(g_scr);
    lv_obj_set_style_text_color(g_detail_label, lv_color_white(), LV_PART_MAIN);
    lv_obj_set_style_text_font(g_detail_label, &lv_font_montserrat_24, LV_PART_MAIN);
    lv_obj_align(g_detail_label, LV_ALIGN_CENTER, 0, 55);

    // Action button
    g_action_btn = lv_btn_create(g_scr);
    lv_obj_set_size(g_action_btn, 180, 52);
    lv_obj_align(g_action_btn, LV_ALIGN_BOTTOM_MID, 0, -40);
    lv_obj_set_style_bg_color(g_action_btn, lv_color_make(0x1D, 0x4E, 0xD8), LV_PART_MAIN);
    lv_obj_set_style_radius(g_action_btn, 26, LV_PART_MAIN);
    lv_obj_set_style_border_width(g_action_btn, 0, LV_PART_MAIN);
    lv_obj_add_event_cb(g_action_btn, action_clicked, LV_EVENT_CLICKED, nullptr);

    g_action_label = lv_label_create(g_action_btn);
    lv_obj_set_style_text_font(g_action_label, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_label_set_text(g_action_label, "RESTART");
    lv_obj_center(g_action_label);

    ESP_LOGI(TAG, "Detail screen created");
    return g_scr;
}

// ---------------------------------------------------------------------------
// Set active sensor key (call before loading screen)
// ---------------------------------------------------------------------------
void screen_detail_set_sensor(const char* sensor_key) {
    strncpy(g_sensor_key, sensor_key, sizeof(g_sensor_key) - 1);

    lv_label_set_text(g_title_label, sensor_title(sensor_key));

    const char* action = sensor_action(sensor_key);
    if (action) {
        lv_obj_remove_flag(g_action_btn, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(g_action_label, action);
    } else {
        lv_obj_add_flag(g_action_btn, LV_OBJ_FLAG_HIDDEN);
    }
}

// ---------------------------------------------------------------------------
// Update
// ---------------------------------------------------------------------------
void screen_detail_update(const StatusModel& model) {
    if (!g_scr) return;

    const SensorStatus* sensor = nullptr;
    if (strcmp(g_sensor_key, "brainbit") == 0) sensor = &model.brainbit;
    else if (strcmp(g_sensor_key, "radar")   == 0) sensor = &model.radar;
    else if (strcmp(g_sensor_key, "emg")     == 0) sensor = &model.emg;
    else if (strcmp(g_sensor_key, "emotion") == 0) sensor = &model.emotion;
    else if (strcmp(g_sensor_key, "ipad")    == 0) sensor = &model.ipad;

    if (!sensor) return;

    lv_obj_set_style_bg_color(g_dot, sensor_state_color(sensor->state), LV_PART_MAIN);
    lv_label_set_text(g_status_label, sensor->status_text.c_str());
    lv_label_set_text(g_detail_label, sensor->detail.empty() ? "—" : sensor->detail.c_str());
}
