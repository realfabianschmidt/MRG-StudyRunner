"""Operator fonts: signatures, slots, choices, and manifest-only resolution."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.runtime_core.settings import font_service  # noqa: E402

WOFF2 = b"wOF2" + b"\x00" * 64
TTF = b"\x00\x01\x00\x00" + b"\x00" * 64


def temporary_branding_dir() -> tempfile.TemporaryDirectory[str]:
    root = PROJECT_ROOT.parent / ".tmp" / "font-tests"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


class FontServiceTests(unittest.TestCase):
    def test_defaults_before_anything_is_configured(self) -> None:
        with temporary_branding_dir() as temporary:
            fonts = font_service.public_manifest(Path(temporary))
            self.assertEqual(fonts["heading"]["choice"], "default")
            self.assertFalse(fonts["body"]["has_upload"])

    def test_upload_selects_the_font_and_replacing_deletes_the_old_file(self) -> None:
        with temporary_branding_dir() as temporary:
            branding = Path(temporary)
            fonts = font_service.store_font(branding, "heading", "Brand Sans.woff2", WOFF2)
            self.assertEqual(fonts["heading"], {"choice": "uploaded", "has_upload": True, "name": "Brand Sans", "version": fonts["heading"]["version"]})
            path, mime = font_service.resolve_font(branding, "heading")
            self.assertEqual((path.read_bytes(), mime), (WOFF2, "font/woff2"))

            font_service.store_font(branding, "heading", "Other.ttf", TTF)
            self.assertFalse(path.exists())
            self.assertEqual(font_service.resolve_font(branding, "heading")[1], "font/ttf")

            fonts = font_service.remove_font(branding, "heading")
            self.assertEqual(fonts["heading"]["choice"], "default")
            with self.assertRaises(font_service.FontError):
                font_service.resolve_font(branding, "heading")

    def test_rejects_wrong_signature_size_slot_and_choice(self) -> None:
        with temporary_branding_dir() as temporary:
            branding = Path(temporary)
            for payload in (b"<html>", b"", b"wOF2" + b"0" * font_service.MAX_FONT_BYTES):
                with self.assertRaises(font_service.FontError):
                    font_service.store_font(branding, "body", "x.woff2", payload)
            with self.assertRaises(font_service.FontError):
                font_service.store_font(branding, "caption", "x.woff2", WOFF2)
            with self.assertRaises(font_service.FontError):
                font_service.set_choices(branding, {"body": "uploaded"})
            with self.assertRaises(font_service.FontError):
                font_service.set_choices(branding, {"body": "comic-sans"})
            self.assertEqual(font_service.set_choices(branding, {"body": "serif"})["body"]["choice"], "serif")

    def test_hand_edited_manifest_cannot_reach_outside_the_fonts_folder(self) -> None:
        with temporary_branding_dir() as temporary:
            branding = Path(temporary)
            (branding / "secret.woff2").write_bytes(WOFF2)
            font_service.fonts_dir(branding).mkdir(parents=True)
            (font_service.fonts_dir(branding) / font_service.MANIFEST_NAME).write_text(
                json.dumps({"body": {"choice": "uploaded", "file": "../secret.woff2", "format": "woff2"}}),
                encoding="utf-8",
            )
            with self.assertRaises(font_service.FontError):
                font_service.resolve_font(branding, "body")
            self.assertEqual(font_service.public_manifest(branding)["body"]["choice"], "default")


if __name__ == "__main__":
    unittest.main()
