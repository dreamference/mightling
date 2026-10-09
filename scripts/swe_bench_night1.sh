#!/bin/bash
# Night 1 of the failure analysis's A/B (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md §6.5):
# the default arm against the test-discipline arm (`--task-rules tests`) on 50 fresh validated
# tasks, two at a time, each graded as it finishes (`--eval`), then both regraded with the test
# files dropped from every patch (`eval --drop-test-hunks`, no agent time), and the reports.
#
# Both arms carry the harness fixes of the same change: the per-file test reset at grading, the
# wider "test patch failed" count and the completion nudge. Settings are those of `im100-default`
# (code index universal, masking off, prompt default, no refine), with `max_parallel = 2`.
#
#   scripts/swe_bench_night1.sh start   checks, then runs `run` as the user unit ling-swe-night1
#   scripts/swe_bench_night1.sh run     the night itself (what the unit runs)
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
PREFIX=${PREFIX:-n1}
UNIT=${UNIT:-ling-swe-night1}
LIVE_UNIT=${LIVE_UNIT:-puffin-swe-im100-refine}
# The disk a repository's images need at once (about 2.3 GB each unpacked), and the disk the run
# keeps free. RESERVE_GB is written into the run's config as `disk_reserve`, so the pre-flight below
# and the runner's own check (which stops starting instances below it) use the same number.
IMAGE_GB=3
RESERVE_GB=${RESERVE_GB:-100}

log() { echo "$(date -Is) $*"; }
admin() { "$PY" -c "import sys; from dreamference.cli.dreamference_cli_controller import main; sys.exit(main(sys.argv[1:]))" "$@"; }

start() {
    if systemctl --user is-active --quiet "$LIVE_UNIT"; then
        echo "❌ $LIVE_UNIT is still running; start night 1 once it has finished."; exit 1
    fi
    if systemctl --user is-active --quiet "$UNIT"; then
        echo "❌ $UNIT is already running."; exit 1
    fi
    if [ ! -d "$WT" ]; then
        git -C "$REPO" fetch origin || exit 1
        git -C "$REPO" worktree add --detach "$WT" origin/main || exit 1
    fi
    if ! grep -q -- "--task-rules" "$WT/dreamference/swe_bench/swe_bench_command.py"; then
        echo "❌ $WT does not have the task-rules arm: update it to origin/main."; exit 1
    fi
    if [ ! -s "$LIST" ]; then
        echo "❌ No task list at $LIST: run scripts/swe_bench_fresh.py validate, then draw."; exit 1
    fi
    count=$(grep -cv '^\s*\(#\|$\)' "$LIST")
    # The largest repository's images are on disk at once while that repository is graded.
    largest=$(grep -v '^\s*\(#\|$\)' "$LIST" | sed 's/-[0-9]*$//' | sort | uniq -c | sort -rn | awk 'NR==1{print $1}')
    need=$(( largest * IMAGE_GB + RESERVE_GB ))
    free=$(df --output=avail -BG "$HOME/.cache" | tail -1 | tr -dc '0-9')
    echo "Tasks: $count; largest repository: $largest; disk needed about ${need}G, free ${free}G."
    if [ "$free" -lt "$need" ]; then
        echo "❌ Not enough disk. Leftover instance images can go with \`ling-admin swe-bench clean --images\`;"
        echo "   or set a smaller reserve, e.g. RESERVE_GB=60 $0 start."; exit 1
    fi
    mkdir -p "$D"
    { cat "$WT/dreamference.toml"; printf '\n[swe_bench]\nmax_parallel = 2\ndisk_reserve = "%sG"\n' "$RESERVE_GB"; } \
        > "$D/dreamference.toml"
    systemd-run --user --unit="$UNIT" -p OOMPolicy=continue \
        --description="SWE-bench night 1: default against test discipline on 50 fresh tasks" \
        --setenv=WT="$WT" --setenv=PY="$PY" --setenv=LIST="$LIST" --setenv=D="$D" --setenv=PREFIX="$PREFIX" \
        /usr/bin/bash -c "'$WT/scripts/swe_bench_night1.sh' run >> '$D/run.log' 2>&1"
    echo "✅ Started $UNIT; log: $D/run.log"
}

run() {
    export PYTHONPATH="$WT"
    export DREAMFERENCE_CONFIG_PATH="$D/dreamference.toml"
    cd "$WT" || exit 1
    log "night 1 from $(git -C "$WT" rev-parse --short HEAD), list $LIST ($(sha256sum "$LIST" | cut -c1-12))"
    for arm in default tests; do
        extra=""
        [ "$arm" = tests ] && extra="--task-rules tests"
        log "round $PREFIX-$arm"
        admin swe-bench run --subset "$LIST" --name "$PREFIX-$arm" --code-index universal --mask off \
            --prompt default $extra --eval --remove-images
        log "$PREFIX-$arm finished ($?)"
        df -h / | tail -1
    done
    for arm in default tests; do
        log "regrade $PREFIX-$arm with the test files dropped"
        admin swe-bench eval "$PREFIX-$arm" --drop-test-hunks --remove-images
        log "regrade of $PREFIX-$arm finished ($?)"
    done
    for arm in default tests; do
        admin swe-bench report "$PREFIX-$arm" > "$D/report-$arm.txt"
        admin swe-bench report "$PREFIX-$arm" --drop-test-hunks --against "$PREFIX-$arm" > "$D/drop-test-hunks-$arm.txt"
    done
    admin swe-bench report "$PREFIX-tests" --against "$PREFIX-default" > "$D/tests-against-default.txt"
    admin swe-bench report "$PREFIX-tests" --drop-test-hunks --against "$PREFIX-default" \
        > "$D/tests-dropped-against-default.txt"
    touch "$D/done"
    log "all done; reports in $D"
}

case "${1:-}" in
    start) start ;;
    run) run ;;
    *) echo "usage: $0 {start|run}"; exit 2 ;;
esac
