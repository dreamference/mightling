#!/usr/bin/env python3
"""CPU benchmark for the code index (`ling-code`) and the file index (`ling-docs`).

What it measures, on fixed corpora and with every state directory in a temp folder:

- `ling-code`: this repository's tree at HEAD (`git archive`, committed into a scratch git repo) is
  indexed with `ling-code index --wait`; then 50 queries (`def`, `refs` and `callers` in turn, over
  50 names drawn with a fixed seed from the tree's own definitions) are run for 5 rounds.
- `ling-docs`: 2,000 generated files (1,000 text, 700 markdown, 300 PDF; fixed seed) are indexed with
  `ling-docs index` (workers as plain children, `MIGHTLING_DOCS_TEST_HOST=direct`, the measurement
  mode); then 50 `search` queries are run for 5 rounds.

Per index run it records wall time, peak RSS and the index size. Peak RSS is the largest sum of RSS
over the command's process tree plus the processes in `mightling-index-*` systemd units (the code
index runs its indexers there, outside the command's tree), sampled every 0.2 s; `/usr/bin/time -v`
gives the command's own maximum for comparison. CPU seconds per process name over the same set show
where the time goes (perf is not available to an unprivileged user when perf_event_paranoid is 4).
A query's latency is the whole CLI invocation, so for `ling-docs` it includes loading the embedder.

Run it inside the batch slice, never beside a model load, with the tools it should measure:

    systemd-run --user --scope --slice=mightling-batch.slice -p MemoryMax=6G nice -n 10 \\
        python3 scripts/bench_index.py --code-bin T/code/release/ling-code \\
        --docs-bin T/docs/release/ling-docs --json-out run1.json --md-out run1.md

Use `--summary run1.json run2.json run3.json` to print the spread of several runs.
Nothing under the real home, `~/.mightling` or the installed binaries is read or written, except the
pinned tools, embedding model and PDFium libraries under the install directory (read only).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
INSTALL_DIR: Final = Path.home() / ".local/share/dreamference/mightling"
SEED: Final = 20261029
QUERIES: Final = 50
ROUNDS: Final = 5
DOC_COUNTS: Final = {"txt": 1000, "md": 700, "pdf": 300}
SAMPLE_INTERVAL: Final = 0.2
TICKS: Final = os.sysconf("SC_CLK_TCK")
PAGE_KB: Final = os.sysconf("SC_PAGE_SIZE") // 1024


# --- process sampling -------------------------------------------------------------------------


def _read_procs() -> dict[int, tuple[int, str, int, int]]:
    """Maps pid to (ppid, comm, rss_kb, cpu_ticks) for every process readable now."""
    procs: dict[int, tuple[int, str, int, int]] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            stat = Path(f"/proc/{entry}/stat").read_text()
        except OSError:
            continue
        head, _, tail = stat.rpartition(")")
        comm = head.split("(", 1)[1]
        fields = tail.split()
        procs[int(entry)] = (
            int(fields[1]),
            comm,
            int(fields[21]) * PAGE_KB,
            int(fields[11]) + int(fields[12]) + int(fields[13]) + int(fields[14]),
        )
    return procs


def _in_index_unit(pid: int) -> bool:
    try:
        return "mightling-index-" in Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return False


class Sampler(threading.Thread):
    """Samples the root's process tree and the index units: peak summed RSS and CPU per comm."""

    def __init__(self, root: int) -> None:
        super().__init__(daemon=True)
        self.root = root
        self.stop_event = threading.Event()
        self.peak_kb = 0
        self.cpu_ticks: dict[int, tuple[str, int]] = {}

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.sample()
            self.stop_event.wait(SAMPLE_INTERVAL)
        self.sample()

    def sample(self) -> None:
        procs = _read_procs()
        mine = {self.root}
        grew = True
        while grew:
            grew = False
            for pid, (ppid, *_rest) in procs.items():
                if ppid in mine and pid not in mine:
                    mine.add(pid)
                    grew = True
        mine |= {pid for pid in procs if _in_index_unit(pid)}
        self.peak_kb = max(self.peak_kb, sum(procs[p][2] for p in mine if p in procs))
        for pid in mine:
            if pid in procs:
                self.cpu_ticks[pid] = (procs[pid][1], procs[pid][3])

    def cpu_by_comm(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for comm, ticks in self.cpu_ticks.values():
            totals[comm] = totals.get(comm, 0.0) + ticks / TICKS
        return dict(sorted(totals.items(), key=lambda kv: -kv[1])[:8])


def run_measured(cmd: list[str], env: dict[str, str], cwd: Path, log: Path) -> dict[str, Any]:
    """Runs `cmd` under `/usr/bin/time -v`; returns wall time, peak RSS and CPU by process name."""
    time_file = log.with_suffix(".time")
    start = time.monotonic()
    with log.open("w") as out:
        proc = subprocess.Popen(
            ["/usr/bin/time", "-v", "-o", str(time_file), *cmd],
            env=env,
            cwd=cwd,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        sampler = Sampler(proc.pid)
        sampler.start()
        code = proc.wait()
    wall = time.monotonic() - start
    sampler.stop_event.set()
    sampler.join()
    own = re.search(r"Maximum resident set size \(kbytes\): (\d+)", time_file.read_text())
    return {
        "exit": code,
        "wall_s": round(wall, 2),
        "peak_rss_mb": round(sampler.peak_kb / 1024, 1),
        "command_max_rss_mb": round(int(own.group(1)) / 1024, 1) if own else None,
        "cpu_s_by_process": {k: round(v, 1) for k, v in sampler.cpu_by_comm().items()},
    }


def du_mb(*paths: Path) -> float:
    total = 0
    for root in paths:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                try:
                    total += os.lstat(os.path.join(dirpath, name)).st_size
                except OSError:
                    pass
    return round(total / 1048576, 1)


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def time_queries(
    commands: list[list[str]], env: dict[str, str], cwd: Path, rounds: int
) -> dict[str, Any]:
    """Runs every command once per round; latency of each invocation in milliseconds."""
    latencies: list[float] = []
    failures = 0
    empty = 0
    for _ in range(rounds):
        for cmd in commands:
            start = time.perf_counter()
            done = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True)
            latencies.append((time.perf_counter() - start) * 1000)
            failures += done.returncode != 0
            empty += len(done.stdout.strip()) < 40 or '"hits": []' in done.stdout
    return {
        "invocations": len(latencies),
        "failures": failures,
        "empty_or_short_answers": empty,
        "p50_ms": round(percentile(latencies, 0.5), 1),
        "p95_ms": round(percentile(latencies, 0.95), 1),
        "mean_ms": round(statistics.fmean(latencies), 1),
    }


# --- ling-code ----------------------------------------------------------------------------------


def code_names(tree: Path) -> list[str]:
    """Fifty definition names from the tree, drawn with the fixed seed from a sorted candidate list."""
    pattern = re.compile(
        r"^\s*(?:pub(?:\([a-z]+\))? )?(?:async )?(?:class|def|fn|struct|enum) (\w{5,})"
    )
    names: set[str] = set()
    for path in sorted(tree.rglob("*")):
        if path.suffix not in {".py", ".rs"} or ".git" in path.parts or "codex" in path.parts[:2]:
            continue
        for line in path.read_text(errors="replace").splitlines():
            if match := pattern.match(line):
                names.add(match.group(1))
    return random.Random(SEED).sample(sorted(names), QUERIES)


def bench_code(
    binary: Path, work: Path, tools: Path, indexers: Path, rounds: int
) -> dict[str, Any]:
    repo, state, cbm, home = (work / n for n in ("repo", "state", "cbm", "home"))
    for folder in (repo, state, cbm, home):
        folder.mkdir()
    archive = subprocess.run(
        ["git", "archive", "HEAD"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(repo)], input=archive, check=True)
    git = ["git", "-c", "user.name=bench", "-c", "user.email=bench@example.invalid"]
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "corpus"], cwd=repo, check=True)
    files = len(
        subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True).stdout.split()
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "CODEX_HOME": str(home / ".mightling"),
        "MIGHTLING_CODE_STATE_DIR": str(state),
        "CBM_CACHE_DIR": str(cbm),
        "MIGHTLING_CODE_TOOLS_DIR": str(tools),
        "MIGHTLING_CODE_INDEXERS_DIR": str(indexers),
    }
    index = run_measured([str(binary), "index", "--wait"], env, repo, work / "code-index.log")
    if index["exit"]:
        raise SystemExit(f"ling-code index failed ({index['exit']}): see {work / 'code-index.log'}")
    index["files"] = files
    index["index_mb"] = du_mb(state, cbm)
    names = code_names(repo)
    verbs = ["def", "refs", "callers"]
    commands = [[str(binary), verbs[i % 3], name, "--json"] for i, name in enumerate(names)]
    return {"index": index, "queries": time_queries(commands, env, repo, rounds)}


