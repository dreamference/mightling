#!/usr/bin/env bash
# SearXNG search wrapper for Codex.
#
# Lives in the repo rather than /tmp so it survives a reboot -- /tmp is cleared, and an agent told
# in its prompt that a command exists should not find it missing.
#
# Two fixes over the original sketch:
#   * the query is URL-encoded via curl -G --data-urlencode. Interpolating it straight into the URL
#     breaks on the first space -- `q=weather in Lisbon` makes curl fail with http=000.
#   * the default instance is 127.0.0.1:8888, which is where this machine's SearXNG listens
#     (loopback only). Nothing is bound to 8080.
#
# The instance also needs `json` in search.formats and `limiter: false`; SearXNG serves HTML only by
# default, so a stock container returns 403 to this script.
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: searxng-search.sh <query>" >&2
  exit 1
fi

QUERY="$1"
BASE_URL="${SEARXNG_URL:-http://127.0.0.1:8888}"

if ! RESPONSE=$(curl -s --fail -G \
      --data-urlencode "q=${QUERY}" \
      --data "format=json" \
      --data "language=en" \
      --max-time 25 \
      "${BASE_URL}/search"); then
  echo "search failed: SearXNG at ${BASE_URL} is unreachable or did not return JSON" >&2
  echo "  start it with: docker start searxng" >&2
  echo "  it must have 'json' in search.formats and limiter disabled" >&2
  exit 1
fi

echo "$RESPONSE" | jq -r '
  .results[] |
  "Title: \(.title)\nURL: \(.url)\nSnippet: \(.content // "No snippet available")\n---"
'
