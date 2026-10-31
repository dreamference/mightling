#!/usr/bin/env bash
# The light daily macOS QA run on the old 8 GB Mac mini (docs/dev/testing.md, "Daily QA on the idle
# machines"). Runs ON the Mac, fed on standard input by scripts/qa/run_desktop_qa.py --platform macos
# on the node, at background priority. The Mac is a person's machine with a browser open and stands
# for the low end (8 GB, macOS 12): nothing is built, nothing is compiled, everything happens in a
# throwaway HOME and is deleted at the end, and the real HOME's `ling` and Mightling.app are never
# touched (their versions are compared before and after).
#
#   QA_LAN_HOST      the node's name on the LAN (its mDNS name)
#   QA_MODEL_PORT    the model server's port (8000)
#
# Every check prints one line `QA-RESULT {"name":…,"status":"pass|fail|skip","detail":…,"seconds":…}`;
# everything else on standard output is the log. The checks, in order:
#   1. install.sh of the release before the latest into a throwaway HOME (no app), `ling --version`;
#   2. `ling update` from it to the latest;
#   3. install.sh of the latest into a second throwaway HOME, with the desktop app's dmg installed
#      into a throwaway folder (MIGHTLING_APP_DIR), `ling --version`;
#   4. one `ling exec` against the node over the LAN;
#   5. the installed app's first window: the audit's session (MIGHTLING_APP_AUDIT: the window hidden,
#      its app-server started with the bundled ling, quit after a few seconds) under a scratch
#      --user-data-dir; skipped while the Mac is short of memory;
#   6. `ling uninstall --purge --yes`, removing the app folder, and a check that the real install
#      did not change.
set -uo pipefail

REPO="dreamference/mightling"
REPO_URL="https://github.com/$REPO"
LAN="${QA_LAN_HOST:-}"
MODEL_PORT="${QA_MODEL_PORT:-8000}"
WORK="$HOME/.cache/mightling-qa/run"
OLD_HOME="$WORK/home-old"
NEW_HOME="$WORK/home-new"
APPS="$WORK/Applications"

real_ling="$HOME/.local/bin/ling"
real_before="$("$real_ling" --version 2>&1 | tail -1)"
real_app_before="$(stat -f '%m' "$HOME/Applications/Mightling.app" /Applications/Mightling.app 2>/dev/null | tr '\n' ' ')"

rm -rf "$WORK"
mkdir -p "$WORK" "$OLD_HOME" "$NEW_HOME" "$APPS"
cleanup() {
    [[ -n "${app_pid:-}" ]] && kill -0 "$app_pid" 2>/dev/null && kill "$app_pid" 2>/dev/null
    hdiutil info 2>/dev/null | awk -v w="$WORK" 'index($0, w) && /\/dev\/disk/ {print $1}' | while read -r d; do hdiutil detach "$d" -force -quiet 2>/dev/null; done
    rm -rf "$WORK"
}
trap cleanup EXIT

