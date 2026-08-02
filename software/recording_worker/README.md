# Hybrid XDF recording worker

The recording worker has two deliberately small parts:

- A separate Python process owns loopback authentication, command replay,
  Lab Streaming Layer (LSL) inlets, leases, and session orchestration.
- A versioned native C ABI owns canonical XDF encoding, exclusive file
  creation, durable flushes, and lossless chunk-level merge.

Flask never writes XDF bytes. If the native core is absent, modified,
non-canonical, or built for another machine, sensor recording is unavailable
and readiness fails explicitly.

## Local setup

The source tree vendors the small, MIT-licensed XDFWriter surface from
App-LabRecorder `v1.17.1`. The setup is network-free and verifies the locked
commit and source hashes before invoking CMake:

```text
python tools/setup_recording_worker.py
```

The default command configures a Release build, runs CTest and the synthetic
XDF writer/merge smoke, installs to a temporary build area, probes the C ABI,
and stages only the runtime library plus `worker-build.json` at:

```text
software/.build/xdf_core/windows-x64/
software/.build/xdf_core/macos-x64/
software/.build/xdf_core/macos-arm64/
```

`software/.build` is ignored by Git. Generated libraries must never be copied
into the tracked `recording_worker` source tree.

Useful CI and diagnostic options are:

```text
--build-dir <path>       select the CMake working directory
--stage-dir <path>       select an explicit generated stage directory
--configuration <name>  Release, RelWithDebInfo, or Debug
--skip-tests             skip CTest and the synthetic XDF smoke
--probe-only             verify an existing stage without writing files
--json                   print one machine-readable result
--require-canonical      fail unless every canonical feature is present
```

An in-repository custom build or stage path is accepted only below
`software/.build`. External temporary paths remain available to CI. A stage
built with `--skip-tests` can be inspected, but the application refuses to use
it for canonical recording.

For a deliberate development override, `STUDY_RUNNER_XDF_CORE` may name either
the staged library or its directory. The override has precedence over the
default local stage and still requires a matching `worker-build.json`, SHA-256,
ABI probe, upstream identity, canonical feature set, and passed native tests.

## Supported platforms

Source-mode recording is enabled only after setup on Windows x64 and on native
macOS x64 or arm64 hosts. Linux is intentionally fail-closed before setup can
create a build or stage directory. A Linux build must not be presented as
canonical until its own release fixtures and packaged hardware gate exist.

Release bundles are not produced by this helper. Packaging must include the
same verified core and build manifest before it may advertise recording
support; otherwise the packaged app remains fail-closed.

## Scientific boundary

The native core uses the official XDFWriter encoding logic. Study Runner keeps
its durability adapter and strict merger separate under `native/src`; the
pristine upstream copies and their exact provenance are described in
`native/UPSTREAM.md` and `native/UPSTREAM_LOCK.json`.

The merger preserves stream payloads, native sample rates, raw timestamps, and
clock-offset chunks while remapping container stream IDs. It does not resample,
synchronize, or dejitter native streams. The native CTest fixture exercises
numeric and string streams, exclusive-create behavior, durable close, and
payload-preserving merge. PyXDF remains a validator/importer, never the writer.

The central slow-grid backup is a labelled `derived_backup` quality-control
artifact. It does not replace any native plugin XDF.
