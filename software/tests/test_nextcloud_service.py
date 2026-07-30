from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.nextcloud_service import (
    NextcloudPublicShareClient,
    parse_share_link,
    test_connection,
)


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeSession:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = list(statuses)
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse(self.statuses.pop(0))


class NextcloudServiceTests(unittest.TestCase):
    def test_parse_share_link_supports_root_and_subpath_installations(self) -> None:
        self.assertEqual(
            parse_share_link("https://cloud.example/s/AbC_123-xy"),
            ("https://cloud.example", "AbC_123-xy"),
        )
        self.assertEqual(
            parse_share_link("https://cloud.example/nextcloud/index.php/s/token123/"),
            ("https://cloud.example/nextcloud", "token123"),
        )

    def test_parse_share_link_rejects_non_share_and_credential_urls(self) -> None:
        for invalid in (
            "",
            "https://cloud.example/files/token",
            "https://user:secret@cloud.example/s/token",
            "https://cloud.example/s/token?password=secret",
        ):
            with self.subTest(url=invalid), self.assertRaises(ValueError):
                parse_share_link(invalid)

    def test_primary_endpoint_is_selected_by_propfind(self) -> None:
        session = FakeSession([207])
        client = NextcloudPublicShareClient(
            "https://cloud.example/s/token",
            password="secret",
            session=session,
        )

        result = client.test_connection()

        self.assertTrue(result["ok"])
        self.assertEqual(result["endpoint"], "dav")
        self.assertEqual(session.calls[0]["method"], "PROPFIND")
        self.assertEqual(session.calls[0]["headers"], {"Depth": "0"})
        self.assertEqual(session.calls[0]["auth"], ("token", "secret"))
        self.assertNotIn("secret", session.calls[0]["url"])

    def test_legacy_endpoint_is_used_after_primary_404_or_405(self) -> None:
        for fallback_status in (404, 405):
            with self.subTest(status=fallback_status):
                session = FakeSession([fallback_status, 207])
                result = test_connection(
                    "https://cloud.example/s/token",
                    session=session,
                )

                self.assertTrue(result["ok"])
                self.assertEqual(result["endpoint"], "legacy_webdav")
                self.assertIn("/public.php/dav/files/token", session.calls[0]["url"])
                self.assertTrue(session.calls[1]["url"].endswith("/public.php/webdav"))

    def test_upload_creates_folders_idempotently_and_puts_each_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "result.json").write_bytes(b"{}")
            (folder / "signals.xdf").write_bytes(b"xdf")
            (folder / "ignored-folder").mkdir()
            session = FakeSession([207, 201, 405, 201, 204])
            client = NextcloudPublicShareClient(
                "https://cloud.example/s/token",
                session=session,
            )

            result = client.upload_session_folder(
                folder,
                study_id="Study A",
                participant_id="P 01",
            )

        self.assertTrue(result["ok"])
        self.assertEqual([item["name"] for item in result["uploaded"]], ["result.json", "signals.xdf"])
        self.assertEqual(
            [call["method"] for call in session.calls],
            ["PROPFIND", "MKCOL", "MKCOL", "PUT", "PUT"],
        )
        self.assertIn("/Study%20A", session.calls[1]["url"])
        self.assertIn("/P%2001", session.calls[2]["url"])

    def test_authentication_failure_is_plain_and_does_not_echo_secret(self) -> None:
        session = FakeSession([401])

        result = test_connection(
            "https://cloud.example/s/token",
            password="do-not-echo",
            session=session,
        )

        self.assertFalse(result["ok"])
        self.assertNotIn("do-not-echo", result["error"])
        self.assertIn("rejected", result["error"])

    def test_invalid_remote_identifier_is_rejected_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = FakeSession([207])
            client = NextcloudPublicShareClient(
                "https://cloud.example/s/token",
                session=session,
            )
            with self.assertRaises(ValueError):
                client.upload_session_folder(
                    Path(temp_dir),
                    study_id="../escape",
                    participant_id="p01",
                )


if __name__ == "__main__":
    unittest.main()
