from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from threading import Thread
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_web_runtime import WebRuntimeError, validate_runtime  # noqa: E402


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; font-src 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=()",
}


def valid_pages() -> dict[str, tuple[int, dict[str, str], str]]:
    return {
        "/healthz": (
            200,
            {"Cache-Control": "no-store"},
            '{"status":"ready","checks":{"backend":true,"download":true,"legal":true}}',
        ),
        "/": (
            200,
            SECURITY_HEADERS.copy(),
            '<html>A focused, native Gmail client for macOS. <a href="https://downloads.electronicmail.app/ElectronicMail.dmg">Download for macOS</a></html>',
        ),
        "/privacy": (
            200,
            {},
            "<html>Effective 2026-07-13. CI hosting providers. CI region. protected backups is scheduled to expire after 14 days.</html>",
        ),
        "/terms": (200, {}, "<html><h1>Terms of Service</h1></html>"),
        "/support": (
            200,
            {},
            '<html>support@electronicmail.app Report a security issue <a href="https://status.electronicmail.app">Electronic Mail service status page</a></html>',
        ),
        "/post-login": (
            200,
            {"Cache-Control": "private, no-store, max-age=0"},
            "<html>Connection status unavailable. Try again.</html>",
        ),
        "/gmail": (404, {}, "Not Found"),
        "/dashboard": (404, {}, "Not Found"),
        "/api/dashboard": (404, {}, "Not Found"),
    }


@contextmanager
def fixture_server(pages: dict[str, tuple[int, dict[str, str], str]]):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            status, headers, body = pages.get(self.path, (500, {}, "missing fixture"))
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


class WebRuntimeVerificationTests(unittest.TestCase):
    def validate(self, pages: dict[str, tuple[int, dict[str, str], str]]) -> None:
        with fixture_server(pages) as web_url:
            validate_runtime(
                web_url=web_url,
                download_url="https://downloads.electronicmail.app/ElectronicMail.dmg",
                support_email="support@electronicmail.app",
                status_page_url="https://status.electronicmail.app",
                effective_date="2026-07-13",
                hosting_providers="CI hosting providers",
                data_regions="CI region",
                backup_retention_days=14,
            )

    def test_complete_native_only_runtime_passes(self) -> None:
        self.validate(valid_pages())

    def test_public_launch_readiness_must_be_complete(self) -> None:
        pages = valid_pages()
        pages["/healthz"] = (
            503,
            {"Cache-Control": "no-store"},
            '{"status":"not_ready","checks":{"backend":true,"download":false,"legal":true}}',
        )
        with self.assertRaisesRegex(WebRuntimeError, "/healthz returned HTTP 503"):
            self.validate(pages)

    def test_public_launch_readiness_must_not_be_cached(self) -> None:
        pages = valid_pages()
        pages["/healthz"] = (200, {}, pages["/healthz"][2])
        with self.assertRaisesRegex(WebRuntimeError, "/healthz is missing Cache-Control: no-store"):
            self.validate(pages)

    def test_retention_copy_failure_is_named(self) -> None:
        pages = valid_pages()
        pages["/privacy"] = (
            200,
            {},
            pages["/privacy"][2].replace(
                "protected backups is scheduled to expire after 14 days",
                "retention unavailable",
            ),
        )
        with self.assertRaisesRegex(WebRuntimeError, "configured retention disclosure"):
            self.validate(pages)

    def test_missing_security_header_failure_is_named(self) -> None:
        pages = valid_pages()
        headers = SECURITY_HEADERS.copy()
        headers.pop("X-Frame-Options")
        pages["/"] = (pages["/"][0], headers, pages["/"][2])
        with self.assertRaisesRegex(WebRuntimeError, "X-Frame-Options"):
            self.validate(pages)

    def test_retired_web_route_must_return_not_found(self) -> None:
        pages = valid_pages()
        pages["/gmail"] = (200, {}, "retired UI")
        with self.assertRaisesRegex(WebRuntimeError, "/gmail returned HTTP 200"):
            self.validate(pages)

    def test_retired_web_route_redirect_does_not_count_as_not_found(self) -> None:
        pages = valid_pages()
        pages["/gmail"] = (302, {"Location": "/"}, "redirect")
        with self.assertRaisesRegex(WebRuntimeError, "/gmail returned HTTP 302"):
            self.validate(pages)


if __name__ == "__main__":
    unittest.main()
