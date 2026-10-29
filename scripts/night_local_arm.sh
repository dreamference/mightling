#!/bin/bash
# The engine A/B's arms on this machine's own model server (the local-arm mode of scripts/swe_bench_night1.sh,
# LOCAL_ARMS=1; the 1.8.0 bar night, 2026-10-30): one arm at a time, each served the way 1.8.0 ships it,
# `ling-admin server start --engine ling|sglang` from the night's worktree at REF, so each arm runs the image and
# the recipe pinned in REF's registry (ling-serve at its context and sessions, SGLang with its runtime patches),
# behind the model gate.
#
#   night_local_arm.sh serve sglang|engine   production's containers in PROD_STOP stopped first; the model server
#                                            restarted with the arm's engine unless it already serves the arm's
#                                            image; ready once /v1/models answers on PORT, the container runs the
#                                            image pinned at REF and the gate is up. Prints `ready <engine> <image>`.
#   night_local_arm.sh restore               production back: with RESTORE_ENGINE=sglang (a machine whose production
#                                            is Mightling's SGLang) `server start --engine sglang` by RESTORE_ADMIN,
#                                            nothing to do when it already serves; with RESTORE_ENGINE=none the arm
#                                            stopped and every PROD_STOP container started again (the two settings
#                                            are exclusive: one production per GPU). Prints `restored`.
#   night_local_arm.sh restore keep          between the smokes and the night: nothing (the night serves each arm
#                                            again; the machine's GPU lock, when it has one, is the chain's).
#   night_local_arm.sh status                one line: the server's image, the gate, PROD_STOP's containers.
#
# Per arm, ENGINE_ARGS (flags added to the engine's command line, e.g. `--fair-pairing 1`) and ENGINE_ENV
# (`KEY=VALUE ...` added to its container, e.g. `LING_STEP_TRACE=/root/.cache/dreamference/night-traces/x.jsonl`,
# which the host sees under ~/.cache/dreamference): `server start` has no option for either (a launch flag belongs
# in the registry), so for that one start a `docker` stand-in first on PATH adds them to the `docker run` of
# dreamference-vllm-PORT and nothing else, and the arm is ready only once the container's command line and
# environment carry them. An arm already up is restarted unless it was served with the same flags and environment.
# The first `serve` of a night records the server as it was (STATE_DIR/before); the full `restore` compares the
# server it brings back with it, says so when they differ, and removes the record.
# Settings (environment): WT and PY (the worktree and its venv's Python), STATE_DIR, PORT (8000), PROD_STOP
# (container names, e.g. `m0-prod8000` on a machine whose production is not Mightling's), RESTORE_ENGINE (sglang),
# RESTORE_ADMIN (a `ling-admin` command; default the installed one, else the worktree's), READY_S (1800),
# RUNNER_WAIT_S (1800: how long `server start` waits for a run holding the runner lock).
set -u
WT=${WT:?the worktree}
PY=${PY:-$WT/.venv/bin/python}
PORT=${PORT:-8000}
STATE_DIR=${STATE_DIR:-$HOME/.local/share/dreamference/swe-bench/local-arm}
PROD_STOP=${PROD_STOP:-}
RESTORE_ENGINE=${RESTORE_ENGINE:-sglang}
RESTORE_ADMIN=${RESTORE_ADMIN:-}
READY_S=${READY_S:-1800}
RUNNER_WAIT_S=${RUNNER_WAIT_S:-1800}
POLL_S=${POLL_S:-5}
ENGINE_ARGS=${ENGINE_ARGS:-}
ENGINE_ENV=${ENGINE_ENV:-}
RUNNER_POLL_S=${RUNNER_POLL_S:-30}
SERVER=dreamference-vllm-$PORT
GATE=dreamference-gate-$PORT

