"""Trusted built-in extension roots, shared by discovery and driver startup."""
from pathlib import Path
import json

CATEGORIES = ("sensors", "cards", "destinations", "outputs")


def trusted_roots() -> tuple[tuple[Path, str], ...]:
    package = Path(__file__).resolve().parent.parent
    return tuple(
        (package / "extensions" / category, f"study_runner.extensions.{category}")
        for category in CATEGORIES
    ) + ((package / "plugins", "study_runner.plugins"),)


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
