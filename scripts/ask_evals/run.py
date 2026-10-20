"""`ask-evals`: runs the phone-chat scenarios against the real `ling` and scores them
(specs/DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md §5, §9).

    python run.py --reps 1,2,3 [--only s6-,s7-] [--out results/new.jsonl] [--model-port 8000]
    python run.py --plan                 the scenarios and what each run would do; runs nothing
    python run.py --rescore RUNS_DIR     scores runs already on disk again, with no model

Every run happens in a jail (`bwrap --unshare-net`): its only network is its own loopback, where
the stand-in SearXNG and proxy listen, and the model server comes in through a Unix socket joined
to `127.0.0.1:<model-port>` outside. `--model-port 18000` reaches the engine behind the model gate
(the night slot, which holds the gate). Each scenario run gets a fresh `CODEX_HOME` and scratch
folder under `--runs`, never under `/tmp` (Codex refuses its helper aliases there).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Final, Optional

HERE: Final[Path] = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from askevals import checks, images, rollout  # noqa: E402
from askevals.standins import read_log  # noqa: E402

SCENARIOS: Final[Path] = HERE / "scenarios"
DEFAULT_RUNS: Final[Path] = Path.home() / ".cache" / "dreamference" / "ask-evals" / "runs"


def load(only: str | None) -> list[dict]:
    """The scenarios, by id, filtered by comma-separated id prefixes."""
    found = [json.loads(p.read_text()) for p in sorted(SCENARIOS.glob("*.json"))]
    if only:
        prefixes = [p.strip() for p in only.split(",") if p.strip()]
        found = [s for s in found if any(s["id"].startswith(p) for p in prefixes)]
    return found


def prepare(scenario: dict, root: Path, seed: int) -> Path:
    """Draws the scenario's images into `root` and writes the scenario the session reads."""
    root.mkdir(parents=True, exist_ok=True)
    prepared = json.loads(json.dumps(scenario))
    for t, turn in enumerate(prepared["turns"]):
        names = []
        for i, image in enumerate(turn.get("images", [])):
            name = f"image-{t}-{i}.png"
            images.draw(image["kind"], root / name, image.get("spec"), seed=seed + i)
            names.append(name)
        if names:
            turn["images"] = names
    path = root / "scenario.json"
    path.write_text(json.dumps(prepared, ensure_ascii=False, indent=2))
    return path


def model_info(port: int) -> dict:
    """The model server's `/model_info` (a read-only probe the gate lets through), or {}."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/model_info", timeout=10) as answer:
            return json.loads(answer.read())
    except (OSError, ValueError):
        return {}


def session_command(
    scenario_path: Path, run_dir: Path, args: argparse.Namespace, socket_path: str | None
) -> list[str]:
    """The command that runs one scenario's session, jailed unless `--no-jail`."""
    session = [
        sys.executable if args.no_jail else "python3",
        "-I",
        str(HERE / "askevals" / "session.py"),
        str(scenario_path),
        str(run_dir / "out"),
        "--home",
        str(run_dir / "home"),
        "--workspace",
        str(run_dir / "workspace"),
        "--ling",
        args.ling,
    ]
    if socket_path:
        session += ["--model-socket", socket_path]
    if args.cave:
        session += ["--cave", args.cave]
    if args.no_jail:
        return session
    return ["bwrap", "--unshare-net", "--die-with-parent", "--dev-bind", "/", "/", "--", *session]


def score_run(scenario: dict, run_dir: Path) -> dict:
    """One run's record, from what its session left on disk."""
    out = run_dir / "out"
    records = (
        [json.loads(line) for line in (out / "turns.jsonl").read_text().splitlines()]
        if (out / "turns.jsonl").exists()
        else []
    )
    turns = rollout.read_home(run_dir / "home")
    result = checks.score(scenario, turns, records, read_log(out / "web.jsonl"))
    (out / "answers.txt").write_text("\n\n=====\n\n".join(t.answer for t in turns))
    return {
        "scenario": scenario["id"],
        "family": scenario["family"],
        "pass": result.passed,
        "failures": result.failures,
        **result.columns,
    }


def start_forwarder(port: int) -> tuple[subprocess.Popen, str]:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or str(Path.home() / ".cache")
    path = os.path.join(runtime, f"ask-evals-{os.getpid()}.sock")
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(HERE / "askevals" / "forward.py"),
            "unix",
            path,
            "127.0.0.1",
            str(port),
        ]
    )
    for _ in range(50):
        if Path(path).exists():
            break
        time.sleep(0.1)
    return process, path


