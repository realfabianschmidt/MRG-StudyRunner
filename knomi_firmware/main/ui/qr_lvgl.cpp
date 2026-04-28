#include "qr_lvgl.h"
#include "qrcodegen.h"
#include "esp_log.h"
#include <cstring>

static const char* TAG = "QR_LVGL";

// Static buffers for qrcodegen (version 1–10, enough for a short URL)
static uint8_t s_qr_data[qrcodegen_BUFFER_LEN_FOR_VERSION(10)];
static uint8_t s_tmp[qrcodegen_BUFFER_LEN_FOR_VERSION(10)];

// Canvas backing buffer — 200×200 RGBA8888 = 160 KB.
// Kept static to avoid heap fragmentation on the LVGL task stack.
static lv_color_t s_canvas_buf[200 * 200];

void qr_lvgl_draw(lv_obj_t* canvas, const char* text, int size) {
    if (!canvas || !text || size <= 0) return;

    bool ok = qrcodegen_encodeText(
        text, s_tmp, s_qr_data,
        qrcodegen_Ecc_LOW,
        qrcodegen_VERSION_MIN, 10,
        qrcodegen_Mask_AUTO, true
    );
    if (!ok) {
        ESP_LOGW(TAG, "QR encode failed for: %s", text);
        return;
    }

    int modules = qrcodegen_getSize(s_qr_data);
    if (modules <= 0) return;

    int quiet = 2;  // quiet zone in modules
    int total  = modules + 2 * quiet;
    // pixels per module, fitting into 'size' px
    int cell   = size / total;
    if (cell < 1) cell = 1;

    // Attach canvas buffer
    lv_canvas_set_buffer(canvas, s_canvas_buf, size, size, LV_IMG_CF_TRUE_COLOR);
    lv_canvas_fill_bg(canvas, lv_color_white(), LV_OPA_COVER);

    lv_draw_rect_dsc_t rect_dsc;
    lv_draw_rect_dsc_init(&rect_dsc);
    rect_dsc.bg_color   = lv_color_black();
    rect_dsc.bg_opa     = LV_OPA_COVER;
    rect_dsc.border_width = 0;
    rect_dsc.radius     = 0;

    int offset = quiet * cell;
    for (int y = 0; y < modules; y++) {
        for (int x = 0; x < modules; x++) {
            if (qrcodegen_getModule(s_qr_data, x, y)) {
                lv_area_t area = {
                    .x1 = (lv_coord_t)(offset + x * cell),
                    .y1 = (lv_coord_t)(offset + y * cell),
                    .x2 = (lv_coord_t)(offset + x * cell + cell - 1),
                    .y2 = (lv_coord_t)(offset + y * cell + cell - 1),
                };
                lv_canvas_draw_rect(canvas, area.x1, area.y1, cell, cell, &rect_dsc);
            }
        }
    }
    ESP_LOGI(TAG, "QR drawn: %d modules, cell=%dpx, total=%dpx", modules, cell, total * cell);
}
