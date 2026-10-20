#!/bin/bash
# Night 1 of the failure analysis's A/B (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md §6.5):
# the default arm against the test-discipline arm (`--task-rules tests-v2`, FAILURES §9.3) on 50
# fresh validated tasks, two at a time, each graded as it finishes (`--eval`), then both regraded
# with the test files dropped from every patch (`eval --drop-test-hunks`, no agent time), and the
# reports.
#
# Both arms carry the harness fixes of the same change: the per-file test reset at grading, the
# wider "test patch failed" count and the completion nudge. Settings are those of `im100-default`
# (code index universal, masking off, prompt default), with `max_parallel = 2`. Refine is off unless
# REFINE=1, which adds `--refine` to both arms: set it when refine has become the default, so the
# night tests tests-v2 on top of what users run.
#
#   scripts/swe_bench_night1.sh start   checks, smokes every arm, then runs `run` as the user unit ling-swe-night1
#   scripts/swe_bench_night1.sh run     the night itself (what the unit runs)
#
# The nights run on this machine alone (`nodes = "none"`, the owner's decision of 2026-10-10): a paired
# node's model server would join one arm as a lane with its own draft-token setting and image, and the
# second machine belongs to the engine's work over the weekend.
#
# Before `start`: the 100-task benchmark (unit puffin-swe-im100-refine) has finished, and the list
# exists (scripts/swe_bench_fresh.py validate, then draw). WT must be a checkout of origin/main
# that has this change; `start` creates it as a detached worktree when it is missing. Never point
# it at the local `main` branch, which is old history.
set -u
# The main checkout (the one holding .venv and .claude/worktrees), found from this script's own
# location: through git's common directory when the script runs from a worktree of it, else the
# folder above scripts/.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
COMMON=$(git -C "$HERE" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
REPO=${REPO:-$( [ -n "$COMMON" ] && cd "$COMMON/.." && pwd || echo "$HERE")}
WT=${WT:-$REPO/.claude/worktrees/swe-night1}
PY=${PY:-$REPO/.venv/bin/python}
LIST=${LIST:-$HOME/.cache/dreamference/swe-bench/fresh-50.txt}
D=${D:-$HOME/.local/share/dreamference/swe-bench/night1}
NIGHT=${NIGHT:-1}
PREFIX=${PREFIX:-n$NIGHT}
UNIT=${UNIT:-ling-swe-night1}
LIVE_UNIT=${LIVE_UNIT:-puffin-swe-im100-refine}
# The disk a repository's images need at once (about 2.3 GB each unpacked), and the disk the run
# keeps free. RESERVE_GB is written into the run's config as `disk_reserve`, so the pre-flight below
# and the runner's own check (which stops starting instances below it) use the same number.
IMAGE_GB=3
RESERVE_GB=${RESERVE_GB:-100}
# A repository's instances are run, graded and their images removed in chunks of at most IMAGE_CHUNK
# (`--image-chunk`; 0: the whole repository at once), so the disk holds one chunk's images, not the
# largest repository's (fresh-150k's 86 django tasks would need about 260 GB at once; 25 need 75).
IMAGE_CHUNK=${IMAGE_CHUNK:-25}
# SHARD=I/K: this machine runs only the I-th of K balanced shards of LIST (`swe-bench run --shard`),
# the other machines the others; `ling-admin swe-bench merge` puts each arm's K rounds together.
SHARD=${SHARD:-}
REFINE=${REFINE:-0}
# The per-task time limit (`[swe_bench] task_timeout`, e.g. 60m); the runner's default (45m) when unset.
TASK_TIMEOUT=${TASK_TIMEOUT:-}
# The smoke check `start` runs before the unit (mandatory, the owner's decision of 2026-10-14, after
# night 5's candidate arm failed at startup on all 44 tasks and its round still exited 0): one real
# task per arm, with the arm's exact settings and a short time limit, against the model server. A
# known-easy validated task (the harness's own agent-smoke task, resolved in 1-3 min by every arm of
# nights 4-6), kept out of every fresh list by construction. Its image stays on disk for the next
# night's smoke.
SMOKE_INSTANCE=${SMOKE_INSTANCE:-sympy__sympy-13480}
SMOKE_TIMEOUT=${SMOKE_TIMEOUT:-6m}
# The two arms, record first (night 1: default against tests-v2; night 3: night 1's winner against it plus
# issue-v1, the record arm winning a tie; night 4: the record arm against `restart-2`; night 5: the
# record arm against `apply-patch-function`; night 6, the Code Mode A/B: `exec-tool-freeform`, exec
# as 1.6.0 offered it, which SGLang drops, against `default`, which now offers it as a function).
# The spelling is `arm_flags`' below.
ARMS=${ARMS:-"default tests-v2"}
# Night 10 (spec §16): `turn-end-compaction-off` against `turn-end-compaction-on`, both named, since
# in `exec` Codex's turn-end key is off unless an arm pins it (`--turn-end-compaction`): `default`
# would be a second off arm, so `start` refuses a night where only one arm names it.
# Best-of-N (night 11, backlog item 6): BEST_OF=N runs the one arm in ARMS N times on the same list
# (`<PREFIX>-<arm>-1` .. `-N`, each graded), then chooser v1 (scripts/swe_bench_chooser_test.py,
# whose chooser is Night Shift's NightShiftChooser) over the N runs against CHOOSER_SERVER with
# CHOOSER_SEED (v1's measured seed), and reports best-of-N against the first run
# (scripts/swe_bench_best_of.py). 1, the default, is the two-arm night.
BEST_OF=${BEST_OF:-1}
CHOOSER_SERVER=${CHOOSER_SERVER:-http://localhost:8000}
CHOOSER_SEED=${CHOOSER_SEED:-20261013}
# The harness commit the night runs: origin/main unless REF pins one (a night is pinned so that work
# landing on main during the day does not change the harness between two arms or two nights).
REF=${REF:-origin/main}

log() { echo "$(date -Is) $*"; }
# An arm is `default`, a task-rules list (`tests-v2`, `tests-v2,issue-v1`), `restart-N` (the restart
# arm, `--restart-after-compactions N`, SWE_BENCH spec §21), `apply-patch-FORM` (FORM function,
# freeform or off: `--apply-patch FORM`, spec §22), `exec-tool-FORM` (FORM function or freeform:
# `--exec-tool FORM`, spec §23), `drop-outputs` (drop-first compaction: `--drop-old-outputs`,
# compaction spec §13), or several of these joined with `+`
# (`issue-v1+restart-2`). Prints the arm's `swe-bench run` options; its run is named by turning
# every `,` and `+` into `-` (`n4-restart-2`).
arm_flags() {
    local part flags=""
    for part in $(echo "$1" | tr + ' '); do
        case "$part" in
            default) ;;
            turn-end-compaction-on|turn-end-compaction-off|turn-end-compaction-[1-9]|turn-end-compaction-[1-9][0-9]) flags="$flags --turn-end-compaction ${part#turn-end-compaction-}" ;;
            turn-end*|turn_end*) echo "❌ Arm $1: write the turn-end compaction arm as turn-end-compaction-on, -off or -N (N a percentage, 1-99)." >&2; return 2 ;;
            restart-[1-9]|restart-[1-9][0-9]) flags="$flags --restart-after-compactions ${part#restart-}" ;;
            restart*) echo "❌ Arm $1: write the restart arm as restart-N (N from 1)." >&2; return 2 ;;
            apply-patch-function|apply-patch-freeform|apply-patch-off) flags="$flags --apply-patch ${part#apply-patch-}" ;;
            apply-patch*|apply_patch*) echo "❌ Arm $1: write the apply_patch arm as apply-patch-function, -freeform or -off." >&2; return 2 ;;
            exec-tool-function|exec-tool-freeform) flags="$flags --exec-tool ${part#exec-tool-}" ;;
            exec-tool*|exec_tool*) echo "❌ Arm $1: write the Code Mode arm as exec-tool-function or exec-tool-freeform." >&2; return 2 ;;
            drop-outputs) flags="$flags --drop-old-outputs" ;;
            drop-*|drop_*) echo "❌ Arm $1: write the drop-first compaction arm as drop-outputs." >&2; return 2 ;;
            *) flags="$flags --task-rules $part" ;;
        esac
    done
    echo "${flags# }"
}
arm_name() { echo "$1" | tr ,+ --; }
# The options every round and regrade takes: the image chunk, and the round's shard of the list.
chunk_flags() { echo "--image-chunk $IMAGE_CHUNK"; }
shard_flags() { [ -n "$SHARD" ] && echo "--shard $SHARD"; }
admin() { "$PY" -c "import sys; from dreamference.cli.dreamference_cli_controller import main; sys.exit(main(sys.argv[1:]))" "$@"; }

