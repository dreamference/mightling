#!/usr/bin/env python3
"""Compare local SWE-bench runs with published SWE-bench Verified submissions.

The SWE-bench project publishes every leaderboard submission's per-instance
results in the GitHub repository ``SWE-bench/experiments``
(``evaluation/verified/<submission>/results/results.json`` with a ``resolved``
list, or ``per_instance_details.json`` for the mini-SWE-agent runs, beside a
``metadata.yaml``). This script fetches only those small files (a blobless,
sparse clone: no trajectories, no logs), scores every submission on exactly the
task IDs a local run used, and estimates which overall Verified score a local
result corresponds to once the subset's difficulty is allowed for.

Two estimators are used, and each is back-tested on the submissions themselves
(predict a submission's overall score from its subset results alone, compare
with its real score):

* **Regression**: overall score against subset score, ordinary least squares,
  fitted on submissions at or above ``--regression-floor`` overall.
* **Rasch (1PL item response)**: every task gets a difficulty and every
  submission an ability, fitted jointly on the full submission x task matrix;
  a local run's ability is fitted on its subset tasks with the difficulties
  held fixed, and its expected score on all 500 tasks is the estimate. This is
  the one that uses *which* tasks were solved, not only how many.

Nothing here needs a GPU, a model server or Docker; it only reads the local
runs (``manifest.json`` and ``eval/1/grading.json``) and writes nothing outside
``--experiments`` (and ``--json`` if given).

Usage::

    # first time (or --fetch again to update the clone)
    python scripts/swe_bench_compare.py --fetch

    # the 24-task rounds
    python scripts/swe_bench_compare.py --subset-run im-index-on \\
        --run im-index-on --run im-index-off --run im-refine \\
        --default-runs im-index-on,im-index-off

    # tonight's 100-task sample, before and after it lands
    python scripts/swe_bench_compare.py \\
        --subset ~/.cache/dreamference/swe-bench/sample-100.txt --run <run-name>
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Final, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import yaml

EXPERIMENTS_URL: Final[str] = "https://github.com/SWE-bench/experiments.git"
DEFAULT_EXPERIMENTS_DIR: Final[Path] = Path.home() / ".cache" / "dreamference" / "swe-bench" / "experiments"
DEFAULT_RUNS_DIR: Final[Path] = Path.home() / ".local" / "share" / "dreamference" / "swe-bench" / "runs"
SPARSE_PATTERNS: Final[Tuple[str, ...]] = (
    "/evaluation/verified/*/results/results.json",
    "/evaluation/verified/*/per_instance_details.json",
    "/evaluation/verified/*/metadata.yaml",
    "/evaluation/verified/*/metadata.yml",
)
VERIFIED_SIZE: Final[int] = 500

# Submissions whose model is in the 20-40B parameter range, by name. The
# metadata has no reliable size field, so this is maintained by hand from each
# submission's model card (``model_display`` / ``model``). Sizes are total
# parameters; "A3B" is the active count of a mixture-of-experts model.
MID_SIZE_MODELS: Final[Tuple[Tuple[str, str], ...]] = (
    (r"qwen3-?coder-?30b-?a3b|qwencoder30ba3b", "Qwen3-Coder-30B-A3B (30B MoE, 3B active)"),
    (r"devstral[-_]small", "Devstral Small (24B dense)"),
    (r"skywork-swe-32b", "Skywork-SWE-32B (Qwen2.5-Coder-32B fine-tune)"),
    (r"sweagent_lm_32b", "SWE-agent-LM-32B (Qwen2.5-Coder-32B fine-tune)"),
    (r"qwen2-5-coder-32b", "Qwen2.5-Coder-32B-Instruct"),
    (r"deepswerl", "DeepSWE-Preview (Qwen3-32B fine-tune)"),
)


# --------------------------------------------------------------------------- data


@dataclass
class Submission:
    """One published SWE-bench Verified submission."""

    name: str
    resolved: Set[str]
    model: str
    open_weights: bool
    attempts: str
    checked: str

    @property
    def date(self) -> str:
        """The submission's date, from its folder name (``YYYY-MM-DD``)."""
        d = self.name[:8]
        return f"{d[:4]}-{d[4:6]}-{d[6:]}" if d.isdigit() else "?"

    @property
    def overall(self) -> float:
        """Resolved fraction of all 500 Verified tasks."""
        return len(self.resolved) / VERIFIED_SIZE

    def subset_count(self, tasks: Sequence[str]) -> int:
        """How many of ``tasks`` this submission resolved."""
        return sum(1 for t in tasks if t in self.resolved)

    @property
    def mid_size(self) -> Optional[str]:
        """The 20-40B model this submission is built on, or None."""
        for pattern, label in MID_SIZE_MODELS:
            if re.search(pattern, self.name, re.IGNORECASE):
                return label
        return None


