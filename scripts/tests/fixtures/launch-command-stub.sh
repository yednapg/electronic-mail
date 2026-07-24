#!/usr/bin/env bash
set -euo pipefail

command_name="$(basename "$0")"

stub_fail() {
  echo "launch command stub failed ($command_name): $*" >&2
  exit 97
}

case "$command_name" in
  curl)
    url=""
    header_path=""
    output_path=""
    write_out=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --dump-header)
          [ "$#" -ge 2 ] || stub_fail "--dump-header is missing its value"
          header_path="$2"
          shift 2
          ;;
        --output)
          [ "$#" -ge 2 ] || stub_fail "--output is missing its value"
          output_path="$2"
          shift 2
          ;;
        --write-out)
          [ "$#" -ge 2 ] || stub_fail "--write-out is missing its value"
          write_out="$2"
          shift 2
          ;;
        http://* | https://*)
          url="$1"
          shift
          ;;
        *)
          shift
          ;;
      esac
    done
    [ -n "$url" ] || stub_fail "request URL was not provided"

    emit_body() {
      if [ -n "$output_path" ]; then
        printf '%s\n' "$1" > "$output_path"
      else
        printf '%s\n' "$1"
      fi
    }

    emit_backend_headers() {
      if [ -n "$header_path" ]; then
        printf '%s\r\n' \
          'HTTP/2 200' \
          'x-request-id: launch-test-request' \
          'x-content-type-options: nosniff' \
          'strict-transport-security: max-age=31536000' \
          > "$header_path"
      fi
    }

    emit_public_headers() {
      if [ -n "$header_path" ]; then
        printf '%s\r\n' \
          'HTTP/2 200' \
          'strict-transport-security: max-age=31536000; includeSubDomains' \
          "content-security-policy: default-src 'self'; font-src 'none'; frame-ancestors 'none'; object-src 'none'" \
          'x-content-type-options: nosniff' \
          'x-frame-options: DENY' \
          'referrer-policy: no-referrer' \
          'permissions-policy: camera=(), geolocation=(), microphone=()' \
          > "$header_path"
      fi
    }

    emit_public_readiness_headers() {
      if [ -n "$header_path" ]; then
        printf '%s\r\n' \
          'HTTP/2 200' \
          'cache-control: no-store' \
          'content-type: application/json' \
          > "$header_path"
      fi
    }

    case "$url" in
      */healthz)
        emit_public_readiness_headers
        if [ "${LAUNCH_TEST_WEB_READY:-true}" = "true" ]; then
          emit_body '{"status":"ready","checks":{"backend":true,"download":true,"legal":true}}'
          [ -n "$write_out" ] && printf '200'
        else
          emit_body '{"status":"not_ready","checks":{"backend":true,"download":false,"legal":true}}'
          [ -n "$write_out" ] && printf '503'
        fi
        ;;
      */v1/ops/health)
        default_queue_depth_json='{"critical":1,"default":2}'
        worker_release="${LAUNCH_TEST_WORKER_RELEASE:-${LAUNCH_TEST_RELEASE_SHA:?}}"
        default_workers_json="$(printf \
          '[{"worker_id":"fast","queues":["critical","default"],"release_sha":"%s","age_seconds":5,"fresh":true,"release_matches_expected":true},{"worker_id":"reader","queues":["reader"],"release_sha":"%s","age_seconds":5,"fresh":true,"release_matches_expected":true},{"worker_id":"slow","queues":["slow"],"release_sha":"%s","age_seconds":5,"fresh":true,"release_matches_expected":true},{"worker_id":"poller","queues":["gmail_poll"],"release_sha":"%s","age_seconds":5,"fresh":true,"release_matches_expected":true}]' \
          "$worker_release" "$worker_release" "$worker_release" "$worker_release")"
        emit_backend_headers
        emit_body "$(printf \
          '{\"environment\":\"%s\",\"release\":\"%s\",\"queue_depth\":%s,\"dead_jobs\":%s,\"stale_running_jobs\":%s,\"oldest_queued_age_seconds\":%s,\"workers\":%s,\"worker_online\":true,\"required_queues_ready\":true,\"worker_releases_match\":%s}' \
          "${LAUNCH_TEST_OPS_ENVIRONMENT:-production}" \
          "${LAUNCH_TEST_OPS_RELEASE:-${LAUNCH_TEST_RELEASE_SHA:?}}" \
          "${LAUNCH_TEST_QUEUE_DEPTH_JSON:-$default_queue_depth_json}" \
          "${LAUNCH_TEST_DEAD_JOBS:-0}" \
          "${LAUNCH_TEST_STALE_JOBS:-0}" \
          "${LAUNCH_TEST_OLDEST_QUEUED_AGE:-10}" \
          "${LAUNCH_TEST_OPS_WORKERS_JSON:-$default_workers_json}" \
          "${LAUNCH_TEST_WORKER_RELEASES_MATCH:-true}")"
        ;;
      */health)
        emit_backend_headers
        emit_body "$(printf \
          '{\"status\":\"ok\",\"release\":\"%s\"}' \
          "${LAUNCH_TEST_HEALTH_RELEASE:-${LAUNCH_TEST_RELEASE_SHA:?}}")"
        ;;
      */ready)
        emit_backend_headers
        emit_body "$(printf \
          '{\"status\":\"ready\",\"environment\":\"%s\",\"database\":\"postgres\",\"google_configured\":true,\"ai_enabled\":false,\"schema_revision\":\"20260721_0022\",\"schema_head\":\"20260721_0022\",\"release\":\"%s\"}' \
          "${LAUNCH_TEST_READY_ENVIRONMENT:-production}" \
          "${LAUNCH_TEST_READY_RELEASE:-${LAUNCH_TEST_RELEASE_SHA:?}}")"
        ;;
      */post-login)
        [ -n "$header_path" ] || stub_fail "post-login request did not request response headers"
        printf '%s\r\n' 'HTTP/2 307' 'location: /' 'cache-control: private, no-store, max-age=0' > "$header_path"
        emit_body '<html>redirect</html>'
        [ -n "$write_out" ] && printf '307'
        ;;
      */privacy)
        emit_body '<html>Google API Services User Data Policy</html>'
        ;;
      */terms)
        emit_body '<html>Terms of Service</html>'
        ;;
      */support)
        emit_body '<html>Support <h2>Report a security issue</h2><a href="https://status.electronicmail.dev">Electronic Mail service status page</a></html>'
        ;;
      https://status.electronicmail.dev | https://status.electronicmail.dev/)
        [ "${LAUNCH_TEST_STATUS_PAGE_FAIL:-0}" != "1" ] || exit 22
        emit_body '<html>Electronic Mail service status</html>'
        ;;
      */ElectronicMail.dmg)
        emit_body "${LAUNCH_TEST_DOWNLOAD_BODY-deterministic fake DMG}"
        ;;
      https://evidence.electronicmail.dev/*)
        if [ "${LAUNCH_TEST_EVIDENCE_EMPTY:-0}" = "1" ]; then
          [ -n "$output_path" ] || stub_fail "empty evidence request did not provide an output path"
          : > "$output_path"
        else
          emit_body "${LAUNCH_TEST_EVIDENCE_BODY_OVERRIDE-evidence:$url}"
        fi
        [ -n "$write_out" ] && printf '200'
        ;;
      */gmail | */dashboard | */api/dashboard)
        emit_body 'not found'
        [ -n "$write_out" ] && printf '404'
        ;;
      */)
        emit_public_headers
        emit_body '<html>Electronic Mail is a native Gmail client for macOS. <a href="https://downloads.electronicmail.dev/ElectronicMail.dmg">Download for macOS</a></html>'
        ;;
      *)
        stub_fail "unexpected URL: $url"
        ;;
    esac
    ;;

  hdiutil)
    action="${1:-}"
    case "$action" in
      verify | detach)
        exit 0
        ;;
      attach)
        mount_point=""
        shift
        while [ "$#" -gt 0 ]; do
          if [ "$1" = "-mountpoint" ]; then
            [ "$#" -ge 2 ] || stub_fail "-mountpoint is missing its value"
            mount_point="$2"
            shift 2
          else
            shift
          fi
        done
        [ -n "$mount_point" ] || stub_fail "attach did not provide a mount point"
        [ -d "${LAUNCH_TEST_DMG_APP_PATH:?}" ] || stub_fail "DMG source app is missing"
        cp -R "$LAUNCH_TEST_DMG_APP_PATH" "$mount_point/ElectronicMail.app"
        ln -s /Applications "$mount_point/Applications"
        if [ "${LAUNCH_TEST_DMG_EXTRA_FILE:-0}" = "1" ]; then
          printf '%s\n' 'unexpected payload' > "$mount_point/Install.command"
        fi
        ;;
      *)
        stub_fail "unexpected hdiutil action: $action"
        ;;
    esac
    ;;

  codesign)
    if [ "${1:-}" = "-d" ] && [ "${2:-}" = "--entitlements" ]; then
      cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.app-sandbox</key><true/>
  <key>com.apple.security.files.user-selected.read-write</key><true/>
  <key>com.apple.security.network.client</key><true/>
  <key>com.apple.application-identifier</key><string>${LAUNCH_TEST_TEAM_ID:?}.app.electronicmail.mac</string>
  <key>com.apple.developer.team-identifier</key><string>${LAUNCH_TEST_TEAM_ID:?}</string>
</dict></plist>
EOF
    elif [ "${1:-}" = "-dvvv" ]; then
      emit_timestamp=1
      if [ "${LAUNCH_TEST_CODESIGN_TIMESTAMP:-1}" != "1" ]; then
        for argument in "$@"; do
          case "$argument" in
            *.dmg) emit_timestamp=0 ;;
          esac
        done
      fi
      printf '%s\n' \
        "Authority=Developer ID Application: Launch Test (${LAUNCH_TEST_TEAM_ID:?})" \
        >&2
      if [ "$emit_timestamp" = "1" ]; then
        printf '%s\n' 'Timestamp=Jul 21, 2026 at 10:00:00 AM' >&2
      fi
      cat >&2 <<EOF
TeamIdentifier=${LAUNCH_TEST_TEAM_ID:?}
flags=0x10000(runtime) hashes=1+1 location=embedded
EOF
    fi
    ;;

  lipo)
    [ "${1:-}" = "-archs" ] || stub_fail "unexpected lipo arguments"
    printf '%s\n' 'arm64 x86_64'
    ;;

  spctl | xcrun)
    exit 0
    ;;

  *)
    stub_fail "unsupported command"
    ;;
esac
