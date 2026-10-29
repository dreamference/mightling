"""`scripts/night_local_arm.sh`: the engine A/B's arms on this machine's own model server.

Nothing reaches the real machine: `docker` and `python3` are stand-ins on PATH that keep the
containers' state in files, `PY` is a stand-in for the venv's Python that answers the registry's
images and plays `ling-admin server stop|start`, and `RESTORE_ADMIN` is the same stand-in as the
installed `ling-admin`. No container is started and no port is opened.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "night_local_arm.sh"
LING_IMAGE = "ghcr.io/example/ling-serve@sha256:aaaa"
SGLANG_IMAGE = "example/sglang@sha256:bbbb"

# The containers' state: one file per container (`yes` running, `no` stopped, absent: none), the
# server's image in `image`. `busy` holds how many more `server start`s a run's runner lock refuses;
# `broken` makes `server start` bring up the wrong image; `bypass` makes it start the container
# without `docker run` (so no flag the arm asked for reaches it). `run` holds the last `docker run`'s
# arguments, one per line.
ADMIN = """
S={state}
echo "$WHO $*" >> "$S/calls"
case "$*" in
"server stop") echo no > "$S/server"; echo no > "$S/gate"; : > "$S/image" ;;
"server start"*)
    n=$(cat "$S/busy" 2>/dev/null || echo 0)
    if [ "$n" -gt 0 ]; then
        echo $((n - 1)) > "$S/busy"
        echo "A SWE-bench run is in progress, so \\`ling-admin server start\\` waits"
        exit 1
    fi
    case "$*" in *"--engine ling"*) image={ling} ;; *) image={sglang} ;; esac
    [ -e "$S/broken" ] && image=other
    docker run -d --name dreamference-gate-8000 "$image" gate --upstream dreamference-vllm-8000
    if [ -e "$S/bypass" ]; then : > "$S/run"; else docker run -d --name dreamference-vllm-8000 "$image" serve; fi
    echo "$image" > "$S/image"; echo yes > "$S/server"; echo yes > "$S/gate" ;;
esac
"""

DOCKER = """#!/bin/bash
S={state}
file() {{
    case "$1" in
    dreamference-vllm-8000) echo server ;;
    dreamference-gate-8000) echo gate ;;
    *) echo "prod-$1" ;;
    esac
}}
case "$1" in
inspect)
    f=$(file "$2")
    [ -s "$S/$f" ] || exit 1
    case "$4" in
    *Image*) cat "$S/image" ;;
    *Args*) sed -n '/^serve$/,$p' "$S/run" | tail -n +2 | tr '\\n' ' ' ;;
    *Env*) grep -A1 -x -- -e "$S/run" | grep -vx -- -e | grep -vx -- -- ;;
    *) [ "$(cat "$S/$f")" = yes ] && echo true || echo false ;;
    esac ;;
run)
    shift
    case " $* " in
    *" dreamference-gate-8000 "*) printf '%s\\n' "$@" > "$S/gate-run" ;;
    *) printf '%s\\n' "$@" > "$S/run" ;;
    esac ;;
stop) echo no > "$S/prod-$2"; echo "docker stop $2" >> "$S/calls" ;;
start) echo yes > "$S/prod-$2"; echo "docker start $2" >> "$S/calls" ;;
*) exit 1 ;;
esac
"""

# Something answers /v1/models when the server or a production container runs.
PYTHON3 = """#!/bin/bash
S={state}
grep -qx yes "$S/server" 2>/dev/null && exit 0
for f in "$S"/prod-*; do grep -qx yes "$f" 2>/dev/null && exit 0; done
exit 1
"""

PY = """#!/bin/bash
case "$2" in
*QWEN38_LING_RECIPE*) [ "$3" = engine ] && echo {ling} || echo {sglang}; exit 0 ;;
esac
shift 2
WHO=worktree
{admin}
"""

INSTALLED = """#!/bin/bash
WHO=installed
{admin}
"""


def executable(path: Path, body: str) -> Path:
    """Writes an executable script."""
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def machine(tmp_path):
    """A machine of stand-ins; returns a function that runs the helper and its state folder."""
    state = tmp_path / "state"
    state.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    admin = ADMIN.format(state=state, ling=LING_IMAGE, sglang=SGLANG_IMAGE)
    executable(bindir / "docker", DOCKER.format(state=state))
    executable(bindir / "python3", PYTHON3.format(state=state))
    py = executable(
        tmp_path / "python", PY.format(ling=LING_IMAGE, sglang=SGLANG_IMAGE, admin=admin)
    )
    installed = executable(tmp_path / "ling-admin", INSTALLED.format(admin=admin))

    def run(*args, **extra):
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "WT": str(ROOT),
            "PY": str(py),
            "STATE_DIR": str(tmp_path / "local-arm"),
            "RESTORE_ADMIN": str(installed),
            "READY_S": "1",
            "POLL_S": "1",
            "RUNNER_POLL_S": "0",
            **extra,
        }
        return subprocess.run(
            ["bash", str(HELPER), *args], env=env, capture_output=True, text=True, timeout=60
        )

    return run, state


def production_sglang(state: Path) -> None:
    """The machine as first- and third-puffin are by day: Mightling's SGLang behind the gate."""
    (state / "image").write_text(SGLANG_IMAGE + "\n")
    (state / "server").write_text("yes\n")
    (state / "gate").write_text("yes\n")


def calls(state: Path) -> list[str]:
    """What the stand-ins were asked, in order."""
    path = state / "calls"
    return path.read_text().splitlines() if path.exists() else []


