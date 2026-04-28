#pragma once
#include "lvgl.h"
#include "../status_model.h"

lv_obj_t* screen_detail_create(void);
void      screen_detail_update(const StatusModel& model);
void      screen_detail_set_sensor(const char* sensor_key);
