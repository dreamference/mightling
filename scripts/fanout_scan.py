#!/usr/bin/env python3
"""Searches one source file for problems by fan-out: many short questions over one shared prefix.

The file, numbered, is the shared context: the system text and the file are byte-identical in
every request, so the model server (SGLang's radix cache) prefills them once and every question
after the first costs only its own few tokens. Round 1 asks one problem class at a time, and for
a long file one class times one region (a function, a class or a window of about 80 lines); each
answer is `no` or `yes L<line> <a few words>`. Round 2 asks of each candidate, in several framings,
whether it is real and how severe, and keeps the candidates a majority confirms. `--baseline`
instead asks one plain "list every problem in this file" over the same prefix.

    fanout_scan.py FILE [--repo ROOT] [--host URL] [--concurrency N] [--baseline] [--no-confirm]

Findings are printed as JSON lines; the last line is a `{"summary": ...}` with the probe count,
the wall time and the prefix-cache hit rate read from the server's `/metrics` (server-wide, so
another client's load shows in it). The host is `--host`, else `DREAMFERENCE_VLLM_HOST`, else
http://127.0.0.1:8000. Nothing is written; the server is never started or stopped.
"""

import argparse
import ast
import json
import os
import pathlib
import re
import sys
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Final

DEFAULT_HOST: Final[str] = "http://127.0.0.1:8000"
REGION_LINES: Final[int] = 80
LONG_REGION: Final[int] = 120
SHORT_FILE: Final[int] = 120
MAX_REGIONS: Final[int] = 12
MAX_CANDIDATES: Final[int] = 25
PROBE_TOKENS: Final[int] = 24
BASELINE_TOKENS: Final[int] = 1500
DEDUPE_LINES: Final[int] = 3

SYSTEM: Final[str] = (
    "You review one source file of a Python and Rust project, the Mightling agent and its admin tool. "
    "Each question asks about one kind of problem, sometimes in one range of lines only. This is the "
    "first pass of two: every report is checked again later, so report the most likely place of the "
    "problem when the code plausibly has it, and answer `no` only when it clearly does not. Style, naming "
    "and missing comments are not problems. Unless a question asks for another format, answer with "
    "exactly `no`, or with `yes L<line> <at most ten words>` naming the one line where the problem is. "
    "No other text."
)

# The project's hard rules (AGENTS.md) that a single file can break.
PROJECT_RULES: Final[str] = (
    "Project rules: tests never touch the real machine (real HOME, real docker changes, the installed "
    "`ling`, the model server ports 8000/18000, sudo, /etc); `ling` is never resolved from a `codex` on "
    "PATH; nothing writes the user's choices into the repository; a path under the source checkout needs "
    "a `has_source()` guard; unattended callers of `ling` set DREAMFERENCE_VLLM_HOST."
)


@dataclass(frozen=True)
class ProblemClass:
    """One question of the checklist: what to look for, in which language, and when it applies."""

    key: str
    question: str
    langs: tuple[str, ...] = ("py", "rs")
    trigger: str = ""
    tests_only: bool = False