# --- ling-docs ----------------------------------------------------------------------------------


def make_vocabulary(rng: random.Random) -> list[str]:
    syllables = [
        "ka",
        "lo",
        "mi",
        "ten",
        "ra",
        "vu",
        "sol",
        "dor",
        "pe",
        "nix",
        "ba",
        "qui",
        "ful",
        "zen",
    ]
    words = {"".join(rng.choices(syllables, k=rng.randint(2, 4))) for _ in range(1500)}
    return sorted(words)[:600]


def pdf_bytes(pages: list[list[str]]) -> bytes:
    """A minimal PDF with Helvetica text, one list of lines per page."""
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")  # object 3
    for page in pages:
        text = "BT /F1 11 Tf 14 TL 50 750 Td " + " ".join(f"({line}) '" for line in page) + " ET"
        stream = text.encode("latin-1")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + f"{len(objects) + 2} 0 R >>".encode()
        )
        kids.append(len(objects))
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects[1] = (
        "<< /Type /Pages /Count %d /Kids [%s] >>" % (len(pages), " ".join(f"{k} 0 R" for k in kids))
    ).encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def make_docs_corpus(root: Path) -> list[str]:
    """Writes the 2,000 files under root; returns the 50 query strings."""
    rng = random.Random(SEED)
    vocab = make_vocabulary(rng)

    def sentence() -> str:
        return " ".join(rng.choices(vocab, k=rng.randint(6, 14))).capitalize() + "."

    def paragraph() -> str:
        return " ".join(sentence() for _ in range(rng.randint(3, 7)))

    number = 0
    for kind, count in DOC_COUNTS.items():
        for _ in range(count):
            folder = root / f"group{number % 40:02d}"
            folder.mkdir(exist_ok=True)
            path = folder / f"doc{number:04d}.{kind}"
            number += 1
            if kind == "txt":
                path.write_text("\n\n".join(paragraph() for _ in range(rng.randint(2, 6))))
            elif kind == "md":
                parts = [f"# {sentence()}"]
                for _ in range(rng.randint(2, 4)):
                    parts += [
                        f"## {sentence()}",
                        paragraph(),
                        *(f"- {sentence()}" for _ in range(3)),
                    ]
                path.write_text("\n\n".join(parts))
            else:
                pages = [
                    [sentence()[:90] for _ in range(rng.randint(15, 30))]
                    for _ in range(rng.randint(1, 3))
                ]
                path.write_bytes(pdf_bytes(pages))
    return [" ".join(rng.sample(vocab, rng.randint(2, 3))) for _ in range(QUERIES)]


