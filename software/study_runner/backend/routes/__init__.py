"""HTTP routes, grouped by domain.

- pages.py    the three HTML pages (/, /admin, /audit)
- study.py    everything the participant tablet calls during a study
- results.py  saving results (crash-safe) + incremental snapshots
- admin.py    operator endpoints: health, studies, status, restart
- sensors.py  hardware config, sensor runtime actions, camera, worker
- update.py   in-app updater
- notion.py   Notion upload integration
- helpers.py  shared request-context helpers

``register_routes(app)`` keeps the same entry point the app factory
has always used.
"""
from flask import Flask, jsonify

from ..services.trial_service import configure_runtime
from ..services.validation import ValidationError
from . import admin, notion, pages, results, sensors, study, update


def register_routes(app: Flask) -> None:
    configure_runtime(
        base_dir=app.config["BASE_DIR"],
        data_dir=app.config["DATA_DIR"],
        hardware_config=app.config.get("HARDWARE_CONFIG", {}),
        local_secrets=app.config.get("LOCAL_SECRETS", {}),
        local_secrets_file=app.config["LOCAL_SECRETS_FILE"],
    )

    app.register_blueprint(pages.bp)
    app.register_blueprint(study.bp)
    app.register_blueprint(results.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(sensors.bp)
    app.register_blueprint(update.bp)
    app.register_blueprint(notion.bp)

    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        return jsonify({"ok": False, "error": str(error)}), 400