CHECKLIST: Final[tuple[ProblemClass, ...]] = (
    ProblemClass(
        "unhandled-error",
        "an error path that is not handled: a call that can raise or fail "
        "(I/O, parsing, network, subprocess, a missing key) whose failure crashes or escapes "
        "where the caller expects a handled result",
    ),
    ProblemClass(
        "swallowed-error",
        "an error that is caught and silently ignored or turned into a "
        "wrong success, hiding a real failure",
    ),
    ProblemClass(
        "wrong-exception",
        "an except clause that catches the wrong exception type, too narrow "
        "to catch what the call raises or so broad it hides bugs",
        ("py",),
        r"\bexcept\b",
    ),
    ProblemClass(
        "resource-leak",
        "a resource (file, socket, HTTP response, process, temporary "
        "directory, lock) opened and not closed or released on every path",
    ),
    ProblemClass(
        "off-by-one", "an off-by-one error in a range, slice, index, length or boundary comparison"
    ),
    ProblemClass(
        "wrong-comparison",
        "a wrong comparison or condition: inverted logic, `and` for `or`, "
        "`<` for `<=`, `is` for `==`, or a check that is always true or always false",
    ),
    ProblemClass(
        "none-misuse",
        "a value that can be None, empty or missing (Optional, dict.get, a "
        "regex match, an environment variable) used as if it were always present",
        ("py",),
    ),
    ProblemClass(
        "unwrap-panic",
        "an unwrap, expect, index or slice that can panic on input the program does not control",
        ("rs",),
    ),
    ProblemClass(
        "file-race",
        "a race on the file system: check-then-use, a file written in place "
        "that a reader can see half-written, or a shared fixed temporary path",
        trigger=r"open|write|rename|exists|mkdir|tmp|temp|fs::|File",
    ),
    ProblemClass(
        "thread-race",
        "a race between threads or processes on shared state without a lock, "
        "or a lock held across a blocking call",
        trigger=r"thread|Thread|Lock|lock\(|Mutex|async|await|spawn|Arc<",
    ),
    ProblemClass(
        "subprocess-misuse",
        "a misused subprocess or command: shell=True or a shell string with "
        "untrusted input, an unchecked exit status, no timeout on a call that can hang, or output "
        "decoded wrongly",
        trigger=r"subprocess|Popen|check_output|Command::new|os\.system",
    ),
    ProblemClass(
        "path-handling",
        "wrong path handling: string concatenation of paths, a relative path "
        "that depends on the working directory, `~` not expanded, a path that can escape its "
        "directory, or a symlink followed where it must not be",
        trigger=r"path|Path|dir|\.join|/",
    ),
    ProblemClass(
        "stale-cache",
        "a cache, memo or saved state that is not invalidated when what it "
        "depends on changes, so stale data is used",
        trigger=r"cache|Cache|memo|lru_cache|stamp|_saved|OnceCell|lazy",
    ),
    ProblemClass(
        "wrong-units",
        "mixed or wrong units: seconds and milliseconds, bytes and KiB/MiB/GiB, "
        "tokens and characters, 1000 and 1024",
        trigger=r"time|sec|ms|byte|[KMG]i?B|1024|1000|timeout|size|len",
    ),
    ProblemClass(
        "timeout-missing",
        "a network call, wait or loop with no timeout or bound, which can hang forever",
        trigger=r"urlopen|requests|socket|connect|wait|while|loop|recv|read\(",
    ),
    ProblemClass(
        "secret-leak",
        "a secret (token, password, key, cookie, credential) printed, logged, "
        "put into an error message, a command line or a world-readable file",
        trigger=r"token|secret|password|passwd|key|credential|cookie|auth",
    ),
    ProblemClass(
        "permissions",
        "a file or directory holding something private created with default or "
        "too-wide permissions, or a permission check that can be bypassed",
        trigger=r"chmod|mode|0o|umask|permission|private|secret|key",
    ),
    ProblemClass(
        "injection",
        "untrusted text put unescaped into a shell command, SQL, a regex, HTML, "
        "TOML/JSON built by hand, or a command-line flag",
        trigger=r"f\"|format!|\.format\(|%s|sql|SQL|re\.|Regex|html|toml",
    ),
    ProblemClass(
        "encoding",
        "text decoded or encoded wrongly: missing encoding, bytes treated as str, "
        "invalid UTF-8 crashing, or a byte length used as a character length",
        trigger=r"decode|encode|utf|bytes|from_utf8|as_bytes|read_text|open\(",
    ),
    ProblemClass(
        "parse-fragile",
        "fragile parsing of text or output: a split or index that breaks on "
        "an unexpected line, a regex that misses valid input, or a format assumed without checking",
        trigger=r"split|re\.|Regex|parse|strip|lines|json|toml",
    ),
    ProblemClass(
        "mutable-default",
        "a mutable default argument or a shared mutable class attribute changed per instance",
        ("py",),
        r"=\s*(\[\]|\{\}|set\(\))",
    ),
    ProblemClass(
        "wrong-return",
        "a function that returns the wrong value or type on some path, falls "
        "off the end returning None, or whose result is ignored by the caller here",
    ),
    ProblemClass(
        "dead-branch",
        "a branch that can never run, or code after a return, or a condition "
        "that makes a later check unreachable",
    ),
    ProblemClass(
        "state-order",
        "an operation done in the wrong order: state used before it is set, "
        "cleanup before use, a write before its check, or a step skipped on one path",
    ),
    ProblemClass(
        "partial-failure",
        "a multi-step change that leaves things half done when a middle "
        "step fails, with no rollback or cleanup",
    ),
    ProblemClass(
        "loop-bug",
        "a loop bug: modifying a collection while iterating it, a loop variable "
        "captured late, a break or continue in the wrong place, or an accumulator reset wrongly",
        trigger=r"\bfor\b|\bwhile\b|\bloop\b",
    ),
    ProblemClass(
        "wrong-variable",
        "the wrong variable, field or argument used, such as a copy-paste "
        "slip that uses one name where a similar one was meant",
    ),
    ProblemClass(
        "type-confusion",
        "a type confusion: str and bytes, str and Path, int and str, list "
        "and tuple, a JSON value of an unchecked type",
        ("py",),
    ),
    ProblemClass(
        "integer-overflow",
        "an integer overflow, underflow or lossy cast (`as`), or a "
        "subtraction of unsigned values that can wrap",
        ("rs",),
        r"\bas\b|u32|u64|usize|i32|-",
    ),
    ProblemClass(
        "env-dependence",
        "behaviour that silently depends on the environment: the current "
        "directory, the locale, the user's HOME or PATH, the time zone, or a variable not checked",
        trigger=r"environ|env::|getenv|HOME|PATH|cwd|getcwd|locale|time",
    ),
    ProblemClass(
        "time-bug",
        "a time bug: local time where UTC is needed, a wall clock used for a "
        "duration, a clock change not handled, or a naive datetime compared with an aware one",
        trigger=r"time|datetime|Instant|SystemTime|clock|date",
    ),
    ProblemClass(
        "signal-cleanup",
        "a child process, container, thread or temporary file left behind "
        "when the program is interrupted or fails",
        trigger=r"Popen|spawn|docker|thread|tmp|temp",
    ),
    ProblemClass(
        "docker-misuse",
        "a docker call that changes the host unsafely, leaves a container "
        "running, or names an image or container that can clash",
        trigger=r"docker",
    ),
    ProblemClass(
        "json-schema",
        "a JSON, TOML or API reply field read without checking that it exists "
        "or has the expected type",
        trigger=r"json|toml|\[\"|\.get\(|serde|Value",
    ),
    ProblemClass(
        "http-status",
        "an HTTP status or error reply not checked, or a redirect or partial "
        "body treated as success",
        trigger=r"http|urlopen|requests|reqwest|status",
    ),
    ProblemClass(
        "float-compare",
        "a float compared for equality, or an integer division where a fractional result is needed",
        trigger=r"float|f64|f32|/|\.0\b",
    ),
    ProblemClass(
        "sort-key",
        "a sort, max, min or dedupe on the wrong key, or one that depends on "
        "dictionary or hash order where a stable order is needed",
        trigger=r"sort|max\(|min\(|set\(|HashMap|HashSet|dedup",
    ),
    ProblemClass(
        "api-misuse",
        "a library or standard API used wrongly: wrong argument order, a flag "
        "that does not do what the code expects, or a deprecated call with different behaviour",
    ),
    ProblemClass(
        "config-default",
        "a configuration value or default read from the wrong place, or a "
        "user's setting overwritten or ignored",
        trigger=r"config|Config|toml|setting|default",
    ),
    ProblemClass(
        "doc-mismatch",
        "a docstring or comment that states behaviour the code does not have, "
        "where a caller relying on it would be misled",
    ),
    ProblemClass(
        "test-real-machine",
        "a test that touches the real machine: the real HOME, the real "
        "docker, network, the installed binary, a model server port, sudo or a system path",
        tests_only=True,
    ),
    ProblemClass(
        "test-weak-assert",
        "a test whose assertion cannot fail, checks the wrong thing, or "
        "passes without exercising the code it names",
        tests_only=True,
    ),
    ProblemClass("project-rule", "a break of one of the project rules below. " + PROJECT_RULES),
)