log() { echo "$(date -Is) $*" | tee -a "$STATE_DIR/local-arm.log" >&2; }
admin() { (cd "$WT" && PYTHONPATH="$WT" "$PY" -c 'import sys; from dreamference.cli.dreamference_cli_controller import main; sys.exit(main(sys.argv[1:]))' "$@"); }
admin_shimmed() { PATH="$dir:$PATH" admin "$@"; }
restore_admin() {
    if [ -n "$RESTORE_ADMIN" ]; then $RESTORE_ADMIN "$@"
    elif [ -x "$HOME/.local/bin/ling-admin" ]; then "$HOME/.local/bin/ling-admin" "$@"
    else admin "$@"; fi
}
# The image REF's registry pins for an arm (`engine` is ling-serve's recipe, `sglang` SGLang's).
want_image() {
    (cd "$WT" && PYTHONPATH="$WT" "$PY" -c 'import sys
from dreamference.hardware.model_matrix_registry import QWEN38_LING_RECIPE, QWEN38_SGLANG_RECIPE
print((QWEN38_LING_RECIPE if sys.argv[1] == "engine" else QWEN38_SGLANG_RECIPE)["docker_image"])' "$1")
}
engine_flag() { case "$1" in engine) echo ling ;; sglang) echo sglang ;; *) return 2 ;; esac; }
image() { docker inspect "$SERVER" --format '{{.Config.Image}}' 2>/dev/null; }
running() { [ "$(docker inspect "$1" --format '{{.State.Running}}' 2>/dev/null)" = true ]; }
answers() {
    python3 -I -c '
import json, sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
data = json.load(opener.open(sys.argv[1] + "/v1/models", timeout=10)).get("data") or []
sys.exit(0 if data and data[0].get("id") else 1)' "http://127.0.0.1:$PORT" 2>/dev/null
}
# Prints what differs from `image $1, running, the gate up, /v1/models answering`; nothing when the server is that.
differs() {
    local have
    have=$(image)
    [ "$have" = "$1" ] || echo "image ${have:-none}"
    running "$SERVER" || echo "$SERVER not running"
    running "$GATE" || echo "no gate"
    answers || echo "/v1/models silent"
}
wait_server() {  # image -> 0 once the server is that image, gate up, answering; within READY_S
    local waited=0
    while [ "$waited" -lt "$READY_S" ]; do
        [ -z "$(differs "$1")" ] && return 0
        sleep "$POLL_S"; waited=$((waited + POLL_S))
    done
    [ -z "$(differs "$1")" ]
}
# `server start` (or stop) by $1 (admin or restore_admin), waiting while a run holds the runner lock.
start_with() {
    local how=$1 out rc waited=0
    shift
    while :; do
        out=$($how server start "$@" 2>&1)
        rc=$?
        echo "$out" | tail -3 >> "$STATE_DIR/local-arm.log"
        case "$out" in *"is in progress, so"*) ;; *) return "$rc" ;; esac
        [ "$waited" -lt "$RUNNER_WAIT_S" ] || { log "a run still holds the runner lock after ${RUNNER_WAIT_S}s"; return 1; }
        sleep "$RUNNER_POLL_S"; waited=$((waited + RUNNER_POLL_S))
    done
}
# The container's command line and environment carry ENGINE_ARGS and ENGINE_ENV (nothing printed) or not.
carries() {
    local args env pair
    [ -z "$ENGINE_ARGS$ENGINE_ENV" ] && return 0
    args=" $(docker inspect "$SERVER" --format '{{join .Args " "}}' 2>/dev/null) "
    case "$args" in *" $ENGINE_ARGS "*) ;; *) echo "command line without $ENGINE_ARGS" ;; esac
    env=$(docker inspect "$SERVER" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null)
    for pair in $ENGINE_ENV; do echo "$env" | grep -qxF -- "$pair" || echo "environment without $pair"; done
}
# A `docker` first on PATH that adds ENGINE_ENV and ENGINE_ARGS to the run of the model server's container only.
shim() {
    local dir=$STATE_DIR/shim real
    real=$(command -v docker) || return 1
    mkdir -p "$dir"
    {
        echo '#!/bin/bash'
        printf 'REAL=%q
' "$real"
        printf 'NAME=%q
' "$SERVER"
        printf 'ENVS=(%s)
' "$(for pair in $ENGINE_ENV; do printf '%q ' -e "$pair"; done)"
        printf 'ARGS=(%s)
' "$(for a in $ENGINE_ARGS; do printf '%q ' "$a"; done)"
        cat <<'SHIM'
if [ "${1:-}" = run ]; then
    for a in "$@"; do
        if [ "$a" = "$NAME" ]; then shift; exec "$REAL" run "${ENVS[@]}" "$@" "${ARGS[@]}"; fi
    done
fi
exec "$REAL" "$@"
SHIM
    } > "$dir/docker"
    chmod +x "$dir/docker"
    echo "$dir"
}
record_before() {
    [ -s "$STATE_DIR/before" ] && return 0
    {
        echo "image $(image)"
        echo "running $(running "$SERVER" && echo yes || echo no)"
        echo "gate $(running "$GATE" && echo yes || echo no)"
        for c in $PROD_STOP; do echo "prod $c $(running "$c" && echo yes || echo no)"; done
    } > "$STATE_DIR/before"
    log "before the night: $(tr '\n' ';' < "$STATE_DIR/before")"
}
stop_prod() {
    local c
    for c in $PROD_STOP; do
        if running "$c"; then docker stop "$c" > /dev/null && log "$c stopped"; fi
    done
}
start_prod() {
    local c
    for c in $PROD_STOP; do
        running "$c" || { docker start "$c" > /dev/null 2>&1 && log "$c started again"; } || log "$c did not start"
    done
}

