"""Minimal plugin SDK for building a new plugin (Phase 5j).

Writing a new sensor, card, destination, or output today means copying an
existing extension folder and editing it by hand. This tool gives that
starting point a name and three simple checks -- it does not invent a second
copy of the real validation rules (CONTRIBUTING.md #7: extend what exists,
do not build a second system next to it).

Commands:
  new <category> <key> [--out DIR]   Copy a template into DIR/<key>.
  validate <plugin_dir>              Check one bundle the same way the real
                                      server does (calls the real discovery
                                      function, not a copy of it).
  check-runtime <plugin_dir>         Actually start the bundle's driver.py
                                      as a real subprocess and call
                                      initialize()/get_status() on it -- a
                                      "fake runtime" that boots the real
                                      machinery instead of only reading the
                                      manifest.
  schema [--write]                   Write or check a JSON reference file
                                      describing the manifest's outer shape
                                      (see `generate_schema()` for exactly
                                      what it does and does not cover).

Every command works on a plugin folder *outside* `study_runner/plugins/`,
so trying things out here never touches the real, shipped plugins.

For a sensor, `tools/synthetic_lsl_source.py` can push fake-but-plausible
samples for the stream(s) your manifest declares, so you can see real
recording behavior without the actual device.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
SOFTWARE_ROOT = REPO_ROOT / "software"
if str(SOFTWARE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOFTWARE_ROOT))

from study_runner.contracts.manifest import (  # noqa: E402
    PLUGIN_API_VERSION,
    RETIRED_CAPABILITIES,
    UI_VISIBILITY_AREAS,
    validate_and_normalize_manifest,
)
from study_runner.contracts.plugin_api import PluginContext  # noqa: E402
from study_runner.plugin_framework.plugin_layout import (  # noqa: E402
    TEST_EXTRA_PLUGIN_ROOT_PACKAGE_ENV_VAR,
    TEST_EXTRA_PLUGIN_ROOT_PATH_ENV_VAR,
)
from study_runner.plugin_framework.plugin_catalog import discover_plugin_catalog  # noqa: E402
from study_runner.plugin_framework.process_host import (  # noqa: E402
    build_process_plugin,
    shutdown_process_plugins,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "plugin_templates"
SCHEMA_PATH = TEMPLATES_DIR / "manifest.schema.json"

# Each template ships with a real, working default key so the raw template
# itself passes `validate`/`check-runtime` unmodified -- `new` only ever
# renames it, never invents structure the template didn't already have.
CATEGORY_DEFAULT_KEYS = {
    "sensors": "example_sensor",
    "cards": "example_card",
    "destinations": "example_destination",
    "outputs": "example_output",
}

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_raw_manifest(plugin_dir: Path) -> dict[str, Any]:
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no manifest.json in {plugin_dir}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

def cmd_validate(plugin_dir: Path) -> int:
    """Discover and validate one bundle through the production pipeline.

    Not a second validator: `discover_plugin_catalog` is the exact function
    `apps/server/__init__.py` calls for the real `plugins/` tree. Passing
    here means the manifest is provably acceptable to the running server,
    not merely "parses" or "looks right by inspection".
    """
    plugin_dir = plugin_dir.resolve()
    if not plugin_dir.is_dir():
        print(f"FAIL: not a directory: {plugin_dir}", file=sys.stderr)
        return 2

    # A throwaway package name per run: discovery only needs it to build an
    # import path, and this bundle is never actually imported by `validate`
    # (only `check-runtime` spawns the real subprocess).
    package_name = f"sdk_validate_{uuid.uuid4().hex}"
    catalog = discover_plugin_catalog(plugin_dir.parent, package_name=package_name)
    entry = next((e for e in catalog.entries if e.directory == plugin_dir.name), None)
    if entry is None:
        print(
            f"FAIL: {plugin_dir.name!r} was not discovered under {plugin_dir.parent}",
            file=sys.stderr,
        )
        return 2
    if entry.status != "valid":
        print(f"FAIL: {plugin_dir.name}: " + "; ".join(entry.errors), file=sys.stderr)
        return 1

    manifest = entry.manifest or {}
    print(
        f"OK: {plugin_dir.name} is a valid api_version {manifest.get('api_version')} "
        f"manifest (capabilities: {', '.join(manifest.get('capabilities', []))})"
    )
    return 0


# --------------------------------------------------------------------------
# check-runtime
# --------------------------------------------------------------------------

@contextmanager
def _fake_runtime_env(plugin_dir: Path) -> Iterator[None]:
    """Make a plugin folder outside `plugins/` findable by its own subprocess.

    When a plugin boots, its driver.py runs as a brand-new subprocess that
    only knows the real extension folders plus one extra path, set via two
    environment variables (`plugin_layout.py`). This function sets those
    two variables temporarily so a template folder can be found the same
    way, then restores whatever was there before. The test suite
    (`software/tests/support/fixture_plugin.py`) already does exactly this
    for the same reason, so this reuses it instead of adding a second copy.
    """
    plugin_dir = plugin_dir.resolve()
    package_dir = plugin_dir.parent
    package_name = package_dir.name
    if not _IDENTIFIER_PATTERN.fullmatch(package_name):
        raise ValueError(
            f"the plugin's parent directory name {package_name!r} must be a valid "
            "Python identifier so the fake runtime can import it as a package -- "
            "rename the folder (letters, digits, underscore; not starting with a digit)"
        )

    init_file = package_dir / "__init__.py"
    created_init = not init_file.exists()
    if created_init:
        init_file.write_text("", encoding="utf-8")

    saved = {
        "PYTHONPATH": os.environ.get("PYTHONPATH"),
        TEST_EXTRA_PLUGIN_ROOT_PATH_ENV_VAR: os.environ.get(TEST_EXTRA_PLUGIN_ROOT_PATH_ENV_VAR),
        TEST_EXTRA_PLUGIN_ROOT_PACKAGE_ENV_VAR: os.environ.get(TEST_EXTRA_PLUGIN_ROOT_PACKAGE_ENV_VAR),
    }
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(package_dir.parent), existing_pythonpath) if item
    )
    os.environ[TEST_EXTRA_PLUGIN_ROOT_PATH_ENV_VAR] = str(package_dir)
    os.environ[TEST_EXTRA_PLUGIN_ROOT_PACKAGE_ENV_VAR] = package_name
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if created_init:
            try:
                init_file.unlink()
            except OSError:
                pass


def cmd_check_runtime(plugin_dir: Path) -> int:
    """Boot the plugin's real driver.py subprocess and exercise it.

    Calls `plugin_framework.process_host.build_process_plugin` -- the same
    function that turns a discovered manifest into the `Plugin` object every
    shipped extension is driven through -- then calls the resulting
    `initialize(context)`/`get_status(context)` exactly as the admin status
    poll does. This is the SDK's "fake runtime": real production machinery
    pointed at a bundle outside the trusted tree, not a simulated stand-in.
    """
    plugin_dir = plugin_dir.resolve()
    try:
        raw_manifest = load_raw_manifest(plugin_dir)
    except FileNotFoundError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    try:
        manifest = validate_and_normalize_manifest(raw_manifest, directory_name=plugin_dir.name)
    except Exception as error:
        print(f"FAIL: manifest is invalid: {error}", file=sys.stderr)
        return 1

    tmp_data_dir = Path(tempfile.mkdtemp(prefix="extension-sdk-"))
    context = PluginContext(
        base_dir=REPO_ROOT,
        data_dir=tmp_data_dir,
        hardware_config={},
        local_secrets={},
        local_secrets_file=tmp_data_dir / "local_secrets.json",
    )
    try:
        with _fake_runtime_env(plugin_dir):
            plugin = build_process_plugin(manifest, plugin_dir)
            plugin.initialize(context)
            status = plugin.get_status(context) if plugin.get_status else {}
    except Exception as error:
        print(f"FAIL: {plugin_dir.name} did not boot: {error}", file=sys.stderr)
        return 1
    finally:
        # Kills the real subprocess this run spawned. Matches
        # software/tests/conftest.py's autouse teardown -- the same cleanup
        # every test that spawns a process-host plugin already relies on.
        shutdown_process_plugins()
        shutil.rmtree(tmp_data_dir, ignore_errors=True)

    print(f"OK: {plugin_dir.name} booted, initialized, and reported status:")
    print(json.dumps(status, indent=2, default=str, sort_keys=True))
    return 0


# --------------------------------------------------------------------------
# new
# --------------------------------------------------------------------------

def cmd_new(category: str, key: str, out_dir: Path | None) -> int:
    if category not in CATEGORY_DEFAULT_KEYS:
        print(
            f"FAIL: category must be one of: {', '.join(CATEGORY_DEFAULT_KEYS)}",
            file=sys.stderr,
        )
        return 2
    if not _KEY_PATTERN.fullmatch(key):
        print(
            f"FAIL: key must be snake_case (a-z0-9_, starting with a letter): {key!r}",
            file=sys.stderr,
        )
        return 2

    template_dir = TEMPLATES_DIR / category
    default_key = CATEGORY_DEFAULT_KEYS[category]
    destination = (out_dir or Path.cwd()) / key
    if destination.exists():
        print(f"FAIL: {destination} already exists", file=sys.stderr)
        return 2

    shutil.copytree(template_dir, destination)
    for path in sorted(destination.rglob("*")):
        if path.is_file() and path.suffix in {".json", ".py", ".js"}:
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace(default_key, key), encoding="utf-8")

    extra = ", card.js" if category == "cards" else ""
    print(
        f"Created {destination}\n"
        f"Review manifest.json, plugin.py{extra} before shipping -- especially "
        "the UI label/description and, for a card, its question_types.\n"
        f"Then: python tools/plugin_sdk.py validate {destination}\n"
        f"      python tools/plugin_sdk.py check-runtime {destination}"
    )
    return 0


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

def generate_schema() -> dict[str, Any]:
    """Describe the manifest's outer shape, for editors -- not a validator.

    This only covers what every manifest has in common: the top-level
    fields, the `ui` block, and the `runtime` block. It does NOT describe
    each capability's own fields (like what `acquisition_transport` or
    `upload_destination` need) -- writing that out by hand a second time is
    exactly how a schema quietly drifts from the real rules (this is why
    cards use real code instead of a schema for their contract too, see the
    5g.B5 decision log). The real check is always `validate`, which calls
    the actual validator. The two lists this file DOES read straight from
    that validator's own code: the current and retired capability *names*.
    """
    visibility_properties = {area: {"type": "boolean"} for area in UI_VISIBILITY_AREAS}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://study-runner.internal/schemas/extension-manifest-v{PLUGIN_API_VERSION}.json",
        "title": f"study_runner extension manifest envelope (api_version {PLUGIN_API_VERSION})",
        "x-generated-by": "tools/plugin_sdk.py generate_schema()",
        "x-generated-from": "study_runner.contracts.manifest",
        "x-note": (
            "Structural reference for the manifest ENVELOPE only -- top-level "
            "fields, the ui block, and the runtime block, all of which "
            "validate_and_normalize_manifest checks with a closed field set. "
            "Capability-internal shapes are NOT covered; run "
            "`python tools/plugin_sdk.py validate <dir>` for the real, "
            "authoritative check instead of trusting this file for that."
        ),
        "x-retired-capabilities": dict(RETIRED_CAPABILITIES),
        "type": "object",
        "required": [
            "api_version", "plugin_key", "config_key", "version", "category",
            "ui", "capabilities", "runtime",
        ],
        # Extra top-level keys are tolerated by the real validator (only
        # `ui` and `runtime` enforce a closed field set) -- so this schema
        # says the same, rather than being stricter than reality.
        "additionalProperties": True,
        "properties": {
            "api_version": {"const": PLUGIN_API_VERSION},
            "plugin_key": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
            "config_key": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
            "version": {"type": "string"},
            "category": {"type": "string"},
            "ui": {
                "type": "object",
                "required": ["label"],
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "order": {"type": "integer", "minimum": 0},
                    "visibility": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": visibility_properties,
                    },
                    "extensions": {"type": "object", "additionalProperties": {"type": "string"}},
                    "assets": {"type": "array", "items": {"type": "string"}},
                    "timeline": {"type": "object"},
                    "icon": {"type": "string"},
                },
            },
            "capabilities": {
                "description": (
                    "Either {capability_name: config} or a list of capability "
                    "names with no config. See "
                    "study_runner/plugins/README.md for the current, "
                    "load-bearing list; `x-retired-capabilities` above for "
                    "names api_version 5 rejects."
                ),
                "oneOf": [
                    {"type": "object"},
                    {"type": "array", "items": {"type": "string"}},
                ],
            },
            "runtime": {
                "type": "object",
                "required": ["entrypoint", "protocol"],
                "additionalProperties": False,
                "properties": {
                    "entrypoint": {"const": "driver.py"},
                    "protocol": {"const": "study-runner-stdio/v1"},
                    "interactive_stdin": {"type": "boolean"},
                    "modes": {"type": "array", "items": {"type": "string"}},
                    "sample_delivery": {"type": "string"},
                    "stream_contract": {"type": "object"},
                    "operation_timeouts_ms": {"type": "object"},
                    "actions": {"type": "array", "items": {"enum": ["start", "stop", "restart"]}},
                    "trial_events": {"type": "array", "items": {"enum": ["start", "stop", "marker"]}},
                    "can_toggle": {"type": "boolean"},
                    "sidecar": {"type": "object"},
                },
            },
            "streams": {"type": "array"},
            "settings": {"type": "object"},
            "lifecycle": {"type": "object"},
            "poll_interval_ms": {"type": "integer", "exclusiveMinimum": 0},
            "request_timeout_ms": {"type": "integer", "exclusiveMinimum": 0},
            "backpressure": {"type": "object"},
            "clock_domain": {"type": "string"},
            "expected_data_rate": {"type": "object"},
        },
    }


def cmd_schema(*, write: bool) -> int:
    schema = generate_schema()
    text = json.dumps(schema, indent=2, sort_keys=False) + "\n"
    if write:
        SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_PATH.write_text(text, encoding="utf-8")
        print(f"Wrote {SCHEMA_PATH}")
        return 0
    if not SCHEMA_PATH.is_file():
        print(f"FAIL: {SCHEMA_PATH} does not exist -- run with --write", file=sys.stderr)
        return 1
    current = SCHEMA_PATH.read_text(encoding="utf-8")
    if current != text:
        print(
            f"FAIL: {SCHEMA_PATH} is stale relative to contracts/manifest.py -- "
            "run `python tools/plugin_sdk.py schema --write`",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {SCHEMA_PATH} matches the generator")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="Scaffold a new extension from a template.")
    new_parser.add_argument("category", choices=sorted(CATEGORY_DEFAULT_KEYS))
    new_parser.add_argument("key", help="snake_case plugin key for the new extension")
    new_parser.add_argument("--out", type=Path, default=None, help="Parent directory (default: cwd)")

    validate_parser = subparsers.add_parser("validate", help="Validate one plugin bundle.")
    validate_parser.add_argument("plugin_dir", type=Path)

    runtime_parser = subparsers.add_parser("check-runtime", help="Boot one plugin bundle's real subprocess.")
    runtime_parser.add_argument("plugin_dir", type=Path)

    schema_parser = subparsers.add_parser("schema", help="Generate/check the manifest envelope reference.")
    schema_parser.add_argument("--write", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "new":
        return cmd_new(args.category, args.key, args.out)
    if args.command == "validate":
        return cmd_validate(args.plugin_dir)
    if args.command == "check-runtime":
        return cmd_check_runtime(args.plugin_dir)
    if args.command == "schema":
        return cmd_schema(write=args.write)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