# The framings of round 2: each asks whether a candidate is real, in its own words.
CONFIRM_FRAMINGS: Final[tuple[str, ...]] = (
    "Candidate problem at line {line}: {text} (class: {kind}). Think of one concrete input or situation "
    "in which line {line} does the wrong thing. Write it in one short sentence, then end with `verdict: "
    "real high`, `verdict: real medium`, `verdict: real low`, or `verdict: no` if no such situation exists.",
    "A reviewer claims line {line} has a bug: {text}. Check the claim against the code, including what "
    "the surrounding code already handles. In one short sentence say whether the claim holds, then end "
    "with `verdict: real` and a severity (high, medium or low), or `verdict: no`.",
    "Would a careful maintainer accept the bug report `L{line}: {text}` and change the code? In one short "
    "sentence say why, then end with `verdict: real high`, `verdict: real medium`, `verdict: real low`, or "
    "`verdict: no` if they would close it as not a bug.",
)
CONFIRM_TOKENS: Final[int] = 80

BASELINE_QUESTION: Final[str] = (
    "List every problem in this file: bugs, unhandled errors, leaks, races and breaks of the project "
    "rules. Write one problem per line as `L<line> <short description>`, and nothing else. If there are "
    "none, answer `none`."
)

ANSWER_RE: Final[re.Pattern[str]] = re.compile(
    r"^\W*yes\W+L?(\d+)\W*(.*)$", re.IGNORECASE | re.DOTALL
)
BASELINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\W*L(?:ines?\s*)?(\d+)(?:\s*[-–]\s*L?\d+)?\W+(.+)$"
)
VERDICT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(real|no|not)\b\W*(high|medium|low)?", re.IGNORECASE
)


