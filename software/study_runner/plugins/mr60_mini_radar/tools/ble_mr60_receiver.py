#!/usr/bin/env python3
"""Manual BLE receiver for the MR60_BLE ESP32C6 firmware.

The tool prints decoded JSON lines and can optionally mirror values to CSV or
OSC. It reuses the Study Runner runtime adapter constants and decoder so the
diagnostic path cannot drift from the production BLE packet format.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any


try:
    from study_runner.plugins.mr60_mini_radar import adapter
except ModuleNotFoundError:
    software_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(software_root))
    from study_runner.plugins.mr60_mini_radar import adapter


class CsvSink:
    def __init__(self, path: Path) -> None:
        self.file = path.open("w", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(
            self.file,
            fieldnames=[
                "server_received_epoch",
                "version",
                "flags",
                "valid",
                "stabilized",
                "present",
                "sequence_number",
                "timestamp_ms",
                "heartRate",
                "breathRate",
                "distance",
                "heartPhase",
                "breathPhase",
                "totalPhase",
                "dropped_since_previous",
                "total_dropped",
                "host_interval_ms",
                "device_interval_ms",
                "jitter_ms",
            ],
        )
        self.writer.writeheader()

    def write(self, sample: dict[str, Any]) -> None:
        self.writer.writerow({key: sample.get(key) for key in self.writer.fieldnames})
        self.file.flush()

    def close(self) -> None:
        self.file.close()


class OscSink:
    def __init__(self, host: str, port: int) -> None:
        try:
            from pythonosc.udp_client import SimpleUDPClient
        except ImportError as exc:
            raise RuntimeError("OSC output needs: pip install python-osc") from exc

        self.client = SimpleUDPClient(host, port)

    def send(self, sample: dict[str, Any]) -> None:
        value_paths = {
            "/heartrate": sample.get("heartRate"),
            "/breathrate": sample.get("breathRate"),
            "/distance": sample.get("distance"),
            "/heart_phase": sample.get("heartPhase"),
            "/breath_phase": sample.get("breathPhase"),
            "/total_phase": sample.get("totalPhase"),
            "/sequence": sample.get("sequence_number"),
            "/valid": int(bool(sample.get("valid"))),
            "/stabilized": int(bool(sample.get("stabilized"))),
            "/present": int(bool(sample.get("present"))),
            "/total_dropped": sample.get("total_dropped"),
            "/jitter_ms": sample.get("jitter_ms"),
        }

        for path, value in value_paths.items():
            if value is not None:
                self.client.send_message(path, value)


async def _import_bleak() -> tuple[Any, Any]:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise RuntimeError("BLE support needs: pip install bleak") from exc

    return BleakClient, BleakScanner


async def _find_device(name: str, address: str | None, scan_timeout: float) -> Any:
    _, scanner = await _import_bleak()

    if address:
        return address

    def matches(device: Any, advertisement: Any) -> bool:
        return device.name == name or getattr(advertisement, "local_name", None) == name

    print(f"Scanning for {name}...", file=sys.stderr)
    device = await scanner.find_device_by_filter(matches, timeout=scan_timeout)
    if device is None:
        raise RuntimeError(f"BLE device not found: {name}")
    return device


async def _receive_once(
    args: argparse.Namespace,
    csv_sink: CsvSink | None,
    osc_sink: OscSink | None,
    stop: asyncio.Event,
) -> None:
    bleak_client, _ = await _import_bleak()
    device = await _find_device(args.name, args.address, args.scan_timeout)
    disconnected = asyncio.Event()

    def on_disconnect(_: Any) -> None:
        disconnected.set()

    async with bleak_client(device, disconnected_callback=on_disconnect) as client:
        print("Connected", file=sys.stderr)

        def on_notify(_: int, data: bytearray) -> None:
            sample = adapter._decode_ble_packet(bytes(data))
            if sample is None:
                print(f"Bad packet: expected {adapter.BLE_PACKET.size} bytes, got {len(data)}", file=sys.stderr)
                return

            sample["server_received_epoch"] = time.time()
            print(json.dumps(sample, separators=(",", ":")), flush=True)

            if csv_sink is not None:
                csv_sink.write(sample)
            if osc_sink is not None:
                osc_sink.send(sample)

        await client.start_notify(adapter.BLE_CHARACTERISTIC_UUID, on_notify)
        stop_wait = asyncio.create_task(stop.wait())
        disconnected_wait = asyncio.create_task(disconnected.wait())
        done, pending = await asyncio.wait(
            {stop_wait, disconnected_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
        try:
            await client.stop_notify(adapter.BLE_CHARACTERISTIC_UUID)
        except Exception:
            pass
        print("Disconnected", file=sys.stderr)


async def _receive_forever(args: argparse.Namespace) -> None:
    adapter._reset_ble_stats()
    csv_sink = CsvSink(Path(args.csv)) if args.csv else None
    osc_sink = OscSink(args.osc_host, args.osc_port) if args.osc_port else None

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for signame in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, signame, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass

    try:
        while not stop.is_set():
            try:
                await _receive_once(args, csv_sink, osc_sink, stop)
            except RuntimeError as exc:
                print(exc, file=sys.stderr)
            except Exception as exc:
                print(f"Receiver error: {exc}", file=sys.stderr)

            try:
                await asyncio.wait_for(stop.wait(), timeout=args.reconnect_delay)
            except asyncio.TimeoutError:
                pass
    finally:
        if csv_sink is not None:
            csv_sink.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Receive MR60_BLE sensor packets and print decoded JSON lines.",
    )
    parser.add_argument("--name", default=adapter.BLE_DEVICE_NAME, help="BLE device name to scan for")
    parser.add_argument("--address", help="BLE address or identifier to connect to directly")
    parser.add_argument("--scan-timeout", type=float, default=10.0, help="BLE scan timeout in seconds")
    parser.add_argument("--reconnect-delay", type=float, default=2.0, help="Delay before reconnect attempts")
    parser.add_argument("--csv", help="Write decoded samples to this CSV file")
    parser.add_argument("--osc-host", default="127.0.0.1", help="OSC UDP destination host")
    parser.add_argument("--osc-port", type=int, help="Enable OSC UDP output to this port")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        asyncio.run(_receive_forever(args))
    except KeyboardInterrupt:
        return 0
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
