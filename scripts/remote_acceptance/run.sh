#!/usr/bin/env bash
# The off-LAN acceptance of remote access, run on the node (docs/dev/remote-access.md, spec §13.3):
# a GitHub-hosted runner in the private repository dreamference/remote-acceptance joins the overlay
# as an ephemeral peer and checks the node by its overlay name from outside the LAN.
#
#   run.sh [--workflow linux|android] [--relay] [--notify]   one run: mint, dispatch, revoke, verify
#   run.sh units                                   prints the daily user timer and its service
#
# One run: revoke any runner peer left by an earlier run; mint one bundle (`ling-admin remote code
# --bundle-file`: one-use, ten-minute key, ephemeral peer) into a 0600 file; set it as the
# repository secret ACCEPTANCE_BUNDLE and delete the file; dispatch; delete the secret as soon as
# the job has read it; wait for the run; revoke the runner's peer and check that it is gone. Prints
# the run's ACCEPT lines (pass/fail and numbers; the workflow masks names, addresses and keys).
# With --notify, a failure, and only a failure, goes to the owner (owner-notify).
set -uo pipefail

REPO="dreamference/remote-acceptance"
SECRET="ACCEPTANCE_BUNDLE"
# The runner peers: the Linux runner names itself; NetBird's Android app names the peer after
# the device model, which on the emulator is an sdk_gphone image.
RUNNER_PATTERN="^(offlan-runner-|sdk_gphone)"
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

if [ "${1:-}" = "units" ]; then
  cat <<EOF
# ~/.config/systemd/user/mightling-remote-acceptance.service
[Unit]
Description=Mightling remote access: the off-LAN acceptance on a GitHub-hosted runner (scripts/remote_acceptance)
After=network-online.target

[Service]
Type=oneshot
Slice=mightling-batch.slice
Nice=10
TimeoutStartSec=60min
ExecStart=$here/run.sh --notify

# ~/.config/systemd/user/mightling-remote-acceptance.timer
[Unit]
Description=Daily off-LAN acceptance of remote access, 13:20 UTC (GitHub's schedule does not fire for us)

[Timer]
OnCalendar=*-*-* 13:20:00 UTC
Persistent=false

[Install]
WantedBy=timers.target
EOF
  exit 0
fi

workflow="linux"
notify=0
relay=false
while [ $# -gt 0 ]; do
  case "$1" in
    --workflow) workflow="${2:-}"; shift 2 ;;
    --notify) notify=1; shift ;;
    --relay) relay=true; shift ;;
    *) echo "usage: run.sh [--workflow linux|android] [--relay] [--notify] | run.sh units" >&2; exit 2 ;;
  esac
done
case "$workflow" in linux|android) ;; *) echo "unknown workflow: $workflow" >&2; exit 2 ;; esac

export PATH="$HOME/.local/bin:$PATH"
work=$(mktemp -d)
chmod 700 "$work"
secret_set=0

cleanup() {
  rm -rf "$work"
  if [ "$secret_set" = 1 ]; then gh secret delete "$SECRET" -R "$REPO" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

fail() {
  echo "❌ $*"
  if [ "$notify" = 1 ]; then owner-notify "Remote access off-LAN acceptance ($workflow) failed: $*" >/dev/null 2>&1 || true; fi
  exit 1
}

# The overlay labels of every runner peer enrolled now (never the node's or a client's).
runner_labels() {
  ling-admin remote peers 2>/dev/null | awk -v p="$RUNNER_PATTERN" '$1 ~ p { print $2 }'
}

revoke_runners() {
  local label count=0
  for label in $(runner_labels); do
    ling-admin remote revoke "$label" >/dev/null 2>&1 && count=$((count + 1))
  done
  echo "Runner peers revoked: $count"
  [ -z "$(runner_labels)" ]
}

ling-admin remote peers >/dev/null 2>&1 || fail "management did not answer (ling-admin remote status)"
revoke_runners || fail "a runner peer from an earlier run could not be revoked"
ling-admin remote peers 2>/dev/null | awk '{ print $1 }' | sort > "$work/before.txt"

ling-admin remote code --bundle-file "$work/bundle.json" >/dev/null || fail "no bundle was minted"
gh secret set "$SECRET" -R "$REPO" < "$work/bundle.json" >/dev/null || fail "the secret was not set"
secret_set=1
rm -f "$work/bundle.json"

label="$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
gh workflow run "$workflow.yml" -R "$REPO" --ref main -f "label=$label" \
  $([ "$workflow" = linux ] && echo "-f relay=$relay") >/dev/null || fail "dispatch refused"

# Find the run, then wait until its first step (the one that reads the secret) has finished.
run_id=""
for _ in $(seq 1 60); do
  run_id=$(gh run list -R "$REPO" -w "$workflow.yml" -L 20 --json databaseId,displayTitle \
    --jq ".[] | select(.displayTitle | startswith(\"$workflow $label\")) | .databaseId" 2>/dev/null | head -n1)
  [ -n "$run_id" ] && break
  sleep 5
done
[ -n "$run_id" ] || fail "the dispatched run did not appear"
read_secret=""
for _ in $(seq 1 120); do
  read_secret=$(gh run view "$run_id" -R "$REPO" --json jobs,status \
    --jq 'if .status == "completed" then "completed" else ([.jobs[]?.steps[]? | select(.number == 2) | .status] | first // "") end' 2>/dev/null)
  case "$read_secret" in completed|in_progress) break ;; esac
  sleep 5
done
gh secret delete "$SECRET" -R "$REPO" >/dev/null 2>&1 && secret_set=0
[ "$secret_set" = 0 ] || fail "the secret could not be deleted"

# Poll until the run has completed (bounded); `gh run watch` can return early on an API error,
# and nothing may be revoked or reported while the runner could still join.
status=""
for _ in $(seq 1 180); do
  status=$(gh run view "$run_id" -R "$REPO" --json status --jq .status 2>/dev/null)
  [ "$status" = "completed" ] && break
  sleep 15
done
if [ "$status" != "completed" ]; then
  gh run cancel "$run_id" -R "$REPO" >/dev/null 2>&1
  sleep 60
fi
conclusion=$(gh run view "$run_id" -R "$REPO" --json conclusion --jq .conclusion 2>/dev/null)
gh run view "$run_id" -R "$REPO" --log 2>/dev/null | grep -oE 'Z ACCEPT (PASS|FAIL|WARN|INFO|RESULT) .*' | cut -c3- > "$work/lines.txt" || true
cat "$work/lines.txt"

# Revoke the runner's peer and check that it is gone (an ephemeral peer may already be).
revoke_runners || fail "the runner's peer is still enrolled after revoke (run $run_id)"
echo "✅ No runner peer is enrolled."
# Anything else that appeared during the run is named, never revoked by this script.
others=$(ling-admin remote peers 2>/dev/null | awk '{ print $1 }' | sort | comm -13 "$work/before.txt" -)
[ -z "$others" ] || fail "peers enrolled during the run that are not runner peers (not revoked): $others"

if [ "$conclusion" != "success" ]; then
  failed=$(grep -E '^ACCEPT FAIL' "$work/lines.txt" | head -n3 | sed 's/^ACCEPT FAIL //' | paste -sd ';' -)
  fail "run $run_id ${conclusion:-unknown}: ${failed:-see the log of the run}"
fi
echo "✅ Off-LAN acceptance ($workflow) passed, run $run_id."
