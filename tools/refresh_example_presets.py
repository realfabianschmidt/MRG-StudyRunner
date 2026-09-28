"""Rebuild the shipped example studies through the canonical validator/writer."""
from __future__ import annotations

import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SOFTWARE_ROOT = REPO_ROOT / "software"
STUDIES_DIR = SOFTWARE_ROOT / "study_content" / "studies"
ACTIVE_CONFIG = SOFTWARE_ROOT / "study_content" / "settings" / "study_config.json"
sys.path.insert(0, str(SOFTWARE_ROOT))

from study_runner.plugin_framework.process_host import shutdown_process_plugins  # noqa: E402
from study_runner.runtime_core.studies.study_package_service import build_package  # noqa: E402
from study_runner.runtime_core.studies.validation import validate_and_normalize_config  # noqa: E402


PLUGIN_KEYS = ("am_hub", "brainbit", "camera_emotion", "mini_radar", "notion", "nextcloud", "osc")
SENSOR_KEYS = ("am_hub", "brainbit", "camera_emotion", "mini_radar")


def plugin_settings() -> dict:
    return {
        key: {"enabled": False, "required": False, "settings": {}}
        for key in PLUGIN_KEYS
    }


def study_settings(*, duration: int, cover_title: str, cover_text: str, progress: bool) -> dict:
    return {
        "sensors_enabled": False,
        "sensors": {key: False for key in SENSOR_KEYS},
        "plugins": plugin_settings(),
        "progress_bar_enabled": progress,
        "planned_session_duration_minutes": duration,
        "cover_page": {
            "enabled": True,
            "title": cover_title,
            "text": cover_text,
            "image_asset": "",
            "image_alt": "",
            "layout": "text",
            "button_label": "Begin",
        },
    }


def bookends() -> tuple[dict, dict]:
    participant = {
        "type": "participant-id",
        "prompt": "Enter the requested details to create your anonymous participant code.",
        "code_label": "Your anonymous participant code",
        "info_top": "The identifying name fields are used to generate the code and are not stored.",
        "info_bottom": "Ask the study operator if you are unsure which details to enter.",
    }
    finish = {
        "type": "finish",
        "title": "Thank you",
        "prompt": "Your answers have been saved. You can now return the tablet to the study operator.",
    }
    return participant, finish


def basic_study() -> dict:
    participant, finish = bookends()
    return {
        "study_id": "Example Basic Study",
        "questions": [
            participant,
            {"type": "info", "title": "About this example", "text": "This short study demonstrates common self-report cards without using sensors."},
            {"type": "mood-meter", "prompt": "How do you feel right now?", "variant": "classic", "required": True},
            {"type": "single", "prompt": "Which environment feels most comfortable?", "options": ["Quiet room", "Lively room", "Outdoors"], "required": True},
            {"type": "slider", "prompt": "How alert do you feel right now?", "label_min": "Not alert", "label_max": "Very alert", "required": True},
            {"type": "semantic", "prompt": "How did the previous card feel?", "pairs": [["Difficult", "Easy"], ["Unfamiliar", "Familiar"]], "required": True},
            {"type": "text", "prompt": "Is there anything else you would like to add?", "required": False},
            finish,
        ],
        "study_settings": study_settings(
            duration=8,
            cover_title="Example Basic Study",
            cover_text="A short, sensor-free example that is safe to preview and edit.",
            progress=True,
        ),
    }


def sensors_study() -> dict:
    participant, finish = bookends()
    return {
        "study_id": "Example Sensors Study",
        "questions": [
            participant,
            {
                "type": "info",
                "title": "Connect sensors before enabling them",
                "text": "This portable example includes every current sensor, but all hardware integrations are disabled. On the study computer, connect and test each device in the dashboard, then enable only the sensors you intend to record in Study settings.",
            },
            {"type": "mood-meter", "prompt": "How do you feel before the measurement?", "variant": "classic", "required": True},
            {"type": "stimulus", "title": "Quiet baseline", "trigger_type": "timer", "warmup_duration_ms": 5000, "duration_ms": 30000, "plugin_actions": {"brainbit": {"to_touchdesigner": False}, "osc": {"forward_marker": False}}},
            {"type": "single", "prompt": "How comfortable was the measurement period?", "options": ["Comfortable", "Neutral", "Uncomfortable"], "required": True},
            finish,
        ],
        "study_settings": study_settings(
            duration=12,
            cover_title="Example Sensors Study",
            cover_text="A portable template containing every supported sensor, initially disabled.",
            progress=True,
        ),
    }


def card_gallery_study() -> dict:
    participant, finish = bookends()
    return {
        "study_id": "Example Card Gallery Study",
        "questions": [
            participant,
            {"type": "info", "title": "Card gallery", "text": "This study demonstrates every question type. Sensors and external outputs are disabled."},
            {"type": "text", "prompt": "Describe this example in one sentence.", "required": False},
            {"type": "choice", "prompt": "Which colors do you like?", "options": ["Blue", "Green", "Orange"], "required": False},
            {"type": "single", "prompt": "Choose one work setting.", "options": ["Quiet", "Music", "Conversation"], "required": True},
            {"type": "likert", "prompt": "I understand the example.", "scale": 5, "label_min": "Disagree", "label_max": "Agree", "required": True},
            {"type": "slider", "prompt": "How focused are you?", "label_min": "Not focused", "label_max": "Very focused", "required": True},
            {"type": "multi-slider", "prompt": "Rate your current state.", "dimensions": [{"label": "Calm", "min_label": "Not calm", "max_label": "Very calm"}, {"label": "Alert", "min_label": "Not alert", "max_label": "Very alert"}], "required": True},
            {"type": "semantic", "prompt": "Rate the example between each pair.", "pairs": [["Complex", "Simple"], ["Slow", "Fast"]], "required": True},
            {"type": "ranking", "prompt": "Order these activities by preference.", "options": ["Reading", "Walking", "Listening to music"], "required": True},
            {"type": "word-cloud", "prompt": "Which words describe your current mood?", "words": ["Calm", "Curious", "Focused", "Tired"], "allow_multiple": True, "required": True},
            {"type": "mood-meter", "prompt": "Where are you on the Mood Meter?", "variant": "field", "required": True},
            {"type": "stimulus", "title": "Timed pause", "trigger_type": "timer", "warmup_duration_ms": 3000, "duration_ms": 10000, "plugin_actions": {"brainbit": {"to_touchdesigner": False}, "osc": {"forward_marker": False}}},
            finish,
        ],
        "study_settings": study_settings(
            duration=15,
            cover_title="Example Card Gallery Study",
            cover_text="A sensor-free tour of every card type available in the editor.",
            progress=True,
        ),
    }


def write_package(raw_config: dict) -> dict:
    config = validate_and_normalize_config(raw_config)
    path = STUDIES_DIR / f"{config['study_id']}.study-runner"
    path.write_bytes(build_package(STUDIES_DIR, config))
    return config


def main() -> int:
    STUDIES_DIR.mkdir(parents=True, exist_ok=True)
    basic = write_package(basic_study())
    write_package(sensors_study())
    write_package(card_gallery_study())
    ACTIVE_CONFIG.write_text(json.dumps(basic, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    shutdown_process_plugins()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