def bench_docs(binary: Path, work: Path, install: Path, rounds: int) -> dict[str, Any]:
    home = work / "home"
    corpus = home / "bench-corpus"
    corpus.mkdir(parents=True)
    queries = make_docs_corpus(corpus)
    env = {
        **os.environ,
        "HOME": str(home),
        "CODEX_HOME": str(home / ".mightling"),
        "MIGHTLING_DOCS_TEST_HOST": "direct",
        "MIGHTLING_DOCS_LIB_DIR": str(install / "lib/ling-docs"),
        "MIGHTLING_DOCS_MODEL_DIR": str(install / "models/snowflake-arctic-embed-m-v2.0-int8"),
        "MIGHTLING_DOCS_OCR_DIR": str(install / "models/rapidocr-3.9.2"),
    }
    # `add` would start a background index run that holds the lock; write the collection file instead.
    (home / ".mightling").mkdir()
    (home / ".mightling/docs.toml").write_text(
        f'[[collection]]\nname = "bench"\nroot = "{corpus}"\nmax_file_mb = 50\n'
        "follow_symlinks = false\nenabled = true\ndefault = true\n"
    )
    index = run_measured(
        [str(binary), "index", "--collection", "bench"], env, work, work / "docs-index.log"
    )
    if index["exit"]:
        raise SystemExit(f"ling-docs index failed ({index['exit']}): see {work / 'docs-index.log'}")
    index["files"] = sum(DOC_COUNTS.values())
    index["corpus_mb"] = du_mb(corpus)
    index["index_mb"] = du_mb(home / ".mightling/docs")
    commands = [[str(binary), "search", "--json", *q.split()] for q in queries]
    return {"index": index, "queries": time_queries(commands, env, work, rounds)}