@dataclass(frozen=True)
class Region:
    """A range of lines asked about on its own, 1-based and inclusive."""

    start: int
    end: int
    name: str


@dataclass
class Finding:
    """One candidate problem, with the votes of round 2 once it has had them."""

    line: int
    text: str
    kind: str
    region: str
    votes: list[str] = field(default_factory=list)
    severity: str = ""
    confirmed: bool | None = None


def language_of(path: str) -> str:
    """Returns `py`, `rs` or `` for a path, by its extension."""
    return {".py": "py", ".rs": "rs"}.get(os.path.splitext(path)[1], "")


def is_test_file(path: str) -> bool:
    """Tells whether a path is a test: under a tests directory, or named test_*.py or *_test(s).rs."""
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    return (
        "tests" in parts[:-1]
        or name.startswith("test_")
        or name.endswith(("_test.rs", "_tests.rs", "tests.rs"))
    )


def number_lines(source: str) -> str:
    """Returns the source with each line prefixed by its 1-based number, as the model reads it."""
    return "\n".join(f"{index}| {line}" for index, line in enumerate(source.splitlines(), 1))


def windows(start: int, end: int, name: str, size: int = REGION_LINES) -> list[Region]:
    """Cuts a range into windows of about `size` lines; a short remainder joins the last window."""
    regions, at = [], start
    while at <= end:
        stop = min(end, at + size - 1)
        if end - stop < size // 3:
            stop = end
        regions.append(Region(at, stop, name if stop == end and at == start else f"{name}@{at}"))
        at = stop + 1
    return regions


