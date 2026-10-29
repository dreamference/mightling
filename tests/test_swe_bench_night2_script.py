"""scripts/swe_bench_night2.sh as a dry run: the night's steps in order, and its failed starts."""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "swe_bench_night2.sh"
PROD = "qwen3.8-27b-nvfp4-dflash2"
MINIMA = "qwen3.8-27b-minima-nvfp4-dflash2"


def dry_run(tmp_path: Path, fail: str = "") -> tuple[str, Path]:
    """`DRY_RUN=1 … run`, its logs in tmp_path; returns what it printed and its log directory."""
    out = tmp_path / "night2"
    out.mkdir()
    (tmp_path / "list.txt").write_text("astropy__astropy-12907\n")
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "DRY_RUN": "1",
        "DRY_FAIL": fail,
        "D": str(out),
        "WT": str(REPO),
        "PY": sys.executable,
        "LIST": str(tmp_path / "list.txt"),
    }
    done = subprocess.run(
        ["bash", str(SCRIPT), "run"], env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout, out


def in_order(text: str, *marks: str) -> bool:
    at = [text.find(mark) for mark in marks]
    return -1 not in at and at == sorted(at)


def test_a_dry_run_goes_through_the_spec_s_steps_in_order(tmp_path):
    # Models spec §2.2 "run": production and its speed, n2-prod, the swap to Minima, its speed and
    # n2-minima, production again and its speed, then the reports.
    printed, out = dry_run(tmp_path)
    assert in_order(
        printed,
        "already serving RadixArk/Qwen3.8-27B-NVFP4",
        f"--out {out}/speed-prod.json",
        "--name n2-prod ",
        f"server start --model {MINIMA} --port 8000",
        f"--out {out}/speed-minima.json",
        "--name n2-minima ",
        f"server start --model {PROD} --port 8000",
        f"--out {out}/speed-prod-after.json",
        "all done",
    )
    # Each arm is graded as it goes and carries the label the model gate shows.
    assert "--eval --remove-images --label night 2, n2-prod" in printed
    # A swap is the normal path: server stop, then server start.
    swap_log = out / f"server-start-1-{MINIMA}.log"
    assert swap_log.read_text() == "    [dry] ling-admin server stop --port 8000\n"
    assert (
        out / "report-prod.txt"
    ).read_text() == "    [dry] ling-admin swe-bench report n2-prod\n"
    assert (out / "minima-against-prod.txt").read_text() == (
        "    [dry] ling-admin swe-bench report n2-minima --against n2-prod\n"
    )
    assert (out / "done").exists()
    assert not (out / "minima-did-not-serve").exists() and not (out / "RESTORE-FAILED").exists()


def test_when_minima_does_not_start_arm_b_is_skipped_and_production_served_again(tmp_path):
    # §2.2: "A start that fails or fails the canary skips arm B and serves production again."
    printed, out = dry_run(tmp_path, fail=MINIMA)
    assert in_order(
        printed,
        f"server start --model {MINIMA}",
        "(fails)",
        "arm B not run",
        f"server start --model {PROD}",
        "speed-prod-after.json",
    )
    assert "--name n2-minima" not in printed
    assert (out / "minima-did-not-serve").exists()
    assert (out / "report-prod.txt").exists()
    assert not (out / "report-minima.txt").exists()
    assert not (out / "minima-against-prod.txt").exists()
    assert (out / "done").exists() and not (out / "RESTORE-FAILED").exists()


def test_when_production_does_not_come_back_it_tries_twice_and_leaves_restore_failed(tmp_path):
    # §2.2: two attempts, then RESTORE-FAILED; the reports need no server and are still written.
    printed, out = dry_run(tmp_path, fail=PROD)
    assert in_order(
        printed,
        "restoring production failed (attempt 1)",
        "restoring production failed (attempt 2)",
        "PRODUCTION IS NOT SERVED",
    )
    assert printed.count(f"server start --model {PROD}") == 2
    assert "speed-prod-after.json" not in printed
    assert (out / "RESTORE-FAILED").exists()
    assert (out / "minima-against-prod.txt").exists() and (out / "done").exists()
