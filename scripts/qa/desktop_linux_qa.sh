#!/usr/bin/env bash
# The daily x86-64 Linux QA run on a client desktop (docs/dev/testing.md, "Daily QA on the idle
# machines"). Runs ON the desktop, fed on standard input by scripts/qa/run_desktop_qa.py on the
# node, inside the memory- and CPU-bounded scope that script opens. It names no machine: the node
# under test comes in the environment, computed by the node at run time.
#
#   QA_LAN_HOST      the node's name on the LAN (its mDNS name)
#   QA_OVERLAY_HOST  the node's name in the remote-access overlay (empty: no overlay leg)
#   QA_MODEL_PORT    the model server's port (8000)
#   QA_WEB_PORT      `ling web`'s port (3100)
#
# Every check prints one line `QA-RESULT {"name":…,"status":"pass|fail|skip","detail":…,"seconds":…}`;
# everything else on standard output is the log. What it does, as a user of this desktop would:
# `ling update` on the desktop's own release install; install.sh from the latest release into a
# throwaway HOME; `ling --version`; one `ling exec` against the node over the LAN and over the
# overlay; `ling web` answering on both; and from a fresh clone of main, the Python package's wheel
# and its test suite in a virtualenv. The throwaway HOME, the clone and the virtualenv are deleted
# at the end; only pip's cache stays (~/.cache/mightling-qa/pip).
set -uo pipefail

REPO_URL="https://github.com/dreamference/mightling"
LAN="${QA_LAN_HOST:-}"
OVERLAY="${QA_OVERLAY_HOST:-}"
MODEL_PORT="${QA_MODEL_PORT:-8000}"
WEB_PORT="${QA_WEB_PORT:-3100}"
CACHE="$HOME/.cache/mightling-qa"
WORK="$CACHE/run"
SCRATCH_HOME="$WORK/home"

rm -rf "$WORK"
mkdir -p "$WORK" "$CACHE/pip"
trap 'rm -rf "$WORK"' EXIT

# result NAME STATUS DETAIL SECONDS: one machine-readable line.
result() {
    python3 -c 'import json, sys; print("QA-RESULT " + json.dumps({"name": sys.argv[1], "status": sys.argv[2], "detail": sys.argv[3][-600:], "seconds": int(sys.argv[4])}))' \
        "$1" "$2" "$3" "$4"
}

started() { date +%s; }
since() { echo $(($(date +%s) - $1)); }
log() { echo "== $*"; }

