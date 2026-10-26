#!/bin/bash
# The engine A/B's model servers on the engine's development machine (`<machine>` below, the ssh name), one at a time (the
# one-model-server rule there): the night script runs this over ssh before each round and at the end.
#
#   ssh <machine> bash -s -- serve sglang < scripts/engine_ab_remote.sh   production's SGLang (scratch container)
#   ssh <machine> bash -s -- serve engine < scripts/engine_ab_remote.sh   ling-serve
#   ssh <machine> bash -s -- restore < scripts/engine_ab_remote.sh        both arms stopped, m0-prod8000 back
#   ssh <machine> bash -s -- status < scripts/engine_ab_remote.sh
#   ssh <machine> AB_DIR=~/ab-n15 bash -s -- adopt ~/ab-n12 < scripts/engine_ab_remote.sh   (see below)
#   ssh <machine> bash -s -- restore keep < scripts/engine_ab_remote.sh   arms stopped, the GPU lock kept
#
# `serve` stops m0-prod8000 and the other arm, starts the arm and waits until /v1/models answers, then prints
# `ready <owned_by>`; it exits non-zero when the arm does not come up. Both arms load the same files: the target
# (RadixArk/Qwen3.8-27B-NVFP4 at Mightling's pinned revision) and the ScaleSearch drafter (the owner's decision of
# 2026-10-18, engine BACKLOG §19.22). SGLang is Mightling's recipe of 1.6.4 (the v0.5.21 image by digest, 12 draft
# tokens; engine bench/sglang/serve.py, whose flag list is production's, with 1.6.4's renamed CUDA-graph flag),
# ling-serve the engine's A/B recipe (engine MILESTONES §14). Files under AB_DIR (default ~/ab-n12): ling-serve (the
# engine binary, pinned by sha256 in its .sha256), serve.py, and the logs.
#
# The GPU lock: the engine's agents share that machine's GPU under `flock ~/.ling-gpu.lock`. The night holds that
# same lock for the whole time an arm runs, not only while it starts: the first `serve` waits for it (GPU_LOCK_WAIT,
# 14 h by default, since day-time engine work ends by 20:00 UTC) and hands it to a detached lease process
# (`ab-n12-gpu-lease`, its pid in AB_DIR/gpu-lease.pid), which later `serve`s reuse and `restore` ends once
# production is started again. The lease ends by itself after HOLD_MAX (30 h) in case no `restore` ever comes.
# ~/gpu.lock stays the inner lock between this helper's own `serve` and `restore`; the GPU lock is always taken first.
#
# One night hands the GPU lock to the next without letting go of it (night 12 to the engine smoke night, 2026-10-20,
# with the engine agents' windows already queued on the lock): `adopt FROM_DIR`, run with the next night's AB_DIR
# after the earlier night's last `serve`, records the earlier night's lease as this AB_DIR's and leaves a decoy in
# FROM_DIR (a process with the lease's name that holds no lock), which the earlier night's `restore` then ends while
# production comes back; the lock stays taken until this AB_DIR's own `restore`. `restore keep` stops the arms and
# keeps the lease (the night script's `start`, between its smokes and the night, so that no waiter takes the lock in
# the gap); with no lease it is a plain `restore`. SG_NAME follows AB_DIR (ab-n12-sglang for ~/ab-n12), so one night's
# late restore never removes the next night's SGLang container.
#
# SGLang's runtime patches (night 17, 2026-10-26): the registry's launches mount patched copies of image files
# (SGLangRuntimePatcher: on v0.5.21, `serving_responses.py` with the phase merge). This launch does not go through
# the registry, so the same files are copied into AB_DIR/sglang-patches/ with a `mounts` file there, one
# `<file> <path in the container>` per line, and each is mounted read-only (serve.py's `--volume`). With a mount
# whose path is `serving_responses.py`, `serve sglang` then asks the server the phase question before it says
# ready: one history with and without `phase: "commentary"` on the announcement before a tool call must cost the
# same prompt tokens (the patched merge ignores the phase; v0.5.21's own does not), else the arm is stopped.
set -u
AB_DIR=${AB_DIR:-$HOME/ab-n12}
SG_NAME=${SG_NAME:-$(basename "$AB_DIR")-sglang}
SG_PORT=${SG_PORT:-30000}
ENGINE_PORT=${ENGINE_PORT:-8300}
SG_IMAGE=lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469
MODEL=$(ls -d "${MODEL_DIR:-$HOME/.cache/huggingface/hub/models--RadixArk--Qwen3.8-27B-NVFP4/snapshots/52d1adc5f38aa5ebf099c29ed7025ba34cfbb854}" 2>/dev/null)
DRAFT=${DRAFT:-$HOME/.cache/dreamference/drafters/dflash2-nvfp4-scalesearch}
DRAFT_SHA256=${DRAFT_SHA256:-5ec78f26b5254d46bd376b2127ce87d6dfb0b6653e01d6f36983cfd3fdc35f4b}
GPU_LOCK=${GPU_LOCK:-$HOME/.ling-gpu.lock}
GPU_LOCK_WAIT=${GPU_LOCK_WAIT:-50400}
HOLD_MAX=${HOLD_MAX:-30h}
SETTLE_S=${SETTLE_S:-20}
HOLDER=ab-n12-gpu-lease

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
sg_volumes() {  # serve.py's `--volume` for each runtime patch in AB_DIR/sglang-patches/mounts
    local file target
    [ -s "$AB_DIR/sglang-patches/mounts" ] || return 0
    while read -r file target; do
        [ -n "$file" ] || continue
        [ -s "$AB_DIR/sglang-patches/$file" ] || { log "runtime patch $file is missing"; return 1; }
        printf -- '--volume\n%s\n' "$AB_DIR/sglang-patches/$file:$target:ro"
    done < "$AB_DIR/sglang-patches/mounts"
}
phase_probe() {  # port: `same N` when the phase merge is in force, `differ A B` when it is not
    [ -n "${PHASE_PROBE_CMD:-}" ] && { $PHASE_PROBE_CMD "$1"; return; }  # tests stand in for the server
    python3 - "http://127.0.0.1:$1" "${SERVED_NAME:-RadixArk/Qwen3.8-27B-NVFP4}" <<'PY'
import json, sys, urllib.request
base, model = sys.argv[1], sys.argv[2]
def items(phase):
    note = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "I will list the files first."}]}
    if phase:
        note["phase"] = phase
    return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "What files are in the repository?"}]},
            note,
            {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": json.dumps({"command": ["ls"]})},
            {"type": "function_call_output", "call_id": "call_1", "output": "README.md\nsetup.py\n"}]
