#pragma once
#include "lvgl.h"
#include "../status_model.h"

lv_obj_t* screen_home_create(void);
void      screen_home_update(const StatusModel& model);
