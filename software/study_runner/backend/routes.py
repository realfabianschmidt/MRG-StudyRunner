import json
import os
import subprocess
import sys
import threading
import time

from flask import Flask, current_app, jsonify, request, send_from_directory

from study_runner.integrations.registry import (
    apply_enabled_runtime,
    build_context,
    get_plugin_status,
    initialize_plugin,
    run_runtime_action,
)
from .services.admin_status_service import build_admin_status
from .services.study_config_service import (
    delete_study,
    list_studies,
    load_config,
    load_study,
    save_config,
    save_study,
)
from .services.hardware_settings_service import save_hardware_config, set_integration_enabled
from .services.results_service import build_answer_details, build_biosignal_summary, save_results_payload
from .services.runtime_config import build_runtime_info
from .services.secrets_service import (
    describe_notion_api_key_source,
    describe_notion_api_key_storage,
    load_local_secrets,
    redact_hardware_config,
    resolve_notion_api_key,
    save_local_secrets,
)
from .services.study_client_service import register_heartbeat
from .services.trial_service import configure_runtime, start_trial_session, stop_trial_session
from .services.update_service import (
    UpdateError,
    build_update_status,
    check_for_update,
    download_and_stage_update,
    request_update_install,
)
from .services.validation import (
    ValidationError,
    validate_and_normalize_config,
    validate_and_normalize_results,
    validate_and_normalize_trial_options,
)


def _integration_context():
    return build_context(
        base_dir=current_app.config["BASE_DIR"],
        data_dir=current_app.config["DATA_DIR"],
        hardware_config=current_app.config.get("HARDWARE_CONFIG", {}),
        local_secrets=current_app.config.get("LOCAL_SECRETS", {}),
        local_secrets_file=current_app.config["LOCAL_SECRETS_FILE"],
    )


def _refresh_trial_runtime() -> None:
    configure_runtime(
        base_dir=current_app.config["BASE_DIR"],
        data_dir=current_app.config["DATA_DIR"],
        hardware_config=current_app.config.get("HARDWARE_CONFIG", {}),
        local_secrets=current_app.config.get("LOCAL_SECRETS", {}),
        local_secrets_file=current_app.config["LOCAL_SECRETS_FILE"],
    )


def _spawn_server_restart(base_dir) -> None:
    server_file = base_dir / "server.py"
    if not server_file.exists():
        server_file = base_dir / "study_runner" / "app_server.py"
    server_path = str(server_file)
    helper_code = (
        "import os, subprocess, sys, time; "
        "time.sleep(1.2); "
        f"cmd={[sys.executable, server_path]!r}; "
        f"cwd={str(base_dir)!r}; "
        "kwargs={'cwd': cwd, 'env': os.environ.copy(), 'close_fds': True}; "
        "if os.name == 'nt': "
        " kwargs['creationflags'] = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0); "
        "else: "
        " kwargs['start_new_session'] = True; "
        "subprocess.Popen(cmd, **kwargs)"
    )
    subprocess.Popen(
        [sys.executable, "-c", helper_code],
        cwd=str(base_dir),
        close_fds=True,
        env=os.environ.copy(),
    )


def _delayed_shutdown(shutdown_func) -> None:
    time.sleep(0.3)
    shutdown_func()


def _save_notion_secret_payload(config_data: dict) -> tuple[dict, bool]:
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

    if secret_updated:
        save_local_secrets(current_app.config["LOCAL_SECRETS_FILE"], local_secrets)
        current_app.config["LOCAL_SECRETS"] = load_local_secrets(current_app.config["LOCAL_SECRETS_FILE"])

    return sanitized_config, secret_updated


