#!/usr/bin/env python3
"""Best-of-N against a single run, from N runs of one arm and chooser v1's summary.

Night 11's report (backlog item 6's acceptance run): `scripts/swe_bench_night1.sh` with `BEST_OF=3`
runs one arm three times on the same list (`<prefix>-<arm>-1..3`), each graded, then
`scripts/swe_bench_chooser_test.py` (chooser v1, the code Night Shift's best-of-N runs) over the
three. This script reads both and prints, per task and in total:

- **single run**: the first run (the record arm: what one attempt gives, on the same tasks);
- **best-of-N**: chooser v1's pick, as the chooser test scores it (a tie at the top counts as the
  share of the tied candidates that resolve; a task every run agrees on needs no choice);
- the mean of the N single runs, and the **oracle** (some run resolved it): the ceiling;
- the paired comparison against the single run: tasks where only one side resolved, and an exact
  two-sided sign-flip test on the per-task differences (the exact McNemar test when no credit is
  fractional).

It reads the runs and the summary only; nothing is written but `--json`. Usage::

    .venv/bin/python scripts/swe_bench_best_of.py --runs n11-default-1,n11-default-2,n11-default-3 \\
        --chooser ~/.local/share/dreamference/swe-bench/night11/chooser-v1/summary.json
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCRIPTS = Path(__file__).resolve().parent


def chooser_module() -> Any:
    """The chooser test's module, beside this one: its run loading is reused."""
    name = "swe_bench_chooser_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "swe_bench_chooser_test.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # its dataclasses look their module up while they are built
    spec.loader.exec_module(module)
    return module


def sign_flip_p(differences: Sequence[float]) -> float:
    """
    The exact two-sided sign-flip p-value of the differences' sum (zeros carry no information).

    Returns:
        float: The share of the 2^k sign assignments of the k non-zero differences whose |sum| is
        at least the observed |sum|; 1.0 with none.
    """
    nonzero = [d for d in differences if abs(d) > 1e-9]
    if not nonzero:
        return 1.0
    observed = abs(sum(nonzero)) - 1e-9
    hits = sum(1 for signs in itertools.product((1, -1), repeat=len(nonzero))
               if abs(sum(s * d for s, d in zip(signs, nonzero))) >= observed)
    return hits / 2 ** len(nonzero)


def compare(runs: List[Dict[str, Any]], summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Per-task credits and totals.

    Args:
        runs: The loaded runs (`swe_bench_chooser_test.load_run`), the single run first.
        summary: chooser v1's `summary.json`.

    Returns:
        Dict[str, Any]: `tasks` (id to single, chooser, mean and oracle credit), the totals, the
        discordant counts and the sign-flip p-value.
    """
    listed = sorted(set(runs[0]["instances"]))
    graded = [i for i in listed if all(i in run["resolved"] for run in runs)]
    per_task = summary.get("per_task") or {}
    tasks: Dict[str, Dict[str, float]] = {}
    for instance_id in graded:
        verdicts = [bool(run["resolved"][instance_id]) for run in runs]
        judged = per_task.get(instance_id)
        chosen = float(judged["chooser"]) if judged else float(all(verdicts))
        tasks[instance_id] = {"single": float(verdicts[0]), "chooser": chosen,
                              "mean": sum(verdicts) / len(verdicts), "oracle": float(any(verdicts))}
    differences = [t["chooser"] - t["single"] for t in tasks.values()]
    total = lambda key: round(sum(t[key] for t in tasks.values()), 2)
    return {
        "runs": [run["name"] for run in runs], "listed": len(listed), "graded": len(graded),
        "tasks": tasks, "single": total("single"), "chooser": total("chooser"), "mean": total("mean"),
        "oracle": total("oracle"), "single_runs": {run["name"]: sum(1 for i in graded if run["resolved"][i])
                                                   for run in runs},
        "only_chooser": round(sum(d for d in differences if d > 0), 2),
        "only_single": round(-sum(d for d in differences if d < 0), 2),
        "p": sign_flip_p(differences), "stages": summary.get("stages") or {},
    }


def render(result: Dict[str, Any]) -> str:
    """The report's text."""
    n, first = len(result["runs"]), result["runs"][0]
    graded = result["graded"] or 1
    share = lambda value: f"{value} ({100 * value / graded:.1f}%)"
    lines = [
        f"Best-of-{n} (chooser v1) against a single run ({first}), on {result['graded']} graded task(s)"
        f" of {result['listed']}.",
        f"  single run ({first}):        {share(result['single'])}",
        f"  best-of-{n}, chooser v1:     {share(result['chooser'])}",
        f"  mean of the {n} single runs:  {share(result['mean'])}   "
        + ", ".join(f"{name} {count}" for name, count in result["single_runs"].items()),
        f"  oracle (some run resolved): {share(result['oracle'])}",
        f"  headroom over the single run captured: {result['chooser'] - result['single']:+.2f} of "
        f"{result['oracle'] - result['single']:+.2f}",
        f"Paired against the single run: only best-of-{n} resolved {result['only_chooser']}, "
        f"only the single run {result['only_single']}; exact sign-flip test p = {result['p']:.3f}"
        " (the exact McNemar test when no credit is fractional).",
        "Chosen by: " + (", ".join(f"{stage} {count}" for stage, count in sorted(result["stages"].items()))
                         or "nothing to choose (every run agreed on every task)"),
        "",
        "Tasks where the runs disagree (single / chooser / mean):",
    ]
    for instance_id, credit in result["tasks"].items():
        if 0 < credit["mean"] < 1:
            lines.append(f"  {instance_id:<36} {credit['single']:.0f} / {credit['chooser']:.2f} / {credit['mean']:.2f}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    """Prints the report."""
    chooser = chooser_module()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", required=True, help="the N runs, comma-separated, the single run first")
    parser.add_argument("--chooser", type=Path, required=True, help="chooser v1's summary.json over those runs")
    parser.add_argument("--runs-dir", type=Path, default=chooser.DEFAULT_RUNS_DIR)
    parser.add_argument("--json", type=Path, help="also write the figures here")
    args = parser.parse_args(argv)
    runs = [chooser.load_run(args.runs_dir, name) for name in args.runs.split(",") if name]
    summary = json.loads(args.chooser.read_text())
    if summary.get("runs") and summary["runs"] != [run["name"] for run in runs]:
        raise SystemExit(f"❌ {args.chooser} was made over {summary['runs']}, not {args.runs}")
    result = compare(runs, summary)
    print(render(result), end="")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