def python_items(source: str) -> list[Region]:
    """The top-level functions and classes of Python source, a long class split into its methods."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    items = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        end = node.end_lineno or node.lineno
        methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if isinstance(node, ast.ClassDef) and end - start + 1 > LONG_REGION and methods:
            for method in methods:
                first = min([method.lineno] + [d.lineno for d in method.decorator_list])
                items.append(
                    Region(first, method.end_lineno or method.lineno, f"{node.name}.{method.name}")
                )
        else:
            items.append(Region(start, end, node.name))
    return items


RUST_ITEM_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+|const\s+)*(fn|impl|mod|struct|enum|trait)\b\s*([^\s<({;]*)"
)


def rust_items(source: str) -> list[Region]:
    """The top-level items of Rust source, found by brace depth; an `impl` is split into its fns."""
    lines = source.splitlines()
    items, depth, open_item = [], 0, None
    for index, line in enumerate(lines, 1):
        code = re.sub(r"//.*$", "", re.sub(r'"(?:\\.|[^"\\])*"', '""', line))
        match = RUST_ITEM_RE.match(code)
        if open_item is None and depth == 0 and match:
            open_item = (index, f"{match.group(1)} {match.group(2)}".strip())
        elif open_item is not None and depth == 1 and match and match.group(1) == "fn":
            items.append(("inner", index, match.group(2), open_item[1]))
        depth += code.count("{") - code.count("}")
        if open_item is not None and depth <= 0 and ("}" in code or code.rstrip().endswith(";")):
            items.append(("item", open_item[0], index, open_item[1]))
            open_item, depth = None, max(depth, 0)
    regions: list[Region] = []
    inner = [i for i in items if i[0] == "inner"]
    for kind, start, end, name in items:
        if kind != "item":
            continue
        fns = [i for i in inner if start < i[1] <= end]
        if end - start + 1 > LONG_REGION and fns:
            starts = [start] + [f[1] for f in fns[1:]]
            for position, first in enumerate(starts):
                last = starts[position + 1] - 1 if position + 1 < len(starts) else end
                regions.append(Region(first, last, f"{name}::{fns[position][2]}"))
        else:
            regions.append(Region(start, end, name))
    return regions


def split_regions(source: str, lang: str, max_regions: int = MAX_REGIONS) -> list[Region]:
    """Splits a file into the regions round 1 asks about one at a time.

    A short file is one region. A long one is cut along its functions and classes (Python, by the
    AST) or items (Rust, by brace depth): small neighbours are merged up to about 80 lines, a long
    item is cut into windows, and lines between items join the region before them. When there are
    more than `max_regions`, neighbours are merged until there are not.

    Args:
        source: The file's text.
        lang: `py` or `rs`; anything else is cut into windows only.
        max_regions: The most regions to return.

    Returns:
        Regions that cover every line of the file once, in order.
    """
    total = len(source.splitlines())
    if total == 0:
        return []
    if total <= SHORT_FILE:
        return [Region(1, total, "file")]
    items = python_items(source) if lang == "py" else rust_items(source) if lang == "rs" else []
    pieces: list[Region] = []
    for item in items:
        pieces.extend(
            windows(item.start, item.end, item.name)
            if item.end - item.start + 1 > LONG_REGION
            else [item]
        )
    covered: list[Region] = []
    at = 1
    for piece in sorted(pieces, key=lambda r: r.start):
        if piece.start < at:
            continue
        if piece.start > at:
            covered.extend(windows(at, piece.start - 1, "lines"))
        covered.append(piece)
        at = piece.end + 1
    if at <= total:
        covered.extend(windows(at, total, "lines"))
    merged: list[Region] = []
    for region in covered:
        last = merged[-1] if merged else None
        if last and region.end - last.start + 1 <= REGION_LINES:
            merged[-1] = Region(last.start, region.end, f"{last.name}+{region.name}")
        else:
            merged.append(region)
    while len(merged) > max_regions:
        sizes = [merged[i + 1].end - merged[i].start for i in range(len(merged) - 1)]
        i = sizes.index(min(sizes))
        merged[i : i + 2] = [
            Region(merged[i].start, merged[i + 1].end, f"{merged[i].name}+{merged[i + 1].name}")
        ]
    return merged


def classes_for(path: str, source: str, region_text: str | None = None) -> list[ProblemClass]:
    """The checklist's classes that apply to a file, or to one region of it when given its text."""
    lang, test = language_of(path), is_test_file(path)
    text = source if region_text is None else region_text
    return [
        c
        for c in CHECKLIST
        if lang in c.langs
        and (test or not c.tests_only)
        and (not c.trigger or re.search(c.trigger, text))
    ]


