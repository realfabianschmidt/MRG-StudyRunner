/* Network screen — shows Pi URL large + QR code for easy connection.
 *
 * Layout:
 *   ← back button (top-left)
 *   "Connect to Study Runner" — top
 *   QR code — centre (200×200 px)
 *   URL text — below QR
 */
#include "screen_network.h"
#include "qr_lvgl.h"
#include "ui.h"
#include "esp_log.h"
#include <cstring>

static const char* TAG = "SCR_NETWORK";

static lv_obj_t* g_scr       = nullptr;
static lv_obj_t* g_url_label = nullptr;
static lv_obj_t* g_qr_canvas = nullptr;

static void back_clicked(lv_event_t*) {
    ui_show_home();
}

lv_obj_t* screen_network_create(void) {
    g_scr = lv_obj_create(nullptr);
    lv_obj_set_size(g_scr, 480, 480);
    lv_obj_set_style_bg_color(g_scr, lv_color_black(), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(g_scr, LV_OPA_COVER, LV_PART_MAIN);

    // Back button
    lv_obj_t* back_btn = lv_btn_create(g_scr);
    lv_obj_set_size(back_btn, 60, 40);
    lv_obj_align(back_btn, LV_ALIGN_TOP_LEFT, 20, 20);
    lv_obj_set_style_bg_color(back_btn, lv_color_make(0x1F, 0x2A, 0x37), LV_PART_MAIN);
    lv_obj_set_style_border_width(back_btn, 0, LV_PART_MAIN);
    lv_obj_add_event_cb(back_btn, back_clicked, LV_EVENT_CLICKED, nullptr);
    lv_obj_t* back_lbl = lv_label_create(back_btn);
    lv_label_set_text(back_lbl, LV_SYMBOL_LEFT);
    lv_obj_center(back_lbl);

    // Instruction label
    lv_obj_t* instr = lv_label_create(g_scr);
    lv_obj_set_style_text_color(instr, lv_color_make(0x9C, 0xA3, 0xAF), LV_PART_MAIN);
    lv_obj_set_style_text_font(instr, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_label_set_text(instr, "Connect to Study Runner");
    lv_obj_align(instr, LV_ALIGN_TOP_MID, 0, 75);

    // QR canvas placeholder — will be redrawn in update()
    g_qr_canvas = lv_canvas_create(g_scr);
    lv_obj_set_size(g_qr_canvas, 200, 200);
    lv_obj_align(g_qr_canvas, LV_ALIGN_CENTER, 0, -20);

    // URL label below QR
    g_url_label = lv_label_create(g_scr);
    lv_obj_set_style_text_color(g_url_label, lv_color_white(), LV_PART_MAIN);
    lv_obj_set_style_text_font(g_url_label, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_label_set_text(g_url_label, "—");
    lv_obj_align(g_url_label, LV_ALIGN_CENTER, 0, 130);

    ESP_LOGI(TAG, "Network screen created");
    return g_scr;
}

void screen_network_update(const StatusModel& model) {
    if (!g_scr) return;

    const std::string& url = model.pi_url;
    lv_label_set_text(g_url_label, url.empty() ? "—" : url.c_str());

    if (!url.empty()) {
        qr_lvgl_draw(g_qr_canvas, url.c_str(), 200);
    }
}
