#!/usr/bin/env python3
"""Manual discovery probe for the BrainBit headband.

Answers one question without starting a recording: *does this computer see the
band, and under which identity?* It prints every band the scan accumulates, the
identity fields you can paste into `hardware_settings.json`, and -- if a target
is given -- which one the production selector would pick and why.

It imports the scan and selection code from the acquisition script rather than
re-implementing it, so a probe that finds the band cannot disagree with a
Study Runner that does not. Run it with the band switched on and *removed from
the Windows Bluetooth settings*; the vendor SDK owns the connection itself and
a Windows pairing is a second owner competing for the same device.

    python ble_brainbit_probe.py
    python ble_brainbit_probe.py --scan-seconds 10 --serial-number A1B2C3
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace


def _find_software_root() -> Path:
    """Locate `software/` by marker, not by counting levels.

    This duplicates `study_runner.shared.software_root` on purpose: the whole
    point of this block is to make `study_runner` importable, so it cannot
    import from it yet. Counting parents would silently return a wrong folder
    when this script moves; the marker search fails loudly instead.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "server.py").exists() and (candidate / "study_runner").is_dir():
            return candidate
    raise RuntimeError("Could not locate the software/ folder above this script.")


try:
    from study_runner.plugins.sensors.brainbit import brainbit_realtime_cli as cli
except ModuleNotFoundError:
    sys.path.insert(0, str(_find_software_root()))
    from study_runner.plugins.sensors.brainbit import brainbit_realtime_cli as cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-seconds", type=int, default=5)
    parser.add_argument("--serial-number", type=str, default="")
    parser.add_argument("--device-address", type=str, default="")
    parser.add_argument("--device-name", type=str, default="")
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable output only.")
    args = parser.parse_args(argv)

    cli._set_output_mode(debug=False, pretty=not args.json)
    cli._ensure_requirements()
    cli._load_sdk_modules()

    scanner = cli.Scanner(cli._brainbit_sensor_families(cli.SensorFamily))
    scanner.sensorsChanged = lambda _, sensors: cli._remember_scan_candidates(sensors)

    cli._reset_scan_candidates()
    stop_event = threading.Event()
    # The same selector arguments the acquisition script builds, so the answer
    # below is the answer Study Runner would give.
    selectors = SimpleNamespace(
        scan_seconds=args.scan_seconds,
        serial_number=args.serial_number,
        device_address=args.device_address,
        device_name=args.device_name,
        device_index=args.device_index,
    )
    candidates = cli._scan_for_target(scanner, selectors, stop_event)

    found = [cli._sensor_info_payload(info, index) for index, info in enumerate(candidates)]
    index, info, source = cli._select_sensor_info(candidates, selectors)
    result = {
        "found": found,
        "selected_index": index,
        "selection_source": source,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if info is not None else 1

    if not found:
        print("No BrainBit-family band answered.")
        print("Switch the band on, remove it from Windows Bluetooth settings, and try again.")
        return 1

    print(f"{len(found)} band(s) found:")
    for band in found:
        print(
            f"  [{band['index']}] {band.get('name') or '?'}"
            f"  serial={band.get('serial') or '-'}"
            f"  address={band.get('address') or '-'}"
            f"  rssi={band.get('rssi')}"
        )
    print()
    if info is None:
        print(f"No band matches the given target ({source}).")
        return 1
    print(f"Study Runner would connect to index {index}, matched by {source}.")
    print("Put this in hardware_settings.json under \"brainbit\" to make it permanent:")
    print(f'  "serial_number": "{found[index].get("serial") or ""}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
