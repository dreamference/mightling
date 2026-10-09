#!/bin/bash
# Night 2: the production checkpoint against Minima, the all-NVFP4 Qwen3.8-27B (arXiv 2609.04098),
# on 50 fresh validated SWE-bench tasks, on this machine and today's SGLang
# (specs/DREAMFERENCE_MODELS.md §2.2). Production switches only if Minima's quality holds and it is
# faster here; this night measures both and decides nothing by itself.
#
# Both arms use one recipe (QWEN38_SGLANG_RECIPE: the pinned SGLang image, its flags, the patched
# chat template and the DFlash2 drafter at 16 tokens) and differ only in the target's weights; the
# candidate's entry adds `--kv-cache-dtype bfloat16` so its KV pool is production's. The harness
# settings are night 1's default arm (code index universal, masking off, prompt default, no task
# rules, `max_parallel = 2`); ARM_FLAGS changes them for both arms at once.
#
#   scripts/swe_bench_night2.sh check     every check `start` makes, and the plan; changes nothing
#   scripts/swe_bench_night2.sh start     checks, draws the list if missing, starts the user unit
#   scripts/swe_bench_night2.sh run       the night itself (what the unit runs)
#   scripts/swe_bench_night2.sh restore   serve production again (what an interrupted night calls)
#   DRY_RUN=1 scripts/swe_bench_night2.sh run   the night's steps printed, nothing executed
#       (DRY_FAIL=<registry key> plays a failed start of that model)
#
# The night, in order: production is served (it normally already is) and its speed measured
# (scripts/decode_speed.py: single stream, temperature 0, prose/code/JSON, median of three, plus a
# 13K-token prefill, each answer checked); arm A (`n2-prod`) runs and is graded; the server is
# swapped to Minima by the normal path (`ling-admin server stop`, then `server start --model …`,
# whose host-safety pre-flight, PSI watchdog, warm-up and NVFP4 canary all apply), and the swap
# counts only if the start said ready, the canary did not fail and /v1/models names Minima; its
# speed is measured and arm B (`n2-minima`) runs; production is served again and measured a second
# time (the drift check); then the reports. Production comes back whatever happens: on an error the
# EXIT trap restores it, and on a stop or a signal a separate unit (`ling-swe-night2-restore`) does,
# since a stopped unit's own processes are killed. After a reboot mid-night Docker restarts whichever
# container ran last: run `scripts/swe_bench_night2.sh restore` by hand.
#
# Before `start`: night 1 (unit ling-swe-night1) and the 100-task benchmark (puffin-swe-im100-refine)
# have finished, and the Minima snapshot is in the HuggingFace cache at its pinned commit. WT must be
# a checkout of origin/main that has this change; `start` creates it as a detached worktree when it
# is missing. Never point it at the local `main` branch, which is old history.
set -u
# The main checkout (the one holding .venv and .claude/worktrees), found from this script's own
# location: through git's common directory when the script runs from a worktree of it, else the
# folder above scripts/.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
COMMON=$(git -C "$HERE" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
REPO=${REPO:-$( [ -n "$COMMON" ] && cd "$COMMON/.." && pwd || echo "$HERE")}
WT=${WT:-$REPO/.claude/worktrees/swe-night2}
PY=${PY:-$REPO/.venv/bin/python}
# A new seed for a new draw from the same pool as night 1 (validated, outside sample-100.txt); the
# pool is about 80 tasks, so the two lists overlap, which does not matter to a comparison of models.
SEED=${SEED:-20261010}
LIST=${LIST:-$HOME/.cache/dreamference/swe-bench/fresh-50-night2.txt}
DRY_RUN=${DRY_RUN:-0}
# A dry run writes its logs to a scratch directory unless D names one.
if [ "$DRY_RUN" = 1 ]; then D=${D:-$(mktemp -d)}; else D=${D:-$HOME/.local/share/dreamference/swe-bench/night2}; fi
SELF=$(cd "$(dirname "$0")/.." && pwd)
PREFIX=${PREFIX:-n2}
UNIT=${UNIT:-ling-swe-night2}
WAIT_UNITS=${WAIT_UNITS:-puffin-swe-im100-refine ling-swe-night1}
REQUIRE_NIGHT1=${REQUIRE_NIGHT1:-1}
NIGHT1_DONE=${NIGHT1_DONE:-$HOME/.local/share/dreamference/swe-bench/night1/done}
PROD=${PROD:-qwen3.8-27b-nvfp4-dflash2}
CAND=${CAND:-qwen3.8-27b-minima-nvfp4-dflash2}
ARM_FLAGS=${ARM_FLAGS:---code-index universal --mask off --prompt default}
PORT=${PORT:-8000}
API="http://localhost:$PORT"
# A first boot of a checkpoint compiles (7.5 min for production); an hour is a failed start.
START_TIMEOUT=${START_TIMEOUT:-3600}
# How long a swap waits for a Night Shift run (puffin-night.timer, 01:00) to release the runner lock.
LOCK_WAIT=${LOCK_WAIT:-10800}
IMAGE_GB=3
RESERVE_GB=${RESERVE_GB:-100}

log() { echo "$(date -Is) $*"; }
admin() {
    if [ "$DRY_RUN" = 1 ]; then echo "    [dry] ling-admin $*"; return 0; fi
    "$PY" -c "import sys; from dreamference.cli.dreamference_cli_controller import main; sys.exit(main(sys.argv[1:]))" "$@"
}
# The HuggingFace repository a registry key serves, from the registry itself.
repo_of() { PYTHONPATH="$WT" "$PY" -c "from dreamference.hardware import ModelMatrixRegistry as R; print(R.resolve_hf_repo('$1'))"; }
revision_of() { PYTHONPATH="$WT" "$PY" -c "from dreamference.hardware import ModelMatrixRegistry as R; print(R.get_launch_overrides('$1').get('revision') or '')"; }
snapshot_of() {
    local hub=${HF_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/huggingface}/hub
    echo "$hub/models--$(repo_of "$1" | sed 's#/#--#g')/snapshots/$(revision_of "$1")"
}
SIMULATED=""
served() {
    if [ "$DRY_RUN" = 1 ]; then echo "$SIMULATED"; return; fi
    curl -sf --max-time 10 "$API/v1/models" 2>/dev/null \
        | "$PY" -c 'import json, sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null
}
runner_holder() {
    PYTHONPATH="$WT" "$PY" -c "from dreamference.night_shift import NightShiftQueue as Q; print(Q.runner_holder() or '')" 2>/dev/null
}

# Serves registry key $1 through the normal path; 0 only when it is served, healthy, and named.
swap() {
    local key=$1 repo n start_log waited=0
    repo=$(repo_of "$key")
    if [ "$(served)" = "$repo" ]; then
        log "already serving $repo"
        return 0
    fi
    # `server start` refuses while a Night Shift run holds the runner lock; wait it out.
    while [ "$DRY_RUN" != 1 ] && [ -n "$(runner_holder)" ] && [ "$waited" -lt "$LOCK_WAIT" ]; do
        [ "$waited" = 0 ] && log "waiting for the runner lock: $(runner_holder)"
        sleep 60; waited=$((waited + 60))
    done
    n=$(ls "$D"/server-start-*.log 2>/dev/null | wc -l)
    start_log="$D/server-start-$((n + 1))-$key.log"
    log "swap to $key ($repo): server stop, then server start; log $start_log"
    admin server stop --port "$PORT" > "$start_log" 2>&1
    if [ "$DRY_RUN" = 1 ]; then
        echo "    [dry] timeout $START_TIMEOUT ling-admin server start --model $key --port $PORT"
        # DRY_FAIL=<key> plays a start of that key that fails, leaving no server.
        if [ "${DRY_FAIL:-}" = "$key" ]; then echo "    [dry] (fails)"; SIMULATED=""; return 1; fi
        SIMULATED=$repo
        return 0
    fi
    timeout "$START_TIMEOUT" "$PY" -c "import sys; from dreamference.cli.dreamference_cli_controller import main; sys.exit(main(sys.argv[1:]))" \
        server start --model "$key" --port "$PORT" >> "$start_log" 2>&1
    local rc=$?
    if ! grep -q "Server Ready" "$start_log"; then
        log "❌ $key did not become ready (exit $rc); last lines:"; tail -15 "$start_log"
        return 1
    fi
    if grep -q "Canary Failed" "$start_log"; then
        log "❌ $key failed the NVFP4 canary: its output is corrupt"; grep "Canary" "$start_log"
        return 1
    fi
    if [ "$(served)" != "$repo" ]; then
        log "❌ the server names '$(served)', not $repo"
        return 1
    fi
    log "✅ serving $repo ($(grep -m1 'Loaded in' "$start_log" | sed 's/^ *//'))"
    return 0
}

speed() {
    log "speed, $1"
    if [ "$DRY_RUN" = 1 ]; then echo "    [dry] decode_speed.py --host $API --out $D/speed-$1.json"; return 0; fi
    "$PY" "$WT/scripts/decode_speed.py" --host "$API" --out "$D/speed-$1.json"
    log "speed, $1, finished ($?)"
}

restore() {
    local tries
    for tries in 1 2; do
        swap "$PROD" && return 0
        log "restoring production failed (attempt $tries)"
        admin server stop --port "$PORT" > /dev/null 2>&1
    done
    log "❌ PRODUCTION IS NOT SERVED. Start it by hand: ling-admin server start"
    touch "$D/RESTORE-FAILED"
    return 1
}

checks() {
    local ok=0 unit prod_snap cand_snap count largest need free
    for unit in $WAIT_UNITS; do
        if systemctl --user is-active --quiet "$unit"; then
            echo "❌ $unit is still running; start night 2 once it has finished."; ok=1
        fi
    done
    if systemctl --user is-active --quiet "$UNIT"; then echo "❌ $UNIT is already running."; ok=1; fi
    # Night 2 follows night 1: an inactive unit can also mean one that never started.
    if [ "$REQUIRE_NIGHT1" = 1 ] && [ ! -e "$NIGHT1_DONE" ]; then
        echo "❌ Night 1 has not finished ($NIGHT1_DONE is missing); REQUIRE_NIGHT1=0 runs night 2 without it."; ok=1
    fi
    if ! grep -q "\"$CAND\"" "$WT/dreamference/hardware/model_matrix_registry.py" 2>/dev/null; then
        echo "❌ $WT has no registry entry $CAND: update it to origin/main."; return 1
    fi
    prod_snap=$(snapshot_of "$PROD"); cand_snap=$(snapshot_of "$CAND")
    for snap in "$prod_snap" "$cand_snap"; do
        if ! ls "$snap"/*.safetensors > /dev/null 2>&1; then
            echo "❌ No checkpoint at $snap: download it at its pinned commit first."; ok=1
        fi
    done
    # SGLang takes the largest global scale of a fused NVFP4 group; unequal scales serve mis-scaled.
    if [ -d "$cand_snap" ] && ! python3 "$WT/scripts/check_nvfp4_fused_scales.py" "$cand_snap"; then
        echo "❌ $CAND's fused groups are not harmonized: SGLang would serve it wrong."; ok=1
    fi
    if [ -s "$LIST" ]; then
        count=$(grep -cv '^\s*\(#\|$\)' "$LIST")
        largest=$(grep -v '^\s*\(#\|$\)' "$LIST" | sed 's/-[0-9]*$//' | sort | uniq -c | sort -rn | awk 'NR==1{print $1}')
        echo "Tasks: $count in $LIST ($(sha256sum "$LIST" | cut -c1-12)); largest repository: $largest."
    else
        echo "No list yet at $LIST: \`start\` draws it (seed $SEED)."
        largest=12
    fi
    need=$(( largest * IMAGE_GB + RESERVE_GB ))
    free=$(df --output=avail -BG "$HOME/.cache" | tail -1 | tr -dc '0-9')
    echo "Disk needed about ${need}G, free ${free}G."
    if [ "$free" -lt "$need" ]; then
        echo "❌ Not enough disk. Leftover instance images can go with \`ling-admin swe-bench clean --images\`;"
        echo "   or set a smaller reserve, e.g. RESERVE_GB=60 $0 start."; ok=1
    fi
    echo "Plan: production $PROD ($(repo_of "$PROD")), candidate $CAND ($(repo_of "$CAND") @ $(revision_of "$CAND" | cut -c1-12));"
    echo "      arms $PREFIX-prod then $PREFIX-minima, each: swe-bench run --subset $LIST $ARM_FLAGS --eval --remove-images;"
    echo "      results in $D; the unit $UNIT."
    return $ok
}

start() {
    if [ ! -d "$WT" ]; then
        git -C "$REPO" fetch origin || exit 1
        git -C "$REPO" worktree add --detach "$WT" origin/main || exit 1
    fi
    checks || exit 1
    if [ ! -s "$LIST" ]; then
        PYTHONPATH="$WT" "$PY" "$WT/scripts/swe_bench_fresh.py" draw --count 50 --seed "$SEED" --out "$LIST" \
            --purpose "night 2, production against Minima (specs/DREAMFERENCE_MODELS.md §2.2)" || exit 1
    fi
    mkdir -p "$D"
    { cat "$WT/dreamference.toml"; printf '\n[swe_bench]\nmax_parallel = 2\ndisk_reserve = "%sG"\n' "$RESERVE_GB"; } \
        > "$D/dreamference.toml"
    systemd-run --user --unit="$UNIT" -p OOMPolicy=continue \
        --description="SWE-bench night 2: production against Minima on 50 fresh tasks" \
        --setenv=WT="$WT" --setenv=PY="$PY" --setenv=LIST="$LIST" --setenv=D="$D" --setenv=PREFIX="$PREFIX" \
        --setenv=ARM_FLAGS="$ARM_FLAGS" --setenv=UNIT="$UNIT" \
        /usr/bin/bash -c "'$WT/scripts/swe_bench_night2.sh' run >> '$D/run.log' 2>&1"
    echo "✅ Started $UNIT; log: $D/run.log"
}

on_exit() {
    [ -e "$D/RESTORE-FAILED" ] && return
    if [ "$(served)" != "$(repo_of "$PROD")" ]; then
        log "the night ended with production not served: restoring it"
        restore
    fi
}

on_signal() {
    log "stopped by a signal: production is restored by the unit $UNIT-restore"
    trap - EXIT
    [ "$DRY_RUN" = 1 ] && { echo "    [dry] systemd-run --user --unit=$UNIT-restore … restore"; exit 143; }
    systemd-run --user --unit="$UNIT-restore" --setenv=WT="$WT" --setenv=PY="$PY" --setenv=D="$D" \
        /usr/bin/bash -c "'$WT/scripts/swe_bench_night2.sh' restore >> '$D/run.log' 2>&1"
    exit 143
}

arm() {
    local name=$1
    log "round $name"
    # shellcheck disable=SC2086
    admin swe-bench run --subset "$LIST" --name "$name" $ARM_FLAGS --eval --remove-images
    log "$name finished ($?)"
    df -h / | tail -1
}

run() {
    export PYTHONPATH="$WT"
    export DREAMFERENCE_CONFIG_PATH="$D/dreamference.toml"
    mkdir -p "$D"
    cd "$WT" || exit 1
    trap on_exit EXIT
    trap on_signal TERM INT HUP
    log "night 2 from $(git -C "$WT" rev-parse --short HEAD), list $LIST ($(sha256sum "$LIST" 2>/dev/null | cut -c1-12))"
    [ "$DRY_RUN" = 1 ] && SIMULATED=$(repo_of "$PROD")
    cp "$LIST" "$D/tasks.txt" 2>/dev/null

    if ! swap "$PROD"; then
        log "❌ production is not served; nothing to compare against"
        exit 1
    fi
    speed prod
    arm "$PREFIX-prod"

    if swap "$CAND"; then
        speed minima
        arm "$PREFIX-minima"
    else
        log "❌ arm B not run: $CAND did not serve (see the server-start log)"
        touch "$D/minima-did-not-serve"
    fi

    # The reports need no model server, so they are written even if production does not come back.
    restore && speed prod-after

    admin swe-bench report "$PREFIX-prod" > "$D/report-prod.txt"
    if [ ! -e "$D/minima-did-not-serve" ]; then
        admin swe-bench report "$PREFIX-minima" > "$D/report-minima.txt"
        admin swe-bench report "$PREFIX-minima" --against "$PREFIX-prod" > "$D/minima-against-prod.txt"
    fi
    touch "$D/done"
    log "all done; reports and speeds in $D"
}

case "${1:-}" in
    check)
        if [ ! -d "$WT" ]; then
            echo "($WT does not exist yet: \`start\` creates it from origin/main. Checking with $SELF.)"
            WT=$SELF
        fi
        checks; exit $? ;;
    start) start ;;
    run) run ;;
    restore)
        export PYTHONPATH="$WT"
        [ -s "$D/dreamference.toml" ] && export DREAMFERENCE_CONFIG_PATH="$D/dreamference.toml"
        mkdir -p "$D"; cd "$WT" || exit 1
        restore ;;
    *) echo "usage: $0 {check|start|run|restore}   (DRY_RUN=1 with run prints the steps)"; exit 2 ;;
esac