# The smoke check: one real task per arm (SMOKE_INSTANCE), with the night's settings and the arm's
# own options, under SMOKE_TIMEOUT and without nudges, against the model server, from the worktree
# the unit will run. It refuses (exit 1, a ❌ line) when an arm's agent errors, exits non-zero or
# never gets an answer from the model; a wrong or empty patch is fine. Judged from the run's files
# by SweBenchArmSmoke, never from the round's exit status alone (night 5's exited 0). Each smoke is
# a run of its own (`<PREFIX>-smoke-<arm>-<time>`: a reused name would resume a finished run and
# run nothing); its config and output are kept in $SMOKE_DIR.
smoke() {
    local arm name extra run rc stamp
    stamp=$(date +%Y%m%d-%H%M%S)
    SMOKE_DIR=${SMOKE_DIR:-$(dirname "$D")/smoke/$PREFIX-$stamp}
    mkdir -p "$SMOKE_DIR" || return 1
    { cat "$WT/dreamference.toml"
      printf '\n[swe_bench]\nmax_parallel = 1\ndisk_reserve = "%sG"\nnodes = "none"\ntask_timeout = "%s"\nnudges = 0\n' \
          "$RESERVE_GB" "$SMOKE_TIMEOUT"; } > "$SMOKE_DIR/dreamference.toml"
    for arm in $ARMS; do
        name=$(arm_name "$arm")
        extra=$(arm_flags "$arm") || return 2
        [ "$REFINE" = 1 ] && extra="$extra --refine"
        run="$PREFIX-smoke-$name-$stamp"
        echo "🔥 Smoke check of arm $arm: $SMOKE_INSTANCE as run $run (limit $SMOKE_TIMEOUT)..."
        ( cd "$WT" && export PYTHONPATH="$WT" DREAMFERENCE_CONFIG_PATH="$SMOKE_DIR/dreamference.toml" &&
          timeout -s INT -k 2m 25m "$PY" -c "import sys; from dreamference.cli.dreamference_cli_controller import main; sys.exit(main(sys.argv[1:]))" \
              swe-bench run --instances "$SMOKE_INSTANCE" --name "$run" --code-index universal --mask off \
              --prompt default $extra --label "night $NIGHT smoke, $name arm" ) > "$SMOKE_DIR/$name.log" 2>&1
        rc=$?
        ( cd "$WT" && PYTHONPATH="$WT" "$PY" -c "import sys; from dreamference.swe_bench.swe_bench_arm_smoke import SweBenchArmSmoke; sys.exit(SweBenchArmSmoke.check(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]))" \
            "$run" "$SMOKE_INSTANCE" "$rc" "$arm" )
        if [ $? -ne 0 ]; then
            tail -5 "$SMOKE_DIR/$name.log" | sed 's/^/   /'
            echo "❌ Night $NIGHT not started: arm $arm failed its smoke check (log: $SMOKE_DIR/$name.log)."
            return 1
        fi
    done
}

