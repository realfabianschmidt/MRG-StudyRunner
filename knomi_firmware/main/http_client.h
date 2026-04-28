#pragma once
#include "status_model.h"
#include <string>

// Fetch /api/admin/status from the Pi Flask server and parse into StatusModel.
StatusModel fetch_status(const char* base_url);

// POST /api/display/action to the Pi Flask server.
// target: "brainbit" | "radar" | "emg" | "emotion_worker"
// action: "restart" | "reconnect"
bool post_display_action(const char* base_url, const char* target, const char* action);