mkdir -p "$STATE_DIR"
if [ "$RESTORE_ENGINE" = sglang ] && [ -n "$PROD_STOP" ]; then
    log "PROD_STOP ($PROD_STOP) goes with RESTORE_ENGINE=none: production is either Mightling's SGLang or those"
    exit 2
fi
case "$RESTORE_ENGINE" in sglang|none) ;; *) log "RESTORE_ENGINE is sglang or none, not $RESTORE_ENGINE"; exit 2 ;; esac
case "${1:-}" in
serve)
    arm=${2:-}
    flag=$(engine_flag "$arm") || { log "serve: no arm ${arm:-(none)} (sglang or engine)"; exit 2; }
    want=$(want_image "$arm") && [ -n "$want" ] || { log "could not read the $arm image pinned at $WT"; exit 1; }
    record_before
    stop_prod
    spec="$arm|$ENGINE_ARGS|$ENGINE_ENV"
    if [ -z "$(differs "$want")" ] && [ "$(cat "$STATE_DIR/serving" 2>/dev/null)" = "$spec" ] \
        || { [ -z "$ENGINE_ARGS$ENGINE_ENV" ] && [ ! -s "$STATE_DIR/serving" ] && [ -z "$(differs "$want")" ]; }; then
        log "the server already serves the $arm arm ($want${ENGINE_ARGS:+, $ENGINE_ARGS}${ENGINE_ENV:+, $ENGINE_ENV})"
    else
        log "serving the $arm arm: server start --engine $flag ($want${ENGINE_ARGS:+, $ENGINE_ARGS}${ENGINE_ENV:+, $ENGINE_ENV})"
        how=admin
        if [ -n "$ENGINE_ARGS$ENGINE_ENV" ]; then
            dir=$(shim) || { log "no docker to wrap for the arm's flags"; exit 1; }
            how=admin_shimmed
        fi
        ok=0
        for attempt in 1 2; do
            rm -f "$STATE_DIR/serving"
            admin server stop > /dev/null 2>&1
            start_with "$how" --engine "$flag" || log "server start --engine $flag exited $? (attempt $attempt)"
            if wait_server "$want" && [ -z "$(carries)" ]; then ok=1; break; fi
            log "the $arm arm is not up (attempt $attempt): $(differs "$want" | tr '\n' ';')$(carries | tr '\n' ';')"
        done
        [ "$ok" = 1 ] || exit 1
        echo "$spec" > "$STATE_DIR/serving"
    fi
    log "arm $arm ready on $PORT ($want${ENGINE_ARGS:+, $ENGINE_ARGS})"
    echo "ready $arm $want${ENGINE_ARGS:+ $ENGINE_ARGS}"
    ;;
restore)
    if [ "${2:-}" = keep ]; then log "restore keep: the arm stays up until the night serves it again"; echo kept; exit 0; fi
    rc=0
    rm -f "$STATE_DIR/serving"
    if [ "$RESTORE_ENGINE" = sglang ]; then
        want=$(want_image sglang) || want=
        if [ -n "$want" ] && [ -z "$(differs "$want")" ]; then
            log "production already serves ($want)"
        else
            restore_admin server stop > /dev/null 2>&1
            start_with restore_admin --engine sglang || log "server start --engine sglang exited $?"
            if [ -n "$want" ] && wait_server "$want"; then log "production back ($want)"
            else log "production is not back: $(differs "$want" | tr '\n' ';')"; rc=1; fi
        fi
    else
        running "$SERVER" && { admin server stop > /dev/null 2>&1; log "the arm stopped"; }
        start_prod
        waited=0
        while ! answers && [ "$waited" -lt "$READY_S" ]; do sleep "$POLL_S"; waited=$((waited + POLL_S)); done
        if answers; then log "production back on $PORT ($PROD_STOP)"; else log "nothing answers on $PORT after the restore"; rc=1; fi
    fi
    if [ -s "$STATE_DIR/before" ]; then
        was=$(sed -n 's/^image //p' "$STATE_DIR/before")
        if [ "$RESTORE_ENGINE" = sglang ] && [ -n "$was" ] && [ "$was" != "$(image)" ]; then
            log "note: production runs $(image), not $was as before the night"
        fi
        mv "$STATE_DIR/before" "$STATE_DIR/before.$(date +%Y%m%d-%H%M%S)"
    fi
    [ "$rc" = 0 ] && echo restored
    exit "$rc"
    ;;
status)
    line="server: $(image || true) $(running "$SERVER" && echo running || echo stopped) gate: $(running "$GATE" && echo up || echo down)"
    for c in $PROD_STOP; do line="$line $c: $(running "$c" && echo up || echo down)"; done
    echo "$line"
    ;;
*) echo "usage: night_local_arm.sh serve sglang|engine | restore [keep] | status" >&2; exit 2 ;;
esac
