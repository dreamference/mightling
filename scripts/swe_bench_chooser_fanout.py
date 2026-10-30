#!/usr/bin/env python3
"""Chooser v3, the fan-out judge, on disk: many one-token questions over one shared context.

Chooser v1 (``scripts/swe_bench_chooser_test.py``) asks the served model one long, thinking judge
call per pair of candidates. The fan-out judge builds **one shared context** per task instead (the
issue, the repository's source around the hunks the candidates edit, and every candidate's diff,
numbered) and asks it many short questions, each answered with one token: ``Yes``/``No`` or a
candidate's number. Every question is appended after the same prefix, so the model server's prefix
cache (SGLang's radix cache) reads the context once and each probe costs about one token of
prefill and one of decode. Thinking is off; the answer is read from the first token's logprobs.

The questions, per task:

- **attribute probes**, per candidate: does it fix the reported behaviour, the root cause, every
  case the issue names; does it change unrelated behaviour, break existing tests, raise an error,
  touch only tests; would the maintainer merge it. Several framings of some, each with a polarity;
- **issue checks**, per candidate: one short generation first lists up to five concrete behaviours
  a fix must have, from the issue alone; each candidate is then asked about each;
- **pairwise probes**: "which fixes the issue, X or Y?" in three framings, each pair asked in both
  orders, so the position bias of the two labels cancels.

The score of a candidate is the mean of three parts, each in [0, 1]: its attribute probes (P(yes),
or 1 - P(yes) for a negative one), its issue checks, and its pairwise win share. The regression
filter comes first, exactly as in chooser v1 (``NightShiftChooser.choose``), so only its survivors
are probed; the top score is the pick, a tie scored as the share of the tied that resolve.

**The chooser never sees whether a patch fixed the issue**: the dataset's ``patch``, ``test_patch``
and ``FAIL_TO_PASS`` are never read, only ``problem_statement``, ``repo`` and ``base_commit``; the
source comes from the repository at ``base_commit`` (GitHub's raw files, cached under
``--source-cache``), before any patch.

``--order N`` rotates the candidates' order in the prefix and shuffles the questions: temperature 0
and a logprob readout are deterministic, so two orders, not two seeds, show the variance.

Each task's result is appended to ``<out>/tasks.jsonl`` as it is made (an interrupted run resumes);
``<out>/summary.json`` has chooser v1's format, so ``scripts/swe_bench_best_of.py --chooser`` scores
it against a single run and the oracle, plus the wall seconds, probes and the prefix-cache hit rate
of each task (from the server's ``/metrics``: the delta of ``sglang:cached_tokens_total`` over that
of ``sglang:prompt_tokens_total``; the server must have no other client meanwhile).

Usage (the model server by node name, never an address)::

    .venv/bin/python scripts/swe_bench_chooser_fanout.py --server http://node-b.local:8000 \\
        --runs n13-default-1,n13-default-2,n13-default-3 --order 0 --out ~/chooser-fanout/n13-o0
    .venv/bin/python scripts/swe_bench_best_of.py --runs n13-default-1,n13-default-2,n13-default-3 \\
        --chooser ~/chooser-fanout/n13-o0/summary.json

A hosted judge through OpenRouter instead of the local server (`--openrouter MODEL --provider TAG`):
pinned to one provider (`allow_fallbacks` false, so the provider's prompt cache serves the prefix),
reasoning off, the key read from `--key-file` inside the process and never printed, the spend
summed from each answer's `usage.cost` and stopped at `--budget` dollars; the cache hit comes from
`usage.prompt_tokens_details.cached_tokens`.

Tests: ``tests/test_swe_bench_chooser_fanout.py`` (a fake client; no server).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Final, List, Optional, Sequence, Tuple

SCRIPTS: Final[Path] = Path(__file__).resolve().parent

STAGE: Final[str] = "fan-out"
MAX_ISSUE_CHARS: Final[int] = 20_000
MAX_PATCH_CHARS: Final[int] = 30_000
MAX_SOURCE_CHARS: Final[int] = 60_000
SOURCE_MARGIN: Final[int] = 40  # lines of base source kept on each side of an edited hunk
MAX_CHECKS: Final[int] = 5
TOP_LOGPROBS: Final[int] = 10
HUNK: Final[re.Pattern] = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
DIFF_FILE: Final[re.Pattern] = re.compile(r"^diff --git a/(\S+) b/(\S+)")

SYSTEM_PROMPT: Final[str] = (
    "You are an expert code reviewer. You are shown a GitHub issue in a Python repository, excerpts of "
    "the repository's source before any change, and several candidate patches (diffs) written to fix "
    "the issue. None of the patches has been run. Answer each question with exactly one word or number, "
    "as the question asks, and nothing else."
)

# (key, polarity, framings): +1 when "yes" is good for the candidate, -1 when it is bad.
ATTRIBUTE_PROBES: Final[Tuple[Tuple[str, int, Tuple[str, ...]], ...]] = (
    ("fixes", 1, ("Does candidate {n} fix the behaviour the issue reports?",
                  "After applying candidate {n}, would the problem described in the issue be gone?")),
    ("root_cause", 1, ("Does candidate {n} fix the root cause of the issue rather than a symptom?",)),
    ("edge_cases", 1, ("Does candidate {n} handle every case and input the issue mentions, including edge cases?",)),
    ("complete", 1, ("Does candidate {n} change every place in the code that must change for the fix?",)),
    ("unrelated", -1, ("Does candidate {n} change behaviour that the issue does not ask to change?",)),
    ("breaks", -1, ("Would candidate {n} make an existing test of the repository fail?",
                    "Could candidate {n} break code that works today?")),
    ("error", -1, ("Would the code candidate {n} changes raise an error when it runs, for example a NameError, "
                   "a wrong import or a wrong call signature?",)),
    ("tests_only", -1, ("Does candidate {n} change only tests or documentation, leaving the library code as it was?",)),
    ("merge", 1, ("Would the repository's maintainers merge candidate {n} as the fix for this issue?",
                  "Is candidate {n} a correct fix for the issue?")),
)
CHECK_PROBE: Final[str] = "Once candidate {n} is applied, is this true: {check}"
PAIRWISE_PROBES: Final[Tuple[str, ...]] = (
    "Which candidate fixes the issue correctly, {x} or {y}?",
    "Which candidate is more likely to pass the tests the maintainers wrote for this issue, {x} or {y}?",
    "Which candidate would the maintainers rather merge, {x} or {y}?",
)
YES_NO: Final[str] = "Answer Yes or No."
WHICH: Final[str] = "Answer with the candidate's number only."
CHECKS_QUESTION: Final[str] = (
    f"From the issue alone, list up to {MAX_CHECKS} concrete behaviours a correct fix must have: what a "
    "call, input or command must do once the issue is fixed. One per line, each starting with '- '. "
    "Do not mention the candidates. No other text."
)


def chooser_module() -> Any:
    """Chooser v1's test, beside this one: its run loading, task selection and scoring are reused."""
    name = "swe_bench_chooser_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "swe_bench_chooser_test.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # its dataclasses look their module up while they are built
    spec.loader.exec_module(module)
    return module


