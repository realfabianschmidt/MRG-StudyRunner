#pragma once
#include "lvgl.h"
#include "../status_model.h"

// Initialise all screens and register touch/swipe handlers.
void ui_init(void);

// Thread-safe update — call from the polling task.
// Queues a re-render on the LVGL task.
void ui_update(const StatusModel& model);

// Navigate to a named screen (called by touch handlers).
void ui_show_home(void);
void ui_show_detail(const char* sensor_key);  // "brainbit"|"radar"|"emg"|"emotion"|"ipad"
void ui_show_network(void);

// Colour helpers
lv_color_t sensor_state_color(SensorState state);
