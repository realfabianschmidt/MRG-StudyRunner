# BrainBit operator reports from 2026-09-15

Historical notes supplied during parallel development. The later investigation
in [the implementation report](../20260915_brainbit.md) supersedes the diagnosis
below: decimal 3221225786 is 0xC000013A (console interruption), not 0xC0000005.
Repeated initialize also occurs at study start, not only through restart RPCs.
The supplied session was rejected by central preflight and contains no EEG.

## Known issue: a crash-and-hang report from before the connection rework

Reported 2026-09-15, running a source checkout at a **different, older**
install location (`C:\Users\fabia\MRG-StudyRunner`) than the one this
repository builds from -- one still laid out as `extensions/sensors/brainbit/`,
from before the folder was renamed to `plugins/`, and predating the connection
rework described above (commit `a31b6f4`, "BrainBit: hold a connection instead
of hoping for one"). **Not reproduced on the current code**, and the specific
mechanics below are what the old, one-shot-per-attempt process model made
possible -- not necessarily what the current, persistent-connection model does.

Two separate symptoms, from two attempts in the same session:

1. **First attempt**, with BrainBit and the radar already connected from an
   earlier admin-hub check: pressing Start hung for a long time, then the
   BrainBit driver was terminated, and the participant page reported the study
   could not start.
2. **Second attempt**, connecting fresh: scan, connect and the six-second
   contact measurement all completed cleanly, then `brainbit_realtime_cli.py`
   exited with code `3221225786` about a minute after calibration finished.
   That number is `0xC0000005`, Windows' own name for a native memory access
   violation -- a fault inside the vendor SDK's compiled code, not a Python
   exception, and not something any `try`/`except` in this folder can catch.
   Retrying reproduced the identical crash.

A third thing in the same log is probably unrelated to both: for about 45
minutes, the log repeated "State file write skipped because the file is
locked" -- the status-file writer (write to a temp file, then rename) makes
exactly one attempt per call and gives up silently on a Windows sharing
violation. The log shows this clearing on its own (later connections wrote
normally again), which doesn't line up with either crash, and points more at
something external holding the file open -- a virus scanner, an indexer, a
sync client -- than at anything in this folder's own handle hygiene.

What is verifiably different in the current code, read directly rather than
assumed: the one-shot "scan, connect, stream, exit" process this log implies
is gone. A device that is already connected is reused immediately, not
reconnected. Waiting for the stream contract is bounded (`scan_seconds +
resist_seconds + 20s`, about 31s by default) instead of open-ended. A crash
lands in the same retry path as any other exit code -- up to three attempts
with backoff, then a clear "tried N times, use Restart" failure -- instead of
leaving the connection in an unrecoverable state. What that does *not* mean:
that a native `0xC0000005` fault inside the vendor SDK cannot still happen on
the current architecture. It is a fault in code this project does not own,
and confirming whether it still occurs needs a fresh run on real hardware, not
a reading of the Python around it.

Two specific spots stay open for whoever picks this up next, deliberately
left unchanged here while a larger connection-reliability rework is in
progress: `_set_state` in `adapter.py` has no retry at all on a locked state
file, and `_EXIT_REASONS`/`_CRASH_REASON` file every unrecognized exit code --
including a native access violation -- under the same generic "crashed, will
retry" bucket as ordinary supervision failures, with no separate label for
diagnosis.

## Known issue: a clean-exit report on current code, cause not yet isolated

Reported 2026-09-15, running a source checkout downloaded fresh from
`origin/main` at commit `496319c` -- unlike the report above, this one
**does** reproduce on current, shipped code, not an old install.

BrainBit connected, ran its six-second contact measurement with poor contact
on all four electrodes throughout (`RESIST` samples mostly `null`, `QUALITY`
`0.0` on every channel the whole window), then calibration stalled and
finished anyway -- none of that is a bug. `CALIB STALLED` only sets a flag and
keeps raw acquisition running (`brainbit_realtime_cli.py@496319c:1624-1638`);
nothing about a stalled or poor-contact calibration sets a failure exit code
or raises. `CALIB FINISHED` after a stall is cosmetic, not an error.

What happened next is the part worth recording. About 26 seconds after `CALIB
FINISHED`, the log printed the same two lines the process prints at its very
first startup -- "TouchDesigner OSC proxy ready" and "Base LSL outlet ready;
waiting for the device channel map" -- meaning `adapter.py`'s `initialize()`
ran again inside the same session
(`_initialize_touchdesigner_client`/`_initialize_lsl_outlets`,
`adapter.py@496319c:1713`/`1876`, both called only from `initialize()` at
`adapter.py@496319c:367-370`). The only path that reruns `initialize()`
without a full stop is the `"restart"` RPC (`plugin.py@496319c:245-256`,
`_restart()`). Ten seconds after that, the CLI exited with code `0` and did
not restart: "External CLI exited with code 0" then "External CLI stopped,"
with no retry line.

That is not a bug in itself. `EXIT_OK = 0`
(`brainbit_realtime_cli.py@496319c:38`) means "stopped on purpose or the
configured duration elapsed," and `adapter.py` deliberately treats it as
final: `_exit_reason()` returns `None` for exit code 0
(`adapter.py@496319c:788-792`, excluded from both `_EXIT_REASONS` and
`_CRASH_REASON`), and `_maybe_restart_after_exit()` returns `False` the
moment `reason is None` (`adapter.py@496319c:2334-2336`), before the retry
budget is even checked. `test_brainbit_launch.py`'s
`test_clean_exit_stops_the_watchdog` asserts exactly this. **The actual gap:
exit 0 does not distinguish "stopped after a genuinely good connection" from
"stopped after contact was never verified good."** A session whose electrodes
never made real contact can end exactly like a session that worked, with no
distinct signal telling the operator the recording that follows may be
meaningless.

What is still unconfirmed, and why nothing was changed here to fix it: which
caller sent that `"restart"` RPC. Nothing in the committed `adapter.py` or
`brainbit_realtime_cli.py` wires calibration stall or poor contact into a
restart -- that trigger, if it is automatic at all, lives outside these two
files, most likely in `ui/dashboard.js`, which (along with `adapter.py`,
`plugin.py`, `brainbit_realtime_cli.py` and `manifest.json`) currently carries
substantial uncommitted work rebuilding exactly this connection-state and
calibration handling. Reading or editing that in-progress version, rather
than the committed one the report reproduced on, would not have answered the
question and risks colliding with work already underway. Confirming the
trigger needs either that rework to land first, or a live reproduction with
logging on the dashboard side.

**For today:** if `CALIB STALLED` reports `contact_quality_state: "poor"` for
more than a few seconds, reseat the electrodes and confirm contact before
starting the study -- a later `CALIB FINISHED` is not a guarantee the signal
was ever good, whatever exit code the session ends on.

A third open spot, in addition to the two already listed above: exit code `0`
carries no memory of whether calibration ever left the stalled state, so nothing
downstream can tell "clean stop, good data" apart from "clean stop, contact
never verified" without re-reading the whole session log.