@dataclass
class Probe:
    """One question after the shared prefix, and what its answer means."""

    key: str  # unique within the task
    kind: str  # "attribute", "check" or "pairwise"
    text: str
    candidate: str = ""  # the candidate an attribute or check probe is about
    polarity: int = 1
    pair: Tuple[str, str] = ("", "")  # (x, y) of a pairwise probe, as candidate ids
    labels: Tuple[str, str] = ("", "")  # their numbers in the prefix
    answer: Optional[float] = None  # P(yes), or P(x) for a pairwise probe; None when unreadable


@dataclass
class Context:
    """One task's shared prefix: the candidates in prefix order, numbered from 1."""

    issue: str
    source: str
    order: List[Any]  # the candidates, in the order they appear
    numbers: Dict[str, str] = field(default_factory=dict)  # candidate id -> its number

    def __post_init__(self) -> None:
        """Numbers the candidates in order."""
        self.numbers = {c.id: str(position + 1) for position, c in enumerate(self.order)}


# --------------------------------------------------------------------------- the source context


def edited_ranges(patch: str) -> Dict[str, List[Tuple[int, int]]]:
    """
    The pre-image line ranges each file's hunks edit.

    Returns:
        Dict[str, List[Tuple[int, int]]]: Path to its (first, last) lines, 1-based; a new file
        (``--- /dev/null``) is left out, since it has no source before the patch.
    """
    ranges: Dict[str, List[Tuple[int, int]]] = {}
    path: Optional[str] = None
    for line in patch.splitlines():
        found = DIFF_FILE.match(line)
        if found:
            path = found.group(1)
            continue
        if line.startswith("--- ") and path is not None:
            if line.strip() == "--- /dev/null":
                path = None
            continue
        hunk = HUNK.match(line)
        if hunk and path is not None:
            start, length = int(hunk.group(1)), int(hunk.group(2) or 1)
            ranges.setdefault(path, []).append((max(1, start), max(start, start + length - 1)))
    return ranges


def is_test_path(path: str) -> bool:
    """A test file, by its path: its source matters less than the library's."""
    parts = path.lower().split("/")
    return any(p in ("tests", "test", "testing") for p in parts[:-1]) or parts[-1].startswith("test_")