# The chooser's smoke (BEST_OF > 1): one judge call through Night Shift's chooser against
# CHOOSER_SERVER, which must come back with both ratings read. Without it a judge that cannot be
# read would score every disputed task as a tie, and the night would say nothing about the chooser.
judge_smoke() {
    echo "🔥 Smoke check of chooser v1's judge against $CHOOSER_SERVER..."
    ( cd "$WT" && PYTHONPATH="$WT" timeout 20m "$PY" -c '
import sys
from dreamference.night_shift.chooser_candidate import ChooserCandidate
from dreamference.night_shift.night_shift_chooser import NightShiftChooser
log = []
compare = NightShiftChooser.server_judge(sys.argv[1], "`add(2, 3)` returns -1 instead of 5.", seed=int(sys.argv[2]), log=log)
right = ChooserCandidate("a1", "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n", ["a1"])
wrong = ChooserCandidate("a2", "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return abs(a - b)\n", ["a2"])
rated = compare(right, wrong)
print("   ratings", rated, "/", log[-1].get("error") or "answer read")
sys.exit(0 if rated is not None else 1)' "$CHOOSER_SERVER" "$CHOOSER_SEED" )
    if [ $? -ne 0 ]; then
        echo "❌ Night $NIGHT not started: chooser v1's judge got no readable answer from $CHOOSER_SERVER."
        return 1
    fi
}

start() {
    if systemctl --user is-active --quiet "$LIVE_UNIT"; then
        echo "❌ $LIVE_UNIT is still running; start night 1 once it has finished."; exit 1
    fi
    if systemctl --user is-active --quiet "$UNIT"; then
        echo "❌ $UNIT is already running."; exit 1
    fi
    # The worktree is what the unit runs (this script included), so it is moved to origin/main
    # every start: a stale worktree would run last night's script with none of today's fixes.
    git -C "$REPO" fetch origin || exit 1
    if [ ! -d "$WT" ]; then
        git -C "$REPO" worktree add --detach "$WT" "$REF" || exit 1
    elif [ -z "$(git -C "$WT" status --porcelain)" ]; then
        git -C "$WT" checkout --quiet --detach "$REF" || exit 1
    else
        echo "❌ $WT has local changes; commit or discard them so the night runs $REF."; exit 1
    fi
    if ! grep -q -- '"tests-v2"' "$WT/dreamference/swe_bench/swe_bench_instance_run.py"; then
        echo "❌ $WT does not have the tests-v2 task rule: update it to origin/main."; exit 1
    fi
    for arm in $ARMS; do
        arm_flags "$arm" > /dev/null || exit 2
    done
    if [ "$BEST_OF" -gt 1 ]; then
        [ "$(echo $ARMS | wc -w)" -eq 1 ] || { echo "❌ BEST_OF=$BEST_OF runs one arm N times: ARMS names one arm, not \"$ARMS\"."; exit 2; }
        [ -f "$WT/scripts/swe_bench_best_of.py" ] && grep -q 'night_shift_chooser import' "$WT/scripts/swe_bench_chooser_test.py" \
            || { echo "❌ $WT does not run chooser v1 from Night Shift's package: pin REF to a commit that has it."; exit 1; }
    fi
    case "$ARMS" in *turn-end-compaction-*)
        if ! grep -q -- '"--turn-end-compaction"' "$WT/dreamference/swe_bench/swe_bench_command.py"; then
            echo "❌ $WT does not have --turn-end-compaction: pin REF to a commit that has it."; exit 1
        fi
        # Codex's key is off in `exec` unless an arm pins it, so an arm without the part (`default`)
        # is a second off arm, and the night would measure off twice.
        for arm in $ARMS; do
            case "$arm" in *turn-end-compaction-*) ;;
                *) echo "❌ Arm $arm pins no turn-end compaction while another arm does: name it in every arm (turn-end-compaction-off against turn-end-compaction-on)."; exit 1 ;;
            esac
        done ;;
    esac
    case "$ARMS" in *restart-*)
        if ! grep -q -- '"--restart-after-compactions"' "$WT/dreamference/swe_bench/swe_bench_command.py"; then
            echo "❌ $WT does not have --restart-after-compactions: pin REF to a commit that has it."; exit 1
        fi ;;
    esac
    case "$ARMS" in *apply-patch-*)
        if ! grep -q -- '"--apply-patch"' "$WT/dreamference/swe_bench/swe_bench_command.py"; then
            echo "❌ $WT does not have --apply-patch: pin REF to a commit that has it."; exit 1
        fi
        # The arm only sets the launcher's override (DREAMFERENCE_MIGHTLING_APPLY_PATCH); a `ling`
        # built before the launcher read it (1.6.0 and older) ignores it, and the night would
        # measure the record arm twice.
        ling=$("$PY" -c "from dreamference.runner.codex_branded_builder import CodexBrandedBuilder; print(CodexBrandedBuilder.executable_path())") || exit 1
        if ! grep -q -a DREAMFERENCE_MIGHTLING_APPLY_PATCH "$ling"; then
            echo "❌ $ling does not read DREAMFERENCE_MIGHTLING_APPLY_PATCH: rebuild it (\`ling-admin codex build\`) from a commit that has the launcher's override."; exit 1
        fi ;;
    esac
    case "$ARMS" in *exec-tool-*)
        if ! grep -q -- '"--exec-tool"' "$WT/dreamference/swe_bench/swe_bench_command.py"; then
            echo "❌ $WT does not have --exec-tool: pin REF to a commit that has it."; exit 1
        fi
        # The arm only sets the launcher's switch (DREAMFERENCE_MIGHTLING_EXEC_TOOL); a `ling` built
        # before the translation (1.6.0 and older) ignores it and offers exec as a custom tool in
        # both arms, which SGLang drops: the night would measure the record arm twice.
        ling=$("$PY" -c "from dreamference.runner.codex_branded_builder import CodexBrandedBuilder; print(CodexBrandedBuilder.executable_path())") || exit 1
        if ! grep -q -a DREAMFERENCE_MIGHTLING_EXEC_TOOL "$ling"; then
            echo "❌ $ling does not read DREAMFERENCE_MIGHTLING_EXEC_TOOL: rebuild it (\`ling-admin codex build\`) from a commit that has the Code Mode translation."; exit 1
        fi ;;
    esac
    case "$ARMS" in *drop-outputs*)
        if ! grep -q -- '"--drop-old-outputs"' "$WT/dreamference/swe_bench/swe_bench_command.py"; then
            echo "❌ $WT does not have --drop-old-outputs: pin REF to a commit that has it."; exit 1
        fi
        # The arm only sets the launcher's switch (DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS); a `ling`
        # built without patch 0026 ignores it and summarises in both arms: the night would measure
        # the record arm twice.
        ling=$("$PY" -c "from dreamference.runner.codex_branded_builder import CodexBrandedBuilder; print(CodexBrandedBuilder.executable_path())") || exit 1
        if ! grep -q -a DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS "$ling"; then
            echo "❌ $ling does not read DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS: rebuild it (\`ling-admin codex build\`) from a commit that has drop-first compaction."; exit 1
        fi ;;
    esac
    # The universal code index needs codebase-memory-mcp beside ling-code in Mightling's install
    # directory (ling-code never looks on PATH). Without it every index fails and the run refuses
    # to start, which is how the night of 2026-10-09 was lost after the old install folder, which
    # held the indexers, was deleted.
    install=$("$PY" -c "from dreamference.runner.codex_branded_builder import INSTALL_DIR; print(INSTALL_DIR)") || exit 1
    for tool in ling-code codebase-memory-mcp; do
        if [ ! -x "$install/bin/$tool" ]; then
            echo "❌ $install/bin/$tool is missing: run \`ling-admin codex build\` and \`ling-admin code setup\` first."; exit 1
        fi
    done
    if [ ! -s "$LIST" ]; then
        echo "❌ No task list at $LIST: run scripts/swe_bench_fresh.py validate, then draw."; exit 1
    fi
    case "$IMAGE_CHUNK" in ''|*[!0-9]*) echo "❌ IMAGE_CHUNK=$IMAGE_CHUNK: a number of instances, 0 for a whole repository."; exit 2 ;; esac
    if [ -n "$SHARD" ] && { ! [[ "$SHARD" =~ ^[1-9][0-9]*/[1-9][0-9]*$ ]] || [ "${SHARD%/*}" -gt "${SHARD#*/}" ]; }; then
        echo "❌ SHARD=$SHARD: I/K, the I-th of K shards of the list."; exit 2
    fi
    count=$(grep -cv '^\s*\(#\|$\)' "$LIST")
    # One chunk of the largest repository's images is on disk at once while it is run and graded
    # (the whole list's count: a shard holds no more).
    largest=$(grep -v '^\s*\(#\|$\)' "$LIST" | sed 's/-[0-9]*$//' | sort | uniq -c | sort -rn | awk 'NR==1{print $1}')
    at_once=$largest
    [ "$IMAGE_CHUNK" -gt 0 ] && [ "$largest" -gt "$IMAGE_CHUNK" ] && at_once=$IMAGE_CHUNK
    need=$(( at_once * IMAGE_GB + RESERVE_GB ))
    free=$(df --output=avail -BG "$HOME/.cache" | tail -1 | tr -dc '0-9')
    echo "Tasks: $count${SHARD:+ (this machine: shard $SHARD)}; largest repository: $largest, at most $at_once of its images at once; disk needed about ${need}G, free ${free}G."
    if [ "$free" -lt "$need" ]; then
        echo "❌ Not enough disk. Leftover instance images can go with \`ling-admin swe-bench clean --images\`;"
        echo "   or set a smaller reserve, e.g. RESERVE_GB=60 $0 start."; exit 1
    fi
    # Every arm runs one real task first; nothing below happens unless each one ran.
    smoke || exit 1
    if [ "$BEST_OF" -gt 1 ]; then judge_smoke || exit 1; fi
    # A finished or failed night keeps its folder; this one starts clean.
    if [ -e "$D/done" ] || [ -e "$D/failed" ]; then
        aside="$D.$(date +%Y%m%d-%H%M%S)"
        mv "$D" "$aside" && echo "ℹ️  Previous night moved to $aside"
    fi
    mkdir -p "$D"
    { cat "$WT/dreamference.toml"; printf '\n[swe_bench]\nmax_parallel = 2\ndisk_reserve = "%sG"\nnodes = "none"\n' "$RESERVE_GB"
      [ -n "$TASK_TIMEOUT" ] && printf 'task_timeout = "%s"\n' "$TASK_TIMEOUT"; } \
        > "$D/dreamference.toml"
    if [ "$BEST_OF" -gt 1 ]; then
        what="best-of-$BEST_OF (chooser v1) against a single run, $ARMS arm"
    else
        what=$(echo $ARMS | sed 's/ / against /')
    fi
    systemd-run --user --unit="$UNIT" -p OOMPolicy=continue \
        --description="SWE-bench night $NIGHT: $what" \
        --setenv=WT="$WT" --setenv=PY="$PY" --setenv=LIST="$LIST" --setenv=D="$D" --setenv=PREFIX="$PREFIX" --setenv=NIGHT="$NIGHT" --setenv=ARMS="$ARMS" --setenv=REFINE="$REFINE" --setenv=TASK_TIMEOUT="$TASK_TIMEOUT" \
        --setenv=IMAGE_CHUNK="$IMAGE_CHUNK" --setenv=SHARD="$SHARD" \
        --setenv=BEST_OF="$BEST_OF" --setenv=CHOOSER_SERVER="$CHOOSER_SERVER" --setenv=CHOOSER_SEED="$CHOOSER_SEED" \
        /usr/bin/bash -c "'$WT/scripts/swe_bench_night1.sh' run >> '$D/run.log' 2>&1"
    echo "✅ Started $UNIT; log: $D/run.log"
}

