from __future__ import annotations

"""Allowlist HTML sanitizer for mail that will be rendered.

The reader used to delete a few dangerous tags with regular expressions and
then hope WebKit agreed about what was left. This parser re-serializes the
document instead: anything that is not on the allowlist is never emitted, URL
attributes are reduced to a canonical scheme, and every attribute value is
quoted. A second pass over already-sanitized markup is stable, so stored rows
can be cleaned again at read time.
"""

import html
import re
from html.parser import HTMLParser


_ALLOWED_TAGS = frozenset(
    {
        "a",
        "abbr",
        "address",
        "article",
        "aside",
        "b",
        "big",
        "blockquote",
        "body",
        "br",
        "caption",
        "center",
        "cite",
        "code",
        "col",
        "colgroup",
        "dd",
        "del",
        "dfn",
        "div",
        "dl",
        "dt",
        "em",
        "figcaption",
        "figure",
        "font",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "head",
        "header",
        "hr",
        "html",
        "i",
        "img",
        "ins",
        "kbd",
        "label",
        "legend",
        "li",
        "main",
        "mark",
        "nav",
        "ol",
        "p",
        "pre",
        "q",
        "s",
        "samp",
        "section",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "title",
        "tr",
        "tt",
        "u",
        "ul",
        "wbr",
    }
)
_VOID_TAGS = frozenset({"br", "col", "hr", "img", "wbr"})
# These are discarded, but they have no end tag. Treating them as containers
# would hide every element that follows an unclosed <meta> or <link>.
_VOID_DROP_TAGS = frozenset({"base", "embed", "input", "link", "meta", "source"})
# Dropped together with their contents. Unknown tags are unwrapped instead,
# so their text survives while the element itself cannot carry a URL.
_DROP_TAGS = frozenset(
    {
        "applet",
        "audio",
        "base",
        "button",
        "embed",
        "form",
        "frame",
        "frameset",
        "iframe",
        "input",
        "link",
        "math",
        "meta",
        "noscript",
        "object",
        "picture",
        "script",
        "select",
        "source",
        "svg",
        "template",
        "textarea",
        "video",
    }
)
_PRESENTATIONAL_ATTRS = frozenset(
    {
        "abbr",
        "align",
        "alt",
        "aria-hidden",
        "axis",
        "bgcolor",
        "border",
        "cellpadding",
        "cellspacing",
        "char",
        "charoff",
        "class",
        "clear",
        "color",
        "colspan",
        "compact",
        "dir",
        "face",
        "frame",
        "headers",
        "height",
        "hspace",
        "id",
        "lang",
        "name",
        "noshade",
        "nowrap",
        "reversed",
        "role",
        "rowspan",
        "rules",
        "scope",
        "shape",
        "size",
        "span",
        "start",
        "summary",
        "title",
        "valign",
        "vspace",
        "width",
        "wrap",
    }
)
_URL_ATTRS = {
    "a": frozenset({"href"}),
    "img": frozenset({"src"}),
    "body": frozenset({"background"}),
    "table": frozenset({"background"}),
    "td": frozenset({"background"}),
    "th": frozenset({"background"}),
}
_DROPPED_URL_ATTRS = frozenset(
    {
        "action",
        "formaction",
        "longdesc",
        "ping",
        "poster",
        "srcset",
        "usemap",
        "xlink:href",
        "xmlns",
    }
)
_DATA_IMAGE_RE = re.compile(
    r"(?i)^data:image/(?:png|jpeg|jpg|gif|webp|bmp);base64,[a-z0-9+/=\s]+$"
)
_MAX_DATA_URL_CHARS = 8_000_000
_CSS_URL_RE = re.compile(r"""(?is)url\(\s*(['"]?)([^)'"]+)\1\s*\)""")
_CSS_IMPORT_RE = re.compile(r"(?is)@import\b[^;]*;?")
_CSS_DANGER_RE = re.compile(
    r"(?is)(?:"
    r"expression\s*\([^)]*\)"
    r"|behavior\s*:[^;}]*"
    r"|-moz-binding\s*:[^;}]*"
    r"|javascript\s*:[^;}\s]*"
    r"|image-set\s*\([^)]*\)"
    r")"
)


def sanitize_email_markup(value: str, *, keep_style: bool) -> str:
    """Return allowlisted markup. ``keep_style`` preserves layout CSS."""
    parser = _EmailHTMLSanitizer(keep_style=keep_style)
    parser.feed(value)
    parser.close()
    return "".join(parser.parts).strip()


def sanitize_css(value: str) -> str:
    """Drop remote and scriptable CSS while leaving ordinary layout rules intact."""

    def replace_url(match: re.Match[str]) -> str:
        canonical = canonical_https_url(html.unescape(match.group(2)).strip())
        if canonical is None:
            return "none"
        # Single quotes survive inside a double-quoted style attribute, so the
        # privacy rewriter can still recognize the URL after HTML escaping.
        return f"url('{canonical}')"

    cleaned = _CSS_IMPORT_RE.sub("", value)
    cleaned = _CSS_DANGER_RE.sub("none", cleaned)
    return _CSS_URL_RE.sub(replace_url, cleaned)


