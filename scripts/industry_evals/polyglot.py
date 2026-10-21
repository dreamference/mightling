"""T2: Aider Polyglot with `ling` as the agent (specs/DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md §10.2).

    python polyglot.py [--languages rust,cpp] [--limit 5] [--tries 2] [--parallel 3]

Scored as aider scores it (`pass_rate_1`, `pass_rate_2`) and driven as aider drives it, with
`ling exec` in the place of aider's chat:
- the agent gets the exercise's instructions (`.docs/introduction.md`, `instructions.md`,
  `instructions.append.md`) and aider's own addendum naming the solution files;
- the workspace holds the solution stubs and the build files, never the tests or the reference
  solution (`.meta/`): aider's model never sees the tests either, and an agent that can read them
  would be playing an easier benchmark;
- the tests are copied in pristine only to grade (Java's `@Disabled` and JavaScript's `xtest` turned
  on, as aider does), with a 3-minute limit, and taken out again; on a failure, one
  `ling exec resume --last` gets the test output and aider's second-try text.

Each exercise runs in its own container from the image `setup_cache.py polyglot` builds (no
network: the internal Docker network `mightling-industry` reaches only the model server at its
gateway), with SWE-bench's runtime copy of `ling` mounted at `/opt/ling` (SWE_BENCH §12: the
image's glibc is older than the host's). The container is the sandbox, as in SWE-bench.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Final

HERE: Final[Path] = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ask_evals"))

from askevals import rollout  # noqa: E402
from setup_cache import DEFAULT_CACHE  # noqa: E402

LANGUAGES: Final[tuple[str, ...]] = ("cpp", "go", "java", "javascript", "python", "rust")
IMAGE: Final[str] = "mightling-polyglot:latest"
NETWORK: Final[str] = "mightling-industry"
# Aider's test commands (benchmark/benchmark.py, run_unit_tests), offline for Java.
TEST_COMMANDS: Final[dict[str, list[str]]] = {
    "python": ["pytest"],
    "rust": ["cargo", "test", "--offline", "--", "--include-ignored"],
    "go": ["go", "test", "./..."],
    "javascript": ["test-js.sh"],
    "cpp": ["test-cpp.sh"],
    "java": ["./gradlew", "--offline", "test"],
}
TEST_LIMIT_S: Final[int] = 180
ATTEMPT_LIMIT_S: Final[int] = 900
# Aider's prompt texts (benchmark/prompts.py), word for word, so the task is the same.
ADDENDUM: Final[str] = """
####

Use the above instructions to modify the supplied files: {file_list}
Don't change the names of existing functions or classes, as they may be referenced from other code like unit tests, etc.
Only use standard libraries, don't suggest installing any packages.
"""  # noqa: E501
TEST_FAILURES: Final[str] = """
####

See the testing errors above.
The tests are correct, don't try and change them.
Fix the code in {file_list} to resolve the errors.
"""
ALWAYS_IGNORED: Final[frozenset[str]] = frozenset({"CMakeLists.txt", "Cargo.toml"})


class Docker:
    """The Docker calls the runner makes; tests replace it, so the suite never runs Docker."""

    def run(self, args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
        )


def exercises(root: Path, languages: list[str], limit: int | None) -> list[Path]:
    """The exercise folders, language by language, in name order."""
    found: list[Path] = []
    for language in languages:
        folders = sorted(
            p for p in (root / language / "exercises" / "practice").iterdir() if p.is_dir()
        )
        found += folders[:limit] if limit else folders
    return found


def files_of(exercise: Path) -> dict[str, list[str]]:
    """The exercise's solution and test files, as aider chooses them from `.meta/config.json`."""
    config = json.loads((exercise / ".meta" / "config.json").read_text()).get("files", {})
    tests = list(config.get("test", []))
    examples = list(config.get("example", []))
    ignored = set(ALWAYS_IGNORED) | set(tests) | set(examples)
    solution = [
        f
        for f in config.get("solution", [])
        if f not in ignored and not f.startswith((".meta/", ".docs/"))
    ]
    return {"solution": solution, "test": tests}


def instructions(exercise: Path, solution: list[str]) -> str:
    """Aider's prompt for one exercise."""
    docs = exercise / ".docs"
    text = ""
    for name in ("introduction.md", "instructions.md", "instructions.append.md"):
        if (docs / name).exists():
            text += (docs / name).read_text()
    return text + ADDENDUM.format(file_list=" ".join(Path(f).name for f in solution))


