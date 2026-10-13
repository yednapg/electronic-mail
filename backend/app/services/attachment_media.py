from __future__ import annotations

"""Server-chosen media types for sender-supplied attachments.

Gmail reports the MIME type the sender declared. That value is never trusted
for the response ``Content-Type``: only a small set of inert types pass
through, and the ones clients may render inline must also match the first
bytes of the payload. Everything else is served as an opaque download.
"""

import re
from typing import Final

OCTET_STREAM: Final = "application/octet-stream"
_MEDIA_TYPE_RE: Final = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$")
_PDF_SCAN_BYTES: Final = 1024

# Types a browser or native viewer may present inline. Each entry's payload is
# verified against its magic signature before the declared type is honoured.
_SIGNATURE_VERIFIED_TYPES: Final[dict[str, tuple[bytes, ...]]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/bmp": (b"BM",),
    "image/tiff": (b"II*\x00", b"MM\x00*"),
}
_HEIF_BRANDS: Final = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heif"})

# Inert types that are safe to label truthfully without a signature check.
# Markup, scriptable vector formats, and anything executable are deliberately
# absent; they fall through to the opaque download type.
_PASSTHROUGH_TYPES: Final = frozenset(
    {
        "text/plain",
        "text/csv",
        "text/calendar",
        "text/vcard",
        "audio/aac",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/x-wav",
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "application/zip",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)


def attachment_response_media_type(declared: str | None, content: bytes) -> str:
    """Return the ``Content-Type`` to serve for one attachment payload."""
    normalized = _normalized_media_type(declared)
    if normalized is None:
        return OCTET_STREAM
    if normalized in _SIGNATURE_VERIFIED_TYPES:
        return normalized if content.startswith(_SIGNATURE_VERIFIED_TYPES[normalized]) else OCTET_STREAM
    if normalized == "image/webp":
        return normalized if _is_webp(content) else OCTET_STREAM
    if normalized in {"image/heic", "image/heif"}:
        return normalized if _is_heif(content) else OCTET_STREAM
    if normalized == "application/pdf":
        return normalized if _is_pdf(content) else OCTET_STREAM
    if normalized in _PASSTHROUGH_TYPES:
        return normalized
    return OCTET_STREAM


def _normalized_media_type(declared: str | None) -> str | None:
    if not declared:
        return None
    candidate = declared.split(";", 1)[0].strip().lower()
    if candidate == "image/jpg":
        candidate = "image/jpeg"
    return candidate if _MEDIA_TYPE_RE.fullmatch(candidate) else None


def _is_webp(content: bytes) -> bool:
    return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"


def _is_heif(content: bytes) -> bool:
    return len(content) >= 12 and content[4:8] == b"ftyp" and content[8:12] in _HEIF_BRANDS


def _is_pdf(content: bytes) -> bool:
    # The PDF specification tolerates leading junk before the header, and some
    # mailers prepend a UTF-8 BOM or blank lines. Keep the scan window short so
    # a decoy cannot bury the signature inside otherwise arbitrary content.
    return b"%PDF-" in content[:_PDF_SCAN_BYTES]
