/* Home screen — 480×480 round display
 *
 * Layout (smartwatch style):
 *
 *        [Study label — top centre]
 *     [Participant ID / "Idle" — centre]
 *
 *   Sensor status dots — bottom arc (5 dots):
 *     BrainBit  Radar  EMG  Emotion  iPad
 *
 *   Bottom-right: [QR icon] → network screen
 */
#include "screen_home.h"
#include "screen_detail.h"
#include "ui.h"
#include "esp_log.h"
#include <cstring>
#include <cstdio>

static const char* TAG = "SCR_HOME";

// Dot layout — 5 evenly spaced on a 340px diameter circle arc at y=340
// Centre of display: (240, 240).  Dots sit at y=355 along a horizontal line.
static const int DOT_COUNT   = 5;
static const int DOT_DIAMETER = 52;
static const int DOT_Y        = 355;
static const int DOT_SPACING  = 76;   // centre-to-centre
static const int DOT_X_START  = 240 - (DOT_COUNT - 1) * DOT_SPACING / 2;

static const char* DOT_KEYS[DOT_COUNT]   = {"brainbit", "radar", "emg", "emotion", "ipad"};
static const char* DOT_LABELS[DOT_COUNT] = {"Brain", "Radar", "EMG", "Emotion", "iPad"};

// Widget references
static lv_obj_t* g_scr           = nullptr;
static lv_obj_t* g_study_label   = nullptr;
static lv_obj_t* g_pid_label     = nullptr;
static lv_obj_t* g_dots[DOT_COUNT];
static lv_obj_t* g_dot_labels[DOT_COUNT];

// ---------------------------------------------------------------------------
// Touch callback for dots → detail screen
// ---------------------------------------------------------------------------
static void dot_clicked(lv_event_t* e) {
    const char* key = static_cast<const char*>(lv_event_get_user_data(e));
    ui_show_detail(key);
}

// Touch callback for QR icon → network screen
static void qr_icon_clicked(lv_event_t* e) {
    ui_show_network();
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------
lv_obj_t* screen_home_create(void) {
    g_scr = lv_obj_create(nullptr);
    lv_obj_set_size(g_scr, 480, 480);
    lv_obj_set_style_bg_color(g_scr, lv_color_black(), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(g_scr, LV_OPA_COVER, LV_PART_MAIN);

    // Study status label — top centre
    g_study_label = lv_label_create(g_scr);
    lv_obj_set_style_text_color(g_study_label, lv_color_make(0x9C, 0xA3, 0xAF), LV_PART_MAIN);
    lv_obj_set_style_text_font(g_study_label, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_label_set_text(g_study_label, "IDLE");
    lv_obj_align(g_study_label, LV_ALIGN_TOP_MID, 0, 70);

    // Participant ID / study name — centre
    g_pid_label = lv_label_create(g_scr);
    lv_obj_set_style_text_color(g_pid_label, lv_color_white(), LV_PART_MAIN);
    lv_obj_set_style_text_font(g_pid_label, &lv_font_montserrat_28, LV_PART_MAIN);
    lv_label_set_long_mode(g_pid_label, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_set_width(g_pid_label, 320);
    lv_label_set_text(g_pid_label, "—");
    lv_obj_align(g_pid_label, LV_ALIGN_CENTER, 0, -20);

    // Sensor dots
    for (int i = 0; i < DOT_COUNT; i++) {
        int cx = DOT_X_START + i * DOT_SPACING;

        lv_obj_t* dot = lv_obj_create(g_scr);
        lv_obj_set_size(dot, DOT_DIAMETER, DOT_DIAMETER);
        lv_obj_set_pos(dot, cx - DOT_DIAMETER / 2, DOT_Y - DOT_DIAMETER / 2);
        lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, LV_PART_MAIN);
        lv_obj_set_style_bg_color(dot, lv_color_make(0x4B, 0x55, 0x63), LV_PART_MAIN);
        lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_border_width(dot, 0, LV_PART_MAIN);
        lv_obj_add_flag(dot, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_event_cb(dot, dot_clicked, LV_EVENT_CLICKED,
                            const_cast<char*>(DOT_KEYS[i]));
        g_dots[i] = dot;

        lv_obj_t* lbl = lv_label_create(g_scr);
        lv_obj_set_style_text_color(lbl, lv_color_make(0x9C, 0xA3, 0xAF), LV_PART_MAIN);
        lv_obj_set_style_text_font(lbl, &lv_font_montserrat_12, LV_PART_MAIN);
        lv_label_set_text(lbl, DOT_LABELS[i]);
        lv_obj_align_to(lbl, dot, LV_ALIGN_OUT_BOTTOM_MID, 0, 4);
        g_dot_labels[i] = lbl;
    }

    // QR / network icon — bottom-right corner
    lv_obj_t* qr_btn = lv_obj_create(g_scr);
    lv_obj_set_size(qr_btn, 48, 48);
    lv_obj_align(qr_btn, LV_ALIGN_BOTTOM_RIGHT, -30, -30);
    lv_obj_set_style_radius(qr_btn, 8, LV_PART_MAIN);
    lv_obj_set_style_bg_color(qr_btn, lv_color_make(0x1F, 0x2A, 0x37), LV_PART_MAIN);
    lv_obj_set_style_border_width(qr_btn, 0, LV_PART_MAIN);
    lv_obj_add_flag(qr_btn, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(qr_btn, qr_icon_clicked, LV_EVENT_CLICKED, nullptr);

    lv_obj_t* qr_lbl = lv_label_create(qr_btn);
    lv_obj_set_style_text_color(qr_lbl, lv_color_white(), LV_PART_MAIN);
    lv_label_set_text(qr_lbl, LV_SYMBOL_HOME);  // placeholder; replace with QR symbol font
    lv_obj_center(qr_lbl);

    ESP_LOGI(TAG, "Home screen created");
    return g_scr;
}

// ---------------------------------------------------------------------------
// Update
// ---------------------------------------------------------------------------
void screen_home_update(const StatusModel& model) {
    if (!g_scr) return;

    // Study label
    if (model.study.active) {
        char buf[32];
        snprintf(buf, sizeof(buf), "STUDY ACTIVE");
        lv_label_set_text(g_study_label, buf);
        lv_obj_set_style_text_color(g_study_label, lv_color_make(0x22, 0xC5, 0x5E), LV_PART_MAIN);

        // Participant / phase
        char pid_buf[64];
        if (!model.study.participant_id.empty()) {
            snprintf(pid_buf, sizeof(pid_buf), "%s", model.study.participant_id.c_str());
        } else {
            snprintf(pid_buf, sizeof(pid_buf), "Running");
        }
        lv_label_set_text(g_pid_label, pid_buf);
    } else {
        lv_label_set_text(g_study_label, "IDLE");
        lv_obj_set_style_text_color(g_study_label, lv_color_make(0x9C, 0xA3, 0xAF), LV_PART_MAIN);
        lv_label_set_text(g_pid_label, "—");
    }

    // Sensor dot colours
    const SensorStatus* sensors[DOT_COUNT] = {
        &model.brainbit, &model.radar, &model.emg, &model.emotion, &model.ipad
    };
    for (int i = 0; i < DOT_COUNT; i++) {
        lv_color_t col = sensor_state_color(sensors[i]->state);
        lv_obj_set_style_bg_color(g_dots[i], col, LV_PART_MAIN);
    }
}
