#!/bin/bash
# The engine A/B's model servers on the engine's development machine (second-puffin), one at a time (the
# one-model-server rule there): the night script runs this over ssh before each round and at the end.
#
#   ssh second-puffin bash -s -- serve sglang < scripts/engine_ab_remote.sh   production's SGLang (scratch container)
#   ssh second-puffin bash -s -- serve engine < scripts/engine_ab_remote.sh   ling-serve
#   ssh second-puffin bash -s -- restore < scripts/engine_ab_remote.sh        both arms stopped, m0-prod8000 back
#   ssh second-puffin bash -s -- status < scripts/engine_ab_remote.sh
#
# `serve` stops m0-prod8000 and the other arm, starts the arm and waits until /v1/models answers, then prints
# `ready <owned_by>`; it exits non-zero when the arm does not come up. Both arms load the same files: the target
# (RadixArk/Qwen3.8-27B-NVFP4 at Mightling's pinned revision) and the ScaleSearch drafter (the owner's decision of
# 2026-10-18, engine BACKLOG §19.22). SGLang is Mightling's recipe of 1.6.4 (the v0.5.21 image by digest, 12 draft
# tokens; engine bench/sglang/serve.py, whose flag list is production's, with 1.6.4's renamed CUDA-graph flag),
# ling-serve the engine's A/B recipe (engine MILESTONES §14). Files under AB_DIR (default ~/ab-n12): ling-serve (the
# engine binary, pinned by sha256 in its .sha256), serve.py, and the logs.
set -u
AB_DIR=${AB_DIR:-$HOME/ab-n12}
SG_NAME=ab-n12-sglang
SG_PORT=${SG_PORT:-30000}
ENGINE_PORT=${ENGINE_PORT:-8300}
SG_IMAGE=lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469
MODEL=$(ls -d "$HOME"/.cache/huggingface/hub/models--RadixArk--Qwen3.8-27B-NVFP4/snapshots/52d1adc5f38aa5ebf099c29ed7025ba34cfbb854 2>/dev/null)
DRAFT=$HOME/.cache/dreamference/drafters/dflash2-nvfp4-scalesearch
DRAFT_SHA256=5ec78f26b5254d46bd376b2127ce87d6dfb0b6653e01d6f36983cfd3fdc35f4b

log() { echo "$(date -Is) $*" | tee -a "$AB_DIR/remote.log" >&2; }
answers() {  # port -> owned_by of /v1/models, empty when nothing answers
    curl -sf -m 5 "http://127.0.0.1:$1/v1/models" 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(d[0].get("owned_by") or "?") if d else None' 2>/dev/null
}
engine_pids() { pgrep -f "^$AB_DIR/ling-serve " ; }
stop_arms() {
    [ -n "$(docker ps -aq -f name=^$SG_NAME$)" ] && docker logs "$SG_NAME" > "$AB_DIR/sglang-last.log" 2>&1
    docker rm -f "$SG_NAME" > /dev/null 2>&1
    for p in $(engine_pids); do kill "$p" 2>/dev/null; done
    for _ in $(seq 30); do [ -z "$(engine_pids)" ] && break; sleep 2; done
    for p in $(engine_pids); do kill -9 "$p" 2>/dev/null; done
}
stop_prod() {
    if [ -n "$(docker ps -q -f name=^m0-prod8000$)" ]; then docker stop m0-prod8000 > /dev/null && log "m0-prod8000 stopped"; fi
}
wait_ready() {  # port, seconds[, container[, pid]] -> owned_by; gives up early when the arm has exited
    local own
    for _ in $(seq $(( $2 / 5 ))); do
        own=$(answers "$1")
        [ -n "$own" ] && { echo "$own"; return 0; }
        if [ -n "${3:-}" ] && [ "$(docker inspect -f '{{.State.Running}}' "$3" 2>/dev/null)" != true ]; then
            log "$3 exited ($(docker inspect -f '{{.State.ExitCode}}' "$3" 2>/dev/null)) before answering"; return 1
        fi
        if [ -n "${4:-}" ] && ! kill -0 "$4" 2>/dev/null; then log "ling-serve exited before answering"; return 1; fi
        sleep 5
    done
    return 1
}
port_free() {  # port, seconds: waits until nothing listens on it; names the holder when something stays
    for _ in $(seq $(( $2 / 5 ))); do
        ss -ltnH "sport = :$1" 2>/dev/null | grep -q . || return 0
        sleep 5
    done
    log "port $1 is still taken: $(ss -ltnpH "sport = :$1" 2>/dev/null | head -1)"
    return 1
}
keep_logs() {  # the SGLang arm's container log, kept before the container goes
    docker logs "$SG_NAME" > "$AB_DIR/sglang-$(date +%Y%m%d-%H%M%S).log" 2>&1
}

