from flask import Flask, current_app, jsonify, request, send_from_directory

from .config_service import load_config, save_config
from .results_service import save_results_payload
from .trial_service import start_trial_session, stop_trial_session


def register_routes(app: Flask) -> None:
    @app.route("/")
    def study_page():
        return send_from_directory(current_app.static_folder, "study.html")

    @app.route("/admin")
    def admin_page():
        return send_from_directory(current_app.static_folder, "admin.html")

    @app.route("/api/config")
    def get_config():
        config_data = load_config(current_app.config["CONFIG_FILE"])
        return jsonify(config_data)

    @app.route("/api/config", methods=["POST"])
    def update_config():
        config_data = request.get_json() or {}
        save_config(current_app.config["CONFIG_FILE"], config_data)
        print("[CONFIG] Saved.")
        return jsonify({"ok": True})

    @app.route("/api/start", methods=["POST"])
    def start_trial():
        start_trial_session()
        return jsonify({"ok": True})

    @app.route("/api/stop", methods=["POST"])
    def stop_trial():
        stop_trial_session()
        return jsonify({"ok": True})

    @app.route("/api/results", methods=["POST"])
    def save_results():
        result_payload = request.get_json() or {}
        config_data = load_config(current_app.config["CONFIG_FILE"])
        filename = save_results_payload(
            current_app.config["DATA_DIR"],
            config_data["study_id"],
            result_payload,
        )
        print(f"[DATA] Saved: {filename}")
        return jsonify({"ok": True, "file": filename})
