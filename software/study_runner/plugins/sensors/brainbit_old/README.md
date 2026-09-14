# BrainBit (old) — frozen fallback

This is the BrainBit integration exactly as it was **before** the 2026-09
reliability rebuild, kept as a manual escape hatch. It ships switched off and
is meant to stay that way.

**Do not fix bugs in here.** Fix them in `../brainbit/` instead. This copy is
an archive: its value is that it behaves precisely like the version that ran
in the lab before the rebuild, and every edit destroys a little of that.

## When to use it

Only if the current BrainBit plugin fails in a way that blocks a session and
cannot be resolved quickly. It buys time; it is not a second supported
integration.

## How to switch over

1. In the admin settings hub, **disable** the current `BrainBit` sensor.
2. Enable `BrainBit (old)` and enter its device settings (it has its own
   configuration section, `brainbit_old`, and shares nothing with the current
   plugin).
3. In the study's sensor selection, pick `BrainBit (old)`.
4. Reverse all three steps once the current plugin works again.

**Never run both at the same time.** A BrainBit band can only be owned by one
process at a time (see `../brainbit/README_ENHANCED.md`), so two plugins
scanning and connecting will fight over it and both will behave worse than
either alone. This is the same failure mode as leaving the band paired in the
Windows Bluetooth settings.

## What differs from the original

Only what had to, so the two can coexist on one machine:

| | current | this copy |
|---|---|---|
| plugin / config key | `brainbit` | `brainbit_old` |
| LSL source IDs | `study_runner.brainbit.*` | `study_runner.brainbit_old.*` |
| sidecar file | `brainbit_signals` | `brainbit_old_signals` |
| log / state files | `brainbit_runtime.log`, `brainbit_state.json` | `brainbit_old_*` |
| enabled by default | yes, and required | **no**, and never required |
| packaged builds | supported | refuses to start (see below) |

The acquisition code itself — `brainbit_realtime_cli.py` — is byte-identical
to the pre-rebuild version.

Packaged builds launch the CLI as `<own exe> --brainbit-cli`, and that flag
dispatches to the *current* CLI. Rather than silently acquiring through the
new code while reporting itself as the old plugin, this copy refuses to start
in a packaged build and says it needs an interpreter path. The fallback is
meant for a source checkout, which is what an operator running from source
has anyway.

## Recording data written by this plugin

Because its LSL source IDs differ, sessions recorded through this plugin are
distinguishable from — but not directly comparable to — sessions recorded
through the current one. That is deliberate: silently reusing the same stream
identity would make two different acquisition implementations look like one
continuous recording contract.