def canonical_https_url(value: str) -> str | None:
    """Return an https URL, upgrading http and protocol-relative forms."""
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 8192
        or "\\" in candidate
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
    ):
        return None
    lowered = candidate.lower()
    if lowered.startswith("https://"):
        return candidate
    if lowered.startswith("http://"):
        return "https://" + candidate[len("http://") :]
    if candidate.startswith("//"):
        return "https:" + candidate
    return None


class _EmailHTMLSanitizer(HTMLParser):
    def __init__(self, *, keep_style: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.keep_style = keep_style
        self.parts: list[str] = []
        self._drop_stack: list[str] = []
        self._in_style = False
        self._style_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._drop_stack:
            if tag in _DROP_TAGS and tag not in _VOID_DROP_TAGS:
                self._drop_stack.append(tag)
            return
        if tag == "style":
            if not self.keep_style:
                self._drop_stack.append(tag)
                return
            self._in_style = True
            self._style_parts = []
            return
        if tag in _DROP_TAGS:
            if tag not in _VOID_DROP_TAGS:
                self._drop_stack.append(tag)
            return
        if tag not in _ALLOWED_TAGS:
            return
        self.parts.append(_render_start(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._in_style and tag == "style":
            css = sanitize_css("".join(self._style_parts)).replace("<", "")
            self._in_style = False
            self._style_parts = []
            if css.strip():
                self.parts.append(f"<style>{css}</style>")
            return
        if self._drop_stack:
            if self._drop_stack[-1] == tag:
                self._drop_stack.pop()
            return
        if tag in _VOID_TAGS or tag not in _ALLOWED_TAGS:
            return
        self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._drop_stack or not data:
            return
        if self._in_style:
            self._style_parts.append(data)
            return
        self.parts.append(html.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        if self._drop_stack or self._in_style:
            return
        if decl.lower().startswith("doctype html"):
            self.parts.append("<!doctype html>")


def _escape_attribute(value: str) -> str:
    """Escape an attribute without turning CSS quotes into character references.

    ``html.escape(..., quote=True)`` rewrites ``'`` to ``&#x27;``, which hides
    ``url('https://...')`` from the privacy rewriter. Single quotes are left
    intact because attributes are always delimited with double quotes.
    """
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _render_start(tag: str, attrs: list[tuple[str, str | None]]) -> str:
    rendered: list[str] = []
    for name, value in attrs:
        cleaned = _clean_attribute(tag, name, value)
        if cleaned is None:
            continue
        attr_name, attr_value = cleaned
        if attr_value is None:
            rendered.append(attr_name)
        else:
            rendered.append(f'{attr_name}="{_escape_attribute(attr_value)}"')
    suffix = f" {' '.join(rendered)}" if rendered else ""
    return f"<{tag}{suffix}>"


def _clean_attribute(tag: str, name: str, value: str | None) -> tuple[str, str | None] | None:
    attr = name.lower().strip()
    if not attr or attr.startswith("on") or attr in _DROPPED_URL_ATTRS or ":" in attr:
        return None
    if attr == "style":
        if value is None:
            return None
        css = sanitize_css(value)
        return ("style", css) if css.strip() else None
    if attr == "target":
        if (value or "").strip().lower() == "_blank":
            return ("target", "_blank")
        return None
    if attr in _URL_ATTRS.get(tag, frozenset()):
        cleaned = _clean_url(tag, attr, value or "")
        return (attr, cleaned) if cleaned is not None else None
    if attr in _PRESENTATIONAL_ATTRS:
        if value is None:
            return (attr, None)
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            return None
        return (attr, value)
    return None


def _clean_url(tag: str, attr: str, value: str) -> str | None:
    candidate = value.strip()
    if not candidate or any(ord(character) < 32 or ord(character) == 127 for character in candidate):
        return None
    lowered = candidate.lower()
    if attr in {"src", "background"}:
        if lowered.startswith("cid:"):
            cid = candidate[4:].strip().strip("<>")
            if cid and not any(character in cid for character in "\"'<>"):
                return f"cid:{cid}"
            return None
        if len(candidate) <= _MAX_DATA_URL_CHARS and _DATA_IMAGE_RE.fullmatch(candidate):
            return candidate
        return canonical_https_url(candidate)
    if lowered.startswith(("javascript:", "vbscript:", "data:")):
        return None
    if candidate.startswith("#") or lowered.startswith(("mailto:", "tel:")):
        return candidate
    https = canonical_https_url(candidate)
    if https is not None:
        return https
    if ":" not in candidate and not candidate.startswith("//"):
        return candidate
    return None