def windows(ranges: Sequence[Tuple[int, int]], total: int, margin: int = SOURCE_MARGIN) -> List[Tuple[int, int]]:
    """The edited ranges widened by `margin` lines, clamped to the file and merged where they meet."""
    merged: List[Tuple[int, int]] = []
    for first, last in sorted((max(1, a - margin), min(total, b + margin)) for a, b in ranges):
        if first > total:
            continue
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def source_context(patches: Sequence[str], read: Callable[[str], Optional[str]],
                   limit: int = MAX_SOURCE_CHARS) -> str:
    """
    The base source around every hunk any candidate edits, numbered, library files first.

    Args:
        patches: The candidates' diffs.
        read: A path's text at the base commit, or None when it cannot be had.
        limit: The most characters kept; the excerpts past it are left out, saying so.

    Returns:
        str: One ``<file path=… lines=…>`` block per window, or "" when nothing could be read.
    """
    ranges: Dict[str, List[Tuple[int, int]]] = {}
    for patch in patches:
        for path, spans in edited_ranges(patch).items():
            ranges.setdefault(path, []).extend(spans)
    blocks: List[str] = []
    used, skipped = 0, 0
    for path in sorted(ranges, key=lambda p: (is_test_path(p), p)):
        text = read(path)
        if text is None:
            continue
        lines = text.splitlines()
        for first, last in windows(ranges[path], len(lines)):
            body = "\n".join(f"{n:>5} {lines[n - 1]}" for n in range(first, last + 1))
            block = f'<file path="{path}" lines="{first}-{last}">\n{body}\n</file>'
            if used + len(block) > limit:
                skipped += 1
                continue
            blocks.append(block)
            used += len(block)
    if skipped:
        blocks.append(f"[... {skipped} more excerpt(s) left out for length]")
    return "\n".join(blocks)


def github_reader(repo: str, commit: str, cache: Path, timeout: float = 30.0) -> Callable[[str], Optional[str]]:
    """
    A reader of the repository's files at `commit`, from GitHub's raw files, cached on disk.

    Returns:
        Callable[[str], Optional[str]]: Path to its text; None when GitHub has no such file.
    """
    def read(path: str) -> Optional[str]:
        local = cache / repo / commit / path
        if local.is_file():
            return local.read_text(errors="replace")
        missing = local.with_name(local.name + ".missing")
        if missing.exists():
            return None
        url = f"https://raw.githubusercontent.com/{repo}/{commit}/{path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                text = response.read().decode(errors="replace")
        except (urllib.error.URLError, OSError):
            local.parent.mkdir(parents=True, exist_ok=True)
            missing.touch()
            return None
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(text)
        return text
    return read


def load_rows(dataset: Path, ids: Sequence[str]) -> Dict[str, Dict[str, str]]:
    """
    The fields the chooser may know of each task: the issue, the repository and its base commit.

    The row's ``patch``, ``test_patch``, ``FAIL_TO_PASS`` and ``PASS_TO_PASS`` are never kept:
    they are what the choice is scored on.
    """
    wanted, rows = set(ids), {}
    for line in dataset.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("instance_id") in wanted:
            rows[row["instance_id"]] = {k: str(row.get(k) or "") for k in ("problem_statement", "repo", "base_commit")}
    missing = sorted(wanted - set(rows))
    if missing:
        raise SystemExit(f"❌ Not in {dataset}: {', '.join(missing)}")
    return rows


# --------------------------------------------------------------------------- prompts


def clip(text: str, limit: int) -> str:
    """Cuts a text at `limit` characters, saying so."""
    return text if len(text) <= limit else text[:limit] + f"\n[... cut: {len(text) - limit} more characters]"


def shared_prefix(context: Context) -> str:
    """The user message's text up to the question: identical for every probe of the task."""
    parts = [f"<issue>\n{clip(context.issue, MAX_ISSUE_CHARS)}\n</issue>"]
    if context.source:
        parts.append(f"<source note=\"the repository before any patch\">\n{context.source}\n</source>")
    for candidate in context.order:
        number = context.numbers[candidate.id]
        parts.append(f"<candidate_{number}>\n{clip(candidate.patch, MAX_PATCH_CHARS)}\n</candidate_{number}>")
    return "\n\n".join(parts) + "\n\nQuestion: "


def messages(context: Context, question: str) -> List[Dict[str, str]]:
    """One request's messages: the system prompt and the shared prefix, then the question."""
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": shared_prefix(context) + question}]