def test_the_engine_arm_restarts_the_server_with_ling_serve_from_the_worktree(machine):
    run, state = machine
    production_sglang(state)
    result = run("serve", "engine")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"ready engine {LING_IMAGE}"
    assert calls(state) == ["worktree server stop", "worktree server start --engine ling"]


def test_an_arm_already_served_is_not_restarted(machine):
    run, state = machine
    production_sglang(state)
    result = run("serve", "sglang")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"ready sglang {SGLANG_IMAGE}"
    assert calls(state) == []


def test_serve_waits_while_a_run_holds_the_runner_lock(machine):
    run, state = machine
    production_sglang(state)
    (state / "busy").write_text("2\n")
    result = run("serve", "engine")
    assert result.returncode == 0, result.stderr
    assert calls(state).count("worktree server start --engine ling") == 3


def test_an_arm_that_never_serves_its_pinned_image_fails_after_a_second_try(machine):
    run, state = machine
    production_sglang(state)
    (state / "broken").touch()
    result = run("serve", "engine")
    assert result.returncode == 1 and "ready" not in result.stdout
    assert calls(state).count("worktree server start --engine ling") == 2


def test_restore_brings_back_sglang_by_the_installed_ling_admin_and_notes_the_night(
    machine, tmp_path
):
    run, state = machine
    production_sglang(state)
    assert run("serve", "engine").returncode == 0
    before = (tmp_path / "local-arm" / "before").read_text()
    assert f"image {SGLANG_IMAGE}" in before and "gate yes" in before
    result = run("restore")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "restored"
    assert calls(state)[-2:] == ["installed server stop", "installed server start --engine sglang"]
    assert (state / "image").read_text().strip() == SGLANG_IMAGE
    # The record goes once production is back: the next night records its own.
    assert not (tmp_path / "local-arm" / "before").exists()


def test_restore_keep_leaves_the_arm_up(machine):
    run, state = machine
    production_sglang(state)
    assert run("serve", "engine").returncode == 0
    result = run("restore", "keep")
    assert result.returncode == 0 and result.stdout.strip() == "kept"
    assert (state / "image").read_text().strip() == LING_IMAGE
    assert calls(state)[-1] == "worktree server start --engine ling"


def test_restore_when_production_already_serves_touches_nothing(machine):
    run, state = machine
    production_sglang(state)
    result = run("restore")
    assert result.returncode == 0 and result.stdout.strip() == "restored"
    assert calls(state) == []


def test_a_machine_whose_production_is_another_container_gets_that_back_and_no_sglang(machine):
    run, state = machine
    (state / "prod-m0-prod8000").write_text("yes\n")
    settings = {"PROD_STOP": "m0-prod8000", "RESTORE_ENGINE": "none"}
    result = run("serve", "sglang", **settings)
    assert result.returncode == 0, result.stderr
    assert calls(state)[0] == "docker stop m0-prod8000"
    assert run("serve", "engine", **settings).returncode == 0
    result = run("restore", **settings)
    assert result.returncode == 0, result.stderr
    asked = calls(state)
    assert asked[-2:] == ["worktree server stop", "docker start m0-prod8000"]
    assert not any("installed" in line for line in asked)
    assert (state / "prod-m0-prod8000").read_text().strip() == "yes"


def test_production_containers_and_an_sglang_restore_are_refused_together(machine):
    run, state = machine
    production_sglang(state)
    result = run("serve", "engine", PROD_STOP="m0-prod8000")
    assert result.returncode == 2
    assert calls(state) == []


def test_an_unknown_arm_is_refused(machine):
    run, state = machine
    assert run("serve", "vllm").returncode == 2
    assert calls(state) == []


def test_an_arms_engine_flags_and_environment_reach_its_container_and_nothing_else(machine):
    run, state = machine
    production_sglang(state)
    arm = {"ENGINE_ARGS": "--fair-pairing 1", "ENGINE_ENV": "LING_STEP_TRACE=/root/t/n22-x.jsonl"}
    result = run("serve", "engine", **arm)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"ready engine {LING_IMAGE} --fair-pairing 1"
    given = (state / "run").read_text().splitlines()
    assert given[:3] == ["-e", "LING_STEP_TRACE=/root/t/n22-x.jsonl", "-d"]
    assert given[-3:] == ["serve", "--fair-pairing", "1"]
    # The gate's run names the server too, but not as its `--name`: nothing is added to it.
    gate = (state / "gate-run").read_text().splitlines()
    assert gate == [
        "-d",
        "--name",
        "dreamference-gate-8000",
        LING_IMAGE,
        "gate",
        "--upstream",
        "dreamference-vllm-8000",
    ]
    # Served again with the same flags: left up; with others: restarted with them.
    assert run("serve", "engine", **arm).returncode == 0
    assert calls(state).count("worktree server start --engine ling") == 1
    result = run("serve", "engine", ENGINE_ARGS="--fair-pairing 0")
    assert result.returncode == 0, result.stderr
    assert calls(state).count("worktree server start --engine ling") == 2
    given = (state / "run").read_text().splitlines()
    assert given[-2:] == ["--fair-pairing", "0"] and "-e" not in given
    # And without any: restarted plain, the flags gone.
    assert run("serve", "engine").returncode == 0
    assert calls(state).count("worktree server start --engine ling") == 3
    assert (state / "run").read_text().splitlines()[-1] == "serve"


def test_an_arm_whose_flags_never_reach_the_container_is_not_ready(machine):
    run, state = machine
    production_sglang(state)
    (state / "bypass").touch()
    result = run("serve", "engine", ENGINE_ARGS="--fair-pairing 1")
    assert result.returncode == 1 and "ready" not in result.stdout
    assert calls(state).count("worktree server start --engine ling") == 2
