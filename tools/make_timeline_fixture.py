"""Write a synthetic completed session so the timeline can be looked at.

A real session needs recording hardware and a matching ``pylsl``; on a
development machine you usually have neither, and the session viewer is then
impossible to see, let alone judge. This writes a session folder that the
sessions index reads exactly like a real one: a result payload with answered
questions, and a merged ``derived/session.xdf`` carrying several streams whose
LSL headers declare their own names, rates, channel labels, types and units.

That last part is the point. The viewer decides what to draw from those
headers alone, so a fixture with a well-described stream proves the same path a
real plugin takes.

    python tools/make_timeline_fixture.py
    python tools/make_timeline_fixture.py --minutes 12 --study "Radar Pilot"

The default study name ("Demo Completed Study") is the one repository-tracked
example under `software/saved_results/` (see `.gitignore`); re-running with
the default will produce a second, differently-named session folder inside
it rather than overwriting the tracked one. A fixture created under any
other --study name is scratch output: delete it when you are done, nothing
else references it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import random
import struct
import sys
import uuid

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def build_streams(seconds: float, seed: int) -> list[dict]:
    """Three streams shaped like the sensors this app actually records.

    Rates and channel types differ on purpose: the viewer should draw the fast,
    typed ones as waveforms and the slow derived ones as lines, without knowing
    what any of them are.
    """
    rng = random.Random(seed)

    ecg_rate = 64.0
    ecg_count = int(seconds * ecg_rate)
    ecg = {
        "name": "MR60 Mini-radar",
        "type": "Physiology",
        "nominal_srate": ecg_rate,
        "plugin_key": "mr60_mini_radar",
        "channels": [
            {"label": "heartPhase", "type": "ECG", "unit": "rad"},
            {"label": "breathPhase", "type": "Respiration", "unit": "rad"},
        ],
        "timestamps": [index / ecg_rate for index in range(ecg_count)],
        "samples": [
            [
                math.sin(2 * math.pi * 1.2 * (i / ecg_rate)) + rng.gauss(0, 0.05),
                math.sin(2 * math.pi * 0.23 * (i / ecg_rate)) + rng.gauss(0, 0.03),
            ]
            for i in range(ecg_count)
        ],
    }

    rate_hz = 1.0
    rate_count = int(seconds * rate_hz)
    rates = {
        "name": "MR60 rates",
        "type": "Derived",
        "nominal_srate": rate_hz,
        "plugin_key": "mr60_mini_radar",
        "channels": [
            {"label": "heartRate", "type": "HeartRate", "unit": "bpm"},
            {"label": "breathRate", "type": "RespirationRate", "unit": "rpm"},
        ],
        "timestamps": [index / rate_hz for index in range(rate_count)],
        "samples": [
            [72 + 6 * math.sin(i / 40) + rng.gauss(0, 0.6), 14 + 2 * math.sin(i / 90)]
            for i in range(rate_count)
        ],
    }

    eeg_rate = 250.0
    eeg_count = int(seconds * eeg_rate)
    eeg = {
        "name": "BrainBit EEG",
        "type": "EEG",
        "nominal_srate": eeg_rate,
        "plugin_key": "brainbit",
        "channels": [{"label": label, "type": "EEG", "unit": "microvolts"} for label in ("T3", "T4", "O1", "O2")],
        "timestamps": [index / eeg_rate for index in range(eeg_count)],
        "samples": [
            [
                20 * math.sin(2 * math.pi * 10 * (i / eeg_rate) + offset) + rng.gauss(0, 4)
                for offset in (0.0, 0.7, 1.4, 2.1)
            ]
            for i in range(eeg_count)
        ],
    }

    return [ecg, rates, eeg]


# --- Minimal XDF 1.0 writer -------------------------------------------------
# Only what a fixture needs: a file header, one StreamHeader and one interleaved
# Samples chunk per stream, then a StreamFooter. Real recordings are written by
# the native XDFWriter; this exists so the viewer has something to read.

def _chunk(tag: int, payload: bytes, stream_id: int | None = None) -> bytes:
    body = struct.pack("<H", tag)
    if stream_id is not None:
        body += struct.pack("<I", stream_id)
    body += payload
    return _varlen(len(body)) + body


def _varlen(length: int) -> bytes:
    if length < 256:
        return struct.pack("<BB", 1, length)
    if length < 2**32:
        return struct.pack("<BI", 4, length)
    return struct.pack("<BQ", 8, length)


def _stream_header_xml(stream: dict, stream_id: int) -> bytes:
    channels = "".join(
        f"<channel><label>{c['label']}</label><type>{c['type']}</type><unit>{c['unit']}</unit></channel>"
        for c in stream["channels"]
    )
    xml = (
        "<?xml version=\"1.0\"?><info>"
        f"<name>{stream['name']}</name>"
        f"<type>{stream['type']}</type>"
        f"<channel_count>{len(stream['channels'])}</channel_count>"
        f"<nominal_srate>{stream['nominal_srate']}</nominal_srate>"
        "<channel_format>float32</channel_format>"
        f"<source_id>{stream['plugin_key']}</source_id>"
        f"<stream_id>{stream_id}</stream_id>"
        f"<desc><channels>{channels}</channels>"
        f"<study_runner><plugin_key>{stream['plugin_key']}</plugin_key></study_runner></desc>"
        "</info>"
    )
    return xml.encode("utf-8")


def _samples_chunk(stream: dict, stream_id: int, base_epoch: float) -> bytes:
    count = len(stream["timestamps"])
    payload = _varlen(count)
    for timestamp, row in zip(stream["timestamps"], stream["samples"]):
        payload += struct.pack("<B", 8) + struct.pack("<d", base_epoch + timestamp)
        payload += b"".join(struct.pack("<f", float(value)) for value in row)
    return _chunk(3, payload, stream_id)


def write_xdf(path: Path, streams: list[dict], base_epoch: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(b"XDF:")
        handle.write(_chunk(1, b"<?xml version=\"1.0\"?><info><version>1.0</version></info>"))
        for index, stream in enumerate(streams, start=1):
            handle.write(_chunk(2, _stream_header_xml(stream, index), index))
            handle.write(_samples_chunk(stream, index, base_epoch))
            first = base_epoch + stream["timestamps"][0]
            last = base_epoch + stream["timestamps"][-1]
            footer = (
                "<?xml version=\"1.0\"?><info>"
                f"<first_timestamp>{first}</first_timestamp>"
                f"<last_timestamp>{last}</last_timestamp>"
                f"<sample_count>{len(stream['timestamps'])}</sample_count>"
                "</info>"
            ).encode("utf-8")
            handle.write(_chunk(6, footer, index))


def write_result(path: Path, *, study_id: str, participant_id: str, session_id: str,
                 base_epoch: float, seconds: float, questions: int) -> None:
    started = datetime.fromtimestamp(base_epoch, tz=timezone.utc)
    answer_details = []
    for number in range(1, questions + 1):
        # Spread the questions across the recording so markers land on signal.
        offset = seconds * number / (questions + 1)
        shown = started + timedelta(seconds=offset)
        answered = shown + timedelta(seconds=6)
        answer_details.append({
            "question_index": number - 1,
            "question_number": number,
            "question_key": f"q{number}",
            "question_type": "likert",
            "question_prompt": f"Fixture question {number}",
            "answer": number % 7 + 1,
            "shown_at": shown.isoformat().replace("+00:00", "Z"),
            "answered_at": answered.isoformat().replace("+00:00", "Z"),
            "server_start_received_epoch_ms": shown.timestamp() * 1000,
            "server_stop_received_epoch_ms": answered.timestamp() * 1000,
            "skipped": False,
        })

    path.write_text(json.dumps({
        "session_id": session_id,
        "participant_id": participant_id,
        "study_id": study_id,
        "timestamp_start": started.isoformat().replace("+00:00", "Z"),
        "timestamp_end": (started + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
        "answers": {detail["question_key"]: detail["answer"] for detail in answer_details},
        "answer_details": answer_details,
        "card_events": [],
        "recovered": False,
        "skipped_questions": [],
    }, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--study", default="Demo Completed Study")
    parser.add_argument("--minutes", type=float, default=6.0)
    parser.add_argument("--questions", type=int, default=5)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=REPOSITORY_ROOT / "software" / "saved_results",
        help="saved_results folder to write into",
    )
    args = parser.parse_args(argv)

    seconds = args.minutes * 60
    participant_id = uuid.uuid4().hex[:16]
    session_id = uuid.uuid4().hex
    base_epoch = (datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)).timestamp()

    # The canonical layout the sessions index scans, plus the COMPLETE marker
    # that tells it the session finished. Anything else is invisible to it.
    session_root = (
        args.results_dir
        / args.study.replace(" ", "_")
        / "participants"
        / participant_id
        / "sessions"
        / f"{datetime.now(tz=timezone.utc):%Y%m%dT%H%M%SZ}__study-session-{session_id}"
    )
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "COMPLETE.json").write_text(
        json.dumps({"status": "completed", "session_id": session_id}, indent=2) + "\n",
        encoding="utf-8",
    )

    write_result(
        session_root / "result.json",
        study_id=args.study,
        participant_id=participant_id,
        session_id=session_id,
        base_epoch=base_epoch,
        seconds=seconds,
        questions=args.questions,
    )
    streams = build_streams(seconds, args.seed)
    write_xdf(session_root / "derived" / "session.xdf", streams, base_epoch)

    print(f"Wrote fixture session: {session_root}")
    for stream in streams:
        labels = ", ".join(channel["label"] for channel in stream["channels"])
        print(f"  {stream['name']:<18} {stream['nominal_srate']:>6} Hz  {labels}")
    print("\nOpen the admin hub and pick it under Completed studies.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
