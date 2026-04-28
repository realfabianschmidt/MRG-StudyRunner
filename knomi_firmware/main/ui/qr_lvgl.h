#pragma once
#include "lvgl.h"

// Draw a QR code for the given text onto an LVGL canvas.
// The canvas must already be created with lv_canvas_create().
// size: pixel side length of the rendered QR (e.g. 200 for 200×200).
void qr_lvgl_draw(lv_obj_t* canvas, const char* text, int size);