def probe_question(kind: ProblemClass, region: Region, whole: bool) -> str:
    """The round-1 question for one class in one region."""
    where = (
        "anywhere in the file"
        if whole
        else f"in lines {region.start}-{region.end} only ({region.name})"
    )
    return f"Problem: {kind.question}.\nLook {where}. Is there such a problem?"


def confirm_question(framing: str, finding: Finding) -> str:
    """The round-2 question for one candidate in one framing."""
    return framing.format(line=finding.line, text=finding.text, kind=finding.kind)


def parse_answer(answer: str, kind: str, region: Region) -> Finding | None:
    """Reads a round-1 answer; `no`, an unparsable answer or a line outside the file is no finding."""
    match = ANSWER_RE.match(answer.strip())
    if not match:
        return None
    text = " ".join(match.group(2).split()[:12])
    return Finding(int(match.group(1)), text, kind, region.name)


def parse_verdict(answer: str) -> tuple[bool, str]:
    """Reads a round-2 answer: whether it says real, and the severity it names (or ``).

    The verdict is read after the last `verdict:` when there is one, so the sentence before it
    (which may say "not" or "real" in passing) does not count; else from the first verdict word.
    """
    at = answer.lower().rfind("verdict")
    match = VERDICT_RE.search(answer[at:] if at >= 0 else answer)
    if not match:
        return False, ""
    real = match.group(1).lower() == "real"
    return real, (match.group(2) or "").lower() if real else ""


def parse_baseline(answer: str) -> list[Finding]:
    """Reads the baseline's list, one `L<line> <description>` per line."""
    findings = []
    for line in answer.splitlines():
        match = BASELINE_RE.match(re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s+", "", line))
        if match:
            findings.append(
                Finding(
                    int(match.group(1)), " ".join(match.group(2).split()[:16]), "baseline", "file"
                )
            )
    return findings


def dedupe(findings: list[Finding], total_lines: int, limit: int = MAX_CANDIDATES) -> list[Finding]:
    """Merges the findings that land within three lines of each other into one candidate each.

    Findings outside the file are dropped. A candidate keeps the first finding's line and text and
    names every class that landed on it; the candidates most classes agree on are kept first, at
    most `limit` of them, and returned in line order.
    """
    clusters: list[list[Finding]] = []
    for finding in sorted(
        (f for f in findings if 1 <= f.line <= total_lines), key=lambda f: f.line
    ):
        if clusters and finding.line - clusters[-1][0].line <= DEDUPE_LINES:
            clusters[-1].append(finding)
        else:
            clusters.append([finding])
    merged = []
    for cluster in clusters:
        kinds = list(dict.fromkeys(f.kind for f in cluster))
        first = min(cluster, key=lambda f: findings.index(f))
        merged.append((len(kinds), Finding(first.line, first.text, "+".join(kinds), first.region)))
    chosen = sorted(merged, key=lambda pair: (-pair[0], pair[1].line))[:limit]
    return sorted((f for _, f in chosen), key=lambda f: f.line)


def tally(finding: Finding, answers: list[str]) -> Finding:
    """Records round 2's votes on a finding: confirmed by a strict majority of the framings."""
    verdicts = [parse_verdict(a) for a in answers]
    finding.votes = [a.strip()[-24:] for a in answers]
    real = [s for ok, s in verdicts if ok]
    finding.confirmed = len(real) * 2 > len(verdicts)
    order = {"high": 3, "medium": 2, "low": 1, "": 0}
    finding.severity = sorted(real, key=lambda s: order[s])[len(real) // 2] if real else ""
    return finding


def messages(path: str, numbered: str, question: str) -> list[dict[str, str]]:
    """The request's messages: the shared prefix (system text and the numbered file), then one question."""
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"File `{path}`:\n\n{numbered}"},
        {"role": "assistant", "content": "I have read the file."},
        {"role": "user", "content": question},
    ]