def tokens(phase):
    tool = {"type": "function", "name": "shell", "description": "Run a command", "parameters": {"type": "object",
            "properties": {"command": {"type": "array", "items": {"type": "string"}}}}}
    body = {"model": model, "input": items(phase), "max_output_tokens": 16, "store": False, "tools": [tool]}
    request = urllib.request.Request(base + "/v1/responses", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=120) as response:
        return json.load(response)["usage"]["input_tokens"]
a, b = tokens("commentary"), tokens(None)
print(f"same {a}" if a == b else f"differ {a} {b}")
PY
}
keep_logs() {  # the SGLang arm's container log, kept before the container goes
    docker logs "$SG_NAME" > "$AB_DIR/sglang-$(date +%Y%m%d-%H%M%S).log" 2>&1
}
lease_pid() {  # the live lease process holding the GPU lock for the night (of AB_DIR, or of folder $1), or nothing
    local p
    p=$(cat "${1:-$AB_DIR}/gpu-lease.pid" 2>/dev/null)
    [ -n "$p" ] && tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q "^$HOLDER " && echo "$p"
}
take_lease() {  # the GPU lock, held from now until `restore` by a detached lease process
    local p
    if p=$(lease_pid); then log "the GPU lock is already the night's (lease $p)"; return 0; fi
    rm -f "$AB_DIR/gpu-lease.pid"
    log "waiting for the GPU lock $GPU_LOCK (at most $GPU_LOCK_WAIT s)"
    exec 8> "$GPU_LOCK"
    flock -w "$GPU_LOCK_WAIT" 8 || { exec 8>&-; log "the GPU lock was not free within $GPU_LOCK_WAIT s"; return 1; }
    # The lease inherits fd 8, so the lock stays taken after this ssh session ends; nothing else started here does.
    setsid nohup bash -c "exec -a $HOLDER sleep $HOLD_MAX" > /dev/null 2>&1 < /dev/null 9>&- &
    p=$!
    exec 8>&-
    echo "$p" > "$AB_DIR/gpu-lease.pid"
    log "GPU lock taken ($GPU_LOCK, lease $p)"
}

