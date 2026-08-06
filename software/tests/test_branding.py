"""Upload validation and slot resolution for branding_service."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.atomic_io import atomic_write_json
from study_runner.backend.services.branding_service import (
    MANIFEST_NAME,
    MAX_ASSET_BYTES,
    MAX_FUNDERS,
    BrandingError,
    load_manifest,
    public_manifest,
    remove_asset,
    resolve_asset,
    store_asset,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def temporary_branding_dir() -> tempfile.TemporaryDirectory[str]:
    root = PROJECT_ROOT.parent / ".tmp" / "branding-tests"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


class BrandingUploadTests(unittest.TestCase):
    def test_group_logo_round_trips_and_is_served_from_the_manifest(self) -> None:
        with temporary_branding_dir() as temporary:
            branding_dir = Path(temporary)
            public = store_asset(branding_dir, "group", "logo.png", PNG, "Materiability")

            self.assertEqual(public["group"], {"slot": "group", "alt": "Materiability"})
            path, content_type = resolve_asset(branding_dir, "group")
            self.assertEqual(path.read_bytes(), PNG)
            self.assertEqual(content_type, "image/png")

    def test_replacing_the_group_logo_deletes_the_previous_file(self) -> None:
        """Otherwise every swap leaves an orphan behind in the settings folder."""
        with temporary_branding_dir() as temporary:
            branding_dir = Path(temporary)
            store_asset(branding_dir, "group", "first.png", PNG)
            first = load_manifest(branding_dir)["group"]["file"]
            store_asset(branding_dir, "group", "second.png", PNG)

            self.assertFalse((branding_dir / first).exists())
            self.assertEqual(len(list(branding_dir.glob("group-*"))), 1)

    def test_rejects_unsupported_types_and_oversized_files(self) -> None:
        with temporary_branding_dir() as temporary:
            branding_dir = Path(temporary)

            with self.assertRaisesRegex(BrandingError, "not a supported image"):
                store_asset(branding_dir, "group", "logo.svgz", PNG)
            with self.assertRaisesRegex(BrandingError, "not a supported image"):
                store_asset(branding_dir, "group", "payload.html", PNG)
            with self.assertRaisesRegex(BrandingError, "1 MB"):
                store_asset(branding_dir, "group", "logo.png", b"0" * (MAX_ASSET_BYTES + 1))
            with self.assertRaisesRegex(BrandingError, "empty"):
                store_asset(branding_dir, "group", "logo.png", b"")

    def test_funders_are_capped_and_removable_one_at_a_time(self) -> None:
        with temporary_branding_dir() as temporary:
            branding_dir = Path(temporary)
            for index in range(MAX_FUNDERS):
                store_asset(branding_dir, "funder", f"f{index}.png", PNG)

            with self.assertRaisesRegex(BrandingError, "At most"):
                store_asset(branding_dir, "funder", "one-too-many.png", PNG)

            slots = [funder["slot"] for funder in public_manifest(branding_dir)["funders"]]
            public = remove_asset(branding_dir, slots[2])

            self.assertEqual(len(public["funders"]), MAX_FUNDERS - 1)
            self.assertNotIn(slots[2], [funder["slot"] for funder in public["funders"]])


class BrandingResolutionTests(unittest.TestCase):
    """A slot may only ever reach a file the manifest itself names."""

    def test_unknown_slots_are_refused(self) -> None:
        with temporary_branding_dir() as temporary:
            branding_dir = Path(temporary)
            for slot in ("group", "funder:deadbeef", "../../etc/passwd", "funder:../x", ""):
                with self.subTest(slot=slot), self.assertRaises(BrandingError):
                    resolve_asset(branding_dir, slot)

    def test_a_manifest_pointing_outside_the_folder_is_refused(self) -> None:
        """The manifest is ours, but a hand-edited one must not escape the dir."""
        with temporary_branding_dir() as temporary:
            branding_dir = Path(temporary)
            outside = branding_dir.parent / "outside.png"
            outside.write_bytes(PNG)
            atomic_write_json(
                branding_dir / MANIFEST_NAME,
                {"group": {"file": "../outside.png", "alt": ""}, "funders": []},
            )

            with self.assertRaises(BrandingError):
                resolve_asset(branding_dir, "group")

    def test_a_corrupt_manifest_degrades_to_no_branding(self) -> None:
        with temporary_branding_dir() as temporary:
            branding_dir = Path(temporary)
            (branding_dir / MANIFEST_NAME).write_text("{ not json", encoding="utf-8")

            self.assertEqual(public_manifest(branding_dir), {"group": None, "funders": []})

    def test_entries_without_a_filename_are_dropped(self) -> None:
        with temporary_branding_dir() as temporary:
            branding_dir = Path(temporary)
            (branding_dir / MANIFEST_NAME).write_text(
                json.dumps({"group": {"alt": "no file"}, "funders": [{"id": "a"}, "nonsense"]}),
                encoding="utf-8",
            )

            self.assertEqual(public_manifest(branding_dir), {"group": None, "funders": []})


if __name__ == "__main__":
    unittest.main()