@dataclass
class LocalRun:
    """One local run: its task IDs and which of them resolved."""

    name: str
    tasks: List[str]
    resolved: Set[str] = field(default_factory=set)
    graded: bool = False
    config: Dict[str, object] = field(default_factory=dict)


def fetch_experiments(target: Path) -> None:
    """Clone (or update) the experiments repository, results files only.

    Args:
        target: The clone's directory.
    """
    if not (target / ".git").exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", "--depth", "1",
                        EXPERIMENTS_URL, str(target)], check=True)
        subprocess.run(["git", "-C", str(target), "sparse-checkout", "init", "--no-cone"], check=True)
    else:
        subprocess.run(["git", "-C", str(target), "fetch", "--depth", "1", "origin", "main"], check=True)
        subprocess.run(["git", "-C", str(target), "reset", "--hard", "origin/main"], check=True)
    subprocess.run(["git", "-C", str(target), "sparse-checkout", "set", *SPARSE_PATTERNS], check=True)
    subprocess.run(["git", "-C", str(target), "checkout"], check=True)


def experiments_commit(root: Path) -> str:
    """The experiments clone's HEAD commit and its date."""
    try:
        out = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%H %cs"],
                             capture_output=True, text=True, check=True).stdout.strip()
        return out
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def load_submissions(root: Path) -> Tuple[List[Submission], List[str], List[str]]:
    """Read every Verified submission that publishes per-instance results.

    Args:
        root: The experiments clone.

    Returns:
        The submissions, the 500 Verified task IDs, and the names skipped for
        having no per-instance results.
    """
    base = root / "evaluation" / "verified"
    subs: List[Submission] = []
    skipped: List[str] = []
    all_ids: Set[str] = set()
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        resolved: Optional[Set[str]] = None
        if (d / "results" / "results.json").exists():
            data = json.loads((d / "results" / "results.json").read_text())
            resolved = set(data.get("resolved", []))
        elif (d / "per_instance_details.json").exists():
            data = json.loads((d / "per_instance_details.json").read_text())
            all_ids.update(data)
            resolved = {k for k, v in data.items() if isinstance(v, dict) and v.get("resolved")}
        if resolved is None:
            skipped.append(d.name)
            continue
        meta_path = d / "metadata.yaml" if (d / "metadata.yaml").exists() else d / "metadata.yml"
        meta = (yaml.safe_load(meta_path.read_text()) or {}) if meta_path.exists() else {}
        tags = meta.get("tags") or {}
        model = tags.get("model_display") or tags.get("model") or "?"
        if isinstance(model, list):
            model = ", ".join(str(m) for m in model)
        model = str(model).replace("https://huggingface.co/", "")
        attempts = str((tags.get("system") or {}).get("attempts", "?"))
        checked = str(tags.get("checked")).split(" ")[0].lower()
        subs.append(Submission(d.name, resolved, model, bool(tags.get("os_model")), attempts, checked))
    for s in subs:
        all_ids.update(s.resolved)
    return subs, sorted(all_ids), skipped