mkdir -p "$AB_DIR"
case "${1:-}" in
serve)
    arm=${2:-}
    [ -n "$MODEL" ] || { log "the target's snapshot is missing"; exit 1; }
    [ "$(sha256sum "$DRAFT/model.safetensors" | cut -d' ' -f1)" = "$DRAFT_SHA256" ] || { log "the ScaleSearch drafter is missing or changed"; exit 1; }
    take_lease || exit 1
    exec 9> "$HOME/gpu.lock"; flock -w 1800 9 || { log "the helper's own lock was not free within 30 min"; exit 1; }
    stop_prod
    stop_arms
    # The night of 2026-10-18 lost its SGLang arm: the container exited (status 0) at about the time it binds its
    # port, 6.5 min into the start, and its log went with it. So: production gets a moment to let go of the GPU,
    # the arm's port must be free, the log is kept, and an arm that exits before it answers is started once more.
    sleep "$SETTLE_S"
    case "$arm" in sglang) port=$SG_PORT; container=$SG_NAME ;; engine) port=$ENGINE_PORT; container= ;;
        *) log "serve: no arm $arm (sglang or engine)"; exit 2 ;; esac
    port_free "$port" 120 || { stop_arms; exit 1; }
    volumes=()
    if [ "$arm" = sglang ]; then
        mounts=$(sg_volumes) || { stop_arms; exit 1; }
        [ -n "$mounts" ] && mapfile -t volumes <<< "$mounts"
        if [ "${#volumes[@]}" -gt 0 ] && ! grep -q -- '"--volume"' "$AB_DIR/serve.py"; then
            log "$AB_DIR/serve.py has no --volume for the runtime patches"; exit 1
        fi
    fi
    [ "$arm" = engine ] && { [ "$(sha256sum "$AB_DIR/ling-serve" | cut -d' ' -f1)" = "$(cat "$AB_DIR/ling-serve.sha256")" ] || { log "ling-serve is not the pinned binary"; exit 1; }; }
    own=
    for attempt in 1 2; do
        case "$arm" in
        sglang)
            python3 "$AB_DIR/serve.py" "$SG_NAME" --image "$SG_IMAGE" --port "$SG_PORT" \
                --set speculative-num-draft-tokens 12 \
                --set speculative-draft-model-path /root/.cache/dreamference/drafters/dflash2-nvfp4-scalesearch \
                --drop cuda-graph-max-bs --set cuda-graph-max-bs-decode 8 "${volumes[@]}" > "$AB_DIR/sglang.cmd" 9>&- \
                || { log "serve.py failed"; exit 1; } ;;
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
        [ "$attempt" = 1 ] && { sleep "$SETTLE_S"; port_free "$port" 120 || exit 1; }
    done
    [ -n "$own" ] || exit 1
    [ -n "$container" ] && keep_logs
    if [ "$arm" = sglang ] && grep -q 'serving_responses.py$' "$AB_DIR/sglang-patches/mounts" 2>/dev/null; then
        mounted=$(docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' "$SG_NAME" 2>/dev/null)
        case "$mounted" in *serving_responses.py*) ;; *) log "the phase-merge patch is not mounted in $SG_NAME"; stop_arms; exit 1 ;; esac
        probe=$(phase_probe "$port" 2>&1 | tail -1)
        case "$probe" in
            same\ *) log "phase merge in force: ${probe#same } prompt tokens with and without phase commentary" ;;
            *) log "phase merge NOT in force ($probe)"; stop_arms; exit 1 ;;
        esac
    fi
    log "arm $arm ready on $port ($own)"
    echo "ready $own"
    ;;
