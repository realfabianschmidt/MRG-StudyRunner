"""Minimal HTTP endpoint for downloading the Study Runner root certificate.

The tablet cannot bootstrap trust through the HTTPS server it does not trust
yet. This service therefore exposes one public file over plain HTTP and returns
404 for every other request target.
"""
from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading


CERTIFICATE_DOWNLOAD_PATH = "/study-runner-local-root-ca.crt"


class CertificateDownloadError(RuntimeError):
    """Raised when the one-file download listener cannot start safely."""


class _CertificateHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


@dataclass
class CertificateDownloadServer:
    """Running download listener with an idempotent shutdown operation."""

    _server: _CertificateHTTPServer
    _thread: threading.Thread

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def stop(self) -> None:
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join(timeout=5)
        self._server.server_close()


def start_certificate_download_server(
    certificate_file: Path,
    *,
    host: str = "0.0.0.0",
    port: int = 3002,
) -> CertificateDownloadServer:
    """Serve ``certificate_file`` at the single documented HTTP path."""
    resolved_file = Path(certificate_file).resolve()
    if not resolved_file.is_file():
        raise CertificateDownloadError(f"Root certificate file not found: {resolved_file}")

    handler = _handler_for(resolved_file)
    try:
        server = _CertificateHTTPServer((host, port), handler)
    except OSError as error:
        raise CertificateDownloadError(
            f"Certificate download port {port} is unavailable: {error}"
        ) from error

    thread = threading.Thread(
        target=server.serve_forever,
        name="study-runner-certificate-download",
        daemon=True,
    )
    thread.start()
    return CertificateDownloadServer(server, thread)


def certificate_download_url(host: str, port: int) -> str:
    """Build the tablet-facing download URL for one LAN host."""
    return f"http://{host}:{port}{CERTIFICATE_DOWNLOAD_PATH}"


def _handler_for(certificate_file: Path) -> type[BaseHTTPRequestHandler]:
    class CertificateDownloadHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            self._send_certificate(include_body=True)

        def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
            self._send_certificate(include_body=False)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            self.send_error(404)

        def do_PUT(self) -> None:  # noqa: N802 - stdlib handler API
            self.send_error(404)

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
            self.send_error(404)

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            self.send_error(404)

        def log_message(self, format: str, *args) -> None:
            return

        def _send_certificate(self, *, include_body: bool) -> None:
            if self.path != CERTIFICATE_DOWNLOAD_PATH:
                self.send_error(404)
                return

            try:
                payload = certificate_file.read_bytes()
            except OSError:
                self.send_error(503)
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/x-x509-ca-cert")
            self.send_header(
                "Content-Disposition",
                'attachment; filename="study-runner-local-root-ca.crt"',
            )
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if include_body:
                self.wfile.write(payload)

    return CertificateDownloadHandler