result() {
    python3 -c 'import json, sys; print("QA-RESULT " + json.dumps({"name": sys.argv[1], "status": sys.argv[2], "detail": sys.argv[3][-600:], "seconds": int(sys.argv[4])}))' \
        "$1" "$2" "$3" "$4"
}
# macOS has no timeout(1): perl's alarm ends the command.
tmo() { local secs="$1"; shift; perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$secs" "$@"; }
started() { date +%s; }
since() { echo $(($(date +%s) - $1)); }
log() { echo "== $*"; }

rate_limited() {
    grep -qi 'rate limit' <<< "$1" && return 0
    [[ "$(curl -s -m 20 https://api.github.com/rate_limit \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["rate"]["remaining"])' 2>/dev/null)" == "0" ]]
}

latest="$(curl -fsSI -m 30 -o /dev/null -w '%{redirect_url}' "$REPO_URL/releases/latest" | sed -E 's#.*/tag/v?##')"
log "latest release: ${latest:-unknown}; this Mac: macOS $(sw_vers -productVersion), $(($(sysctl -n hw.memsize) / 1073741824)) GB, real ling: $real_before"

# The release before the latest: the redirect of /releases gives no list; the page's tag links do.
previous="$(curl -fsSL -m 30 "$REPO_URL/releases" | grep -o "/releases/tag/v[0-9][0-9.]*" | sed -E 's#.*/tag/v##' | awk '!seen[$0]++' \
    | grep -vx "${latest:-none}" | head -1)"
log "previous release: ${previous:-unknown}"

curl -fsSL -m 120 -o "$WORK/install.sh" "$REPO_URL/releases/latest/download/install.sh" || echo "== could not download install.sh"

# install_into NAME HOME ARGS...: install.sh into HOME; sets $out and $code.
install_into() {
    local home="$1"
    shift
    for attempt in 1 2; do
        out="$(HOME="$home" tmo 900 bash "$WORK/install.sh" --role client "$@" 2>&1 < /dev/null)"
        code=$?
        [[ $code -eq 0 ]] && break
        echo "$out"
        echo "== install.sh attempt $attempt failed (exit $code)"
        rate_limited "$out" && break
        sleep 30
    done
    echo "$out"
}

# 1. The release before the latest, into the first throwaway HOME.
t=$(started)
old_ling=""
if [[ -z "$previous" || ! -s "$WORK/install.sh" ]]; then
    result "install.sh of the previous release" skip "no previous release or no install.sh" "$(since "$t")"
else
    install_into "$OLD_HOME" --version "$previous" --no-app
    version="$(HOME="$OLD_HOME" "$OLD_HOME/.local/bin/ling" --version 2>&1 | tail -1)"
    if [[ $code -eq 0 && "$version" == *" $previous" ]]; then
        old_ling="$OLD_HOME/.local/bin/ling"
        result "install.sh of the previous release" pass "$version" "$(since "$t")"
    elif rate_limited "$out"; then
        result "install.sh of the previous release" skip "GitHub's API rate limit for this network" "$(since "$t")"
    else
        result "install.sh of the previous release" fail "exit $code, '$version', wanted $previous: $(echo "$out" | tail -3)" "$(since "$t")"
    fi
fi

# 2. `ling update` from the previous release to the latest.
t=$(started)
if [[ -n "$old_ling" ]]; then
    out="$(HOME="$OLD_HOME" tmo 900 "$old_ling" update 2>&1 < /dev/null)"
    code=$?
    echo "$out"
    after="$(HOME="$OLD_HOME" "$old_ling" --version 2>&1 | tail -1)"
    if [[ $code -ne 0 ]] && rate_limited "$out"; then
        result "ling update from the previous release" skip "GitHub's API rate limit for this network" "$(since "$t")"
    elif [[ $code -ne 0 ]]; then
        result "ling update from the previous release" fail "exit $code: $(echo "$out" | tail -3)" "$(since "$t")"
    elif [[ -n "$latest" && "$after" != *" $latest" ]]; then
        result "ling update from the previous release" fail "$previous -> '$after', latest is $latest" "$(since "$t")"
    else
        result "ling update from the previous release" pass "$previous -> $after" "$(since "$t")"
    fi
else
    result "ling update from the previous release" skip "no previous install" "$(since "$t")"
fi
rm -rf "$OLD_HOME"

# 3. The latest, with the app's dmg, into the second throwaway HOME and the throwaway app folder.
t=$(started)
ling=""
if [[ -s "$WORK/install.sh" ]]; then
    MIGHTLING_APP_DIR="$APPS" install_into "$NEW_HOME"
    if [[ $code -ne 0 ]] && grep -q 'has no tag' <<< "$out" && [[ -n "$latest" ]]; then
        # The release's own install.sh read the API's compact answer as having no tag (the parser
        # fixed on main in 0bdbd347; releases up to 1.7.2 carry the old one). That is a real failure
        # of the released installer, recorded as one; the legs below go on with --version.
        result "install.sh of the latest release (no --version): the API's compact answer" fail "the release's install.sh found no tag in GitHub's answer (a parser fixed after 1.7.2): $(echo "$out" | tail -1)" "$(since "$t")"
        MIGHTLING_APP_DIR="$APPS" install_into "$NEW_HOME" --version "$latest"
    fi
    version="$(HOME="$NEW_HOME" "$NEW_HOME/.local/bin/ling" --version 2>&1 | tail -1)"
    if [[ $code -eq 0 && -x "$NEW_HOME/.local/bin/ling" && -n "$latest" && "$version" == *" $latest" ]]; then
        ling="$NEW_HOME/.local/bin/ling"
        result "install.sh of the latest release, ling --version" pass "$version" "$(since "$t")"
    elif rate_limited "$out"; then
        result "install.sh of the latest release, ling --version" skip "GitHub's API rate limit for this network" "$(since "$t")"
    else
        # What the API answers, for the log: install.sh reads the tag from it.
        curl -s -m 20 -H "Accept: application/vnd.github+json" "https://api.github.com/repos/$REPO/releases/latest" | head -c 300 | sed 's/^/   api: /'
        echo
        result "install.sh of the latest release, ling --version" fail "exit $code, '$version', latest is ${latest:-unknown}: $(echo "$out" | tail -3)" "$(since "$t")"
    fi
else
    result "install.sh of the latest release, ling --version" fail "could not download install.sh from the latest release" "$(since "$t")"
fi

t=$(started)
app="$APPS/Mightling.app"
if [[ -d "$app" && -x "$app/Contents/MacOS/Mightling" ]]; then
    result "desktop app: dmg install" pass "$(defaults read "$app/Contents/Info" CFBundleShortVersionString 2>/dev/null) in a throwaway folder, $(du -sh "$app" | cut -f1)" "$(since "$t")"
elif [[ -n "$ling" ]]; then
    result "desktop app: dmg install" fail "install.sh left no Mightling.app in the throwaway folder: $(echo "$out" | grep -i 'app' | tail -2 | tr '\n' ' ')" "$(since "$t")"
else
    result "desktop app: dmg install" skip "no fresh install" "$(since "$t")"
fi

# 4. One `ling exec` against the node over the LAN. A closed model gate (403) is a benchmark run.
t=$(started)
if [[ -z "$ling" ]]; then
    result "ling exec over the LAN" skip "no fresh install" "$(since "$t")"
elif [[ -z "$LAN" ]]; then
    result "ling exec over the LAN" fail "no LAN name for the node was given" 0
else
    status="$(curl -s -m 20 -o /dev/null -w '%{http_code}' "http://$LAN:$MODEL_PORT/v1/models")"
    if [[ "$status" == "403" ]]; then
        result "ling exec over the LAN" skip "the model gate is closed (a benchmark run holds the server)" "$(since "$t")"
    elif [[ "$status" != "200" ]]; then
        result "ling exec over the LAN" fail "the model server did not answer /v1/models (HTTP $status)" "$(since "$t")"
    else
        dir="$WORK/exec"
        mkdir -p "$dir"
        out="$(cd "$dir" && HOME="$NEW_HOME" CODEX_HOME="$NEW_HOME/.mightling" DREAMFERENCE_MIGHTLING_DOCS=0 \
            DREAMFERENCE_VLLM_HOST="http://$LAN:$MODEL_PORT" tmo 300 "$ling" exec --skip-git-repo-check \
            --output-last-message "$dir/answer.txt" "Reply with exactly one word: pong" < /dev/null 2>&1)"
        code=$?
        answer="$(tr -d '[:space:][:punct:]' < "$dir/answer.txt" 2>/dev/null | tr '[:upper:]' '[:lower:]')"
        if [[ $code -eq 0 && "$answer" == *pong* ]]; then
            result "ling exec over the LAN" pass "answered '$(head -c 80 "$dir/answer.txt")'" "$(since "$t")"
        elif [[ $code -ne 0 && "$out" == *"503"*"running a benchmark"* ]]; then
            result "ling exec over the LAN" skip "model busy (benchmark)" "$(since "$t")"
        else
            result "ling exec over the LAN" fail "exit $code, answer '$(head -c 80 "$dir/answer.txt" 2>/dev/null)': $(echo "$out" | tail -3)" "$(since "$t")"
        fi
    fi
