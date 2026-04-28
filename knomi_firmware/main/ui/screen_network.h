#pragma once
#include "lvgl.h"
#include "../status_model.h"

lv_obj_t* screen_network_create(void);
void      screen_network_update(const StatusModel& model);
