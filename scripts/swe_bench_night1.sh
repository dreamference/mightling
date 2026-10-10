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
#   scripts/swe_bench_night1.sh start   checks, then runs `run` as the user unit ling-swe-night1
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
REFINE=${REFINE:-0}
# The two arms, record first (night 1: default against tests-v2; night 3: night 1's winner against it plus
# issue-v1, the record arm winning a tie).
ARMS=${ARMS:-"default tests-v2"}

log() { echo "$(date -Is) $*"; }
admin() { "$PY" -c "import sys; from dreamference.cli.dreamference_cli_controller import main; sys.exit(main(sys.argv[1:]))" "$@"; }

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
        git -C "$REPO" worktree add --detach "$WT" origin/main || exit 1
    elif [ -z "$(git -C "$WT" status --porcelain)" ]; then
        git -C "$WT" checkout --quiet --detach origin/main || exit 1
    else
        echo "❌ $WT has local changes; commit or discard them so the night runs origin/main."; exit 1
    fi
    if ! grep -q -- '"tests-v2"' "$WT/dreamference/swe_bench/swe_bench_instance_run.py"; then
        echo "❌ $WT does not have the tests-v2 task rule: update it to origin/main."; exit 1
    fi
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
    # A finished or failed night keeps its folder; this one starts clean.
    if [ -e "$D/done" ] || [ -e "$D/failed" ]; then
        aside="$D.$(date +%Y%m%d-%H%M%S)"
        mv "$D" "$aside" && echo "ℹ️  Previous night moved to $aside"
    fi
    mkdir -p "$D"
    { cat "$WT/dreamference.toml"; printf '\n[swe_bench]\nmax_parallel = 2\ndisk_reserve = "%sG"\nnodes = "none"\n' "$RESERVE_GB"; } \
        > "$D/dreamference.toml"
    systemd-run --user --unit="$UNIT" -p OOMPolicy=continue \
        --description="SWE-bench night $NIGHT: default against test discipline (tests-v2) on fresh tasks" \
        --setenv=WT="$WT" --setenv=PY="$PY" --setenv=LIST="$LIST" --setenv=D="$D" --setenv=PREFIX="$PREFIX" --setenv=NIGHT="$NIGHT" --setenv=ARMS="$ARMS" --setenv=REFINE="$REFINE" \
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

run() {
    export PYTHONPATH="$WT"
    export DREAMFERENCE_CONFIG_PATH="$D/dreamference.toml"
    cd "$WT" || exit 1
    rm -f "$D/failed"
    log "night $NIGHT from $(git -C "$WT" rev-parse --short HEAD), list $LIST ($(sha256sum "$LIST" | cut -c1-12)), arms [$ARMS], refine $REFINE"
    # ARMS names the two arms: the record arm first, then the candidate. An arm is `default` or a
    # task-rules list (`tests-v2`, `tests-v2,issue-v1`); its run is named by joining the rules with `-`.
    set -- $ARMS
    record=$1; candidate=$2
    rname=$(echo "$record" | tr , -); cname=$(echo "$candidate" | tr , -)
    for arm in $record $candidate; do
        name=$(echo "$arm" | tr , -)
        extra=""
        [ "$arm" != default ] && extra="--task-rules $arm"
        [ "$REFINE" = 1 ] && extra="$extra --refine"
        log "round $PREFIX-$name"
        # The label is what anyone the model gate turns away reads (SWE_BENCH spec §18).
        admin swe-bench run --subset "$LIST" --name "$PREFIX-$name" --code-index universal --mask off \
            --prompt default $extra --eval --remove-images --label "night $NIGHT, $name arm"
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
        admin swe-bench eval "$PREFIX-$name" --drop-test-hunks --remove-images
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