# --- output -------------------------------------------------------------------------------------


def markdown(result: dict[str, Any]) -> str:
    lines = [
        "| Tool | Files | Index wall (s) | Peak RSS (MB) | Index size (MB) | Query p50 (ms) | Query p95 (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for tool in ("ling-code", "ling-docs"):
        if tool in result:
            part = result[tool]
            i, q = part["index"], part["queries"]
            lines.append(
                f"| {tool} | {i['files']} | {i['wall_s']} | {i['peak_rss_mb']} | {i['index_mb']} "
                f"| {q['p50_ms']} | {q['p95_ms']} |"
            )
    return "\n".join(lines) + "\n"


def summarize(paths: list[str]) -> str:
    runs = [json.loads(Path(p).read_text()) for p in paths]
    lines = [
        "| Tool | Metric | Runs | Min | Max | Spread (max/min - 1) |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for tool in ("ling-code", "ling-docs"):
        for label, section, key in (
            ("index wall (s)", "index", "wall_s"),
            ("peak RSS (MB)", "index", "peak_rss_mb"),
            ("index size (MB)", "index", "index_mb"),
            ("query p50 (ms)", "queries", "p50_ms"),
            ("query p95 (ms)", "queries", "p95_ms"),
        ):
            values = [r[tool][section][key] for r in runs if tool in r]
            if values:
                spread = f"{(max(values) / min(values) - 1) * 100:.1f}%" if min(values) else "n/a"
                lines.append(
                    f"| {tool} | {label} | {', '.join(str(v) for v in values)} | {min(values)} | {max(values)} | {spread} |"
                )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--code-bin", type=Path, help="the built ling-code (release)")
    parser.add_argument("--docs-bin", type=Path, help="the built ling-docs (release)")
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=INSTALL_DIR,
        help="where the pinned tools, models and libraries are",
    )
    parser.add_argument("--rounds", type=int, default=ROUNDS)
    parser.add_argument("--keep", action="store_true", help="keep the temp folder")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument(
        "--summary", nargs="+", metavar="RUN_JSON", help="print the spread of these runs and exit"
    )
    args = parser.parse_args()
    if args.summary:
        print(summarize(args.summary))
        return 0
    if not (args.code_bin or args.docs_bin):
        parser.error("name --code-bin and/or --docs-bin")
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    os.environ.setdefault(
        "DBUS_SESSION_BUS_ADDRESS", f"unix:path={os.environ['XDG_RUNTIME_DIR']}/bus"
    )
    work = Path(tempfile.mkdtemp(prefix="bench-index."))
    result: dict[str, Any] = {
        "seed": SEED,
        "rounds": args.rounds,
        "commit": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
        ).stdout.strip(),
    }
    try:
        if args.code_bin:
            (work / "code").mkdir()
            result["ling-code"] = bench_code(
                args.code_bin.resolve(),
                work / "code",
                args.install_dir / "bin",
                args.install_dir / "indexers",
                args.rounds,
            )
        if args.docs_bin:
            (work / "docs").mkdir()
            result["ling-docs"] = bench_docs(
                args.docs_bin.resolve(), work / "docs", args.install_dir, args.rounds
            )
    finally:
        if args.keep:
            print(f"kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    text = json.dumps(result, indent=2)
    if args.json_out:
        args.json_out.write_text(text + "\n")
    table = markdown(result)
    if args.md_out:
        args.md_out.write_text(table)
    print(text)
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