def parse_checks(answer: str) -> List[str]:
    """The behaviours the checks question listed: lines starting with '- ', at most MAX_CHECKS."""
    found = [line.strip()[2:].strip() for line in answer.splitlines() if line.strip().startswith("- ")]
    return [c for c in found if c][:MAX_CHECKS]


def build_probes(context: Context, checks: Sequence[str], rng: random.Random) -> List[Probe]:
    """
    Every probe of a task, shuffled by `rng`.

    Args:
        context: The task's shared prefix.
        checks: The behaviours the issue asks for (``parse_checks``).
        rng: Shuffles the questions (the prompt order).

    Returns:
        List[Probe]: The attribute and check probes of each candidate, and the pairwise probes of
        each pair in both orders.
    """
    probes: List[Probe] = []
    for candidate in context.order:
        n = context.numbers[candidate.id]
        for key, polarity, framings in ATTRIBUTE_PROBES:
            for index, framing in enumerate(framings):
                probes.append(Probe(key=f"{key}.{index}:{candidate.id}", kind="attribute", candidate=candidate.id,
                                    polarity=polarity, text=f"{framing.format(n=n)} {YES_NO}"))
        for index, check in enumerate(checks):
            probes.append(Probe(key=f"check.{index}:{candidate.id}", kind="check", candidate=candidate.id,
                                text=f"{CHECK_PROBE.format(n=n, check=check)} {YES_NO}"))
    for x in context.order:
        for y in context.order:
            if x.id == y.id:
                continue
            nx, ny = context.numbers[x.id], context.numbers[y.id]
            for index, framing in enumerate(PAIRWISE_PROBES):
                probes.append(Probe(key=f"pair.{index}:{x.id}>{y.id}", kind="pairwise", pair=(x.id, y.id),
                                    labels=(nx, ny), text=f"{framing.format(x=nx, y=ny)} {WHICH}"))
    rng.shuffle(probes)
    return probes


# --------------------------------------------------------------------------- answers and scores


def token_mass(choice: Dict[str, Any], wanted: Callable[[str], Optional[str]]) -> Dict[str, float]:
    """
    The probability mass of the first answer token's alternatives, grouped by `wanted`.

    Args:
        choice: One choice of a chat completion with logprobs.
        wanted: A token to its group (``"yes"``, ``"no"``, a number), or None to ignore it.

    Returns:
        Dict[str, float]: Group to the summed probability of its tokens among the top logprobs;
        empty when the answer carried no logprobs.
    """
    content = ((choice.get("logprobs") or {}).get("content") or [])
    if not content:
        return {}
    mass: Dict[str, float] = {}
    seen = set()
    for alternative in content[0].get("top_logprobs") or [content[0]]:
        token = str(alternative.get("token") or "")
        group = wanted(token)
        if group is None or token in seen:
            continue
        seen.add(token)
        mass[group] = mass.get(group, 0.0) + math.exp(float(alternative.get("logprob") or -1e9))
    return mass


def yes_no(token: str) -> Optional[str]:
    """`yes` or `no` for a token that says so, in any case or spacing; None otherwise."""
    word = token.strip().lower()
    return word if word in ("yes", "no") else None


def answer_of(probe: Probe, choice: Dict[str, Any]) -> Optional[float]:
    """
    A probe's answer: P(yes) for a yes/no probe, P(x) for a pairwise one, normalised over the two
    readings; the sampled token as a vote (1.0 or 0.0) when the logprobs name neither; None when
    the answer is neither.
    """
    if probe.kind == "pairwise":
        x, y = probe.labels
        def group(token: str) -> Optional[str]:
            word = token.strip()
            return word if word in (x, y) else None
        good, bad = x, y
    else:
        group, good, bad = yes_no, "yes", "no"
    mass = token_mass(choice, group)
    total = mass.get(good, 0.0) + mass.get(bad, 0.0)
    if total > 0:
        return mass.get(good, 0.0) / total
    words = str((choice.get("message") or {}).get("content") or "").split()
    said = group(words[0].rstrip(".")) if words else None
    return None if said is None else float(said == good)


def candidate_scores(ids: Sequence[str], probes: Sequence[Probe]) -> Dict[str, Dict[str, Optional[float]]]:
    """
    Each candidate's three parts and their mean.

    Returns:
        Dict[str, Dict[str, Optional[float]]]: Candidate to ``attribute`` (the mean of its attribute
        probes, a negative one counted as 1 - P(yes)), ``check`` (the mean of its issue checks),
        ``pairwise`` (the mean P of winning over the probes it is in, as x or as y) and ``total``
        (the mean of the parts it has; None when it has none). Unreadable probes are left out.
    """
    parts: Dict[str, Dict[str, List[float]]] = {i: {"attribute": [], "check": [], "pairwise": []} for i in ids}
    for probe in probes:
        if probe.answer is None:
            continue
        if probe.kind == "pairwise":
            x, y = probe.pair
            if x in parts and y in parts:
                parts[x]["pairwise"].append(probe.answer)
                parts[y]["pairwise"].append(1.0 - probe.answer)
        elif probe.candidate in parts:
            value = probe.answer if probe.polarity > 0 else 1.0 - probe.answer
            parts[probe.candidate][probe.kind].append(value)
    scores: Dict[str, Dict[str, Optional[float]]] = {}
    for i, found in parts.items():
        means = {k: (sum(v) / len(v) if v else None) for k, v in found.items()}
        present = [m for m in means.values() if m is not None]
        scores[i] = {**means, "total": sum(present) / len(present) if present else None}
    return scores