# The latest release, from the web page's redirect rather than the API: unauthenticated API calls
# share 60 an hour with every other machine behind the same public address, and `ling update` and
# install.sh need theirs.
# release_tag URL: the tag a `/releases/latest` redirect points at, empty when there is none (with no
# published release the page redirects to `/releases`, which has no `/tag/` part).
release_tag() {
    [[ "$1" == */tag/* ]] && sed -E 's#.*/tag/v?##' <<< "$1"
    return 0
}
latest="$(release_tag "$(curl -fsSI -m 30 -o /dev/null -w '%{redirect_url}' "$REPO_URL/releases/latest")")"
log "latest release: ${latest:-unknown}"

# A check that met GitHub's rate limit for unauthenticated calls proves nothing about Mightling: it
# is skipped with that reason rather than failed.
# install.sh's curl says only "403", so the budget itself is asked too (that call is free).
rate_limited() {
    grep -qi 'rate limit' <<< "$1" && return 0
    [[ "$(curl -s -m 20 https://api.github.com/rate_limit \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["rate"]["remaining"])' 2>/dev/null)" == "0" ]]
}

# 1. `ling update` on the desktop's own release install, as its user runs it.
t=$(started)
real="$HOME/.local/bin/ling"
if [[ -x "$real" ]]; then
    before="$("$real" --version 2>&1 | tail -1)"
    out="$(timeout 600 "$real" update 2>&1)"
    code=$?
    after="$("$real" --version 2>&1 | tail -1)"
    echo "$out"
    if [[ $code -ne 0 ]] && rate_limited "$out"; then
        result "ling update (the desktop's install)" skip "GitHub's API rate limit for this network" "$(since "$t")"
    elif [[ $code -ne 0 ]]; then
        result "ling update (the desktop's install)" fail "exit $code: $(echo "$out" | tail -3)" "$(since "$t")"
    elif [[ -n "$latest" && "$after" != *" $latest" ]]; then
        result "ling update (the desktop's install)" fail "$before -> $after, latest is $latest" "$(since "$t")"
    else
        result "ling update (the desktop's install)" pass "$before -> $after" "$(since "$t")"
    fi
else
    result "ling update (the desktop's install)" skip "no release install at ~/.local/bin/ling" "$(since "$t")"
fi

# 2. install.sh from the latest release into a throwaway HOME: a fresh client install.
t=$(started)
mkdir -p "$SCRATCH_HOME"
ling=""
if [[ -z "$latest" ]] && curl -fsSI -m 30 -o /dev/null "$REPO_URL/releases" 2>/dev/null; then
    # The releases page answers but names no latest tag: nothing is published, so nothing to install.
    result "install.sh into a throwaway HOME" skip "no published release at $REPO_URL/releases yet" "$(since "$t")"
elif curl -fsSL -m 120 -o "$WORK/install.sh" "$REPO_URL/releases/latest/download/install.sh"; then
    # Standard input is the script bash is reading: the installer gets none. One retry: on 2026-10-28
    # the API answered once in compact JSON (install.sh reads the tag from a pretty-printed line:
    # "has no tag") and in pretty JSON a minute later.
    for attempt in 1 2; do
        out="$(HOME="$SCRATCH_HOME" timeout 900 bash "$WORK/install.sh" --role client 2>&1 < /dev/null)"
        code=$?
        [[ $code -eq 0 ]] && break
        echo "$out"
        echo "== install.sh attempt $attempt failed (exit $code)"
        rate_limited "$out" && break
        sleep 30
    done
    echo "$out"
    if [[ $code -eq 0 && -x "$SCRATCH_HOME/.local/bin/ling" ]]; then
        ling="$SCRATCH_HOME/.local/bin/ling"
        result "install.sh into a throwaway HOME" pass "$(echo "$out" | grep -m1 -i 'done\|installed' || true)" "$(since "$t")"
    elif rate_limited "$out"; then
        result "install.sh into a throwaway HOME" skip "GitHub's API rate limit for this network" "$(since "$t")"
    else
        # What the API answered, for the log: install.sh reads the tag from it line by line.
        curl -s -m 20 -H "Accept: application/vnd.github+json" https://api.github.com/repos/dreamference/mightling/releases/latest \
            | head -c 300 | head -3 | sed 's/^/   api: /'
        echo  # head -c leaves no final newline, and a result line must start its own line
        result "install.sh into a throwaway HOME" fail "exit $code: $(echo "$out" | tail -3)" "$(since "$t")"
    fi
else
    result "install.sh into a throwaway HOME" fail "could not download install.sh from the latest release" "$(since "$t")"
fi

# 3. `ling --version` of the fresh install.
t=$(started)
if [[ -n "$ling" ]]; then
    version="$(HOME="$SCRATCH_HOME" "$ling" --version 2>&1 | tail -1)"
    if [[ -n "$latest" && "$version" == *" $latest" ]]; then
        result "ling --version" pass "$version" "$(since "$t")"
    else
        result "ling --version" fail "'$version', latest release is ${latest:-unknown}" "$(since "$t")"
    fi
else
    result "ling --version" skip "no fresh install" "$(since "$t")"
fi

# Without a fresh install, the legs below use the desktop's own `ling` (with the same throwaway HOME
# and CODEX_HOME), so one failed install does not hide the rest.
which_ling="the fresh install"
if [[ -z "$ling" && -x "$real" ]]; then
    ling="$real"
    which_ling="the desktop's own ling, $("$real" --version 2>&1 | tail -1)"
fi

# 4. One `ling exec` per leg. A model gate answering 403 is a benchmark run holding the server: the
# leg is skipped, not failed. The session's CODEX_HOME is its own, under the throwaway HOME, and the
# launcher's file index stays off (DREAMFERENCE_MIGHTLING_DOCS=0, docs/dev/testing.md).
exec_leg() {
    local leg="$1" host="$2" t code status answer
    t=$(started)
    local name="ling exec over the $leg"
    if [[ -z "$ling" ]]; then
        result "$name" skip "no ling to run" "$(since "$t")"
        return
    fi
    status="$(curl -s -m 20 -o /dev/null -w '%{http_code}' "http://$host:$MODEL_PORT/v1/models")"
    if [[ "$status" == "403" ]]; then
        result "$name" skip "the model gate is closed (a benchmark run holds the server)" "$(since "$t")"
        return
    fi
    if [[ "$status" != "200" ]]; then
        result "$name" fail "the model server did not answer /v1/models (HTTP $status)" "$(since "$t")"
        return
    fi
    local dir="$WORK/exec-$leg"
    mkdir -p "$dir" "$SCRATCH_HOME/.mightling-$leg"
    out="$(cd "$dir" && HOME="$SCRATCH_HOME" CODEX_HOME="$SCRATCH_HOME/.mightling-$leg" \
        DREAMFERENCE_MIGHTLING_DOCS=0 DREAMFERENCE_VLLM_HOST="http://$host:$MODEL_PORT" \
        timeout 300 "$ling" exec --skip-git-repo-check --output-last-message "$dir/answer.txt" \
        "Reply with exactly one word: pong" < /dev/null 2>&1)"
    code=$?
    answer="$(tr -d '[:space:][:punct:]' < "$dir/answer.txt" 2>/dev/null | tr '[:upper:]' '[:lower:]')"
    if [[ $code -eq 0 && "$answer" == "pong" ]]; then
        result "$name" pass "answered pong ($which_ling)" "$(since "$t")"
    elif [[ $code -eq 0 && "$answer" == *pong* ]]; then
        result "$name" pass "answered '$(head -c 80 "$dir/answer.txt")'" "$(since "$t")"
    elif [[ $code -ne 0 && "$out" == *"503"*"running a benchmark"* ]]; then
        result "$name" skip "model busy (benchmark)" "$(since "$t")"
    else
        result "$name" fail "exit $code, answer '$(head -c 80 "$dir/answer.txt" 2>/dev/null)' ($which_ling): $(echo "$out" | tail -3)" "$(since "$t")"
    fi
}

web_leg() {
    local leg="$1" host="$2" t status
    t=$(started)
    status="$(curl -s -m 20 -o /dev/null -w '%{http_code}' "http://$host:$WEB_PORT/")"
    case "$status" in
        200 | 301 | 302 | 401) result "ling web over the $leg" pass "HTTP $status" "$(since "$t")" ;;
        *) result "ling web over the $leg" fail "HTTP $status from port $WEB_PORT" "$(since "$t")" ;;
    esac
}

