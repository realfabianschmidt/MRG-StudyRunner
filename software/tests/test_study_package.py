"""Study images and portable .study-runner packages."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.runtime_core.studies.study_assets_service import (  # noqa: E402
    MAX_ASSET_BYTES,
    StudyAssetError,
    referenced_assets,
    require_assets,
    resolve_asset,
    store_asset,
)
from study_runner.runtime_core.studies.study_package_service import (  # noqa: E402
    StudyPackageError,
    build_package,
    read_package,
    store_package_assets,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"image-bytes" * 8
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'


def temporary_studies_dir() -> tempfile.TemporaryDirectory[str]:
    root = PROJECT_ROOT.parent / ".tmp" / "study-package-tests"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


def study_with(asset_id: str) -> dict:
    return {
        "study_id": "Pictures",
        "questions": [
            {"type": "participant-id"},
            {"type": "info", "title": "Hello", "text": "Read me", "image_asset": asset_id, "layout": "image-left"},
            {"type": "finish"},
        ],
        "study_settings": {"cover_page": {"enabled": True, "image_asset": asset_id}},
    }


class StudyAssetTests(unittest.TestCase):
    def test_assets_are_content_addressed_and_typed_by_content(self) -> None:
        with temporary_studies_dir() as temporary:
            studies = Path(temporary)
            first = store_asset(studies, PNG)
            again = store_asset(studies, PNG)
            self.assertEqual(first, again)
            self.assertTrue(first.endswith(".png"))
            path, mime = resolve_asset(studies, first)
            self.assertEqual(path.read_bytes(), PNG)
            self.assertEqual(mime, "image/png")
            self.assertTrue(store_asset(studies, SVG).endswith(".svg"))

    def test_rejects_unknown_types_scripts_size_and_traversal(self) -> None:
        with temporary_studies_dir() as temporary:
            studies = Path(temporary)
            for bad in (b"GIF89a....", b"", b'<svg><script>alert(1)</script></svg>', PNG + b"0" * MAX_ASSET_BYTES):
                with self.assertRaises(StudyAssetError):
                    store_asset(studies, bad)
            for name in ("../secret.png", "abc.png", "a" * 64 + ".exe"):
                with self.assertRaises(StudyAssetError):
                    resolve_asset(studies, name)

    def test_saving_a_study_requires_its_images(self) -> None:
        with temporary_studies_dir() as temporary:
            studies = Path(temporary)
            asset_id = "0" * 64 + ".png"
            self.assertEqual(referenced_assets(study_with(asset_id)), [asset_id])
            with self.assertRaisesRegex(StudyAssetError, "not stored here"):
                require_assets(studies, study_with(asset_id))
            require_assets(studies, study_with(store_asset(studies, PNG)))


class StudyPackageTests(unittest.TestCase):
    def test_package_round_trips_study_and_images_into_another_installation(self) -> None:
        with temporary_studies_dir() as source, temporary_studies_dir() as target:
            asset_id = store_asset(Path(source), PNG)
            package = build_package(Path(source), study_with(asset_id))
            self.assertEqual(package, build_package(Path(source), study_with(asset_id)), "export is deterministic")

            config, assets = read_package(package)
            self.assertEqual(config, study_with(asset_id))
            self.assertEqual(assets, {asset_id: PNG})
            store_package_assets(Path(target), assets)
            require_assets(Path(target), config)

    def test_plain_json_studies_still_import(self) -> None:
        config, assets = read_package(json.dumps({"study_id": "Old", "questions": []}).encode())
        self.assertEqual(config["study_id"], "Old")
        self.assertEqual(assets, {})

    def test_damaged_or_hostile_packages_are_rejected(self) -> None:
        with temporary_studies_dir() as temporary:
            asset_id = store_asset(Path(temporary), PNG)
            good = build_package(Path(temporary), study_with(asset_id))

        def rewrite(change) -> bytes:
            source = zipfile.ZipFile(io.BytesIO(good))
            members = {info.filename: source.read(info.filename) for info in source.infolist()}
            change(members)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
            return buffer.getvalue()

        cases = {
            "checksum": lambda m: m.__setitem__(f"assets/{asset_id}", PNG + b"x"),
            "Unexpected file": lambda m: m.__setitem__("../evil.txt", b"x"),
            "no study.json": lambda m: m.pop("study.json"),
            "does not list": lambda m: m.pop(f"assets/{asset_id}"),
        }
        for message, change in cases.items():
            with self.subTest(message), self.assertRaisesRegex(StudyPackageError, message):
                read_package(rewrite(change))
        with self.assertRaises(StudyPackageError):
            read_package(b"PK\x03\x04 not really a zip")


if __name__ == "__main__":
    unittest.main()