def prepare_workspace(exercise: Path, work: Path, tests: list[str]) -> None:
    """The exercise without its tests and without `.meta/` (the reference solution lives there)."""
    shutil.copytree(exercise, work, ignore=shutil.ignore_patterns(".meta"))
    for name in tests:
        (work / name).unlink(missing_ok=True)


def install_tests(exercise: Path, work: Path, tests: list[str], language: str) -> None:
    """Copies the pristine tests in for grading, with every test switched on as aider does."""
    for name in tests:
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(exercise / name, target)
        if language == "java":
            target.write_text(re.sub(r"@Disabled\([^)]*\)\s*\n", "", target.read_text()))


def grade(docker: Docker, container: str, language: str) -> tuple[bool, str]:
    """Runs the language's tests in the container; (passed, output)."""
    command = [
        "exec",
        "-w",
        "/work",
        container,
        "timeout",
        str(TEST_LIMIT_S),
        *TEST_COMMANDS[language],
    ]
    try:
        done = docker.run(command, timeout=TEST_LIMIT_S + 30)
    except subprocess.TimeoutExpired:
        return False, "Tests timed out!"
    output = (done.stdout or "") + (done.stderr or "")
    if done.returncode == 124:
        return False, "Tests timed out!"
    return done.returncode == 0, output[-8000:]


def agent(
    docker: Docker, container: str, text: str, resume: bool, model_host: str, ling: str
) -> int:
    """One `ling exec` attempt in the container; its exit status (124 when cut at the limit)."""
    tail = ["resume", "--last", text] if resume else [text]
    command = [
        "exec",
        "-w",
        "/work",
        "-e",
        "CODEX_HOME=/codex-home",
        "-e",
        f"DREAMFERENCE_VLLM_HOST={model_host}",
        "-e",
        "DREAMFERENCE_MIGHTLING_PROMPT=default",
        container,
        "timeout",
        str(ATTEMPT_LIMIT_S),
        ling,
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "-C",
        "/work",
        "--skip-git-repo-check",
        *tail,
    ]
    try:
        return docker.run(command, timeout=ATTEMPT_LIMIT_S + 60).returncode
    except subprocess.TimeoutExpired:
        return 124


def run_exercise(
    exercise: Path,
    language: str,
    run_dir: Path,
    args: argparse.Namespace,
    docker: Docker,
    model_host: str,
) -> dict:
    """One exercise, up to `--tries` attempts; its record."""
    files = files_of(exercise)
    work, home = run_dir / "work", run_dir / "codex-home"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    home.mkdir()
    prepare_workspace(exercise, work, files["test"])
    name = f"mightling-polyglot-{language}-{exercise.name}-{os.getpid()}"
    started = time.time()
    record: dict = {"language": language, "exercise": exercise.name, "outcomes": []}
    docker.run(["rm", "-f", name])
    created = docker.run(
        [
            "run",
            "-d",
            "--name",
            name,
            "--network",
            NETWORK,
            "--memory",
            "4g",
            "--cpus",
            "2",
            "-v",
            f"{work}:/work",
            "-v",
            f"{home}:/codex-home",
            "-v",
            f"{args.runtime}:/opt/ling:ro",
            args.image,
            "sleep",
            "infinity",
        ]
    )
    if created.returncode != 0:
        return {**record, "error": f"container did not start: {created.stderr.strip()[:300]}"}
    try:
        text = instructions(exercise, files["solution"])
        for attempt in range(args.tries):
            rc = agent(docker, name, text, attempt > 0, model_host, args.ling)
            install_tests(exercise, work, files["test"], language)
            passed, output = grade(docker, name, language)
            record["outcomes"].append(passed)
            record.setdefault("agent_rc", []).append(rc)
            if passed:
                break
            # Out again before the retry: the second try sees the test output, never the tests.
            for test in files["test"]:
                (work / test).unlink(missing_ok=True)
            text = output + TEST_FAILURES.format(
                file_list=" ".join(Path(f).name for f in files["solution"])
            )
    finally:
        # The container ran as root: give the files back before it goes.
        docker.run(
            ["exec", name, "chown", "-R", f"{os.getuid()}:{os.getgid()}", "/work", "/codex-home"]
        )
        docker.run(["rm", "-f", name])
    turns = rollout.read_home(home)
    record.update(
        {
            "pass_1": bool(record["outcomes"][:1] == [True]),
            "pass_2": any(record["outcomes"]),
            "tool_calls": sum(len(t.calls) for t in turns),
            "output_tokens": sum(t.output_tokens or 0 for t in turns),
            "wall_s": round(time.time() - started, 1),
        }
    )
    return record