def read_task_file(path: Path) -> List[str]:
    """Task IDs from a text file, one per line, ``#`` comments ignored."""
    out = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def load_run(runs_dir: Path, name: str) -> LocalRun:
    """Read a local run's task IDs and, once graded, its resolved IDs.

    Per-instance outcomes come from ``eval/1/grading.json``, where an empty
    patch is an unresolved entry, so the denominator is always the manifest's
    full task list.

    Args:
        runs_dir: Directory holding the runs.
        name: The run's name.

    Returns:
        The run.
    """
    d = runs_dir / name
    manifest = json.loads((d / "manifest.json").read_text())
    run = LocalRun(name, list(manifest["instances"]))
    run.config = {k: manifest.get(k) for k in
                  ("started", "prompt", "code_index", "masking", "refine", "issue_text")}
    grading = d / "eval" / "1" / "grading.json"
    if grading.exists():
        results = json.loads(grading.read_text()).get("results", {})
        run.resolved = {k for k, v in results.items() if v.get("resolved")}
        run.graded = True
    else:
        for rep in sorted((d / "eval" / "1").glob("*.json")) if (d / "eval" / "1").exists() else []:
            if rep.name == "grading.json":
                continue
            data = json.loads(rep.read_text())
            if "resolved_ids" in data:
                run.resolved = set(data["resolved_ids"])
                run.graded = True
                break
    return run


# --------------------------------------------------------------------- statistics


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def fit_rasch(matrix: np.ndarray, iters: int = 500, prior_sd: float = 3.0) -> Tuple[np.ndarray, np.ndarray]:
    """Joint maximum a posteriori fit of a Rasch model.

    ``P(submission s solves task i) = sigmoid(theta_s - b_i)``. A weak Gaussian
    prior keeps tasks solved by every submission (or by none) finite.

    Args:
        matrix: Submissions x tasks, 1 where resolved.
        iters: Alternating Newton iterations.
        prior_sd: Standard deviation of the prior on every parameter.

    Returns:
        Abilities (one per submission) and difficulties (one per task).
    """
    s, t = matrix.shape
    theta = np.zeros(s)
    b = np.zeros(t)
    lam = 1.0 / prior_sd ** 2
    for _ in range(iters):
        p = _sigmoid(theta[:, None] - b[None, :])
        w = p * (1 - p)
        theta += ((matrix - p).sum(1) - lam * theta) / (w.sum(1) + lam)
        p = _sigmoid(theta[:, None] - b[None, :])
        w = p * (1 - p)
        b += (-(matrix - p).sum(0) - lam * b) / (w.sum(0) + lam)
        m = b.mean()  # anchor: mean difficulty 0
        b -= m
        theta -= m
    return theta, b


def ability_for(k: float, diffs: np.ndarray, prior_sd: float = 3.0) -> float:
    """Ability whose expected count on tasks of difficulty ``diffs`` is ``k``.

    Uses the same weak prior as the joint fit, so 0 or all-solved stay finite.
    """
    lam = 1.0 / prior_sd ** 2
    theta = 0.0
    for _ in range(200):
        p = _sigmoid(theta - diffs)
        grad = k - p.sum() - lam * theta
        hess = (p * (1 - p)).sum() + lam
        theta += grad / hess
    return theta


@dataclass
class Model:
    """Everything needed to turn a subset score into an overall estimate."""

    tasks: List[str]
    subs: List[Submission]
    all_ids: List[str]
    theta: np.ndarray
    b: np.ndarray
    reg_coef: Tuple[float, float]
    reg_resid_sd: float
    reg_floor: float
    rasch_rmse: float
    reg_rmse: float

    @property
    def n(self) -> int:
        return len(self.tasks)

    def subset_diffs(self) -> np.ndarray:
        idx = {t: i for i, t in enumerate(self.all_ids)}
        return self.b[[idx[t] for t in self.tasks]]

    def rasch_overall(self, k: float) -> float:
        """Expected overall Verified fraction for ``k`` of ``n`` on the subset."""
        theta = ability_for(k, self.subset_diffs())
        return float(_sigmoid(theta - self.b).mean())

    def regression_overall(self, k: float) -> float:
        a, slope = self.reg_coef
        return min(1.0, max(0.0, a + slope * (k / self.n)))

    def calibrated(self, k: float) -> Tuple[float, float]:
        """Rasch estimate +/- 1.96 x the back-test RMSE (an empirical 95% band)."""
        est = self.rasch_overall(k)
        half = 1.96 * self.rasch_rmse
        return max(0.0, est - half), min(1.0, est + half)


