import json
import time

from flask import Flask, current_app, jsonify, request, send_from_directory

from .admin_status_service import build_admin_status
from .config_service import load_config, save_config, list_studies, save_study, load_study
from .integrations import raspi_adapter
from .results_service import build_biosignal_summary, save_results_payload
from .secrets_service import (
    load_local_secrets,
    redact_hardware_config,
    resolve_notion_api_key,
    save_local_secrets,
)
from .study_client_service import register_heartbeat
from .trial_service import start_trial_session, stop_trial_session
from .validation import (
    ValidationError,
    validate_and_normalize_config,
    validate_and_normalize_results,
    validate_and_normalize_trial_options,
)


def register_routes(app: Flask) -> None:
    @app.route("/")
    def study_page():
        return send_from_directory(current_app.static_folder, "study.html")

    @app.route("/admin")
    def admin_page():
        return send_from_directory(current_app.static_folder, "admin.html")

    @app.route("/api/config")
    def get_config():
        config_data = validate_and_normalize_config(
            load_config(current_app.config["CONFIG_FILE"])
        )
        config_data["_capabilities"] = {
            "unsafe_stimulus_code": bool(
                current_app.config.get("ALLOW_UNSAFE_STIMULUS_CODE", False)
            )
        }
        return jsonify(config_data)

    @app.route("/api/config", methods=["POST"])
    def update_config():
        config_data = request.get_json() or {}
        validated_config = validate_and_normalize_config(config_data)
        save_config(current_app.config["CONFIG_FILE"], validated_config)
        
        # Archivkopie im Ordner /studies anlegen
        studies_dir = current_app.config["BASE_DIR"] / "studies"
        save_study(studies_dir, validated_config)
        
        print("[CONFIG] Saved.")
        return jsonify({"ok": True})

    @app.route("/api/start", methods=["POST"])
    def start_trial():
        start_trial_session(validate_and_normalize_trial_options(request.get_json()))
        return jsonify({"ok": True})

    @app.route("/api/stop", methods=["POST"])
    def stop_trial():
        stop_trial_session(validate_and_normalize_trial_options(request.get_json()))
        return jsonify({"ok": True})

    @app.route("/api/study-client/heartbeat", methods=["POST"])
    def study_client_heartbeat():
        payload = request.get_json() or {}
        heartbeat_result = register_heartbeat(
            payload,
            request.remote_addr,
            request.headers.get("User-Agent", ""),
        )
        return jsonify({"ok": True, **heartbeat_result})

    @app.route("/api/admin/studies", methods=["GET"])
    def admin_list_studies():
        studies_dir = current_app.config["BASE_DIR"] / "studies"
        return jsonify(list_studies(studies_dir))

    @app.route("/api/admin/studies/active", methods=["POST"])
    def admin_set_active_study():
        payload = request.get_json() or {}
        study_id = payload.get("id")
        if not study_id:
            return jsonify({"ok": False, "error": "No study ID provided"}), 400
            
        studies_dir = current_app.config["BASE_DIR"] / "studies"
        try:
            config_data = load_study(studies_dir, study_id)
            validated_config = validate_and_normalize_config(config_data)
            save_config(current_app.config["CONFIG_FILE"], validated_config)
            print(f"[CONFIG] Activated study: {study_id}")
            return jsonify(validated_config)
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 404

    @app.route("/api/admin/status")
    def admin_status():
        return jsonify(
            build_admin_status(
                current_app.config["BASE_DIR"],
                current_app.config.get("HARDWARE_CONFIG", {}),
            )
        )

    @app.route("/api/hardware-config")
    def get_hardware_config():
        return jsonify(
            redact_hardware_config(
                current_app.config.get("HARDWARE_CONFIG", {}),
                current_app.config.get("LOCAL_SECRETS", {}),
            )
        )

    @app.route("/api/hardware-config", methods=["POST"])
    def update_hardware_config():
        config_data = request.get_json()
        if not isinstance(config_data, dict):
            return jsonify({"ok": False, "error": "hardware_config payload must be a JSON object."}), 400

        sanitized_config = json.loads(json.dumps(config_data))
        notion_config = sanitized_config.get("notion")
        local_secrets = dict(current_app.config.get("LOCAL_SECRETS", {}))
        secret_updated = False

        if isinstance(notion_config, dict):
            provided_api_key = str(notion_config.get("api_key") or "").strip()
            if provided_api_key:
                local_secrets.setdefault("notion", {})["api_key"] = provided_api_key
                secret_updated = True

            if notion_config.get("clear_api_key"):
                local_secrets.setdefault("notion", {}).pop("api_key", None)
                if not local_secrets.get("notion"):
                    local_secrets.pop("notion", None)
                secret_updated = True

            notion_config.pop("api_key", None)
            notion_config.pop("api_key_configured", None)
            notion_config.pop("api_key_source", None)
            notion_config.pop("clear_api_key", None)

        config_path = current_app.config["BASE_DIR"] / "hardware_config.json"
        temp_path = config_path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(sanitized_config, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(config_path)
        current_app.config["HARDWARE_CONFIG"] = sanitized_config

        if secret_updated:
            save_local_secrets(current_app.config["LOCAL_SECRETS_FILE"], local_secrets)
            current_app.config["LOCAL_SECRETS"] = load_local_secrets(
                current_app.config["LOCAL_SECRETS_FILE"]
            )

        raspi_result = None
        raspi_config = sanitized_config.get("raspi", {})
        if isinstance(raspi_config, dict) and raspi_config.get("enabled") and raspi_config.get("push_config_on_save"):
            raspi_result = raspi_adapter.push_config(raspi_config)

        return jsonify(
            {
                "ok": True,
                "restart_required": True,
                "message": "Hardware config saved. Secrets stay backend-local. Restart the server to reinitialize startup integrations.",
                "raspi": raspi_result,
            }
        )

    @app.route("/api/raspi/<command>", methods=["POST"])
    def raspi_action(command: str):
        payload = request.get_json() or {}
        sensor = str(payload.get("sensor") or "").strip().lower()
        if not sensor:
            return jsonify({"ok": False, "error": "sensor is required."}), 400

        raspi_config = current_app.config.get("HARDWARE_CONFIG", {}).get("raspi", {})
        try:
            result = raspi_adapter.control_sensor(
                raspi_config,
                sensor=sensor,
                command=command,
            )
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400

        status_code = 200 if result.get("ok") else 502
        return jsonify(result), status_code

    @app.route("/api/admin/radar/start", methods=["POST"])
    def start_mini_radar():
        from .integrations import mini_radar_adapter

        return jsonify({"ok": True, "mini_radar": mini_radar_adapter.start()})

    @app.route("/api/admin/radar/stop", methods=["POST"])
    def stop_mini_radar():
        from .integrations import mini_radar_adapter

        return jsonify({"ok": True, "mini_radar": mini_radar_adapter.stop()})

    @app.route("/api/admin/radar/restart", methods=["POST"])
    def restart_mini_radar():
        from .integrations import mini_radar_adapter

        return jsonify({"ok": True, "mini_radar": mini_radar_adapter.restart()})

    @app.route("/api/camera/frame", methods=["POST"])
    def process_camera_frame():
        from .integrations import camera_affect_adapter

        frame_result = camera_affect_adapter.process_frame(request.get_json() or {})
        return jsonify({"ok": bool(frame_result.get("accepted", False)), **frame_result})

    @app.route("/api/admin/camera/start", methods=["POST"])
    def start_camera_affect():
        from .integrations import camera_affect_adapter

        return jsonify({"ok": True, "camera_emotion": camera_affect_adapter.start()})

    @app.route("/api/admin/camera/stop", methods=["POST"])
    def stop_camera_affect():
        from .integrations import camera_affect_adapter

        return jsonify({"ok": True, "camera_emotion": camera_affect_adapter.stop()})

    @app.route("/api/results", methods=["POST"])
    def save_results():
        result_payload = request.get_json() or {}
        config_data = validate_and_normalize_config(
            load_config(current_app.config["CONFIG_FILE"])
        )
        validated_results = validate_and_normalize_results(result_payload, config_data)
        saved_output = save_results_payload(
            current_app.config["DATA_DIR"],
            config_data["study_id"],
            validated_results,
            current_app.config.get("HARDWARE_CONFIG"),
        )
        print(f"[DATA] Saved: {saved_output['json_file']}")
        if saved_output.get("xdf_file"):
            print(f"[DATA] XDF: {saved_output['xdf_file']}")

        hardware_config = current_app.config.get("HARDWARE_CONFIG", {})
        if hardware_config.get("notion", {}).get("enabled"):
            from .integrations import notion_adapter
            biosignal_summary = build_biosignal_summary(hardware_config, saved_output)
            notion_result = notion_adapter.upload_study_result(
                result_payload=validated_results,
                hardware_config=hardware_config,
                saved_output={**saved_output, "biosignal_summary": biosignal_summary},
            )
            print(f"[NOTION] {'Uploaded' if notion_result.get('ok') else 'Queued (offline)'}")

        return jsonify({"ok": True, **saved_output})

    @app.route("/api/notion/status")
    def notion_status():
        from .integrations import notion_adapter
        return jsonify(notion_adapter.get_status())

    @app.route("/api/notion/flush-queue", methods=["POST"])
    def notion_flush_queue():
        from .integrations import notion_adapter
        return jsonify(notion_adapter.flush_queue())

    @app.route("/api/notion/test", methods=["POST"])
    def notion_test():
        from .integrations import notion_adapter
        payload = request.get_json() or {}
        result = notion_adapter.test_connection(
            api_key=(
                str(payload.get("api_key") or "").strip()
                or resolve_notion_api_key(
                    current_app.config.get("HARDWARE_CONFIG", {}),
                    current_app.config.get("LOCAL_SECRETS", {}),
                )
            ),
            parent_page_id=str(payload.get("parent_page_id") or ""),
            database_id=str(payload.get("database_id") or ""),
            timeout_seconds=int(payload.get("timeout_seconds") or 10),
        )
        return jsonify(result)

    @app.route("/api/sync-clock", methods=["POST"])
    def sync_clock():
        """Clock-sync endpoint for iPad trigger precision.
        iPad sends client_send_ms (performance.now()), server returns timestamps for
        offset calculation: offset = ((srv_recv - cli_send) + (srv_send - cli_recv)) / 2
        """
        data = request.get_json(force=True) or {}
        server_receive_ms = time.time() * 1000
        return jsonify({
            "client_send_ms": data.get("client_send_ms"),
            "server_receive_ms": server_receive_ms,
            "server_send_ms": time.time() * 1000,
        })

    @app.route("/api/display/action", methods=["POST"])
    def display_action():
        """Called by KNOMI 2 display for reconnect/restart actions."""
        from .integrations import camera_affect_adapter, mini_radar_adapter

        data = request.get_json(force=True) or {}
        target = data.get("target")
        action = data.get("action")

        if target == "radar" and action == "restart":
            mini_radar_adapter.restart()
        elif target == "emotion_worker" and action == "reconnect":
            camera_affect_adapter.stop()
            camera_affect_adapter.start()
        elif target == "brainbit" and action in ("restart", "reconnect"):
            from .integrations import brainbit_adapter
            brainbit_adapter.restart()

        return jsonify({"ok": True, "target": target, "action": action})

    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        return jsonify({"ok": False, "error": str(error)}), 400