def summary(records: list[dict]) -> list[str]:
    """One line per scenario: its verdict over the repetitions and its first failure."""
    by_id: dict[str, list[dict]] = {}
    for record in records:
        by_id.setdefault(record["scenario"], []).append(record)
    lines = []
    for sid, runs in sorted(by_id.items()):
        verdict = checks.decide([r["pass"] for r in runs if not r.get("skipped")])
        mark = {True: "pass", False: "FAIL", None: "skip"}[verdict]
        failure = next((r["failures"][0] for r in runs if r["failures"]), "")
        calls = [r.get("tool_calls", 0) for r in runs]
        lines.append(
            f"{mark:4}  {sid:32} {sum(r['pass'] for r in runs)}/{len(runs)}  "
            f"calls {calls}  {failure}"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", default="1")
    parser.add_argument("--only", help="comma-separated scenario id prefixes")
    parser.add_argument("--out", default=str(HERE / "results" / "new.jsonl"))
    parser.add_argument("--runs", default=str(DEFAULT_RUNS / time.strftime("%Y%m%d-%H%M%S")))
    parser.add_argument("--model-port", type=int, default=8000)
    parser.add_argument("--ling", default="ling")
    parser.add_argument("--cave", help="cave level for scenarios that do not set one")
    parser.add_argument("--plan", action="store_true", help="print the plan; run nothing")
    parser.add_argument("--rescore", help="score the runs under this directory again")
    parser.add_argument("--no-jail", action="store_true", help="tests only: run sessions unjailed")
    parser.add_argument(
        "--no-model", action="store_true", help="tests only: no forwarder, no probe"
    )
    parser.add_argument(
        "--hold-gate",
        metavar="LABEL",
        help="close the model gate while the suite runs (the night slot; needs --model-port 18000)",
    )
    args = parser.parse_args(argv)
    scenarios = load(args.only)
    reps = [int(r) for r in args.reps.split(",")]
    if args.plan:
        for s in scenarios:
            print(
                f"{s['id']:32} {s['family']:4} turns {len(s['turns'])}  failures {s['failure']}"
                f"{'  vision' if s.get('requires_vision') else ''}{'  cave ' + s['cave'] if s.get('cave') else ''}"
            )
        print(f"{len(scenarios)} scenarios x {len(reps)} repetitions")
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.rescore:
        by_id = {s["id"]: s for s in scenarios}
        records = []
        for run_dir in sorted(Path(args.rescore).glob("*-r*")):
            sid = run_dir.name.rsplit("-r", 1)[0]
            if sid in by_id:
                records.append(
                    {**score_run(by_id[sid], run_dir), "rep": int(run_dir.name.rsplit("-r", 1)[1])}
                )
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
        print("\n".join(summary(records)))
        return 0
    if not args.no_jail and not shutil.which("bwrap"):
        print("❌ bwrap is not installed; the suite never runs unjailed.", file=sys.stderr)
        return 2
    vision = True
    forwarder, socket_path = None, None
    if not args.no_model:
        info = model_info(args.model_port)
        if not info:
            print(f"❌ No model server answers on 127.0.0.1:{args.model_port}.", file=sys.stderr)
            return 2
        vision = bool(info.get("has_image_understanding", True))
        forwarder, socket_path = start_forwarder(args.model_port)
    runs = Path(args.runs)
    records = []
    # A stop (the night slot's `timeout`, `systemctl stop`) must still open the gate, stop the
    # forwarder and remove its socket: SIGTERM becomes an exit that runs the `finally` below.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    hold = None
    if args.hold_gate:
        from askevals.gate import GateHold  # noqa: PLC0415 - needs the dreamference package

        hold = GateHold(args.hold_gate, total=len(scenarios) * len(reps)).__enter__()
    try:
        for rep in reps:
            for scenario in scenarios:
                run_dir = runs / f"{scenario['id']}-r{rep}"
                if scenario.get("requires_vision") and not vision:
                    record = {
                        "scenario": scenario["id"],
                        "family": scenario["family"],
                        "rep": rep,
                        "pass": False,
                        "skipped": True,
                        "failures": ["the served model takes no images"],
                    }
                else:
                    shutil.rmtree(run_dir, ignore_errors=True)
                    path = prepare(scenario, run_dir, seed=rep)
                    started = time.time()
                    subprocess.run(session_command(path, run_dir, args, socket_path), check=False)
                    record = {
                        **score_run(scenario, run_dir),
                        "rep": rep,
                        "wall_s": round(time.time() - started, 1),
                    }
                records.append(record)
                if hold:
                    hold.advance()
                with out.open("a") as log:
                    log.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        if hold:
            hold.__exit__()
        if forwarder:
            forwarder.terminate()
        if socket_path and Path(socket_path).exists():
            Path(socket_path).unlink()
    print("\n".join(summary(records)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