def register_routes(app: Flask) -> None:
    configure_runtime(
        base_dir=app.config["BASE_DIR"],
        data_dir=app.config["DATA_DIR"],
        hardware_config=app.config.get("HARDWARE_CONFIG", {}),
        local_secrets=app.config.get("LOCAL_SECRETS", {}),
        local_secrets_file=app.config["LOCAL_SECRETS_FILE"],
    )

    @app.route("/")
    def study_page():
        return send_from_directory(current_app.static_folder, "pages/study.html")

    @app.route("/admin")
    def admin_page():
        return send_from_directory(current_app.static_folder, "pages/admin.html")

    @app.route("/api/health")
    def health():
        return jsonify(
            {
                "ok": True,
                "status": "running",
                "app_mode": current_app.config.get("APP_MODE", "python"),
            }
        )

    @app.route("/api/runtime-info")
    def runtime_info():
        return jsonify(build_runtime_info(current_app.config, request.scheme))

    @app.route("/api/config")
    def get_config():
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        config_data["_capabilities"] = {
            "unsafe_stimulus_code": bool(current_app.config.get("ALLOW_UNSAFE_STIMULUS_CODE", False))
        }
        return jsonify(config_data)

    @app.route("/api/config", methods=["POST"])
    def update_config():
        config_data = request.get_json() or {}
        validated_config = validate_and_normalize_config(config_data)
        save_config(current_app.config["CONFIG_FILE"], validated_config)
        save_study(current_app.config["SAVED_STUDIES_DIR"], validated_config)
        print("[CONFIG] Saved.")
        return jsonify({"ok": True})

    @app.route("/api/start", methods=["POST"])
    def start_trial():
        _refresh_trial_runtime()
        start_trial_session(validate_and_normalize_trial_options(request.get_json()))
        return jsonify({"ok": True})

    @app.route("/api/stop", methods=["POST"])
    def stop_trial():
        _refresh_trial_runtime()
        stop_trial_session(validate_and_normalize_trial_options(request.get_json()))
        return jsonify({"ok": True})

    @app.route("/api/admin/restart", methods=["POST"])
    def admin_restart():
        app_mode = str(current_app.config.get("APP_MODE", "python")).strip().lower()
        if app_mode in {"desktop", "packaged"} or getattr(sys, "frozen", False):
            return jsonify(
                {
                    "ok": False,
                    "error": "Server restart is unavailable in packaged builds. Close and reopen Study Runner, or use the update restart action after staging an update.",
                }
            ), 503

        shutdown_func = request.environ.get("werkzeug.server.shutdown")
        if shutdown_func is None:
            return jsonify({"ok": False, "error": "Server restart is only available on the built-in Study Runner server."}), 503

        try:
            _spawn_server_restart(current_app.config["BASE_DIR"])
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 500

        threading.Thread(target=_delayed_shutdown, args=(shutdown_func,), daemon=True).start()
        return jsonify({"ok": True, "message": "Server restart requested."})

    @app.route("/api/study-client/heartbeat", methods=["POST"])
    def study_client_heartbeat():
        payload = request.get_json() or {}
        heartbeat_result = register_heartbeat(payload, request.remote_addr, request.headers.get("User-Agent", ""))
        return jsonify({"ok": True, **heartbeat_result})

    @app.route("/api/admin/studies", methods=["GET"])
    def admin_list_studies():
        return jsonify(list_studies(current_app.config["SAVED_STUDIES_DIR"]))

    @app.route("/api/admin/studies/active", methods=["POST"])
    def admin_set_active_study():
        payload = request.get_json() or {}
        study_id = payload.get("id")
        if not study_id:
            return jsonify({"ok": False, "error": "No study ID provided"}), 400
        try:
            config_data = load_study(current_app.config["SAVED_STUDIES_DIR"], study_id)
            validated_config = validate_and_normalize_config(config_data)
            save_config(current_app.config["CONFIG_FILE"], validated_config)
            print(f"[CONFIG] Activated study: {study_id}")
            return jsonify(validated_config)
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 404

    @app.route("/api/admin/studies/<study_id>", methods=["GET"])
    def admin_get_study(study_id):
        try:
            return jsonify(load_study(current_app.config["SAVED_STUDIES_DIR"], study_id))
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 404

    @app.route("/api/admin/studies/<study_id>", methods=["DELETE"])
    def admin_delete_study(study_id):
        if delete_study(current_app.config["SAVED_STUDIES_DIR"], study_id):
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Not found"}), 404

    @app.route("/api/admin/status")
    def admin_status():
        return jsonify(build_admin_status(_integration_context()))

    @app.route("/api/admin/update/status")
    def admin_update_status():
        return jsonify(build_update_status(current_app.config))

    @app.route("/api/admin/update/check", methods=["POST"])
    def admin_update_check():
        try:
            return jsonify(check_for_update(current_app.config))
        except UpdateError as error:
            return jsonify({"ok": False, "error": str(error)}), 400

    @app.route("/api/admin/update/download", methods=["POST"])
    def admin_update_download():
        try:
            return jsonify(download_and_stage_update(current_app.config))
        except UpdateError as error:
            return jsonify({"ok": False, "error": str(error)}), 400

    @app.route("/api/admin/update/install", methods=["POST"])
    def admin_update_install():
        shutdown_func = request.environ.get("werkzeug.server.shutdown")
        if shutdown_func is None:
            return jsonify({"ok": False, "error": "Update restart is only available on the built-in Study Runner server."}), 503

        try:
            result = request_update_install(current_app.config)
        except UpdateError as error:
            return jsonify({"ok": False, "error": str(error)}), 503

        threading.Thread(target=_delayed_shutdown, args=(shutdown_func,), daemon=True).start()
        return jsonify({"ok": True, **result})

    @app.route("/api/hardware-config")
    def get_hardware_config():
        return jsonify(redact_hardware_config(current_app.config.get("HARDWARE_CONFIG", {}), current_app.config.get("LOCAL_SECRETS", {})))

    @app.route("/api/hardware-config", methods=["POST"])
    def update_hardware_config():
        config_data = request.get_json()
        if not isinstance(config_data, dict):
            return jsonify({"ok": False, "error": "hardware_config payload must be a JSON object."}), 400

        sanitized_config, _secret_updated = _save_notion_secret_payload(config_data)
        save_hardware_config(current_app.config["HARDWARE_CONFIG_FILE"], sanitized_config)
        current_app.config["HARDWARE_CONFIG"] = sanitized_config
        _refresh_trial_runtime()
        initialize_plugin("notion", _integration_context())

        return jsonify(
            {
                "ok": True,
                "restart_required": True,
                "message": "Hardware config saved. Secrets stay backend-local. Notion was refreshed immediately; restart is recommended for startup integrations.",
                "notion_runtime": get_plugin_status("notion", _integration_context()),
            }
        )

    @app.route("/api/admin/integrations/<integration_key>/enabled", methods=["POST"])
    def update_integration_enabled(integration_key: str):
        payload = request.get_json() or {}
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            return jsonify({"ok": False, "error": "enabled must be true or false."}), 400

        hardware_config = json.loads(json.dumps(current_app.config.get("HARDWARE_CONFIG", {})))
        try:
            set_integration_enabled(hardware_config, integration_key, enabled)
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400

        save_hardware_config(current_app.config["HARDWARE_CONFIG_FILE"], hardware_config)
        current_app.config["HARDWARE_CONFIG"] = hardware_config
        _refresh_trial_runtime()

        try:
            apply_enabled_runtime(integration_key, enabled, _integration_context())
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400

        return jsonify(
            {
                "ok": True,
                "integration": integration_key,
                "enabled": enabled,
                "restart_required": False,
                "runtime_status": get_plugin_status(integration_key, _integration_context()),
            }
        )

    @app.route("/api/admin/integrations/<integration_key>/<action>", methods=["POST"])
    def run_integration_runtime_action(integration_key: str, action: str):
        return _run_integration_action_json(integration_key, action)

    def _run_integration_action_json(integration_key: str, action: str):
        try:
            result = run_runtime_action(integration_key, action, _integration_context())
            return jsonify(result)
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 500

    @app.route("/api/admin/brainbit/start", methods=["POST"])
    def start_brainbit():
        return _run_integration_action_json("brainbit", "start")

    @app.route("/api/admin/brainbit/stop", methods=["POST"])
    def stop_brainbit():
        return _run_integration_action_json("brainbit", "stop")

    @app.route("/api/admin/brainbit/restart", methods=["POST"])
    def restart_brainbit():
        return _run_integration_action_json("brainbit", "restart")

    @app.route("/api/admin/radar/start", methods=["POST"])
    def start_mini_radar():
        return _run_integration_action_json("mini_radar", "start")

    @app.route("/api/admin/radar/stop", methods=["POST"])
    def stop_mini_radar():
        return _run_integration_action_json("mini_radar", "stop")

    @app.route("/api/admin/radar/restart", methods=["POST"])
    def restart_mini_radar():
        return _run_integration_action_json("mini_radar", "restart")

    @app.route("/api/camera/frame", methods=["POST"])
    def process_camera_frame():
        from study_runner.integrations.tablet_camera_emotion import adapter as camera_affect_adapter

        frame_result = camera_affect_adapter.process_frame(request.get_json() or {})
        return jsonify({"ok": bool(frame_result.get("accepted", False)), **frame_result})

    @app.route("/api/admin/camera/start", methods=["POST"])
    def start_camera_affect():
        return _run_integration_action_json("camera_emotion", "start")

    @app.route("/api/admin/camera/stop", methods=["POST"])
    def stop_camera_affect():
        return _run_integration_action_json("camera_emotion", "stop")

    @app.route("/api/results", methods=["POST"])
    def save_results():
        result_payload = request.get_json() or {}
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        validated_results = validate_and_normalize_results(result_payload, config_data)
        validated_results["answer_details"] = build_answer_details(
            validated_results,
            config_data,
            current_app.config.get("HARDWARE_CONFIG", {}),
        )
        saved_output = save_results_payload(
            current_app.config["DATA_DIR"],
            config_data["study_id"],
            validated_results,
            current_app.config.get("HARDWARE_CONFIG"),
            context=_integration_context(),
        )
        print(f"[DATA] Saved: {saved_output['json_file']}")
        if saved_output.get("xdf_file"):
            print(f"[DATA] XDF: {saved_output['xdf_file']}")

        hardware_config = current_app.config.get("HARDWARE_CONFIG", {})
        study_settings = config_data.get("study_settings", {})
        if study_settings.get("notion_enabled"):
            from study_runner.integrations.notion_upload import adapter as notion_adapter

            biosignal_summary = build_biosignal_summary(hardware_config, saved_output, context=_integration_context())
            notion_result = notion_adapter.upload_study_result(
                result_payload=validated_results,
                hardware_config=hardware_config,
                saved_output={**saved_output, "biosignal_summary": biosignal_summary},
                config_data=config_data,
            )
            if notion_result.get("ok"):
                print("[NOTION] Uploaded")
            elif notion_result.get("queued"):
                print("[NOTION] Queued (offline)")
            elif notion_result.get("skipped"):
                print(f"[NOTION] Skipped: {notion_result.get('error', 'not configured')}")

        return jsonify({"ok": True, **saved_output})

    @app.route("/api/notion/status")
    def notion_status():
        from study_runner.integrations.notion_upload import adapter as notion_adapter

        hardware_config = current_app.config.get("HARDWARE_CONFIG", {})
        local_secrets = current_app.config.get("LOCAL_SECRETS", {})
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        study_settings = config_data.get("study_settings", {})

        status = notion_adapter.get_status()
        status.update(
            {
                "enabled_globally": bool(hardware_config.get("notion", {}).get("enabled")),
                "auto_retry_failed": bool(hardware_config.get("notion", {}).get("auto_retry_failed", True)),
                "api_key_configured": bool(resolve_notion_api_key(hardware_config, local_secrets)),
                "api_key_source": describe_notion_api_key_source(hardware_config, local_secrets),
                "api_key_storage": describe_notion_api_key_storage(hardware_config, local_secrets, current_app.config["LOCAL_SECRETS_FILE"]),
                "local_secrets_file": current_app.config["LOCAL_SECRETS_FILE"].name,
                "current_study_id": config_data.get("study_id", ""),
                "current_study_notion_enabled": bool(study_settings.get("notion_enabled")),
                "current_study_parent_page_id": study_settings.get("notion_parent_page_id", ""),
                "current_study_database_id": study_settings.get("notion_database_id", ""),
                "current_study_target_ready": bool(study_settings.get("notion_parent_page_id") or study_settings.get("notion_database_id")),
            }
        )
        return jsonify(status)

    @app.route("/api/notion/flush-queue", methods=["POST"])
    def notion_flush_queue():
        from study_runner.integrations.notion_upload import adapter as notion_adapter

        return jsonify(notion_adapter.flush_queue())

    @app.route("/api/notion/test", methods=["POST"])
    def notion_test():
        from study_runner.integrations.notion_upload import adapter as notion_adapter

        payload = request.get_json() or {}
        result = notion_adapter.test_connection(
            api_key=(
                str(payload.get("api_key") or "").strip()
                or resolve_notion_api_key(current_app.config.get("HARDWARE_CONFIG", {}), current_app.config.get("LOCAL_SECRETS", {}))
            ),
            timeout_seconds=int(payload.get("timeout_seconds") or 10),
        )
        return jsonify(result)

    @app.route("/api/sync-clock", methods=["POST"])
    def sync_clock():
        """Clock-sync endpoint for tablet trigger precision against the Study Runner server."""
        data = request.get_json(force=True) or {}
        server_receive_ms = time.time() * 1000
        return jsonify(
            {
                "client_send_ms": data.get("client_send_ms"),
                "server_receive_ms": server_receive_ms,
                "server_send_ms": time.time() * 1000,
            }
        )

    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        return jsonify({"ok": False, "error": str(error)}), 400
