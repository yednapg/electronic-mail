from __future__ import annotations

import unittest

from app.services.attachment_media import OCTET_STREAM, attachment_response_media_type


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
MACH_O = b"\xcf\xfa\xed\xfe" + b"\x00" * 16


class AttachmentMediaTypeTests(unittest.TestCase):
    def test_verified_inline_types_pass_when_signature_matches(self) -> None:
        cases = [
            ("image/png", PNG),
            ("image/jpeg", JPEG),
            ("image/jpg", JPEG),
            ("IMAGE/GIF; name=a.gif", GIF),
            ("image/webp", WEBP),
            ("image/heic", HEIC),
            ("application/pdf", PDF),
            ("application/pdf", b"\xef\xbb\xbf\n\n" + PDF),
        ]
        for declared, content in cases:
            with self.subTest(declared=declared):
                self.assertNotEqual(attachment_response_media_type(declared, content), OCTET_STREAM)

    def test_signature_mismatch_downgrades_to_opaque_download(self) -> None:
        cases = [
            ("image/png", MACH_O),
            ("image/png", JPEG),
            ("image/jpeg", b"<html><script>alert(1)</script></html>"),
            ("image/webp", PNG),
            ("image/heic", PNG),
            ("application/pdf", b"\x00" * 2048 + PDF),
            ("application/pdf", MACH_O),
        ]
        for declared, content in cases:
            with self.subTest(declared=declared):
                self.assertEqual(attachment_response_media_type(declared, content), OCTET_STREAM)

    def test_scriptable_or_executable_types_are_never_served_truthfully(self) -> None:
        for declared in (
            "text/html",
            "application/xhtml+xml",
            "image/svg+xml",
            "application/javascript",
            "text/javascript",
            "application/x-sh",
            "application/x-msdownload",
            "application/octet-stream",
            "application/x-apple-diskimage",
            "text/xml",
        ):
            with self.subTest(declared=declared):
                self.assertEqual(attachment_response_media_type(declared, b"anything"), OCTET_STREAM)

    def test_inert_document_types_pass_through(self) -> None:
        for declared in (
            "text/plain",
            "text/csv",
            "application/zip",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "audio/mpeg",
            "video/mp4",
        ):
            with self.subTest(declared=declared):
                self.assertEqual(attachment_response_media_type(declared, b"payload"), declared)

    def test_malformed_declarations_fall_back(self) -> None:
        for declared in (None, "", "image", "image/png\r\nX-Injected: 1", "../etc/passwd", "image/png" * 40):
            with self.subTest(declared=declared):
                self.assertEqual(attachment_response_media_type(declared, PNG), OCTET_STREAM)


if __name__ == "__main__":
    unittest.main()
