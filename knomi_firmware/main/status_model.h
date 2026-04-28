#pragma once
#include <string>

// Sensor connection states — maps to dashboard dot colours
enum class SensorState {
    UNKNOWN,    // grey  — not yet polled
    OK,         // green — connected & receiving data
    WARNING,    // yellow — degraded (low quality, partial data)
    ERROR,      // red   — disconnected / failed
    DISABLED,   // grey  — disabled in config
};

struct SensorStatus {
    SensorState state = SensorState::UNKNOWN;
    std::string status_text;   // "connected", "waiting", "failed", …
    std::string detail;        // human-readable: "HR: 72 bpm", "Bat: 85%", …
};

struct StudyStatus {
    bool active        = false;
    std::string study_id;
    std::string participant_id;
    std::string phase;         // "stimulus", "answer", "rest", …
    int client_count   = 0;    // connected iPads
};

struct StatusModel {
    bool          ok            = false;   // was the HTTP poll successful?
    SensorStatus  brainbit;
    SensorStatus  radar;
    SensorStatus  emg;
    SensorStatus  emotion;       // remote emotion worker
    SensorStatus  ipad;
    StudyStatus   study;
    std::string   pi_url;        // "http://192.168.1.100:3000"
    long long     polled_at_ms  = 0;
};