def build_model(subs: List[Submission], all_ids: List[str], tasks: List[str],
                reg_floor: float) -> Model:
    """Fit both estimators for one task subset and back-test them.

    Args:
        subs: Published submissions.
        all_ids: The 500 Verified IDs.
        tasks: The local subset.
        reg_floor: Minimum overall score for a submission to enter the regression.

    Returns:
        The fitted model.
    """
    idx = {t: i for i, t in enumerate(all_ids)}
    mat = np.zeros((len(subs), len(all_ids)))
    for r, s in enumerate(subs):
        for t in s.resolved:
            if t in idx:
                mat[r, idx[t]] = 1
    theta, b = fit_rasch(mat)

    sub_frac = np.array([s.subset_count(tasks) / len(tasks) for s in subs])
    overall = np.array([s.overall for s in subs])
    keep = overall >= reg_floor
    slope, intercept = np.polyfit(sub_frac[keep], overall[keep], 1)
    resid = overall[keep] - (intercept + slope * sub_frac[keep])
    resid_sd = float(np.std(resid, ddof=2))

    model = Model(tasks, subs, all_ids, theta, b, (float(intercept), float(slope)), resid_sd,
                  reg_floor, 0.0, 0.0)
    # Back-test both on the same population (overall >= floor).
    rasch_err = [model.rasch_overall(s.subset_count(tasks)) - s.overall
                 for s, k in zip(subs, keep) if k]
    model.rasch_rmse = float(np.sqrt(np.mean(np.square(rasch_err))))
    model.reg_rmse = float(np.sqrt(np.mean(np.square(resid))))
    return model


# ------------------------------------------------------------------------ output


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def md_table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def system_rows(subs: Sequence[Submission], tasks: Sequence[str]) -> List[List[object]]:
    n = len(tasks)
    rows = []
    for s in subs:
        k = s.subset_count(tasks)
        rows.append([f"`{s.name}`", s.date, s.model, s.attempts, pct(s.overall),
                     f"{k}/{n}", pct(k / n), f"{100 * (k / n - s.overall):+.1f}"])
    return rows


SYSTEM_HEADER: Final[Tuple[str, ...]] = (
    "submission", "date", "model", "attempts", "overall (500)", "subset", "subset %", "subset - overall (pp)")


