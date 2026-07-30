from __future__ import annotations

from pathlib import Path
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.certificate_download_service import (
    CERTIFICATE_DOWNLOAD_PATH,
    CertificateDownloadError,
    start_certificate_download_server,
)


class CertificateDownloadServiceTests(unittest.TestCase):
    def test_listener_serves_only_the_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            certificate_file = Path(temp_dir) / "root.crt"
            certificate_payload = b"test root certificate"
            certificate_file.write_bytes(certificate_payload)
            server = start_certificate_download_server(
                certificate_file,
                host="127.0.0.1",
                port=0,
            )
            base_url = f"http://127.0.0.1:{server.port}"
            try:
                with urllib.request.urlopen(
                    f"{base_url}{CERTIFICATE_DOWNLOAD_PATH}",
                    timeout=2,
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), certificate_payload)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(
                        response.headers["Content-Disposition"],
                        'attachment; filename="study-runner-local-root-ca.crt"',
                    )

                for rejected_target in (
                    "/",
                    "/root.crt",
                    f"{CERTIFICATE_DOWNLOAD_PATH}?download=1",
                    f"{CERTIFICATE_DOWNLOAD_PATH}/extra",
                ):
                    with self.subTest(target=rejected_target):
                        with self.assertRaises(urllib.error.HTTPError) as caught:
                            urllib.request.urlopen(f"{base_url}{rejected_target}", timeout=2)
                        self.assertEqual(caught.exception.code, 404)
            finally:
                server.stop()

    def test_head_has_headers_without_a_body(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            certificate_file = Path(temp_dir) / "root.crt"
            certificate_file.write_bytes(b"certificate")
            server = start_certificate_download_server(
                certificate_file,
                host="127.0.0.1",
                port=0,
            )
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.port}{CERTIFICATE_DOWNLOAD_PATH}",
                    method="HEAD",
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Content-Length"], "11")
                    self.assertEqual(response.read(), b"")
            finally:
                server.stop()

    def test_missing_file_is_rejected_before_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(CertificateDownloadError):
                start_certificate_download_server(
                    Path(temp_dir) / "missing.crt",
                    host="127.0.0.1",
                    port=0,
                )

    def test_port_collision_has_a_clear_service_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            certificate_file = Path(temp_dir) / "root.crt"
            certificate_file.write_bytes(b"certificate")
            occupied = socket.socket()
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            port = occupied.getsockname()[1]
            try:
                with self.assertRaisesRegex(
                    CertificateDownloadError,
                    f"port {port} is unavailable",
                ):
                    start_certificate_download_server(
                        certificate_file,
                        host="127.0.0.1",
                        port=port,
                    )
            finally:
                occupied.close()


if __name__ == "__main__":
    unittest.main()
