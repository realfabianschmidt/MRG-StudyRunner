"""Trusted built-in extension roots, shared by discovery and driver startup."""
from pathlib import Path
import json
import os

CATEGORIES = ("sensors", "cards", "destinations", "outputs")

# Lets a test register exactly one additional root for a synthetic fixture
# plugin. Needed because `run_plugin_driver` (driver_runtime.py) runs in a
# freshly spawned subprocess that never sees a parent test's monkeypatches --
# an environment variable is the only channel that reaches it. Read only from
# the environment, never from a request or manifest value, so this can never
# become an attacker-controlled plugin path (CONTRIBUTING.md #1: "Never load
# plugin paths, packages, or dependencies supplied by a web request").
TEST_EXTRA_ROOT_PATH_ENV_VAR = "STUDY_RUNNER_TEST_EXTRA_EXTENSION_ROOT"
TEST_EXTRA_ROOT_PACKAGE_ENV_VAR = "STUDY_RUNNER_TEST_EXTRA_EXTENSION_PACKAGE"


def trusted_roots() -> tuple[tuple[Path, str], ...]:
    package = Path(__file__).resolve().parent.parent
    roots = [
        (package / "extensions" / category, f"study_runner.extensions.{category}")
        for category in CATEGORIES
    ]
    extra_path = os.environ.get(TEST_EXTRA_ROOT_PATH_ENV_VAR, "").strip()
    extra_package = os.environ.get(TEST_EXTRA_ROOT_PACKAGE_ENV_VAR, "").strip()
    if extra_path and extra_package:
        roots.append((Path(extra_path), extra_package))
    return tuple(roots)


def candidate_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_dir()
         and not path.name.startswith((".", "_"))
         and not (path / ".pluginignore").is_file()),
        key=lambda path: path.name,
    )


def resolve_extension(plugin_key: str) -> tuple[Path, str]:
    matches = []
    for root, package in trusted_roots():
        for directory in candidate_directories(root):
            try:
                manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(manifest, dict) and manifest.get("plugin_key") == plugin_key:
                matches.append((directory.resolve(), f"{package}.{directory.name}"))
    if len(matches) != 1:
        raise LookupError(f"expected one plugin bundle for {plugin_key!r}, found {len(matches)}")
    return matches[0]
