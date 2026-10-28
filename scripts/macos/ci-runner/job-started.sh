#!/bin/bash
# The macOS CI runner's hook before every job (scripts/macos/ci-runner/setup.sh installs it, root's
# and read-only to the runner account): refuse any job that is not from the trusted events and
# workflows of dreamference/mightling, then take the Mac's lock, waiting while anything else (a hand
# build) holds it. This repository is public and a fork's pull request runs the workflow as the
# fork wrote it, so no pull request job may ever run here.
set -euo pipefail
repo="dreamference/mightling"
workflow="${GITHUB_WORKFLOW_REF%%@*}"
echo "job: ${GITHUB_REPOSITORY:-} ${GITHUB_EVENT_NAME:-} ${GITHUB_REF:-} ${GITHUB_WORKFLOW_REF:-}"
refuse() { echo "refused: $*" >&2; exit 1; }
[[ "${GITHUB_REPOSITORY:-}" == "$repo" ]] || refuse "repository ${GITHUB_REPOSITORY:-}"
case "${GITHUB_EVENT_NAME:-}" in
  push|workflow_dispatch|schedule) ;;
  *) refuse "event ${GITHUB_EVENT_NAME:-} (only push, workflow_dispatch, schedule run here)" ;;
esac
[[ "${GITHUB_REF:-}" == refs/heads/* ]] || refuse "ref ${GITHUB_REF:-}"
case "$workflow" in
  "$repo/.github/workflows/build-clients.yml"|"$repo/.github/workflows/release.yml"|"$repo/.github/workflows/release-dry-run.yml") ;;
  *) refuse "workflow ${GITHUB_WORKFLOW_REF:-}" ;;
esac
if [[ -n "${GITHUB_EVENT_PATH:-}" && -f "$GITHUB_EVENT_PATH" ]]; then
  /usr/bin/python3 -I -c 'import json,sys; sys.exit(1 if "pull_request" in json.load(open(sys.argv[1])) else 0)' "$GITHUB_EVENT_PATH" \
    || refuse "a pull request event"
fi

lock=/Users/Shared/ci-lock/mac.lock
mine="ci run ${GITHUB_RUN_ID:-?} job ${GITHUB_JOB:-?} since $(date -u +%Y-%m-%dT%H:%M:%SZ)"
deadline=$(( $(date +%s) + 3 * 3600 ))
while true; do
  if ( set -o noclobber; printf '%s' "$mine" > "$lock" ) 2>/dev/null; then break; fi
  holder="$(cat "$lock" 2>/dev/null || true)"
  # This runner runs one job at a time, so a CI lock still here was left by an earlier job.
  if [[ "$holder" == ci\ * ]]; then printf '%s' "$mine" > "$lock"; break; fi
  (( $(date +%s) < deadline )) || refuse "the Mac is still locked after 3 hours: $holder"
  echo "waiting for the Mac's lock, held by: $holder"
  sleep 30
done
echo "Mac lock taken: $mine"
