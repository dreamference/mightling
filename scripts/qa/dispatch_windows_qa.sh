#!/usr/bin/env bash
# Dispatch the daily Windows QA run (.github/workflows/windows-qa.yml) on main and wait for it; alert
# the owner (owner-notify) only when it fails. docs/dev/testing.md, "Daily QA on the idle machines".
# A user timer on the node runs this at 06:30 UTC: GitHub's `schedule` trigger never fires in this
# repository. The workflow itself waits for release work and keeps the "Windows QA failing" issue.
#
#   dispatch_windows_qa.sh [owner/repo]
set -uo pipefail

REPO="${1:-dreamference/mightling}"
NOTIFY="$HOME/.local/bin/owner-notify"
WORKFLOW=windows-qa.yml

notify() { [[ -x "$NOTIFY" ]] && "$NOTIFY" "$1" > /dev/null 2>&1; true; }

since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if ! gh workflow run "$WORKFLOW" -R "$REPO" --ref main; then
    notify "Windows QA could not be dispatched (gh workflow run failed)."
    exit 1
fi

# The run just dispatched: the newest dispatched run created since the dispatch.
id=""
for _ in $(seq 30); do
    sleep 10
    id="$(gh run list -R "$REPO" --workflow "$WORKFLOW" --event workflow_dispatch --limit 5 \
        --json databaseId,createdAt --jq "map(select(.createdAt >= \"$since\")) | .[0].databaseId // empty")"
    [[ -n "$id" ]] && break
done
if [[ -z "$id" ]]; then
    notify "Windows QA was dispatched but no run appeared."
    exit 1
fi
url="https://github.com/$REPO/actions/runs/$id"
echo "run $url"

# Up to 8 hours: three for the gate's wait on release work, the rest for the jobs.
deadline=$(($(date +%s) + 8 * 3600))
while (($(date +%s) < deadline)); do
    read -r status conclusion < <(gh run view "$id" -R "$REPO" --json status,conclusion --jq '"\(.status) \(.conclusion)"')
    if [[ "$status" == "completed" ]]; then
        echo "completed: $conclusion"
        if [[ "$conclusion" != "success" ]]; then
            notify "Windows QA $conclusion: $url"
            exit 1
        fi
        exit 0
    fi
    sleep 300
done
notify "Windows QA still running after 8 hours: $url"
exit 1
