"""Is the system clock plausible, and is a time-sync service running?

Package 5b (docs/archive/architecture-1.0-umbau.md): the target architecture requires
preflight to check "die Systemuhr auf Plausibilität und einen laufenden
Zeitdienst" (the system clock for plausibility, and a running time service)
before a recording session may start.

Neither signal alone proves the clock is correct, and this module never
claims otherwise:

- A plausible timestamp can still be free-running with no sync service at
  all -- a machine can coincidentally show a believable time while its clock
  drifts unchecked for the whole session.
- A running sync service does not guarantee *this* host has ever actually
  synced, or that the current reading isn't a huge, temporary correction in
  progress.

So both must hold, and the caller decides what "both must hold" means for
starting a session -- this module only reports what it observed.

Deliberately not a network time check: this makes no outbound connection.
Labs record offline on purpose (no lab network, a field site with no
internet), and a live NTP round-trip would fail a preflight check for a
machine that is working exactly as intended. Checking for local evidence of a
sync service is a bound on plausibility, not a guarantee of accuracy -- an
NTP-grade audit is out of scope by design, matching the German wording
("Plausibilität"), not a strict correctness proof.
"""
from __future__ import annotations

import subprocess
import sys
import time
from typing import Any, Callable

# Hardcoded floor and ceiling, not computed ones: there is no way to derive
# "the plausible range for right now" from the machine's own (untrusted)
# clock -- that would be circular. Both need a manual bump every so often
# (same maintenance shape as the pinned dependency versions elsewhere in this
# codebase). The gap between them is deliberately generous -- this is a sanity
# bound against a badly wrong clock (years off), not a tight tolerance.
MIN_PLAUSIBLE_EPOCH_SECONDS = 1_735_689_600.0  # 2025-01-01T00:00:00Z
MAX_PLAUSIBLE_EPOCH_SECONDS = 1_893_456_000.0  # 2030-01-01T00:00:00Z


def probe_system_clock(
    *,
    now: Callable[[], float] = time.time,
    platform_name: str | None = None,
    run: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
) -> dict[str, Any]:
    """Report clock plausibility and time-service evidence. Never raises."""

    epoch_seconds = float(now())
    plausible = MIN_PLAUSIBLE_EPOCH_SECONDS <= epoch_seconds <= MAX_PLAUSIBLE_EPOCH_SECONDS

    service_running: bool | None
    reason: str | None
    target = (platform_name or sys.platform).strip().lower()
    if target == "darwin" or target == "macos":
        service_running, reason = _macos_time_service_running(run)
    elif target.startswith("win"):
        service_running, reason = _windows_time_service_running(run)
    elif target.startswith("linux"):
        # Recording itself is deliberately fail-closed on Linux (see
        # recording/worker_binary.py's platform gate) -- there is no
        # supported canonical time-service check to perform here either.
        service_running, reason = False, "Linux is fail-closed for canonical recording."
    else:
        service_running, reason = None, f"No time-service check is defined for platform {target!r}."

    if not plausible:
        if epoch_seconds < MIN_PLAUSIBLE_EPOCH_SECONDS:
            reason = (
                f"System clock reads {epoch_seconds:.0f}s epoch, before the "
                f"plausible floor of {MIN_PLAUSIBLE_EPOCH_SECONDS:.0f}s."
            )
        else:
            reason = (
                f"System clock reads {epoch_seconds:.0f}s epoch, beyond the "
                f"plausible ceiling of {MAX_PLAUSIBLE_EPOCH_SECONDS:.0f}s."
            )

    ok = plausible and bool(service_running)
    if ok:
        reason = None
    elif reason is None:
        reason = "No time-sync service was detected running."

    return {
        "ok": ok,
        "plausible": plausible,
        "service_running": service_running,
        "epoch_seconds": epoch_seconds,
        "reason": reason,
    }


def _windows_time_service_running(run: Callable[..., "subprocess.CompletedProcess[str]"]) -> tuple[bool | None, str | None]:
    # `sc query`'s text output -- including the field labels, not just the
    # trailing status word -- is localized on non-English Windows displays, so
    # matching for a literal "STATE" would silently misreport on those
    # machines. PowerShell's Get-Service exposes .Status as a .NET enum
    # (ServiceControllerStatus): printing it always yields the English member
    # name ("Running"/"Stopped"/...) regardless of display language, so this
    # asks for exactly that one field instead of parsing free text.
    try:
        result = run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-Service -Name w32time -ErrorAction Stop).Status",
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return None, f"Could not query the Windows Time service: {error}"

    status = (result.stdout or "").strip()
    if not status:
        return None, "Windows Time service state could not be determined."
    running = status == "Running"
    return running, (None if running else f"Windows Time service (w32time) is not running ({status}).")


def _macos_time_service_running(run: Callable[..., "subprocess.CompletedProcess[str]"]) -> tuple[bool | None, str | None]:
    try:
        result = run(
            ["launchctl", "print", "system/com.apple.timed"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return None, f"Could not query the macOS time daemon: {error}"

    running = result.returncode == 0
    return running, (None if running else "macOS time daemon (timed) is not loaded.")
