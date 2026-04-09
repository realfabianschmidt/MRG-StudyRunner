from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def format_values(payload: dict[str, Any] | None, fields: tuple[str, ...]) -> str:
    if not payload:
        return "-"
    parts = []
    for field in fields:
        value = payload.get(field)
        if isinstance(value, float):
            parts.append(f"{field}={value:.3f}")
        else:
            parts.append(f"{field}={value}")
    return " | ".join(parts)


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def render(state: dict[str, Any], state_file: Path, log_file: Path) -> None:
    clear_screen()
    print("BrainBit Monitor")
    print("=" * 72)
    print(f"Status:       {state.get('status', 'waiting')}")
    print(f"Last update:  {state.get('updated_at', '-')}")
    print(f"Message:      {state.get('last_message', '-')}")
    print(f"Last active:  {state.get('last_activity_at', '-')}")
    print(f"Age (s):      {state.get('seconds_since_last_activity', '-')}")
    print(f"OSC target:   {state.get('osc_target', '-')}")
    print(f"State file:   {state_file}")
    print(f"Raw log:      {log_file}")
    print()

    device = state.get("device") or {}
    print("Device")
    print(f"  Name:       {device.get('name', '-')}")
    print(f"  Family:     {device.get('family', '-')}")
    print(f"  Serial:     {device.get('serial_number', device.get('serial', '-'))}")
    print(f"  FS:         {device.get('fs_hz', '-')}")
    print(f"  Battery:    {((state.get('battery') or {}).get('percent', '-'))}")
    print()

    print("Calibration")
    print(f"  Data:       {state.get('calibration', '-')}")
    print()

    print("Quality")
    print(f"  {format_values(state.get('quality'), ('O1', 'O2', 'T3', 'T4'))}")
    print()

    print("EEG")
    print(f"  {format_values(state.get('eeg'), ('O1', 'O2', 'T3', 'T4'))}")
    print()

    print("Bands")
    print(f"  {format_values(state.get('bands'), ('delta', 'theta', 'alpha', 'beta', 'gamma'))}")
    print()

    print("Mental")
    print(
        "  "
        + format_values(
            state.get('mental'),
            ('Inst_Attention', 'Inst_Relaxation', 'Rel_Attention', 'Rel_Relaxation'),
        )
    )
    print()
    print("Ctrl+C closes this monitor window.")


def main() -> None:
    parser = argparse.ArgumentParser(description="BrainBit monitor window for Study Runner")
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--refresh-ms", type=int, default=1000)
    args = parser.parse_args()

    state_file = Path(args.state_file)
    log_file = Path(args.log_file)
    refresh_seconds = max(0.25, args.refresh_ms / 1000.0)
    stopped_seen_at: float | None = None

    while True:
        state = load_state(state_file)
        render(state, state_file, log_file)

        if state.get("status") in {"stopped", "exited", "failed"}:
            if stopped_seen_at is None:
                stopped_seen_at = time.time()
            elif (time.time() - stopped_seen_at) >= max(2.0, refresh_seconds * 2):
                break
        else:
            stopped_seen_at = None

        time.sleep(refresh_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