fi

# 5. The app's first window: the audit's session, hidden, under scratch state. The Mac's owner may
# have a browser open: with under 15% of memory free, the launch is skipped, not forced.
t=$(started)
free_pct="$(memory_pressure 2>/dev/null | sed -nE 's/.*free percentage: ([0-9]+)%.*/\1/p' | head -1)"
if [[ ! -x "$app/Contents/MacOS/Mightling" ]]; then
    result "desktop app: first window (hidden audit session)" skip "no installed app" "$(since "$t")"
elif [[ -n "$free_pct" && "$free_pct" -lt 15 ]]; then
    result "desktop app: first window (hidden audit session)" skip "the Mac is short of memory (${free_pct}% free); not launching an app beside the owner's work" "$(since "$t")"
else
    scratch="$WORK/app-state"
    mkdir -p "$scratch"
    HOME="$NEW_HOME" CODEX_HOME="$NEW_HOME/.mightling-app" MIGHTLING_APP_AUDIT=8 ELECTRON_ENABLE_LOGGING=1 \
        DREAMFERENCE_VLLM_HOST="http://${LAN:-127.0.0.1}:$MODEL_PORT" \
        "$app/Contents/MacOS/Mightling" --use-mock-keychain --user-data-dir="$scratch/user-data" \
        > "$scratch/out.log" 2>&1 < /dev/null &
    app_pid=$!
    ling_seen=false
    for _ in $(seq 1 90); do
        kill -0 "$app_pid" 2>/dev/null || break
        pgrep -f "$app/Contents/Resources/ling" > /dev/null && ling_seen=true
        sleep 1
    done
    if kill -0 "$app_pid" 2>/dev/null; then
        kill "$app_pid" 2>/dev/null
        sleep 2
        kill -9 "$app_pid" 2>/dev/null
        tail -20 "$scratch/out.log"
        result "desktop app: first window (hidden audit session)" fail "the app did not quit after its audit session (90 s)" "$(since "$t")"
    else
        wait "$app_pid"
        code=$?
        app_pid=""
        tail -15 "$scratch/out.log"
        if [[ $code -eq 0 ]]; then
            result "desktop app: first window (hidden audit session)" pass "started and quit cleanly; bundled ling started: $ling_seen" "$(since "$t")"
        else
            result "desktop app: first window (hidden audit session)" fail "exit $code: $(tail -3 "$scratch/out.log" | tr '\n' ' ')" "$(since "$t")"
        fi
    fi
