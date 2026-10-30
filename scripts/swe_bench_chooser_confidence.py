#!/usr/bin/env python3
"""Confidence selectors for best-of-N: each attempt's patch scored by the model's own log-probability.

The owner's follow-up to the fan-out judge (`swe_bench_chooser_fanout.py`, 2026-10-30): after v1's
regression filter, every surviving patch is *teacher-forced* behind one prefix shared by the task's
attempts (the issue, the source at `base_commit` around the edited hunks, a request for a fix), and
SGLang returns each patch token's log-probability (`/generate` with `max_new_tokens` 0 and
`logprob_start_len`). One pass per task: one patch goes alone so the prefix is cached, the others
follow at once. The tokens are kept, so every selector is computed from the same pass:

- `mean`: the mean log-probability per patch token;
- `low10`: the mean of the lowest 10% of the patch's token log-probabilities;
- `win32`: the weakest 32-token window's mean;
- `sum`: the total (it favours short patches; shown for the length bias);
- `shortest`, `longest`: by patch tokens, as baselines for the length bias;
- `mean+v3`, `low10+v3`, `win32+v3`: the confidence and the fan-out judge's total (the mean over the
  orders given in `--fanout`), each min-max scaled over the task's survivors, averaged.

Each selector's `summary.json` has chooser v1's format, for `swe_bench_best_of.py --chooser`.
`report.json` adds the length bias. The runs and the dataset are only read (`problem_statement`,
`repo`, `base_commit`). Usage::

    .venv/bin/python scripts/swe_bench_chooser_confidence.py --server http://node-b.local:8000 \\
        --runs n13-default-1,n13-default-2,n13-default-3 --out /tmp/n13-confidence \\
        --fanout /tmp/n13-o0/summary.json,/tmp/n13-o1/summary.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Final, List, Optional, Sequence

SCRIPTS: Final[Path] = Path(__file__).resolve().parent
WINDOW: Final[int] = 32
LOW_SHARE: Final[float] = 0.10
SYSTEM_PROMPT: Final[str] = "You are an expert software engineer. You fix issues in Python repositories."
REQUEST: Final[str] = "Write a patch, as a unified diff, that resolves the issue."
# Qwen's chat template with thinking off, the assistant's turn opened on a diff block.
TEMPLATE: Final[str] = ("<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
                        "<|im_start|>assistant\n<think>\n\n</think>\n\n```diff\n")
CONFIDENCE: Final[Sequence[str]] = ("mean", "low10", "win32")


def fanout_module() -> Any:
    """The fan-out judge's module, beside this one: its context, filter and server are reused."""
    name = "swe_bench_chooser_fanout"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # its dataclasses look their module up while they are built
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- statistics


def stats(logprobs: Sequence[float]) -> Dict[str, Optional[float]]:
    """
    One patch's confidence figures.

    Args:
        logprobs: The patch tokens' log-probabilities, in order.

    Returns:
        Dict[str, Optional[float]]: `tokens`, `sum`, `mean`, `low10` and `win32` (None without tokens).
    """
    n = len(logprobs)
    if not n:
        return {"tokens": 0, "sum": None, "mean": None, "low10": None, "win32": None}
    lowest = sorted(logprobs)[:max(1, math.ceil(n * LOW_SHARE))]
    width = min(WINDOW, n)
    running = sum(logprobs[:width])
    weakest = running
    for i in range(width, n):
        running += logprobs[i] - logprobs[i - width]
        weakest = min(weakest, running)
    return {"tokens": n, "sum": sum(logprobs), "mean": sum(logprobs) / n,
            "low10": sum(lowest) / len(lowest), "win32": weakest / width}