mkdir -p "$AB_DIR"
case "${1:-}" in
serve)
    arm=${2:-}
    [ -n "$MODEL" ] || { log "the target's snapshot is missing"; exit 1; }
    [ "$(sha256sum "$DRAFT/model.safetensors" | cut -d' ' -f1)" = "$DRAFT_SHA256" ] || { log "the ScaleSearch drafter is missing or changed"; exit 1; }
    exec 9> "$HOME/gpu.lock"; flock -w 1800 9 || { log "the GPU lock was not free within 30 min"; exit 1; }
    stop_prod
    stop_arms
    # The night of 2026-10-18 lost its SGLang arm: the container exited (status 0) at about the time it binds its
    # port, 6.5 min into the start, and its log went with it. So: production gets a moment to let go of the GPU,
    # the arm's port must be free, the log is kept, and an arm that exits before it answers is started once more.
    sleep 20
    case "$arm" in sglang) port=$SG_PORT; container=$SG_NAME ;; engine) port=$ENGINE_PORT; container= ;;
        *) log "serve: no arm $arm (sglang or engine)"; exit 2 ;; esac
    port_free "$port" 120 || { stop_arms; exit 1; }
    [ "$arm" = engine ] && { [ "$(sha256sum "$AB_DIR/ling-serve" | cut -d' ' -f1)" = "$(cat "$AB_DIR/ling-serve.sha256")" ] || { log "ling-serve is not the pinned binary"; exit 1; }; }
    own=
    for attempt in 1 2; do
        case "$arm" in
        sglang)
            python3 "$AB_DIR/serve.py" "$SG_NAME" --image "$SG_IMAGE" --port "$SG_PORT" \
                --set speculative-num-draft-tokens 12 \
                --set speculative-draft-model-path /root/.cache/dreamference/drafters/dflash2-nvfp4-scalesearch \
                --drop cuda-graph-max-bs --set cuda-graph-max-bs-decode 8 > "$AB_DIR/sglang.cmd" 9>&- || { log "serve.py failed"; exit 1; } ;;
        engine)
            setsid "$AB_DIR/ling-serve" --model "$MODEL" --draft "$DRAFT" --host 0.0.0.0 --port "$ENGINE_PORT" \
                --served-model-name RadixArk/Qwen3.8-27B-NVFP4 --max-context 65536 --draft-block 12 --graphs --pdl \
                >> "$AB_DIR/engine.log" 2>&1 < /dev/null 9>&- &
            engine_pid=$! ;;
        esac
        if own=$(wait_ready "$port" 1200 "$container" "${engine_pid:-}"); then break; fi
        log "arm $arm did not answer on $port (attempt $attempt)"
        [ -n "$container" ] && keep_logs
        stop_arms
        own=
        [ "$attempt" = 1 ] && { sleep 20; port_free "$port" 120 || exit 1; }
    done
    [ -n "$own" ] || exit 1
    [ -n "$container" ] && keep_logs
    log "arm $arm ready on $port ($own)"
    echo "ready $own"
    ;;
restore)
    # The stop and the start run detached from this ssh session, so production comes back even when the caller
    # is stopped or its connection drops halfway (a stopped night unit has 90 s before systemd kills it).
    export AB_DIR SG_NAME
    export -f log engine_pids stop_arms
    setsid nohup bash -c 'exec 9> "$HOME/gpu.lock"; flock -w 1800 9; stop_arms; docker start m0-prod8000 > /dev/null 2>&1' \
        >> "$AB_DIR/remote.log" 2>&1 < /dev/null &
    own=$(wait_ready 8000 900) && log "m0-prod8000 back ($own)" || { log "m0-prod8000 did not answer within 15 min"; exit 1; }
    echo "restored"
    ;;
status)
    echo "sglang: $(answers "$SG_PORT") engine: $(answers "$ENGINE_PORT") m0-prod8000: $(answers 8000)"
    ;;
*) echo "usage: engine_ab_remote.sh serve sglang|engine | restore | status" >&2; exit 2 ;;
esac
