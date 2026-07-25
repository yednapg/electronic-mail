from __future__ import annotations

"""Privacy-preserving remote image assets for the native mail reader.

The reader never receives the sender-controlled URL.  Instead, HTML image
sources are replaced with an authenticated, message-bound opaque asset ID.
Fetching that asset validates DNS immediately before opening a pinned TLS
connection, follows only revalidated HTTPS redirects, and deliberately omits
cookies and referrers.
"""

from dataclasses import dataclass
from base64 import urlsafe_b64decode, urlsafe_b64encode
import hashlib
import hmac
import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import struct
from typing import Final
from urllib.parse import quote, urljoin, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings


MAX_REMOTE_IMAGE_BYTES: Final = 5 * 1024 * 1024
MAX_REMOTE_IMAGE_REDIRECTS: Final = 3
REMOTE_IMAGE_TIMEOUT_SECONDS: Final = 8.0
REMOTE_IMAGE_SCHEME: Final = "electronicmail-image"
_TOKEN_AAD_PREFIX: Final = b"electronic-mail:remote-image:v1:"
_TOKEN_KEY_CONTEXT: Final = b"electronic-mail:remote-image-key:v1\0"
_TOKEN_NONCE_KEY_CONTEXT: Final = b"electronic-mail:remote-image-nonce-key:v1\0"
_REMOTE_SOURCE_RE = re.compile(
    r"(?is)(\b(?:src|background)\s*=\s*)(['\"])(https://[^'\"]+)\2"
)
_SRCSET_RE = re.compile(r"(?is)\s+srcset\s*=\s*(['\"]).*?\1")
_CSS_URL_RE = re.compile(r"(?is)url\(\s*(['\"]?)(https://[^)'\"]+)\1\s*\)")
_IMG_TAG_RE = re.compile(r"(?is)<img\b[^>]*>")
_DIMENSION_ATTRIBUTE_RE = re.compile(
    r"(?is)\b(width|height)\s*=\s*(?:['\"]\s*)?([0-9]+(?:\.[0-9]+)?)"
)
_STYLE_ATTRIBUTE_RE = re.compile(r"(?is)\bstyle\s*=\s*(['\"])(.*?)\1")
_STYLE_DIMENSION_RE = re.compile(
    r"(?is)\b(width|height)\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*px(?:\s*!important)?"
)
_TRACKING_PATH_RE = re.compile(
    r"(?ix)(?:^|/)"
    r"(?:open(?:[-_.]?pixel)?|track(?:er|ing)?|beacon|pixel|webbug)"
    r"(?:[-_.][^/]*)?(?:/|$)"
)
_TRACKING_QUERY_KEYS: Final = frozenset(
    {
        "beacon",
        "open_pixel",
        "openpixel",
        "pixel",
        "tracking_pixel",
        "trackingpixel",
        "webbug",
    }
)


class RemoteImageError(RuntimeError):
    """Safe public failure while resolving or retrieving a remote image."""


class RemoteImageTokenError(RemoteImageError):
    pass


class RemoteImageBlocked(RemoteImageError):
    pass


@dataclass(frozen=True)
class RemoteImageAsset:
    message_id: str
    source_url: str


@dataclass(frozen=True)
class RemoteImagePayload:
    content: bytes
    mime_type: str
    etag: str


def rewrite_external_image_sources(
    settings: Settings,
    *,
    user_id: str,
    message_id: str,
    document: str | None,
) -> str | None:
    """Replace sender URLs with native custom-scheme opaque asset IDs."""
    if not document:
        return document

    def replace_attribute(match: re.Match[str]) -> str:
        source_url = html.unescape(match.group(3)).strip()
        try:
            asset_id = create_remote_image_asset_id(
                settings,
                user_id=user_id,
                message_id=message_id,
                source_url=source_url,
            )
        except RemoteImageBlocked:
            return f'{match.group(1)}{match.group(2)}about:blank{match.group(2)}'
        return (
            f"{match.group(1)}{match.group(2)}"
            f"{REMOTE_IMAGE_SCHEME}://asset/{asset_id}{match.group(2)}"
        )

    def replace_css(match: re.Match[str]) -> str:
        source_url = html.unescape(match.group(2)).strip()
        try:
            asset_id = create_remote_image_asset_id(
                settings,
                user_id=user_id,
                message_id=message_id,
                source_url=source_url,
            )
        except RemoteImageBlocked:
            return "url(about:blank)"
        return f"url('{REMOTE_IMAGE_SCHEME}://asset/{asset_id}')"

    # srcset could otherwise bypass the privacy endpoint even when src was
    # rewritten, so remove it. A single privacy-fetched source is sufficient.
    rewritten = _SRCSET_RE.sub("", document)
    # A sender-provided 1x1/2x2 hint is sufficient to suppress a recognized
    # tracking image before an opaque asset can ever be opened. The byte-level
    # detector in fetch_remote_image remains a second line of defense for
    # deceptive or missing markup dimensions.
    rewritten = _IMG_TAG_RE.sub(
        lambda match: _block_tracking_image_tag(match.group(0)),
        rewritten,
    )
    rewritten = _REMOTE_SOURCE_RE.sub(replace_attribute, rewritten)
    return _CSS_URL_RE.sub(replace_css, rewritten)