class Client:
    """Sends chat requests to the model server, thinking off, and reads its prefix-cache counters."""

    def __init__(self, host: str, model: str | None = None, timeout: float = 600.0) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.model = model or self.served_model()
        self.requests = 0

    def get(self, path: str) -> str:
        """Fetches one path of the server as text."""
        with urllib.request.urlopen(self.host + path, timeout=30) as reply:
            return reply.read().decode("utf-8", "replace")

    def served_model(self) -> str:
        """The id of the model the server runs."""
        return json.loads(self.get("/v1/models"))["data"][0]["id"]

    def chat(self, msgs: list[dict[str, str]], max_tokens: int) -> str:
        """One completion at temperature 0; returns its text."""
        body = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        request = urllib.request.Request(
            self.host + "/v1/chat/completions",
            json.dumps(body).encode(),
            {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as reply:
            data = json.load(reply)
        self.requests += 1
        return data["choices"][0]["message"]["content"] or ""

    def cache_counters(self) -> dict[str, float]:
        """The server's cumulative prefilled-from-cache and computed prompt tokens, or {} without /metrics."""
        try:
            text = self.get("/metrics")
        except OSError:
            return {}
        return parse_metrics(text)


def parse_metrics(text: str) -> dict[str, float]:
    """Sums SGLang's `realtime_tokens_total` by mode (`prefill_cache`, `prefill_compute`, `decode`)."""
    counters: dict[str, float] = {}
    for line in text.splitlines():
        match = re.match(
            r'^sglang:realtime_tokens_total\{[^}]*mode="([a-z_]+)"[^}]*\}\s+([0-9.e+]+)$', line
        )
        if match:
            counters[match.group(1)] = counters.get(match.group(1), 0.0) + float(match.group(2))
    return counters


def hit_rate(before: dict[str, float], after: dict[str, float]) -> float | None:
    """The share of prompt tokens served from the prefix cache between two readings."""
    if not before or not after:
        return None
    cached = after.get("prefill_cache", 0.0) - before.get("prefill_cache", 0.0)
    computed = after.get("prefill_compute", 0.0) - before.get("prefill_compute", 0.0)
    return round(cached / (cached + computed), 4) if cached + computed > 0 else None


def fan_out(
    ask: Callable[[str, int], str], questions: list[str], max_tokens: int, concurrency: int
) -> list[str]:
    """Asks every question; the first alone, so its prefill fills the cache before the rest arrive."""
    if not questions:
        return []
    first = ask(questions[0], max_tokens)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        rest = list(pool.map(lambda q: ask(q, max_tokens), questions[1:]))
    return [first, *rest]


def plan_probes(
    path: str, source: str, max_regions: int = MAX_REGIONS
) -> list[tuple[ProblemClass, Region, str]]:
    """Every round-1 question of a file: one per applicable class and region."""
    lang = language_of(path)
    regions = split_regions(source, lang, max_regions)
    lines = source.splitlines()
    whole = len(regions) == 1
    plan = []
    for region in regions:
        text = "\n".join(lines[region.start - 1 : region.end])
        for kind in classes_for(path, source, text):
            plan.append((kind, region, probe_question(kind, region, whole)))
    return plan


def scan(
    client: Any,
    path: str,
    source: str,
    concurrency: int = 4,
    confirm: bool = True,
    max_regions: int = MAX_REGIONS,
) -> dict[str, Any]:
    """Runs rounds 1 and 2 on one file.

    Args:
        client: A `Client` (or a stand-in with `chat` and `cache_counters`).
        path: The file's path as shown to the model, relative to the repository.
        source: The file's text.
        concurrency: Requests in flight at once.
        confirm: Whether to run round 2.
        max_regions: The most regions round 1 splits the file into.

    Returns:
        `{"findings": [...], "candidates": [...], "summary": {...}}`, findings being the confirmed
        candidates (all candidates when `confirm` is off).
    """
    numbered = number_lines(source)
    total = len(source.splitlines())
    before, started = client.cache_counters(), time.time()

    def ask(question: str, max_tokens: int) -> str:
        return client.chat(messages(path, numbered, question), max_tokens)

    plan = plan_probes(path, source, max_regions)
    answers = fan_out(ask, [q for _, _, q in plan], PROBE_TOKENS, concurrency)
    raw = [
        f for (kind, region, _), a in zip(plan, answers) if (f := parse_answer(a, kind.key, region))
    ]
    candidates = dedupe(raw, total)
    round1 = time.time() - started
    confirm_probes = 0
    if confirm and candidates:
        questions = [confirm_question(fr, c) for c in candidates for fr in CONFIRM_FRAMINGS]
        votes = fan_out(ask, questions, CONFIRM_TOKENS, concurrency)
        confirm_probes = len(questions)
        width = len(CONFIRM_FRAMINGS)
        for index, candidate in enumerate(candidates):
            tally(candidate, votes[index * width : (index + 1) * width])
    findings = [c for c in candidates if c.confirmed] if confirm else candidates
    summary = {
        "path": path,
        "lines": total,
        "regions": len({r.name for _, r, _ in plan}),
        "probes_round1": len(plan),
        "probes_round2": confirm_probes,
        "raw_yes": len(raw),
        "candidates": len(candidates),
        "confirmed": len(findings),
        "seconds_round1": round(round1, 2),
        "seconds": round(time.time() - started, 2),
        "cache_hit_rate": hit_rate(before, client.cache_counters()),
    }
    return {
        "findings": [asdict(f) for f in findings],
        "candidates": [asdict(c) for c in candidates],
        "summary": summary,
    }


def baseline(client: Any, path: str, source: str) -> dict[str, Any]:
    """The one-call baseline over the same prefix: list every problem in the file."""
    numbered = number_lines(source)
    before, started = client.cache_counters(), time.time()
    answer = client.chat(messages(path, numbered, BASELINE_QUESTION), BASELINE_TOKENS)
    total = len(source.splitlines())
    findings = [f for f in parse_baseline(answer) if 1 <= f.line <= total]
    summary = {
        "path": path,
        "lines": total,
        "probes_round1": 1,
        "probes_round2": 0,
        "confirmed": len(findings),
        "seconds": round(time.time() - started, 2),
        "cache_hit_rate": hit_rate(before, client.cache_counters()),
    }
    return {
        "findings": [asdict(f) for f in findings],
        "candidates": [],
        "summary": summary,
        "answer": answer,
    }


def main(argv: list[str] | None = None) -> int:
    """Scans one file and prints its findings as JSON lines, then a summary line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file")
    parser.add_argument(
        "--repo", default=".", help="repository root; the path shown is relative to it"
    )
    parser.add_argument("--host", default=os.environ.get("DREAMFERENCE_VLLM_HOST") or DEFAULT_HOST)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-regions", type=int, default=MAX_REGIONS)
    parser.add_argument(
        "--baseline", action="store_true", help="one 'list every problem' call instead"
    )
    parser.add_argument("--no-confirm", action="store_true", help="round 1 only")
    args = parser.parse_args(argv)
    with pathlib.Path(args.file).open(encoding="utf-8", errors="replace") as handle:
        source = handle.read()
    shown = os.path.relpath(os.path.abspath(args.file), os.path.abspath(args.repo))
    client = Client(args.host)
    if args.baseline:
        result = baseline(client, shown, source)
    else:
        result = scan(
            client, shown, source, args.concurrency, not args.no_confirm, args.max_regions
        )
    for finding in result["findings"]:
        print(json.dumps(finding))
    print(json.dumps({"summary": result["summary"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
