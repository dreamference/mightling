"""T1: IFEval and IFBench through `ling` (specs/DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md §10.3).

    python ifeval.py --sets ifeval,ifbench --cave shipped,off,ultra [--limit 20] [--parallel 3]

Each prompt is one turn of `ling exec` in the ask-evals jail (scripts/ask_evals: no network but
the stand-ins, which answer nothing useful here, and the model through its socket), with the prompt
(`--prompt`, `ask` by default) and the cave level as the variables. The answers go to each set's own
reference scorer, fetched at a pinned commit into the cache by `setup_cache.py ifeval`, run in its
own virtualenv with no network. The result is the four standard numbers per set and level:
strict and loose, prompt-level and instruction-level. `summary.json` holds them with the manifest's
sources, revisions and licences.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Final

HERE: Final[Path] = Path(__file__).resolve().parent
ASK_EVALS: Final[Path] = HERE.parent / "ask_evals"
sys.path.insert(0, str(ASK_EVALS))

from askevals import rollout  # noqa: E402
from setup_cache import DEFAULT_CACHE, venv_python  # noqa: E402

# Each set: its prompts, and how its scorer is run (from which folder, with which module).
SETS: Final[dict] = {
    "ifeval": {
        "data": "ifeval/instruction_following_eval/data/input_data.jsonl",
        "cwd": "ifeval",
        "scorer": ["-m", "instruction_following_eval.evaluation_main"],
    },
    "ifbench": {
        "data": "ifbench/data/IFBench_test.jsonl",
        "cwd": "ifbench",
        "scorer": ["run_eval.py"],
    },
}
SHIPPED: Final[str] = "shipped"


def _load_runner():
    spec = importlib.util.spec_from_file_location("ask_evals_run", ASK_EVALS / "run.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def prompts(cache: Path, name: str, limit: int | None) -> list[dict]:
    """The set's prompts, in file order, the first `limit` of them when given."""
    rows = [
        json.loads(line)
        for line in (cache / SETS[name]["data"]).read_text().splitlines()
        if line.strip()
    ]
    return rows[:limit] if limit else rows


def scenario_for(name: str, row: dict, level: str, prompt: str) -> dict:
    """One prompt as an ask-evals scenario: one turn, no web, no checks (the scorer judges)."""
    scenario = {
        "id": f"{name}-{row.get('key', abs(hash(row['prompt'])))}",
        "family": name,
        "failure": [],
        "turns": [{"text": row["prompt"]}],
        "web": {},
        "expect": {},
        "prompt": prompt,
    }
    if level != SHIPPED:
        scenario["cave"] = level
    return scenario


def answer_of(run_dir: Path) -> str:
    turns = rollout.read_home(run_dir / "home")
    return turns[-1].answer if turns else ""


def accuracy(results: Path) -> dict:
    """Prompt-level and instruction-level accuracy from one `eval_results_*.jsonl`."""
    rows = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    followed = [flag for row in rows for flag in row["follow_instruction_list"]]
    return {
        "prompts": len(rows),
        "prompt_level": round(
            sum(bool(r["follow_all_instructions"]) for r in rows) / max(1, len(rows)), 4
        ),
        "instruction_level": round(sum(bool(f) for f in followed) / max(1, len(followed)), 4),
    }