fi

# 6. Uninstall, and the real install untouched.
t=$(started)
if [[ -n "$ling" ]]; then
    out="$(HOME="$NEW_HOME" tmo 120 "$ling" uninstall --purge --yes < /dev/null 2>&1)"
    code=$?
    echo "$out"
    rm -rf "$app"
    leftovers="$(ls "$NEW_HOME/.local/bin" 2>/dev/null | tr '\n' ' ')"
    if [[ $code -ne 0 ]]; then
        result "uninstall" fail "ling uninstall exit $code: $(echo "$out" | tail -3)" "$(since "$t")"
    elif [[ -n "$leftovers" || -e "$app" ]]; then
        result "uninstall" fail "left behind: $leftovers" "$(since "$t")"
    else
        result "uninstall" pass "ling uninstall --purge --yes, the app folder removed" "$(since "$t")"
    fi
else
    result "uninstall" skip "no fresh install" "$(since "$t")"
fi
t=$(started)
real_after="$("$real_ling" --version 2>&1 | tail -1)"
real_app_after="$(stat -f '%m' "$HOME/Applications/Mightling.app" /Applications/Mightling.app 2>/dev/null | tr '\n' ' ')"
if [[ "$real_before" == "$real_after" && "$real_app_before" == "$real_app_after" ]]; then
    result "the Mac's own install is untouched" pass "$real_after" "$(since "$t")"
else
    result "the Mac's own install is untouched" fail "ling '$real_before' -> '$real_after'; app times '$real_app_before' -> '$real_app_after'" "$(since "$t")"
fi
