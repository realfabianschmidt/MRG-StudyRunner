#include "http_client.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "cJSON.h"
#include <string>
#include <cstring>

static const char* TAG = "HTTP_CLIENT";

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

static std::string g_response_body;

static esp_err_t http_event_handler(esp_http_client_event_t* evt) {
    switch (evt->event_id) {
        case HTTP_EVENT_ON_DATA:
            if (evt->data_len > 0) {
                g_response_body.append(static_cast<const char*>(evt->data), evt->data_len);
            }
            break;
        case HTTP_EVENT_ON_FINISH:
            break;
        case HTTP_EVENT_DISCONNECTED:
            break;
        default:
            break;
    }
    return ESP_OK;
}

static SensorState state_from_string(const char* s) {
    if (!s) return SensorState::UNKNOWN;
    if (strcmp(s, "connected") == 0) return SensorState::OK;
    if (strcmp(s, "running")   == 0) return SensorState::OK;
    if (strcmp(s, "ready")     == 0) return SensorState::OK;
    if (strcmp(s, "waiting")   == 0) return SensorState::WARNING;
    if (strcmp(s, "no_presence") == 0) return SensorState::WARNING;
    if (strcmp(s, "disabled")  == 0) return SensorState::DISABLED;
    if (strcmp(s, "failed")    == 0) return SensorState::ERROR;
    if (strcmp(s, "stopped")   == 0) return SensorState::ERROR;
    return SensorState::UNKNOWN;
}

static SensorStatus parse_sensor(cJSON* obj) {
    SensorStatus s;
    if (!obj) return s;
    cJSON* status_j = cJSON_GetObjectItem(obj, "status");
    if (status_j && cJSON_IsString(status_j)) {
        s.status_text = status_j->valuestring;
        s.state = state_from_string(status_j->valuestring);
    }
    // Extract a short detail string from "latest" if present
    cJSON* latest = cJSON_GetObjectItem(obj, "latest");
    if (latest && cJSON_IsObject(latest)) {
        // Radar: heartRate + breathRate
        cJSON* hr = cJSON_GetObjectItem(latest, "heartRate");
        cJSON* br = cJSON_GetObjectItem(latest, "breathRate");
        if (hr && br) {
            char buf[32];
            snprintf(buf, sizeof(buf), "HR: %d  BR: %d",
                     (int)hr->valuedouble, (int)br->valuedouble);
            s.detail = buf;
        }
        // BrainBit: battery
        cJSON* bat = cJSON_GetObjectItem(latest, "battery");
        if (bat) {
            char buf[16];
            snprintf(buf, sizeof(buf), "Bat: %d%%", (int)bat->valuedouble);
            s.detail = buf;
        }
        // Emotion: emotion label
        cJSON* em = cJSON_GetObjectItem(latest, "analysis");
        if (em) {
            cJSON* emo = cJSON_GetObjectItem(em, "emotion");
            if (emo && cJSON_IsString(emo)) {
                s.detail = emo->valuestring;
            }
        }
    }
    return s;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

StatusModel fetch_status(const char* base_url) {
    StatusModel model;
    char url[128];
    snprintf(url, sizeof(url), "%s/api/admin/status", base_url);

    g_response_body.clear();
    esp_http_client_config_t config = {};
    config.url              = url;
    config.event_handler    = http_event_handler;
    config.timeout_ms       = 3000;

    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_err_t err = esp_http_client_perform(client);
    int status_code = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);

    if (err != ESP_OK || status_code != 200) {
        ESP_LOGW(TAG, "fetch_status failed: err=%d code=%d", err, status_code);
        return model;
    }

    cJSON* root = cJSON_Parse(g_response_body.c_str());
    if (!root) {
        ESP_LOGW(TAG, "JSON parse failed");
        return model;
    }

    model.ok = true;

    // pi_url (reconstruct from base_url)
    model.pi_url = base_url;

    // integrations
    cJSON* integrations = cJSON_GetObjectItem(root, "integrations");
    if (integrations) {
        model.brainbit = parse_sensor(cJSON_GetObjectItem(integrations, "brainbit"));
        model.radar    = parse_sensor(cJSON_GetObjectItem(integrations, "mini_radar"));
        // EMG is not yet in admin_status_service — graceful missing
        cJSON* emg_j   = cJSON_GetObjectItem(integrations, "emg");
        model.emg      = emg_j ? parse_sensor(emg_j) : SensorStatus{SensorState::DISABLED, "disabled", ""};
        model.emotion  = parse_sensor(cJSON_GetObjectItem(integrations, "camera_emotion"));
    }

    // study_clients → ipad status
    cJSON* clients = cJSON_GetObjectItem(root, "study_clients");
    if (clients && cJSON_IsArray(clients)) {
        int count = cJSON_GetArraySize(clients);
        model.ipad.state       = count > 0 ? SensorState::OK : SensorState::WARNING;
        model.ipad.status_text = count > 0 ? "connected" : "waiting";
        char buf[16];
        snprintf(buf, sizeof(buf), "%d client%s", count, count == 1 ? "" : "s");
        model.ipad.detail = buf;
        model.study.client_count = count;
    }

    cJSON_Delete(root);
    return model;
}

bool post_display_action(const char* base_url, const char* target, const char* action) {
    char url[128];
    snprintf(url, sizeof(url), "%s/api/display/action", base_url);

    // Build JSON body
    cJSON* body = cJSON_CreateObject();
    cJSON_AddStringToObject(body, "target", target);
    cJSON_AddStringToObject(body, "action", action);
    char* body_str = cJSON_PrintUnformatted(body);
    cJSON_Delete(body);

    g_response_body.clear();
    esp_http_client_config_t config = {};
    config.url           = url;
    config.method        = HTTP_METHOD_POST;
    config.event_handler = http_event_handler;
    config.timeout_ms    = 3000;

    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, body_str, (int)strlen(body_str));

    esp_err_t err = esp_http_client_perform(client);
    int code = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    free(body_str);

    return (err == ESP_OK && code == 200);
}
