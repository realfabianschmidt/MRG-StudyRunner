from flask import Flask, current_app, jsonify, request, send_from_directory

from .config_service import load_config, save_config
from .results_service import save_results_payload
from .trial_service import start_trial_session, stop_trial_session
from .validation import (
    ValidationError,
    validate_and_normalize_config,
    validate_and_normalize_results,
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
        return jsonify(config_data)

    @app.route("/api/config", methods=["POST"])
    def update_config():
        config_data = request.get_json() or {}
        validated_config = validate_and_normalize_config(config_data)
        save_config(current_app.config["CONFIG_FILE"], validated_config)
        print("[CONFIG] Saved.")
        return jsonify({"ok": True})

    @app.route("/api/start", methods=["POST"])
    def start_trial():
        start_trial_session(request.get_json() or {})
        return jsonify({"ok": True})

    @app.route("/api/stop", methods=["POST"])
    def stop_trial():
        stop_trial_session(request.get_json() or {})
        return jsonify({"ok": True})

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
        return jsonify({"ok": True, **saved_output})

    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        return jsonify({"ok": False, "error": str(error)}), 400