def create_remote_image_asset_id(
    settings: Settings,
    *,
    user_id: str,
    message_id: str,
    source_url: str,
) -> str:
    normalized = _validated_https_url(source_url)
    if _is_recognized_tracking_url(normalized):
        raise RemoteImageBlocked("Tracking image was suppressed")
    payload = json.dumps(
        {"message_id": message_id, "source_url": normalized},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    # Stable, message-bound tokens let the native 50 MB cache survive reader
    # reconstruction and offline relaunch. The nonce is a PRF of the exact
    # authenticated plaintext under a separately derived key: identical input
    # reuses the identical nonce/plaintext pair, while distinct inputs collide
    # only with negligible HMAC truncation probability.
    nonce = hmac.new(
        _token_nonce_key(settings),
        _token_aad(user_id) + b"\0" + payload,
        hashlib.sha256,
    ).digest()[:12]
    encrypted = AESGCM(_token_key(settings)).encrypt(
        nonce,
        payload,
        _token_aad(user_id),
    )
    return urlsafe_b64encode(nonce + encrypted).decode("ascii").rstrip("=")


def decode_remote_image_asset_id(
    settings: Settings,
    *,
    user_id: str,
    asset_id: str,
) -> RemoteImageAsset:
    if not asset_id or len(asset_id) > 8192 or not re.fullmatch(r"[A-Za-z0-9_-]+", asset_id):
        raise RemoteImageTokenError("Invalid image asset")
    try:
        raw = urlsafe_b64decode(asset_id + "=" * (-len(asset_id) % 4))
        if len(raw) < 29:
            raise ValueError("short token")
        plaintext = AESGCM(_token_key(settings)).decrypt(
            raw[:12],
            raw[12:],
            _token_aad(user_id),
        )
        decoded = json.loads(plaintext)
        message_id = str(decoded["message_id"])
        source_url = _validated_https_url(str(decoded["source_url"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RemoteImageTokenError("Invalid image asset") from exc
    except Exception as exc:
        raise RemoteImageTokenError("Invalid image asset") from exc
    if not message_id or len(message_id) > 1024:
        raise RemoteImageTokenError("Invalid image asset")
    return RemoteImageAsset(message_id=message_id, source_url=source_url)


def fetch_remote_image(source_url: str) -> RemoteImagePayload:
    current_url = _validated_https_url(source_url)
    for redirect_count in range(MAX_REMOTE_IMAGE_REDIRECTS + 1):
        if _is_recognized_tracking_url(current_url):
            raise RemoteImageBlocked("Tracking image was suppressed")
        response, connection = _open_public_https(current_url)
        try:
            if response.status in {301, 302, 303, 307, 308}:
                if redirect_count >= MAX_REMOTE_IMAGE_REDIRECTS:
                    raise RemoteImageBlocked("Remote image redirected too many times")
                location = response.getheader("Location")
                if not location:
                    raise RemoteImageBlocked("Remote image redirect is invalid")
                current_url = _validated_https_url(urljoin(current_url, location))
                if _is_recognized_tracking_url(current_url):
                    raise RemoteImageBlocked("Tracking image was suppressed")
                continue
            if response.status != 200:
                raise RemoteImageError("Remote image could not be loaded")

            mime_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            if not mime_type.startswith("image/") or mime_type in {
                "image/svg+xml",
                "image/svg",
            }:
                raise RemoteImageBlocked("Remote resource is not a safe image")
            content_length = response.getheader("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_REMOTE_IMAGE_BYTES:
                        raise RemoteImageBlocked("Remote image exceeds 5 MB")
                except ValueError as exc:
                    raise RemoteImageBlocked("Remote image has an invalid size") from exc

            content = response.read(MAX_REMOTE_IMAGE_BYTES + 1)
            if len(content) > MAX_REMOTE_IMAGE_BYTES:
                raise RemoteImageBlocked("Remote image exceeds 5 MB")
            if not content:
                raise RemoteImageError("Remote image is empty")
            if _is_tracking_pixel(content, mime_type=mime_type):
                raise RemoteImageBlocked("Tracking image was suppressed")
            etag = f'"{hashlib.sha256(content).hexdigest()}"'
            return RemoteImagePayload(content=content, mime_type=mime_type, etag=etag)
        finally:
            response.close()
            connection.close()
    raise RemoteImageBlocked("Remote image redirected too many times")


def _validated_https_url(value: str) -> str:
    if len(value) > 8192:
        raise RemoteImageBlocked("Remote image URL is too long")
    if "\\" in value or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise RemoteImageBlocked("Remote image URL is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise RemoteImageBlocked("Remote image URL is invalid") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RemoteImageBlocked("Remote images must use HTTPS")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise RemoteImageBlocked("Remote image URL is invalid")
    hostname = parsed.hostname.rstrip(".").lower()
    if (
        hostname in {"localhost", "localhost.localdomain"}
        or hostname.endswith((".localhost", ".local", ".internal", ".home.arpa"))
    ):
        raise RemoteImageBlocked("Remote image host is not public")
    if port is not None and not 1 <= port <= 65535:
        raise RemoteImageBlocked("Remote image port is invalid")
    return parsed.geturl()


def _block_tracking_image_tag(tag: str) -> str:
    if not _html_tag_looks_like_tracking_pixel(tag):
        return tag
    return _REMOTE_SOURCE_RE.sub(
        lambda match: f'{match.group(1)}{match.group(2)}about:blank{match.group(2)}',
        tag,
    )


def _html_tag_looks_like_tracking_pixel(tag: str) -> bool:
    dimensions: dict[str, float] = {}
    for match in _DIMENSION_ATTRIBUTE_RE.finditer(tag):
        try:
            dimensions[match.group(1).lower()] = float(match.group(2))
        except ValueError:
            continue
    for style_match in _STYLE_ATTRIBUTE_RE.finditer(tag):
        for match in _STYLE_DIMENSION_RE.finditer(style_match.group(2)):
            try:
                dimensions[match.group(1).lower()] = float(match.group(2))
            except ValueError:
                continue
    return (
        dimensions.get("width", float("inf")) <= 2
        and dimensions.get("height", float("inf")) <= 2
    )


def _is_recognized_tracking_url(value: str) -> bool:
    """Conservatively recognize explicit tracking-pixel URL conventions.

    This deliberately avoids broad terms such as ``open`` query parameters or
    ``recipient`` so legitimate images are not suppressed merely for being
    personalized. Markup dimensions and decoded image dimensions cover those
    remaining cases.
    """
    parsed = urlsplit(value)
    path = parsed.path.lower()
    if _TRACKING_PATH_RE.search(path):
        return True
    query = parsed.query.lower()
    if not query:
        return False
    query_dimensions: dict[str, float] = {}
    for component in query.split("&"):
        key, _, raw_value = component.partition("=")
        if key in _TRACKING_QUERY_KEYS:
            return True
        if key in {"width", "w", "height", "h"}:
            try:
                query_dimensions[key] = float(raw_value)
            except ValueError:
                continue
    width = query_dimensions.get("width", query_dimensions.get("w", float("inf")))
    height = query_dimensions.get("height", query_dimensions.get("h", float("inf")))
    return 0 < width <= 2 and 0 < height <= 2


def _open_public_https(url: str) -> tuple[http.client.HTTPResponse, ssl.SSLSocket]:
    parsed = urlsplit(_validated_https_url(url))
    raw_hostname = parsed.hostname or ""
    try:
        parsed_ip = ipaddress.ip_address(raw_hostname)
        hostname = parsed_ip.compressed
    except ValueError:
        try:
            hostname = raw_hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise RemoteImageBlocked("Remote image host is invalid") from exc
    port = parsed.port or 443
    addresses = _public_addresses(hostname, port)
    last_error: OSError | ssl.SSLError | None = None
    for family, sockaddr in addresses:
        raw_socket: socket.socket | None = None
        tls_socket: ssl.SSLSocket | None = None
        try:
            raw_socket = socket.socket(family, socket.SOCK_STREAM)
            raw_socket.settimeout(REMOTE_IMAGE_TIMEOUT_SECONDS)
            raw_socket.connect(sockaddr)
            tls_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=hostname)
            raw_socket = None
            request_target = quote(
                parsed.path or "/",
                safe="/%:@-._~!$&'()*+,;=",
            )
            if parsed.query:
                request_target += "?" + quote(
                    parsed.query,
                    safe="/?%:@-._~!$&'()*+,;=",
                )
            host_name_for_header = f"[{hostname}]" if ":" in hostname else hostname
            host_header = host_name_for_header if port == 443 else f"{host_name_for_header}:{port}"
            request_bytes = (
                f"GET {request_target} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                "Accept: image/avif,image/webp,image/*\r\n"
                "User-Agent: ElectronicMail-PrivacyImage/1.0\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii", errors="strict")
            tls_socket.sendall(request_bytes)
            response = http.client.HTTPResponse(tls_socket)
            response.begin()
            return response, tls_socket
        except (OSError, ssl.SSLError) as exc:
            last_error = exc
            if tls_socket is not None:
                tls_socket.close()
            if raw_socket is not None:
                raw_socket.close()
    raise RemoteImageError("Remote image host could not be reached") from last_error


def _public_addresses(hostname: str, port: int) -> list[tuple[int, tuple]]:
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RemoteImageError("Remote image host could not be resolved") from exc
    addresses: list[tuple[int, tuple]] = []
    seen: set[tuple[int, str, int]] = set()
    for family, socktype, _protocol, _canonname, sockaddr in records:
        if socktype != socket.SOCK_STREAM or family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            raise RemoteImageBlocked("Remote image host resolved to a non-public address")
        key = (family, str(address), port)
        if key in seen:
            continue
        seen.add(key)
        addresses.append((family, sockaddr))
    if not addresses:
        raise RemoteImageBlocked("Remote image host has no public address")
    return addresses


def _is_tracking_pixel(content: bytes, *, mime_type: str) -> bool:
    dimensions = _image_dimensions(content, mime_type=mime_type)
    return dimensions is not None and dimensions[0] <= 2 and dimensions[1] <= 2


def _image_dimensions(content: bytes, *, mime_type: str) -> tuple[int, int] | None:
    if mime_type == "image/png" and len(content) >= 24 and content[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", content[16:24])
    if mime_type == "image/gif" and len(content) >= 10 and content[:6] in {b"GIF87a", b"GIF89a"}:
        return struct.unpack("<HH", content[6:10])
    if mime_type in {"image/jpeg", "image/jpg"} and content[:2] == b"\xff\xd8":
        index = 2
        while index + 9 <= len(content):
            if content[index] != 0xFF:
                index += 1
                continue
            marker = content[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(content):
                break
            segment_length = int.from_bytes(content[index : index + 2], "big")
            if segment_length < 2 or index + segment_length > len(content):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if segment_length >= 7:
                    height = int.from_bytes(content[index + 3 : index + 5], "big")
                    width = int.from_bytes(content[index + 5 : index + 7], "big")
                    return width, height
                break
            index += segment_length
    if mime_type == "image/webp" and len(content) >= 30 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        chunk = content[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(content[24:27], "little")
            height = 1 + int.from_bytes(content[27:30], "little")
            return width, height
    return None


def _token_key(settings: Settings) -> bytes:
    return hashlib.sha256(_TOKEN_KEY_CONTEXT + settings.app_encryption_key.encode("utf-8")).digest()


def _token_nonce_key(settings: Settings) -> bytes:
    return hashlib.sha256(
        _TOKEN_NONCE_KEY_CONTEXT + settings.app_encryption_key.encode("utf-8")
    ).digest()


def _token_aad(user_id: str) -> bytes:
    return _TOKEN_AAD_PREFIX + user_id.encode("utf-8")