def report(model: Model, runs: List[LocalRun], default_runs: List[str], top: int,
           commit: str, skipped: List[str], label: str) -> Tuple[str, dict]:
    """Markdown report and a JSON summary for one task subset."""
    subs, tasks, n = model.subs, model.tasks, model.n
    out: List[str] = []
    js: dict = {"subset": label, "n": n, "experiments_commit": commit, "submissions": len(subs)}

    out.append(f"## Subset: {label} ({n} tasks)\n")
    out.append(f"Published submissions with per-instance results: **{len(subs)}** "
               f"(experiments commit `{commit}`; {len(skipped)} skipped, no per-instance results).\n")

    # Difficulty index.
    overall = np.array([s.overall for s in subs])
    subset = np.array([s.subset_count(tasks) / n for s in subs])
    strong = overall >= 0.6
    rows = []
    for name, mask in (("all submissions", np.ones(len(subs), bool)),
                       ("overall >= 40%", overall >= 0.4), ("overall >= 60%", strong)):
        rows.append([name, int(mask.sum()), pct(overall[mask].mean()), pct(subset[mask].mean()),
                     f"{subset[mask].mean() / overall[mask].mean():.2f}",
                     f"{100 * (subset[mask] - overall[mask]).mean():+.1f}"])
    out.append("### Difficulty index\n")
    out.append(md_table(["population", "submissions", "mean on all 500", "mean on subset",
                         "ratio", "mean gap (pp)"], rows) + "\n")
    js["difficulty"] = {r[0]: {"n": r[1], "overall": r[2], "subset": r[3], "ratio": r[4]} for r in rows}

    # Systems.
    ranked = sorted(subs, key=lambda s: -s.overall)
    out.append(f"### Top {top} submissions by overall Verified score\n")
    out.append(md_table(SYSTEM_HEADER, system_rows(ranked[:top], tasks)) + "\n")
    open_w = [s for s in ranked if s.open_weights][:top]
    out.append(f"### Best open-weight-model submissions (metadata `os_model: true`, top {len(open_w)})\n")
    out.append(md_table(SYSTEM_HEADER, system_rows(open_w, tasks)) + "\n")
    mid = [s for s in ranked if s.mid_size]
    out.append("### Submissions built on 20-40B models\n")
    out.append(md_table(("model size",) + SYSTEM_HEADER,
                        ([s.mid_size] + r for s, r in zip(mid, system_rows(mid, tasks)))) + "\n")

    # Per task.
    graded = [r for r in runs if r.graded]
    frac_all = {t: sum(t in s.resolved for s in subs) / len(subs) for t in tasks}
    strong_subs = [s for s in subs if s.overall >= 0.6]
    frac_strong = {t: sum(t in s.resolved for s in strong_subs) / max(1, len(strong_subs)) for t in tasks}
    idx = {t: i for i, t in enumerate(model.all_ids)}
    out.append("### Per task\n")
    out.append("Fraction of all submissions, and of those at or above 60% overall, that resolved each task; "
               "Rasch difficulty (0 is the Verified average, higher is harder); then each local run "
               "(`y` resolved, `.` not).\n")
    header = ["task", "all subs", ">=60% subs", "difficulty"] + [r.name for r in graded]
    rows = []
    for t in sorted(tasks, key=lambda t: -frac_all[t]):
        rows.append([f"`{t}`", pct(frac_all[t]), pct(frac_strong[t]), f"{model.b[idx[t]]:+.2f}"]
                    + ["y" if t in r.resolved else "." for r in graded])
    if graded:
        rows.append(["**resolved**", "", "", ""] + [f"**{len(r.resolved & set(tasks))}**" for r in graded])
    out.append(md_table(header, rows) + "\n")
    js["per_task"] = {t: {"all": frac_all[t], "strong": frac_strong[t], "difficulty": float(model.b[idx[t]])}
                      for t in tasks}

    # Estimators.
    a, slope = model.reg_coef
    out.append("### From subset score to overall Verified score\n")
    out.append(f"- Regression (submissions >= {pct(model.reg_floor)} overall, "
               f"n={int((overall >= model.reg_floor).sum())}): overall = {a:.3f} + {slope:.3f} x subset; "
               f"residual SD {pct(model.reg_resid_sd)}.\n"
               f"- Rasch back-test on the same submissions (predict each one's overall score from its "
               f"{n} subset tasks alone): RMSE {pct(model.rasch_rmse)} (regression: {pct(model.reg_rmse)}).\n")
    lookup_rows = []
    lo_k = max(0, int(0.3 * n))
    step = 1 if n <= 30 else 5
    for k in range(lo_k, n + 1, step):
        lo, hi = wilson(k, n)
        c_lo, c_hi = model.calibrated(k)
        lookup_rows.append([f"{k}/{n}", pct(k / n), pct(model.rasch_overall(k)),
                            pct(model.regression_overall(k)), f"{pct(c_lo)} - {pct(c_hi)}",
                            f"{pct(model.rasch_overall(lo * n))} - {pct(model.rasch_overall(hi * n))}"])
    out.append("Lookup: a local result of k/n corresponds to roughly this overall Verified score. "
               "\"95% back-test\" is the Rasch estimate +/- 1.96 x the back-test RMSE, the error actually "
               "seen when the published submissions are scored on these tasks alone; \"95% Wilson\" is the "
               "Rasch estimate at the ends of the Wilson interval for k/n, which ignores what is known "
               "about each task and is wider.\n")
    out.append(md_table(["k/n", "subset %", "Rasch estimate", "regression estimate", "95% back-test",
                         "95% Wilson"], lookup_rows) + "\n")

    if graded:
        rows = []
        js["runs"] = {}
        for r in graded:
            k = len(r.resolved & set(tasks))
            lo, hi = wilson(k, n)
            ra = model.rasch_overall(k)
            rg = model.regression_overall(k)
            c_lo, c_hi = model.calibrated(k)
            rows.append([r.name, f"{k}/{n}", f"{pct(lo)} - {pct(hi)}", pct(ra), pct(rg),
                         f"{pct(c_lo)} - {pct(c_hi)}",
                         f"{pct(model.rasch_overall(lo * n))} - {pct(model.rasch_overall(hi * n))}"])
            js["runs"][r.name] = {"k": k, "n": n, "rasch": ra, "regression": rg,
                                  "wilson": [lo, hi], "config": r.config}
        out.append("### Local runs\n")
        out.append(md_table(["run", "resolved", "95% Wilson (subset)", "Rasch overall", "regression overall",
                             "95% back-test", "95% Wilson (overall)"], rows) + "\n")
        defaults = [len(r.resolved & set(tasks)) for r in graded if r.name in default_runs]
        if defaults:
            med = statistics.median(defaults)
            spread = (min(defaults), max(defaults))
            js["default_median"] = {"k": med, "rasch": model.rasch_overall(med),
                                    "regression": model.regression_overall(med), "spread": spread}
            out.append(f"Default-mode rounds ({', '.join(default_runs)}): resolved "
                       f"{', '.join(str(d) for d in defaults)}; median **{med:g}/{n}** "
                       f"-> Rasch **{pct(model.rasch_overall(med))}**, regression "
                       f"{pct(model.regression_overall(med))}; run-to-run range {spread[0]}-{spread[1]} -> "
                       f"{pct(model.rasch_overall(spread[0]))} - {pct(model.rasch_overall(spread[1]))}.\n")
        union = set().union(*(r.resolved & set(tasks) for r in graded))
        js["union"] = len(union)
        out.append(f"Resolved by at least one local run (an any-of-{len(graded)} figure, comparable only "
                   f"to multi-attempt submissions, never to pass@1): {len(union)}/{n}.\n")

    js["submission_names"] = [s.name for s in subs]
    return "\n".join(out), js


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--experiments", type=Path, default=DEFAULT_EXPERIMENTS_DIR,
                    help="clone of SWE-bench/experiments (results files only)")
    ap.add_argument("--fetch", action="store_true", help="clone or update --experiments first")
    ap.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    ap.add_argument("--subset", type=Path, action="append", default=[],
                    help="task-ID file (one per line, # comments); repeatable")
    ap.add_argument("--subset-run", action="append", default=[],
                    help="use this local run's manifest task IDs as a subset; repeatable")
    ap.add_argument("--run", action="append", default=[], help="local run to place; repeatable")
    ap.add_argument("--default-runs", default="",
                    help="comma-separated runs whose median is reported as the default configuration")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--regression-floor", type=float, default=0.4)
    ap.add_argument("--json", type=Path, help="also write a JSON summary here")
    args = ap.parse_args(argv)

    if args.fetch:
        fetch_experiments(args.experiments)
    if not (args.experiments / "evaluation" / "verified").exists():
        print(f"no experiments clone at {args.experiments}; run with --fetch", file=sys.stderr)
        return 2
    subs, all_ids, skipped = load_submissions(args.experiments)
    commit = experiments_commit(args.experiments)
    if len(all_ids) != VERIFIED_SIZE:
        print(f"warning: found {len(all_ids)} Verified IDs, expected {VERIFIED_SIZE}", file=sys.stderr)

    runs = [load_run(args.runs_dir, r) for r in args.run]
    subsets: List[Tuple[str, List[str]]] = []
    for p in args.subset:
        subsets.append((p.name, read_task_file(p.expanduser())))
    for r in args.subset_run:
        subsets.append((f"tasks of run {r}", load_run(args.runs_dir, r).tasks))
    if not subsets:
        subsets.append(("all Verified", all_ids))
    default_runs = [r for r in args.default_runs.split(",") if r]

    summaries = []
    for label, tasks in subsets:
        missing = [t for t in tasks if t not in set(all_ids)]
        if missing:
            print(f"warning: {len(missing)} task(s) of {label} are not in Verified: {missing[:3]}", file=sys.stderr)
            tasks = [t for t in tasks if t not in missing]
        own = [r for r in runs if set(r.tasks) == set(tasks)]
        other = [r.name for r in runs if r not in own]
        if other:
            print(f"note: runs not on subset {label} (task IDs differ), left out: {other}", file=sys.stderr)
        model = build_model(subs, all_ids, tasks, args.regression_floor)
        text, js = report(model, own, default_runs, args.top, commit, skipped, label)
        print(text)
        summaries.append(js)
    if args.json:
        args.json.write_text(json.dumps(summaries, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