if [[ -n "$LAN" ]]; then
    exec_leg LAN "$LAN"
    web_leg LAN "$LAN"
else
    result "ling exec over the LAN" fail "no LAN name for the node was given" 0
fi

# The overlay leg only while this desktop's overlay client is connected and resolves the name.
overlay_up=""
if [[ -n "$OVERLAY" ]] && command -v netbird > /dev/null 2>&1 \
    && netbird status 2>/dev/null | grep -q '^Management: Connected' \
    && getent hosts "$OVERLAY" > /dev/null 2>&1; then
    overlay_up=1
fi
if [[ -n "$overlay_up" ]]; then
    exec_leg overlay "$OVERLAY"
    web_leg overlay "$OVERLAY"
else
    result "ling exec over the overlay" skip "the overlay is not connected here, or the node has no overlay name" 0
fi

# 5. The Python package from a fresh clone of main: its wheel, and its test suite in a virtualenv.
# PyTorch (sentence-transformers') comes from its CPU index: CUDA's wheels are gigabytes this
# desktop does not need, and the suite never uses a GPU.
t=$(started)
export PIP_CACHE_DIR="$CACHE/pip" PIP_DISABLE_PIP_VERSION_CHECK=1
src="$WORK/src"
if ! git clone -q --depth 1 "$REPO_URL" "$src" 2>&1; then
    result "Python package: wheel" fail "git clone of main failed" "$(since "$t")"
    result "Python package: test suite" skip "no clone" 0
else
    log "main at $(git -C "$src" log --oneline -1)"
    python3 -m venv "$WORK/venv"
    py="$WORK/venv/bin/python"
    if "$py" -m pip install -q torch --index-url https://download.pytorch.org/whl/cpu \
        && "$py" -m pip install -q -e "$src[dev]" pytest build; then
        if out="$(cd "$src" && "$py" -m build --wheel -o "$WORK/dist" . 2>&1)"; then
            result "Python package: wheel" pass "$(ls "$WORK/dist")" "$(since "$t")"
        else
            echo "$out"
            result "Python package: wheel" fail "$(echo "$out" | tail -3)" "$(since "$t")"
        fi
        t=$(started)
        out="$(cd "$src" && "$py" -m pytest tests/ -q -p no:cacheprovider 2>&1)"
        code=$?
        echo "$out" | tail -40
        summary="$(echo "$out" | tail -1)"
        if [[ $code -eq 0 ]]; then
            result "Python package: test suite" pass "$summary" "$(since "$t")"
        else
            result "Python package: test suite" fail "$(echo "$out" | grep -E '^(FAILED|ERROR) ' | head -8 | tr '\n' ' ') $summary" "$(since "$t")"
        fi
    else
        result "Python package: wheel" fail "pip install of the package failed" "$(since "$t")"
        result "Python package: test suite" skip "the package did not install" 0
    fi
fi