adopt)
    from=${2:-}
    [ -n "$from" ] && [ -d "$from" ] && [ "$(cd "$from" && pwd -P)" != "$(cd "$AB_DIR" && pwd -P)" ] \
        || { log "adopt: no other night's folder ${from:-(none given)}"; exit 2; }
    if p=$(lease_pid); then log "adopt: the GPU lock is already this night's (lease $p)"; echo "adopted $p"; exit 0; fi
    real=$(lease_pid "$from") || { log "adopt: no live lease in $from"; exit 1; }
    flock -n "$GPU_LOCK" true && { log "adopt: the GPU lock is free, so lease $real holds nothing"; exit 1; }
    setsid nohup bash -c "exec -a $HOLDER sleep $HOLD_MAX" > /dev/null 2>&1 < /dev/null 8>&- 9>&- &
    decoy=$!
    sleep 0.5
    tr '\0' ' ' < "/proc/$decoy/cmdline" 2>/dev/null | grep -q "^$HOLDER " || { log "adopt: the decoy did not start"; exit 1; }
    echo "$real" > "$AB_DIR/gpu-lease.pid"
    echo "$decoy" > "$from/gpu-lease.pid.new" && mv "$from/gpu-lease.pid.new" "$from/gpu-lease.pid"
    log "adopted lease $real (the GPU lock) from $from; the decoy $decoy left there holds no lock"
    echo "adopted $real"
    ;;
restore)
    if [ "${2:-}" = keep ] && p=$(lease_pid); then
        exec 9> "$HOME/gpu.lock"; flock -w 1800 9
        stop_arms
        log "arms stopped; the GPU lock kept for the night (lease $p)"
        echo "kept"
        exit 0
    fi
    # The stop and the start run detached from this ssh session, so production comes back even when the caller
    # is stopped or its connection drops halfway (a stopped night unit has 90 s before systemd kills it). The night's
    # lease on the GPU lock ends only after production is started; with no lease, the GPU lock is taken for the
    # restore, and when someone else holds it production is theirs to bring back (their own protocol).
    export AB_DIR SG_NAME GPU_LOCK HOLDER
    export -f log engine_pids stop_arms lease_pid
    setsid nohup bash -c '
        lease=$(lease_pid)
        if [ -z "$lease" ]; then
            exec 8> "$GPU_LOCK"
            flock -w 1800 8 || { log "the GPU lock is held by other work; m0-prod8000 left to it"; stop_arms; exit 1; }
        fi
        exec 9> "$HOME/gpu.lock"; flock -w 1800 9
        stop_arms; docker start m0-prod8000 > /dev/null 2>&1
        if [ -n "$lease" ]; then kill "$lease"; rm -f "$AB_DIR/gpu-lease.pid"; log "GPU lock released (lease $lease)"; fi' \
        >> "$AB_DIR/remote.log" 2>&1 < /dev/null &
    own=$(wait_ready 8000 900) && log "m0-prod8000 back ($own)" || { log "m0-prod8000 did not answer within 15 min"; exit 1; }
    echo "restored"
    ;;
status)
    echo "sglang: $(answers "$SG_PORT") engine: $(answers "$ENGINE_PORT") m0-prod8000: $(answers 8000) lease: $(lease_pid)"
    ;;
*) echo "usage: engine_ab_remote.sh serve sglang|engine | restore [keep] | adopt FROM_DIR | status" >&2; exit 2 ;;
esac