def top(scores: Dict[str, Dict[str, Optional[float]]], part: str = "total") -> List[str]:
    """The candidates with the best `part`, sorted; every candidate when none has it."""
    values = {i: s[part] for i, s in scores.items() if s.get(part) is not None}
    if not values:
        return sorted(scores)
    best = max(values.values())
    return sorted(i for i, v in values.items() if abs(v - best) < 1e-12)


def choose(candidates: Sequence[Any], judge: Callable[[List[Any]], Dict[str, Any]]) -> Dict[str, Any]:
    """
    The regression filter, as chooser v1 has it, then the fan-out judge over the survivors.

    Args:
        candidates: The task's candidates (``ChooserCandidate``).
        judge: The survivors to the judge's record (``scores`` at least).

    Returns:
        Dict[str, Any]: chooser v1's choice (`stage`, `survivors`, `points`, `chosen`), plus the
        judge's record under `fanout` when it ran.
    """
    if not candidates:
        return {"stage": "none", "survivors": [], "points": {}, "chosen": []}
    if len(candidates) == 1:
        return {"stage": "only candidate", "survivors": [candidates[0].id], "points": {}, "chosen": [candidates[0].id]}
    survivors = [c for c in candidates if not c.breaks]
    if len(survivors) == 1:
        return {"stage": "regression filter", "survivors": [survivors[0].id], "points": {},
                "chosen": [survivors[0].id]}
    pool = survivors or list(candidates)
    record = judge(pool)
    scores = record["scores"]
    return {"stage": STAGE if survivors else f"{STAGE}, all broke", "survivors": [c.id for c in survivors],
            "points": {i: s["total"] for i, s in scores.items()}, "chosen": top(scores), "fanout": record}


# --------------------------------------------------------------------------- the server


def cache_counters(text: str) -> Tuple[float, float]:
    """(cached prompt tokens, prompt tokens) summed over every label, from SGLang's /metrics text."""
    cached = prompt = 0.0
    for line in text.splitlines():
        name, _, value = line.rpartition(" ")
        try:
            number = float(value)
        except ValueError:
            continue
        if name.startswith("sglang:cached_tokens_total"):
            cached += number
        elif name.startswith("sglang:prompt_tokens_total"):
            prompt += number
    return cached, prompt


