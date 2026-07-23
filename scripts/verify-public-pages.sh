#!/usr/bin/env bash
set -euo pipefail

WEB_URL="${WEB_URL:-}"
EXPECTED_DMG_SHA256="${EXPECTED_DMG_SHA256:-}"
[ -n "$WEB_URL" ] || { echo "WEB_URL is required" >&2; exit 1; }
WEB_URL="${WEB_URL%/}"
[[ "$WEB_URL" == https://* ]] || { echo "WEB_URL must use HTTPS" >&2; exit 1; }
if [ -n "$EXPECTED_DMG_SHA256" ]; then
  [[ "$EXPECTED_DMG_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "EXPECTED_DMG_SHA256 must be a lowercase SHA-256 digest" >&2
    exit 1
  }
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

curl --silent --show-error --fail-with-body --retry 3 --max-time 30 \
  --dump-header "$tmp_dir/home.headers" "$WEB_URL/" > "$tmp_dir/home.html"
grep -qi '<html' "$tmp_dir/home.html"
grep -qi 'native Gmail client for macOS' "$tmp_dir/home.html"
grep -qi '^strict-transport-security:.*max-age=' "$tmp_dir/home.headers" || {
  echo "public web is missing HSTS" >&2
  exit 1
}
grep -qi '^content-security-policy:.*frame-ancestors.*none' "$tmp_dir/home.headers" || {
  echo "public web is missing the anti-framing Content Security Policy" >&2
  exit 1
}
grep -qi "^content-security-policy:.*font-src[[:space:]]*'none'" "$tmp_dir/home.headers" || {
  echo "public web CSP does not disable web-font loading" >&2
  exit 1
}
grep -qi '^x-content-type-options:[[:space:]]*nosniff' "$tmp_dir/home.headers" || {
  echo "public web is missing X-Content-Type-Options" >&2
  exit 1
}
grep -qi '^x-frame-options:[[:space:]]*DENY' "$tmp_dir/home.headers" || {
  echo "public web is missing X-Frame-Options" >&2
  exit 1
}
grep -qi '^referrer-policy:[[:space:]]*no-referrer' "$tmp_dir/home.headers" || {
  echo "public web is missing Referrer-Policy" >&2
  exit 1
}
grep -qi '^permissions-policy:.*camera=()' "$tmp_dir/home.headers" || {
  echo "public web is missing Permissions-Policy" >&2
  exit 1
}
if grep -qi '^x-powered-by:' "$tmp_dir/home.headers"; then
  echo "public web exposes its framework signature" >&2
  exit 1
fi
if grep -Eqi 'Continue with Google|/auth/google|href="/gmail|href="/dashboard' "$tmp_dir/home.html"; then
  echo "public home page still advertises the retired web product UI" >&2
  exit 1
fi
if grep -qi 'Downloads paused' "$tmp_dir/home.html"; then
  echo "public home page has downloads paused" >&2
  exit 1
fi
download_url="$(python3 - "$tmp_dir/home.html" <<'PY'
from html.parser import HTMLParser
import sys


class DownloadLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self._candidate = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._candidate = dict(attrs).get("href")

    def handle_data(self, data):
        if self._candidate and "Download for macOS" in data:
            self.href = self._candidate

    def handle_endtag(self, tag):
        if tag.lower() == "a":
            self._candidate = None


parser = DownloadLinkParser()
with open(sys.argv[1], encoding="utf-8") as handle:
    parser.feed(handle.read())
if not parser.href or not parser.href.startswith("https://"):
    raise SystemExit("public home page is missing a public HTTPS macOS download link")
print(parser.href)
PY
)"
if [ -n "$EXPECTED_DMG_SHA256" ]; then
  curl --silent --show-error --fail --location --retry 3 --max-time 600 \
    --proto '=https' --proto-redir '=https' \
    --output "$tmp_dir/public-download.dmg" "$download_url"
  [ -s "$tmp_dir/public-download.dmg" ] || {
    echo "public macOS download is empty" >&2
    exit 1
  }
  actual_download_sha256="$(shasum -a 256 "$tmp_dir/public-download.dmg" | awk '{print $1}')"
  [ "$actual_download_sha256" = "$EXPECTED_DMG_SHA256" ] || {
    echo "public macOS download does not match the approved DMG" >&2
    exit 1
  }
else
  curl --silent --show-error --fail --location --head --retry 3 --max-time 30 \
    --proto '=https' --proto-redir '=https' "$download_url" >/dev/null
fi

for route in privacy terms support; do
  curl --silent --show-error --fail-with-body --retry 3 --max-time 30 "$WEB_URL/$route" > "$tmp_dir/$route.html"
  grep -qi '<html' "$tmp_dir/$route.html"
  if grep -Eqi 'OWNER REVIEW REQUIRED|not approved for public launch|support@example\.com|your-owned-domain\.example|replace-with|replace-after' "$tmp_dir/$route.html"; then
    echo "$route page still contains an unapproved placeholder" >&2
    exit 1
  fi
done

grep -qi 'Google API Services User Data Policy' "$tmp_dir/privacy.html"
grep -qi 'Terms of Service' "$tmp_dir/terms.html"
grep -qi 'Support' "$tmp_dir/support.html"
grep -qi 'Report a security issue' "$tmp_dir/support.html"
grep -Eqi 'href="https://[^\"]+"[^>]*>Electronic Mail service status page</a>' "$tmp_dir/support.html"

post_login_status="$(curl --silent --show-error --retry 3 --max-time 30 \
  --dump-header "$tmp_dir/post-login.headers" \
  --output "$tmp_dir/post-login.html" \
  --write-out '%{http_code}' \
  "$WEB_URL/post-login")"
case "$post_login_status" in
  303|307|308) ;;
  *)
    echo "anonymous /post-login must redirect safely to the public home page (received $post_login_status)" >&2
    exit 1
    ;;
esac
grep -Eqi '^location:[[:space:]]*/([?#].*)?[[:space:]]*$' "$tmp_dir/post-login.headers" || {
  echo "anonymous /post-login did not redirect to the public home page" >&2
  exit 1
}
grep -qi '^cache-control:.*no-store' "$tmp_dir/post-login.headers" || {
  echo "anonymous /post-login is missing no-store cache protection" >&2
  exit 1
}

for route in gmail dashboard api/dashboard; do
  status="$(curl --silent --show-error --retry 3 --max-time 30 --output "$tmp_dir/${route//\//-}.txt" --write-out '%{http_code}' "$WEB_URL/$route")"
  if [ "$status" != "404" ]; then
    echo "$route must return 404 on the native-only production web service (received $status)" >&2
    exit 1
  fi
done

echo "Public native landing, legal/support pages, anonymous OAuth-completion boundary, and production web-product boundary passed launch verification."