def scaled(values: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """Min-max scaling over one task's candidates; all equal gives 0.5, missing stays None."""
    known = [v for v in values.values() if v is not None]
    if not known:
        return dict(values)
    low, high = min(known), max(known)
    return {k: None if v is None else (0.5 if high == low else (v - low) / (high - low)) for k, v in values.items()}


def selector_values(record: Dict[str, Any], selector: str,
                    v3: Optional[Dict[str, float]] = None) -> Dict[str, Optional[float]]:
    """
    A selector's value per candidate (higher wins).

    Args:
        record: One task's confidence record (`candidates`: id to `stats`).
        selector: A name from the module's docstring.
        v3: The fan-out judge's total per candidate, for the combined selectors.

    Returns:
        Dict[str, Optional[float]]: Candidate id to value; None where it cannot be had.
    """
    figures = record["candidates"]
    if selector in ("shortest", "longest"):
        sign = -1 if selector == "shortest" else 1
        return {i: float(sign * f["tokens"]) for i, f in figures.items()}
    if selector.endswith("+v3"):
        base = scaled({i: f[selector[:-3]] for i, f in figures.items()})
        judged = scaled({i: (v3 or {}).get(i) for i in figures})
        return {i: (None if base[i] is None or judged[i] is None else (base[i] + judged[i]) / 2) for i in figures}
    return {i: f[selector] for i, f in figures.items()}


def top(values: Dict[str, Optional[float]]) -> List[str]:
    """The ids with the highest value (all of them when none has one)."""
    known = {i: v for i, v in values.items() if v is not None}
    if not known:
        return list(values)
    best = max(known.values())
    return [i for i, v in known.items() if v >= best - 1e-12]


def rank_correlation(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Spearman's rho (average ranks for ties); None under three pairs."""
    if len(xs) < 3:
        return None

    def ranks(values: Sequence[float]) -> List[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    var = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return None if var == 0 else cov / var


# --------------------------------------------------------------------------- the server


def prompt_text(issue: str, source: str) -> str:
    """The shared prefix, in the chat template, up to the first patch token."""
    fanout = fanout_module()
    parts = [f"<issue>\n{fanout.clip(issue, fanout.MAX_ISSUE_CHARS)}\n</issue>"]
    if source:
        parts.append(f"<source note=\"the repository before any patch\">\n{source}\n</source>")
    parts.append(REQUEST)
    return TEMPLATE.format(system=SYSTEM_PROMPT, user="\n\n".join(parts))


class ScoringServer:
    """SGLang's native endpoints: `/tokenize`, and `/generate` returning the input's log-probabilities."""

    def __init__(self, base: str, timeout: float = 300.0) -> None:
        """
        Args:
            base: The server's URL (``http://<node>.local:8000``).
            timeout: Seconds to wait for one answer.
        """
        self.inner = fanout_module().Server(base, timeout)
        self.model = ""

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """A POST's JSON answer."""
        request = urllib.request.Request(self.inner.root + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        with self.inner.opener.open(request, timeout=self.inner.timeout) as response:
            return json.loads(response.read())

    def tokenize(self, text: str) -> List[int]:
        """The text's token ids, no special tokens added."""
        return [int(t) for t in self.post("/tokenize", {"prompt": text})["tokens"]]

    def logprobs(self, prefix: List[int], tail: List[int]) -> List[float]:
        """The log-probability of every `tail` token behind `prefix`."""
        answer = self.post("/generate", {"input_ids": prefix + tail, "sampling_params": {"max_new_tokens": 0},
                                         "return_logprob": True, "logprob_start_len": len(prefix) - 1})
        entries = (answer.get("meta_info") or {}).get("input_token_logprobs") or []
        values = [float(e[0]) for e in entries if e and e[0] is not None]
        return values[-len(tail):] if tail else []

    def counters(self) -> Any:
        """The prefix cache's counters (the fan-out judge's)."""
        if not self.model:
            try:
                self.model = str(json.loads(self.inner.get("/v1/models"))["data"][0]["id"])
            except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError):
                pass
        return self.inner.counters()


def score_pool(server: Any, prefix_text: str, pool: Sequence[Any], workers: int) -> Dict[str, Any]:
    """
    One task's pass: the prefix once, then every candidate's patch teacher-forced behind it.

    Returns:
        Dict[str, Any]: `candidates` (id to `stats`), `logprobs` (id to the rounded values), `tokens`
        (the prefix's), `seconds` and `cache_hit`.
    """
    fanout = fanout_module()
    before = server.counters()
    started = time.monotonic()
    prefix = server.tokenize(prefix_text)
    tails = {c.id: server.tokenize(fanout.clip(c.patch, fanout.MAX_PATCH_CHARS)) for c in pool}
    found: Dict[str, List[float]] = {}

    def one(cid: str) -> None:
        try:
            found[cid] = server.logprobs(prefix, tails[cid])
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            found[cid] = []

    ids = [c.id for c in pool]
    if ids:
        one(ids[0])  # alone, so the others find the prefix cached
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        list(executor.map(one, ids[1:]))
    seconds = time.monotonic() - started
    after = server.counters()
    hit = None
    if before and after and after[1] > before[1]:
        hit = round((after[0] - before[0]) / (after[1] - before[1]), 4)
    return {"candidates": {i: stats(found[i]) for i in ids}, "logprobs": {i: [round(v, 4) for v in found[i]] for i in ids},
            "tokens": len(prefix), "seconds": round(seconds, 2), "cache_hit": hit}


# --------------------------------------------------------------------------- main


def v3_totals(paths: Sequence[Path]) -> Dict[str, Dict[str, float]]:
    """The fan-out judge's total per task and candidate, the mean over the given summaries."""
    sums: Dict[str, Dict[str, List[float]]] = {}
    for path in paths:
        for instance_id, task in (json.loads(path.read_text()).get("per_task") or {}).items():
            for cid, total in (task.get("points") or {}).items():
                if total is not None:
                    sums.setdefault(instance_id, {}).setdefault(cid, []).append(float(total))
    return {i: {c: sum(v) / len(v) for c, v in per.items()} for i, per in sums.items()}


def length_bias(tasks: Sequence[Any], records: Dict[str, Dict[str, Any]],
                choices: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    """
    Do short patches win? Per selector, the share of judged tasks where it chose the shortest patch,
    and over every judged candidate the rank correlation of tokens with each confidence figure.
    """
    shortest_share: Dict[str, float] = {}
    for selector, chosen in choices.items():
        hits = n = 0
        for task in tasks:
            record = records.get(task.instance_id)
            choice = chosen.get(task.instance_id) or {}
            if not record or not choice.get("chosen") or "confidence" not in choice:
                continue
            lengths = {i: f["tokens"] for i, f in record["candidates"].items()}
            n += 1
            hits += sum(1 for i in choice["chosen"] if lengths.get(i) == min(lengths.values())) / len(choice["chosen"])
        shortest_share[selector] = round(hits / n, 3) if n else None
    tokens: List[float] = []
    figures: Dict[str, List[float]] = {k: [] for k in ("sum", *CONFIDENCE)}
    resolved_len: Dict[bool, List[int]] = {True: [], False: []}
    for task in tasks:
        record = records.get(task.instance_id)
        if not record:
            continue
        for cid, f in record["candidates"].items():
            if f["mean"] is None:
                continue
            tokens.append(float(f["tokens"]))
            for k in figures:
                figures[k].append(float(f[k]))
            resolved_len[bool(task.resolves.get(cid))].append(int(f["tokens"]))
    median = lambda v: sorted(v)[len(v) // 2] if v else None
    return {"chose_the_shortest": shortest_share,
            "spearman_tokens_vs": {k: None if (r := rank_correlation(tokens, v)) is None else round(r, 3)
                                   for k, v in figures.items()},
            "median_tokens": {"resolving": median(resolved_len[True]), "not_resolving": median(resolved_len[False])},
            "candidates": len(tokens)}


SELECTORS: Final[Sequence[str]] = ("mean", "low10", "win32", "sum", "shortest", "longest",
                                   "mean+v3", "low10+v3", "win32+v3")


def run(args: argparse.Namespace, server_factory: Callable[[str, float], Any] = ScoringServer,
        reader_factory: Optional[Callable[[str, str, Path], Callable[[str], Optional[str]]]] = None) -> Dict[str, Any]:
    """The pass and every selector's summary; the server and the source reader are replaceable for the tests."""
    fanout = fanout_module()
    chooser = fanout.chooser_module()
    reader_factory = reader_factory or fanout.github_reader
    runs = [chooser.load_run(args.runs_dir, name) for name in args.runs.split(",") if name]
    tasks = chooser.select_tasks(runs, False)
    judged = [t for t in tasks if len([c for c in t.candidates if not c.breaks] or t.candidates) > 1]
    print(f"{len(tasks)} task(s) from {', '.join(r['name'] for r in runs)}; {len(judged)} to score")
    args.out.mkdir(parents=True, exist_ok=True)
    log = args.out / "tasks.jsonl"
    records: Dict[str, Dict[str, Any]] = {}
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            records[record["instance_id"]] = record
    todo = [t for t in judged if t.instance_id not in records]
    if todo:
        if not args.server:
            raise SystemExit("❌ --server is required (the model server's URL, by node name)")
        rows = fanout.load_rows(args.dataset, [t.instance_id for t in todo])
        server = server_factory(args.server, args.timeout)
        for task in todo:
            pool = [c for c in task.candidates if not c.breaks] or list(task.candidates)
            row = rows[task.instance_id]
            read = reader_factory(row["repo"], row["base_commit"], args.source_cache)
            source = fanout.source_context([c.patch for c in pool], read)
            record = {"instance_id": task.instance_id,
                      **score_pool(server, prompt_text(row["problem_statement"], source), pool, args.workers)}
            records[task.instance_id] = record
            with open(log, "a") as handle:
                handle.write(json.dumps(record) + "\n")
            print(f"  {task.instance_id:<34} {len(pool)} patches, prefix {record['tokens']} tokens, "
                  f"{record['seconds']}s, cache hit {record['cache_hit']}", flush=True)

    v3 = v3_totals(args.fanout)
    report: Dict[str, Any] = {"selectors": {}}
    all_choices: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for selector in SELECTORS:
        if selector.endswith("+v3") and not v3:
            continue
        choices, scores = {}, {}
        for task in tasks:
            record = records.get(task.instance_id)

            def judge(pool: List[Any], record: Optional[Dict[str, Any]] = record,
                      task: Any = task) -> Dict[str, Any]:
                values = selector_values(record, selector, v3.get(task.instance_id)) if record else {}
                return {"scores": {c.id: {"total": values.get(c.id)} for c in pool}}

            choice = fanout.choose(task.candidates, judge)
            if "fanout" in choice:
                choice["confidence"] = choice.pop("fanout")
            choices[task.instance_id], scores[task.instance_id] = choice, chooser.score(task, choice)
        summary = chooser.summarise(runs, tasks, scores, choices)
        summary.update({"chooser_name": f"confidence {selector}", "server": args.server,
                        "per_task": {i: {**choices[i], **scores[i]} for i in choices}})
        (args.out / selector).mkdir(exist_ok=True)
        (args.out / selector / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        report["selectors"][selector] = {k: summary[k] for k in ("chooser", "filter_alone", "random_run",
                                                                  "oracle", "projected", "listed")}
        all_choices[selector] = choices
    timed = [r for r in records.values() if r.get("seconds") is not None]
    hits = [r["cache_hit"] for r in timed if r.get("cache_hit") is not None]
    report["pass"] = {"tasks": len(timed), "seconds_per_task": round(sum(r["seconds"] for r in timed) / len(timed), 2)
                      if timed else None, "prefix_tokens_mean": round(sum(r["tokens"] for r in timed) / len(timed))
                      if timed else None, "cache_hit_mean": round(sum(hits) / len(hits), 4) if hits else None}
    report["length_bias"] = length_bias(tasks, records, all_choices)
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    for selector, figures in report["selectors"].items():
        print(f"  {selector:<10} {figures['chooser']} of the disagreeing tasks, "
              f"{figures['projected']} of {figures['listed']} (oracle {figures['oracle']})")
    print(json.dumps({"pass": report["pass"], "length_bias": report["length_bias"]}))
    return report


def parser() -> argparse.ArgumentParser:
    """The command line."""
    fanout = fanout_module()
    chooser = fanout.chooser_module()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--server", help="the model server's URL, by node name (http://node-b.local:8000)")
    p.add_argument("--runs", required=True, help="comma-separated run names, the single run first")
    p.add_argument("--runs-dir", type=Path, default=chooser.DEFAULT_RUNS_DIR, help="where the runs are (read only)")
    p.add_argument("--dataset", type=Path, default=chooser.DEFAULT_DATASET, help="the local dataset snapshot")
    p.add_argument("--source-cache", type=Path,
                   default=Path.home() / ".cache" / "dreamference" / "swe-bench" / "source-at-base",
                   help="where the repositories' files at the base commit are cached")
    p.add_argument("--fanout", default="", help="the fan-out judge's summaries, comma-separated, for the +v3 selectors")
    p.add_argument("--out", type=Path, default=Path("swe-bench-chooser-confidence"), help="where results go")
    p.add_argument("--workers", type=int, default=4, help="concurrent patches")
    p.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for one answer")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Runs the pass and writes the summaries."""
    args = parser().parse_args(argv)
    args.fanout = [Path(p) for p in args.fanout.split(",") if p]
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