class Server:
    """The model server: one-token probes with logprobs, the checks question, and its counters."""

    def __init__(self, base: str, timeout: float = 300.0) -> None:
        """
        Args:
            base: The server's URL (``http://<node>.local:8000``), with or without ``/v1``.
            timeout: Seconds to wait for one answer.
        """
        self.root = base.rstrip("/").removesuffix("/v1")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.model = ""

    def get(self, path: str) -> bytes:
        """A GET's body."""
        with self.opener.open(self.root + path, timeout=self.timeout) as response:
            return response.read()

    def chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """One chat completion's first choice."""
        if not self.model:
            self.model = str(json.loads(self.get("/v1/models"))["data"][0]["id"])
        request = urllib.request.Request(self.root + "/v1/chat/completions",
                                         data=json.dumps({"model": self.model, **payload}).encode(),
                                         headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            return (json.loads(response.read()).get("choices") or [{}])[0]

    def counters(self) -> Optional[Tuple[float, float]]:
        """The prefix cache's counters; None when the server has no SGLang metrics."""
        try:
            found = cache_counters(self.get("/metrics").decode(errors="replace"))
        except (urllib.error.URLError, OSError, ValueError):
            return None
        return found if found[1] > 0 else None


OPENROUTER: Final[str] = "https://openrouter.ai/api/v1"
RETRIED: Final[Tuple[int, ...]] = (429, 500, 502, 503, 504)


class BudgetSpent(RuntimeError):
    """The hosted judge's spend reached `--budget`: the run stops; `tasks.jsonl` keeps what was done."""


def answer_start(choice: Dict[str, Any]) -> Dict[str, Any]:
    """
    The choice with its logprobs and content starting at the answer's first word: past a closing
    ``</think>`` and any markdown (``**Yes.**``), so a reasoning model's probe reads like a one-token one.
    """
    content = list(((choice.get("logprobs") or {}).get("content") or []))
    ends = [i for i, item in enumerate(content) if str(item.get("token") or "").strip() == "</think>"]
    if ends:
        content = content[ends[-1] + 1:]
    while content and not str(content[0].get("token") or "").strip(" \n*#_`>-"):
        content = content[1:]
    message = dict(choice.get("message") or {})
    message["content"] = str(message.get("content") or "").split("</think>")[-1].lstrip(" \n*#_`>-")
    return {**choice, "message": message, "logprobs": {"content": content} if content else None}


class OpenRouterServer:
    """A hosted model through OpenRouter, pinned to one provider: the same probes, the spend counted."""

    def __init__(self, model: str, provider: str, key_file: Path, budget: float, timeout: float = 300.0,
                 effort: Optional[str] = None, thinking_tokens: int = 512) -> None:
        """
        Args:
            model: OpenRouter's model id (``z-ai/glm-5.3``).
            provider: The one provider (endpoint tag) every request goes to.
            key_file: The API key's file; read here, kept in memory, never printed.
            budget: Dollars after which every request raises `BudgetSpent`.
            timeout: Seconds to wait for one answer.
            effort: Reasoning effort (``minimal``) for a model whose provider refuses to switch
                reasoning off (GLM 5.3); None switches it off.
            thinking_tokens: Tokens added to every request's ``max_tokens`` when reasoning is on.
        """
        self.model, self.provider, self.budget, self.timeout = model, provider, budget, timeout
        self.effort, self.thinking_tokens = effort, thinking_tokens
        self._key = key_file.expanduser().read_text().strip()
        self.cost = self.cached = self.prompt = 0.0
        self.requests = 0
        self.lock = threading.Lock()

    def body(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """The request's body: the local parameters, OpenRouter's in place of the chat template's."""
        body = {k: v for k, v in payload.items() if k != "chat_template_kwargs"}
        reasoning: Dict[str, Any] = {"enabled": False}
        if self.effort:
            reasoning = {"effort": self.effort, "exclude": True}
            body["max_tokens"] = int(body.get("max_tokens") or 0) + self.thinking_tokens
        return {"model": self.model, **body, "provider": {"order": [self.provider], "allow_fallbacks": False},
                "reasoning": reasoning, "usage": {"include": True}}

    def chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """One chat completion's first choice; retried on rate limits and gateway errors."""
        if self.cost >= self.budget:
            raise BudgetSpent(f"spent {self.cost:.4f} of {self.budget} USD")
        request = urllib.request.Request(OPENROUTER + "/chat/completions", data=json.dumps(self.body(payload)).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {self._key}"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    answer = json.loads(response.read())
                break
            except urllib.error.HTTPError as error:
                if error.code not in RETRIED or attempt == 3:
                    raise
                time.sleep(2 ** attempt)
            except OSError:  # a timeout or a dropped connection: a provider stalls now and then
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        usage = answer.get("usage") or {}
        with self.lock:
            self.requests += 1
            self.cost += float(usage.get("cost") or 0.0)
            self.prompt += float(usage.get("prompt_tokens") or 0)
            self.cached += float((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        return answer_start((answer.get("choices") or [{}])[0])

    def counters(self) -> Optional[Tuple[float, float]]:
        """(cached prompt tokens, prompt tokens) so far, as the provider reported them."""
        with self.lock:
            return (self.cached, self.prompt) if self.prompt else None


PROBE_PARAMS: Final[Dict[str, Any]] = {
    "max_tokens": 1, "temperature": 0.0, "logprobs": True, "top_logprobs": TOP_LOGPROBS,
    "chat_template_kwargs": {"enable_thinking": False},
}
CHECKS_PARAMS: Final[Dict[str, Any]] = {
    "max_tokens": 400, "temperature": 0.0, "chat_template_kwargs": {"enable_thinking": False},
}


def fan_out(server: Any, context: Context, rng: random.Random, workers: int) -> Dict[str, Any]:
    """
    One task's fan-out: the checks question first (it also puts the prefix in the cache), then
    every probe at once.

    Returns:
        Dict[str, Any]: `scores` (``candidate_scores``), `checks`, `answers` (probe key to answer),
        `probes`, `unreadable`, `seconds`, and `cache_hit` (None without counters).
    """
    before = server.counters()
    started = time.monotonic()
    try:
        checks_answer = str((server.chat({**CHECKS_PARAMS, "messages": messages(context, CHECKS_QUESTION)})
                             .get("message") or {}).get("content") or "")
    except (urllib.error.URLError, OSError, ValueError):
        checks_answer = ""
    checks = parse_checks(checks_answer)
    checks_seconds = time.monotonic() - started
    probes = build_probes(context, checks, rng)

    def ask(probe: Probe) -> None:
        try:
            probe.answer = answer_of(probe, server.chat({**PROBE_PARAMS, "messages": messages(context, probe.text)}))
        except (urllib.error.URLError, OSError, ValueError):
            probe.answer = None

    if probes:
        ask(probes[0])  # alone: the server keeps the state at the point the questions branch off
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(ask, probes[1:]))
    seconds = time.monotonic() - started
    after = server.counters()
    hit = None
    if before and after and after[1] > before[1]:
        hit = (after[0] - before[0]) / (after[1] - before[1])
    return {"scores": candidate_scores([c.id for c in context.order], probes), "checks": checks,
            "answers": {p.key: p.answer for p in probes}, "probes": len(probes) + 1,
            "unreadable": sum(1 for p in probes if p.answer is None), "seconds": round(seconds, 2),
            "checks_seconds": round(checks_seconds, 2),
            "cache_hit": None if hit is None else round(hit, 4)}


def rotate(items: Sequence[Any], by: int) -> List[Any]:
    """`items` rotated left by `by` places."""
    if not items:
        return []
    by %= len(items)
    return list(items[by:]) + list(items[:by])


# --------------------------------------------------------------------------- main


def run(args: argparse.Namespace, server_factory: Callable[[str, float], Any] = Server,
        reader_factory: Callable[[str, str, Path], Callable[[str], Optional[str]]] = github_reader) -> Dict[str, Any]:
    """The test, as `main` runs it; the server and the source reader are replaceable for the tests."""
    chooser = chooser_module()
    runs = [chooser.load_run(args.runs_dir, name) for name in args.runs.split(",") if name]
    if len(runs) < 2:
        raise SystemExit("❌ Name at least two runs")
    tasks = chooser.select_tasks(runs, args.all)
    if args.limit:
        tasks = tasks[:args.limit]
    judged = [t for t in tasks if len([c for c in t.candidates if not c.breaks] or t.candidates) > 1]
    print(f"{len(tasks)} task(s) from {', '.join(r['name'] for r in runs)}; {len(judged)} go to the fan-out judge")
    if args.dry_run:
        return {}
    if args.openrouter and not args.provider:
        raise SystemExit("❌ --provider is required with --openrouter (one provider, no fallbacks)")
    if not args.server and not args.openrouter:
        raise SystemExit("❌ --server is required (the model server's URL, by node name)")
    rows = load_rows(args.dataset, [t.instance_id for t in judged])
    if args.openrouter:
        server: Any = OpenRouterServer(args.openrouter, args.provider, args.key_file, args.budget, args.timeout,
                                       args.effort, args.thinking_tokens)
        args.server = f"openrouter {args.openrouter} via {args.provider}"
    else:
        server = server_factory(args.server, args.timeout)
    args.out.mkdir(parents=True, exist_ok=True)
    log = args.out / "tasks.jsonl"
    done: Dict[str, Dict[str, Any]] = {}
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            done[record["instance_id"]] = record["choice"]

    choices, scores = {}, {}
    for task in tasks:
        if task.instance_id in done:
            choice = done[task.instance_id]
        else:
            def judge(pool: List[Any], task: Any = task) -> Dict[str, Any]:
                row = rows[task.instance_id]
                read = reader_factory(row["repo"], row["base_commit"], args.source_cache)
                order = rotate(pool, args.order)
                context = Context(issue=row["problem_statement"], order=order,
                                  source=source_context([c.patch for c in order], read))
                spent = getattr(server, "cost", None)
                record = fan_out(server, context, random.Random(f"{args.order}:{task.instance_id}"), args.workers)
                if spent is not None:
                    record["cost"] = round(server.cost - spent, 6)
                record["numbers"] = context.numbers
                record["prefix_chars"] = len(shared_prefix(context))
                return record
            choice = choose(task.candidates, judge)
            with open(log, "a") as handle:
                handle.write(json.dumps({"instance_id": task.instance_id, "choice": choice}) + "\n")
        choices[task.instance_id], scores[task.instance_id] = choice, chooser.score(task, choice)
        fanout = choice.get("fanout") or {}
        timing = (f", {fanout['probes']} probes in {fanout['seconds']}s, cache hit {fanout['cache_hit']}"
                  if fanout else "")
        print(f"  {task.instance_id:<34} {choice['stage']}: chose {', '.join(choice['chosen']) or 'nothing'}, "
              f"credit {scores[task.instance_id]['chooser']:.2f}{timing}", flush=True)

    summary = chooser.summarise(runs, tasks, scores, choices)
    fanned = [c["fanout"] for c in choices.values() if c.get("fanout")]
    hits = [f["cache_hit"] for f in fanned if f.get("cache_hit") is not None]
    summary.update({
        "chooser_name": "v3 fan-out", "order": args.order, "server": args.server, "model": getattr(server, "model", ""),
        "per_task": {i: {**choices[i], **scores[i]} for i in choices},
        "fanout": {"tasks": len(fanned), "probes": sum(f["probes"] for f in fanned),
                   "unreadable": sum(f["unreadable"] for f in fanned),
                   "seconds": round(sum(f["seconds"] for f in fanned), 1),
                   "seconds_per_task": round(sum(f["seconds"] for f in fanned) / len(fanned), 2) if fanned else None,
                   "cache_hit_mean": round(sum(hits) / len(hits), 4) if hits else None,
                   "cache_hit_min": min(hits) if hits else None,
                   "cost_usd": round(sum(f.get("cost") or 0.0 for f in fanned), 4) if args.openrouter else None},
        "parts": part_credits(tasks, choices),
    })
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    f = summary["fanout"]
    print(f"\nOn the {summary['tasks']} task(s): fan-out {summary['chooser']}, the regression filter alone "
          f"{summary['filter_alone']}, a random run {summary['random_run']}, any run (oracle) {summary['oracle']}")
    print(f"Fan-out: {f['tasks']} task(s), {f['probes']} probes ({f['unreadable']} unreadable), "
          f"{f['seconds_per_task']}s a task, prefix-cache hit {f['cache_hit_mean']} (min {f['cache_hit_min']})")
    print("By one part alone: " + ", ".join(f"{k} {v}" for k, v in summary["parts"].items()))
    print(f"On all {summary['listed']}: best-of-{len(runs)} with this chooser {summary['projected']}")
    return summary


def part_credits(tasks: Sequence[Any], choices: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    """
    The credit had each part chosen alone (an ablation, reported beside the total, never tuned on).

    Returns:
        Dict[str, float]: Part to its summed credit over the tasks the fan-out judged.
    """
    credits: Dict[str, float] = {}
    for task in tasks:
        record = (choices.get(task.instance_id) or {}).get("fanout")
        if not record:
            continue
        for part in ("attribute", "check", "pairwise"):
            chosen = top(record["scores"], part)
            share = sum(1 for i in chosen if task.resolves.get(i)) / len(chosen) if chosen else 0.0
            credits[part] = round(credits.get(part, 0.0) + share, 2)
    return credits


def parser() -> argparse.ArgumentParser:
    """The command line."""
    chooser = chooser_module()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--server", help="the model server's URL, by node name (http://node-b.local:8000)")
    p.add_argument("--runs", required=True, help="comma-separated run names, the single run first")
    p.add_argument("--runs-dir", type=Path, default=chooser.DEFAULT_RUNS_DIR, help="where the runs are (read only)")
    p.add_argument("--dataset", type=Path, default=chooser.DEFAULT_DATASET, help="the local dataset snapshot")
    p.add_argument("--source-cache", type=Path,
                   default=Path.home() / ".cache" / "dreamference" / "swe-bench" / "source-at-base",
                   help="where the repositories' files at the base commit are cached")
    p.add_argument("--out", type=Path, default=Path("swe-bench-chooser-fanout"), help="where results go")
    p.add_argument("--order", type=int, default=0, help="rotates the candidates in the prefix and shuffles the questions")
    p.add_argument("--workers", type=int, default=16, help="concurrent probes")
    p.add_argument("--timeout", type=float, default=300.0, help="seconds to wait for one answer")
    p.add_argument("--openrouter", help="judge with this OpenRouter model instead of --server")
    p.add_argument("--provider", help="the one OpenRouter provider (endpoint tag) to use, no fallbacks")
    p.add_argument("--key-file", type=Path, default=Path("~/.config/dreamference/openrouter-key"),
                   help="OpenRouter's API key (read in the process, never printed)")
    p.add_argument("--effort", help="reasoning effort for a hosted model that cannot switch it off (minimal)")
    p.add_argument("--thinking-tokens", type=int, default=512, help="max_tokens added when --effort is set")
    p.add_argument("--budget", type=float, default=1.0, help="dollars after which the hosted judge stops")
    p.add_argument("--all", action="store_true", help="judge every task, not only those where the runs disagree")
    p.add_argument("--limit", type=int, help="judge only the first N tasks")
    p.add_argument("--dry-run", action="store_true", help="list the tasks; contact no server")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Runs the test."""
    run(parser().parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
