#!/usr/bin/env bash
# Smoke-test a built PMRF image: start it, wait for /api/health, then check that
# HEAD / is served by the static export rather than by the API-only fallback.
#
# Usage: deploy/smoke-test.sh <image> [host-port]
#
# Why this exists: the `docker-build` job proved the image *builds* and nothing
# more. On 2026-11-20 a bare `docker run` of the image this repo builds exited 3
# within seconds --
#
#   RuntimeError: API_WRITE_KEY is empty. Set API_WRITE_KEY to protect write
#   endpoints, or set ALLOW_OPEN_WRITES=true to explicitly run with public
#   writes (local dev only).
#
# -- so CI had been green for months over an image that could not answer a
# single request (§53.144). The two answers below are what make it start:
# ALLOW_OPEN_WRITES=true because a smoke test must not carry a real write key,
# and SCHEDULER_ENABLED=false so the container stays read-only while it is being
# probed (the same switch §53.132 used for its read-only probe).
#
# Judge by the exit status; every failure prints the container's last log lines.

set -euo pipefail

IMAGE="${1:?usage: smoke-test.sh <image> [host-port]}"
PORT="${2:-8000}"
NAME="pmrf-smoke-$$"
BASE="http://127.0.0.1:${PORT}"

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run -d --name "$NAME" -p "127.0.0.1:${PORT}:8000" \
  -e ALLOW_OPEN_WRITES=true -e SCHEDULER_ENABLED=false "$IMAGE" >/dev/null

code=""
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "${BASE}/api/health" || true)
  if [ "$code" = "200" ]; then
    break
  fi
  sleep 2
done

if [ "$code" != "200" ]; then
  echo "::error::/api/health never answered 200 (last: ${code:-none})" >&2
  docker logs "$NAME" 2>&1 | tail -30 >&2
  exit 1
fi
echo "api/health -> 200"

# `-I` (not `-X HEAD`): curl sends a real HEAD and does not sit waiting for a
# body it will never get. Measured in this image: 200 text/html, 45 935 bytes.
read -r head_code head_type <<<"$(
  curl -sS -I -o /dev/null --max-time 5 -w '%{http_code} %{content_type}' "${BASE}/"
)"

echo "HEAD / -> ${head_code} (${head_type:-no content-type})"

if [ "$head_code" != "200" ]; then
  echo "::error::HEAD / did not answer 200 -- a 307 to /docs means the image" \
    "mounted no frontend build" >&2
  docker logs "$NAME" 2>&1 | tail -20 >&2
  exit 1
fi

case "$head_type" in
  text/html*)
    ;;
  *)
    echo "::error::HEAD / answered 200 but not text/html (${head_type:-none});" \
      "the API fallback route served it instead of frontend/out" >&2
    exit 1
    ;;
esac

echo "image serves the static export"