# A step that failed ends the night: `failed` is written (never `done`), the cause is logged, and
# the unit exits with the step's status, so `systemctl --user is-failed` and a watcher see it. The
# night of 2026-10-09 ran on after both rounds exited 1, regraded nothing with a green tick and
# wrote `done`.
fail() {
    local step=$1 rc=$2
    log "❌ $step failed ($rc); night 1 stopped. See above and the run's logs under $(dirname "$D")/runs/."
    echo "$step $rc $(date -Is)" > "$D/failed"
    exit "$rc"
}

# BEST_OF > 1: the one arm N times, then chooser v1 over the N runs and the report against run 1.
best_of_run() {
    local arm name extra k round names=""
    set -- $ARMS
    [ $# -eq 1 ] || fail "BEST_OF=$BEST_OF with arms [$ARMS] (one arm)" 2
    arm=$1
    arm_flags "$arm" > /dev/null || fail "arm $arm" 2
    name=$(arm_name "$arm")
    extra=$(arm_flags "$arm")
    [ "$REFINE" = 1 ] && extra="$extra --refine"
    for k in $(seq 1 "$BEST_OF"); do
        round="$PREFIX-$name-$k"
        log "round $round (attempt $k of $BEST_OF)"
        admin swe-bench run --subset "$LIST" --name "$round" --code-index universal --mask off \
            --prompt default $extra --eval --remove-images $(chunk_flags) $(shard_flags) \
            --label "night $NIGHT, $name arm, attempt $k of $BEST_OF"
        rc=$?
        log "$round finished ($rc)"
        df -h / | tail -1
        [ "$rc" -eq 0 ] || fail "round $round" "$rc"
        if admin swe-bench report "$round" | grep -q '^INCOMPLETE .*[1-9][0-9]* not run yet'; then
            admin swe-bench report "$round" | grep '^INCOMPLETE'
            fail "round $round (instances not run)" 1
        fi
        admin swe-bench report "$round" > "$D/report-$name-$k.txt"
        [ "$k" -gt 1 ] && admin swe-bench report "$round" --against "$PREFIX-$name-1" > "$D/$name-$k-against-$name-1.txt"
        names="$names,$round"
    done
    names=${names#,}
    # The rounds' gate holds have ended with them, so the judge reaches the server like anyone.
    log "chooser v1 over $names (server $CHOOSER_SERVER, seed $CHOOSER_SEED)"
    "$PY" "$WT/scripts/swe_bench_chooser_test.py" --server "$CHOOSER_SERVER" --runs "$names" \
        --seed "$CHOOSER_SEED" --out "$D/chooser-v1" > "$D/chooser-v1.txt" 2>&1
    rc=$?
    cat "$D/chooser-v1.txt"
    log "chooser v1 finished ($rc)"
    [ "$rc" -eq 0 ] || fail "chooser v1" "$rc"
    "$PY" "$WT/scripts/swe_bench_best_of.py" --runs "$names" --chooser "$D/chooser-v1/summary.json" \
        --json "$D/best-of-$BEST_OF.json" > "$D/best-of-$BEST_OF-against-single.txt"
    rc=$?
    [ "$rc" -eq 0 ] || fail "best-of report" "$rc"
    cat "$D/best-of-$BEST_OF-against-single.txt"
    touch "$D/done"
    log "all done; reports in $D"
}

run() {
    export PYTHONPATH="$WT"
    export DREAMFERENCE_CONFIG_PATH="$D/dreamference.toml"
    cd "$WT" || exit 1
    rm -f "$D/failed"
    log "night $NIGHT from $(git -C "$WT" rev-parse --short HEAD), list $LIST ($(sha256sum "$LIST" | cut -c1-12)), arms [$ARMS], best of $BEST_OF, refine $REFINE, task limit ${TASK_TIMEOUT:-default}, image chunk $IMAGE_CHUNK${SHARD:+, shard $SHARD}"
    if [ "$BEST_OF" -gt 1 ]; then
        best_of_run
        return
    fi
    # ARMS names the two arms: the record arm first, then the candidate (spelling: `arm_flags`).
    set -- $ARMS
    record=$1; candidate=$2
    rname=$(arm_name "$record"); cname=$(arm_name "$candidate")
    # A misspelt arm ends the night before either round, not after the record arm's.
    for arm in $record $candidate; do
        arm_flags "$arm" > /dev/null || fail "arm $arm" 2
    done
    for arm in $record $candidate; do
        name=$(arm_name "$arm")
        extra=$(arm_flags "$arm")
        [ "$REFINE" = 1 ] && extra="$extra --refine"
        log "round $PREFIX-$name"
        # The label is what anyone the model gate turns away reads (SWE_BENCH spec §18).
        admin swe-bench run --subset "$LIST" --name "$PREFIX-$name" --code-index universal --mask off \
            --prompt default $extra --eval --remove-images $(chunk_flags) $(shard_flags) --label "night $NIGHT, $name arm"
        rc=$?
        log "$PREFIX-$name finished ($rc)"
        df -h / | tail -1
        [ "$rc" -eq 0 ] || fail "round $PREFIX-$name" "$rc"
        # A round that ran nothing is a failure whatever its status: the report says what was left.
        if admin swe-bench report "$PREFIX-$name" | grep -q '^INCOMPLETE .*[1-9][0-9]* not run yet'; then
            admin swe-bench report "$PREFIX-$name" | grep '^INCOMPLETE'
            fail "round $PREFIX-$name (instances not run)" 1
        fi
    done
    for name in $rname $cname; do
        log "regrade $PREFIX-$name with the test files dropped"
        admin swe-bench eval "$PREFIX-$name" --drop-test-hunks --remove-images $(chunk_flags)
        rc=$?
        log "regrade of $PREFIX-$name finished ($rc)"
        [ "$rc" -eq 0 ] || fail "regrade $PREFIX-$name" "$rc"
    done
    for name in $rname $cname; do
        admin swe-bench report "$PREFIX-$name" > "$D/report-$name.txt"
        admin swe-bench report "$PREFIX-$name" --drop-test-hunks --against "$PREFIX-$name" > "$D/drop-test-hunks-$name.txt"
    done
    admin swe-bench report "$PREFIX-$cname" --against "$PREFIX-$rname" > "$D/$cname-against-$rname.txt"
    admin swe-bench report "$PREFIX-$cname" --drop-test-hunks --against "$PREFIX-$rname" \
        > "$D/$cname-dropped-against-$rname.txt"
    touch "$D/done"
    log "all done; reports in $D"
}

case "${1:-}" in
    start) start ;;
    run) run ;;
    *) echo "usage: $0 {start|run}"; exit 2 ;;
esac