def summary(records: list[dict]) -> dict:
    """Aider's two rates overall and per language, in percent."""

    def rates(rows: list[dict]) -> dict:
        n = max(1, len(rows))
        return {
            "cases": len(rows),
            "pass_rate_1": round(100 * sum(r.get("pass_1", False) for r in rows) / n, 1),
            "pass_rate_2": round(100 * sum(r.get("pass_2", False) for r in rows) / n, 1),
            "errors": sum(1 for r in rows if "error" in r),
        }

    by_language = {
        lang: rates([r for r in records if r["language"] == lang])
        for lang in sorted({r["language"] for r in records})
    }
    return {"all": rates(records), "by_language": by_language}


def main(argv: list[str] | None = None, docker: Docker | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--languages", default=",".join(LANGUAGES))
    parser.add_argument("--limit", type=int, help="the first N exercises of each language")
    parser.add_argument("--tries", type=int, default=2)
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument(
        "--cache", default=os.environ.get("DREAMFERENCE_INDUSTRY_CACHE", str(DEFAULT_CACHE))
    )
    parser.add_argument(
        "--runs", default=str(DEFAULT_CACHE / "runs" / time.strftime("polyglot-%Y%m%d-%H%M%S"))
    )
    parser.add_argument("--image", default=IMAGE)
    parser.add_argument(
        "--runtime", help="SWE-bench's runtime copy of ling (default: its cache folder)"
    )
    parser.add_argument("--ling", default="/opt/ling/bin/ling")
    parser.add_argument(
        "--model-host", help="the model server as the container sees it (default: the gateway)"
    )
    parser.add_argument(
        "--hold-gate", metavar="LABEL", help="close the model gate to all but this run"
    )
    args = parser.parse_args(argv)
    docker = docker or Docker()
    cache, runs = Path(args.cache), Path(args.runs)
    root = cache / "polyglot-benchmark"
    if not root.exists():
        print(f"❌ No exercises in {root}: run setup_cache.py polyglot first.", file=sys.stderr)
        return 2
    if not args.runtime:
        from dreamference.swe_bench.swe_bench_runtime import SweBenchRuntime  # noqa: PLC0415

        args.runtime = str(SweBenchRuntime.directory())
    if not (Path(args.runtime) / "bin" / "ling").exists():
        # Built by SWE-bench (`ling-admin swe-bench smoke` or a run), never rebuilt here: a night's
        # SWE-bench containers mount the same folder.
        print(
            f"❌ No runtime copy of ling in {args.runtime}: run `ling-admin swe-bench smoke` once.",
            file=sys.stderr,
        )
        return 2
    model_host, subnet = args.model_host, None
    if not model_host:
        from dreamference.swe_bench.swe_bench_docker import SweBenchDocker  # noqa: PLC0415

        gateway = SweBenchDocker.ensure_network(NETWORK)
        if not gateway:
            print(f"❌ Could not make the internal network {NETWORK}.", file=sys.stderr)
            return 2
        model_host, subnet = f"http://{gateway}:8000", SweBenchDocker.subnet(NETWORK)
    todo = exercises(root, args.languages.split(","), args.limit)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    hold = None
    if args.hold_gate and subnet:
        from askevals.gate import GateHold  # noqa: PLC0415

        hold = GateHold(args.hold_gate, total=len(todo), networks=[subnet]).__enter__()
    out = runs / "results.jsonl"
    runs.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    try:

        def one(exercise: Path) -> dict:
            language = exercise.parts[-4]
            record = run_exercise(
                exercise, language, runs / language / exercise.name, args, docker, model_host
            )
            if hold:
                hold.advance()
            with out.open("a") as log:
                log.write(json.dumps(record) + "\n")
            print(json.dumps(record), flush=True)
            return record

        with ThreadPoolExecutor(max(1, args.parallel)) as pool:
            records = list(pool.map(one, todo))
    finally:
        if hold:
            hold.__exit__()
    manifest = (
        json.loads((cache / "manifest.json").read_text())
        if (cache / "manifest.json").exists()
        else {}
    )
    result = {
        "summary": summary(records),
        "manifest": manifest.get("polyglot", {}),
        "tries": args.tries,
    }
    (runs / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"]["all"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