def score(
    cache: Path,
    name: str,
    responses: Path,
    out: Path,
    python: Path,
    rows: list[dict] | None = None,
    run=subprocess.run,
) -> dict:
    """Runs the set's reference scorer over `responses` and reads its two result files.

    The scorer looks up an answer for every prompt of its input file, so a run over some of the
    prompts (`--limit`) hands it an input file of those rows alone.
    """
    out.mkdir(parents=True, exist_ok=True)
    spec = SETS[name]
    data = cache / spec["data"]
    if rows is not None:
        data = out / "input_data.jsonl"
        data.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    command = [
        str(python),
        *spec["scorer"],
        f"--input_data={data}",
        f"--input_response_data={responses}",
        f"--output_dir={out}",
    ]
    env = {
        **os.environ,
        "NLTK_DATA": str(cache / "nltk_data"),
        "PYTHONPATH": str(cache / spec["cwd"]),
    }
    done = run(
        command, cwd=cache / spec["cwd"], env=env, capture_output=True, text=True, check=False
    )
    (out / "scorer.log").write_text((done.stdout or "") + (done.stderr or ""))
    if done.returncode != 0:
        return {"error": f"the scorer exited {done.returncode}; see {out / 'scorer.log'}"}
    # IFEval writes `eval_results_<kind>.jsonl`; IFBench prefixes the responses file's name.
    found = {kind: sorted(out.glob(f"*eval_results_{kind}.jsonl")) for kind in ("strict", "loose")}
    if not all(found.values()):
        return {"error": f"the scorer wrote no results; see {out / 'scorer.log'}"}
    return {kind: accuracy(paths[0]) for kind, paths in found.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sets", default="ifeval,ifbench")
    parser.add_argument(
        "--cave", default=SHIPPED, help=f"comma-separated levels; {SHIPPED!r} sets none"
    )
    parser.add_argument("--prompt", default="ask")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument(
        "--cache", default=os.environ.get("DREAMFERENCE_INDUSTRY_CACHE", str(DEFAULT_CACHE))
    )
    parser.add_argument(
        "--runs",
        default=str(
            Path.home()
            / ".cache"
            / "dreamference"
            / "industry"
            / "runs"
            / time.strftime("ifeval-%Y%m%d-%H%M%S")
        ),
    )
    parser.add_argument("--model-port", type=int, default=8000)
    parser.add_argument("--ling", default="ling")
    parser.add_argument("--scorer-python", help="defaults to the cache's virtualenv")
    parser.add_argument("--hold-gate", metavar="LABEL", help="close the model gate while it runs")
    parser.add_argument("--no-jail", action="store_true", help="tests only")
    parser.add_argument("--no-model", action="store_true", help="tests only")
    args = parser.parse_args(argv)
    cache, runs = Path(args.cache), Path(args.runs)
    if not (cache / "manifest.json").exists():
        print(f"❌ Nothing set up in {cache}: run setup_cache.py ifeval first.", file=sys.stderr)
        return 2
    runner = _load_runner()
    forwarder, socket_path = None, None
    if not args.no_model:
        if not runner.model_info(args.model_port):
            print(f"❌ No model server answers on 127.0.0.1:{args.model_port}.", file=sys.stderr)
            return 2
        forwarder, socket_path = runner.start_forwarder(args.model_port)
    session_args = argparse.Namespace(no_jail=args.no_jail, ling=args.ling, cave=None)
    # A stop still opens the gate and removes the forwarder's socket (the `finally` below).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    hold = None
    if args.hold_gate:
        from askevals.gate import GateHold  # noqa: PLC0415 - needs the dreamference package

        sets, levels = args.sets.split(","), args.cave.split(",")
        total = sum(len(prompts(cache, n, args.limit)) for n in sets) * len(levels)
        hold = GateHold(args.hold_gate, total=total).__enter__()
    summary: dict = {
        "manifest": json.loads((cache / "manifest.json").read_text()),
        "prompt": args.prompt,
        "results": {},
    }
    try:
        for name in args.sets.split(","):
            rows = prompts(cache, name, args.limit)
            for level in args.cave.split(","):
                base = runs / name / level

                def one(row: dict, base: Path = base, level: str = level, name: str = name) -> dict:
                    scenario = scenario_for(name, row, level, args.prompt)
                    run_dir = base / scenario["id"]
                    path = runner.prepare(scenario, run_dir, seed=0)
                    subprocess.run(
                        runner.session_command(path, run_dir, session_args, socket_path),
                        check=False,
                    )
                    if hold:
                        hold.advance()
                    return {"prompt": row["prompt"], "response": answer_of(run_dir)}

                with ThreadPoolExecutor(max(1, args.parallel)) as pool:
                    answered = list(pool.map(one, rows))
                responses = base / "responses.jsonl"
                responses.write_text(
                    "".join(json.dumps(a, ensure_ascii=False) + "\n" for a in answered)
                )
                python = Path(args.scorer_python) if args.scorer_python else venv_python(cache)
                result = score(cache, name, responses, base / "scored", python, rows=rows)
                result["empty_answers"] = sum(1 for a in answered if not a["response"].strip())
                summary["results"].setdefault(name, {})[level] = result
                print(json.dumps({"set": name, "cave": level, **result}), flush=True)
    finally:
        if hold:
            hold.__exit__()
        if forwarder:
            forwarder.terminate()
        if socket_path and Path(socket_path).exists():
            Path(socket_path).unlink()
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
