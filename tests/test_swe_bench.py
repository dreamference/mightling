"""`ling-admin swe-bench` (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §7.2).

A stand-in plays `docker`: a "container" is a scratch git repository on the host, the scripts the
runner executes in a container run against it with bash, and `ling exec` is a scripted agent
that edits it. Another stand-in plays the upstream harness. Nothing here starts a container,
pulls an image, installs a package or reaches the network.
"""

import hashlib
import json
import os
import shutil
import subprocess
import time
import types
from pathlib import Path

import pytest

from dreamference.night_shift import NightShiftQueue
from dreamference.night_shift.night_shift_task_run import NUDGE
from dreamference.swe_bench import (
    SweBenchCodeIndex, SweBenchCommand, SweBenchDocker, SweBenchEvaluator, SweBenchHarness, SweBenchImages,
    SweBenchInstanceRun, SweBenchNameStripper, SweBenchReport, SweBenchRunStore, SweBenchRunner,
    SweBenchRuntime, SweBenchSettings,
)
from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_evaluator import DROP_TEST_HUNKS
from dreamference.swe_bench.swe_bench_harness import FORBIDDEN_FIELDS, NOOP_PATCH
from dreamference.swe_bench.swe_bench_instance_run import (CODE_INDEX_HINT, COLLECT_SCRIPT, COMPLETION_NUDGE,
                                                          NO_REFINED, PREPARE_SCRIPT, REFINE_CODE_INDEX_HINT,
                                                          SCRUB_SCRIPT, TASK_RULES)
from dreamference.swe_bench.swe_bench_patch_filter import SweBenchPatchFilter

REPOSITORY = "greynewell/swe-bench-arm64"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)


def row(instance_id: str) -> dict:
    """A dataset row; every answer-bearing field carries a marker the tests look for."""
    return {
        "instance_id": instance_id, "repo": instance_id.rsplit("-", 1)[0].replace("__", "/"),
        "base_commit": "BASE", "image": f"swebench/sweb.eval.x86_64.{instance_id}:latest",
        "problem_statement": f"The widget is broken in {instance_id}.",
        "hints_text": "SECRET-HINT use the frobnicator", "patch": "SECRET-GOLD-PATCH",
        "test_patch": "SECRET-TEST-PATCH", "FAIL_TO_PASS": "['SECRET_F2P_test']",
        "PASS_TO_PASS": "['SECRET_P2P_test']", "eval_script": "#!/bin/bash\n", "version": "1.0",
        "log_parser": "parse_log_pytest", "eval_type": "pass_and_fail",
    }


IDS = ["acme__widget-1", "acme__widget-2", "beta__gadget-7", "beta__gadget-9"]


class FakeProcess:
    """The `docker exec … ling exec` client: finished at once, or hanging until stopped."""

    def __init__(self, code, hang=False):
        self.code, self.hang, self.stopped = code, hang, False

    def wait(self, timeout=None):
        if self.hang and not self.stopped:
            time.sleep(0.01)
            raise subprocess.TimeoutExpired("docker", timeout)
        return self.code

    def kill(self):
        self.stopped = True


class FakeDocker:
    """Records every docker call; containers are scratch repositories."""

    def __init__(self, root: Path):
        self.root = root
        self.calls = []
        self.present = set()
        self.unpullable = set()
        self.containers = {}
        self.removed = {}
        self.modes = {}
        self.default_mode = "change"
        self.processes = []

    # -- SweBenchDocker.run ------------------------------------------------------------------
    def run(self, args, timeout=None, input_text=None):
        self.calls.append(list(args))
        done = lambda code=0, out="": subprocess.CompletedProcess(args, code, out, "")
        verb = args[0]
        if verb == "image" and args[1] == "inspect":
            return done(0, f'["{REPOSITORY}@sha256:{abs(hash(args[2])) % 10**8:08d}"] sha256:id') \
                if args[2] in self.present else done(1)
        if verb == "pull":
            if args[-1] in self.unpullable:
                return done(1)
            self.present.add(args[-1])
            return done()
        if verb == "network":
            if args[1] == "inspect":
                return done(0, json.dumps([{"Internal": True, "IPAM": {"Config": [{"Gateway": "172.30.0.1"}]}}]))
            return done()
        if verb == "run":
            name = args[args.index("--name") + 1]
            scratch = next(a.split(":")[0] for a in args if a.endswith(":/mightling-scratch"))
            env = dict(a.split("=", 1) for i, a in enumerate(args) if i and args[i - 1] == "-e")
            repo = self.root / "containers" / name
            repo.mkdir(parents=True)
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "t@t")
            git(repo, "config", "user.name", "t")
            (repo / "widget.py").write_text("def widget():\n    return 1\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "SWE-bench")
            git(repo, "tag", "v9.9-with-the-fix")
            base = git(repo, "rev-parse", "HEAD").stdout.strip()
            self.containers[name] = {"repo": repo, "scratch": Path(scratch), "env": dict(env, BASE_COMMIT=base),
                                     "args": list(args), "instance": name}
            return done()
        if verb == "exec" and "bash" in args:
            at = args.index("bash")
            box = self.containers[args[at - 1]]
            environment = dict(os.environ, SCRATCH=str(box["scratch"]), TESTBED=str(box["repo"]),
                               BASE_COMMIT=box["env"]["BASE_COMMIT"])
            result = subprocess.run(["bash", "-c", args[at + 2]], capture_output=True, text=True, env=environment)
            return subprocess.CompletedProcess(args, result.returncode, result.stdout, result.stderr)
        if verb == "create":
            return done(0, f"copy-of-{args[1].split(':')[-1]}\n")
        if verb == "cp":
            Path(args[-1]).mkdir(parents=True)
            (Path(args[-1]) / "widget.py").write_text("def widget():\n    return 1\n")
            return done()
        if verb == "stop":
            for process in self.processes:
                process.stopped = True
            return done()
        if verb == "images":
            return done(0, "".join(f"{image}\t2.2GB\n" for image in sorted(self.present)))
        if verb == "rmi":
            self.present.discard(args[-1])
            return done()
        if verb == "ps":
            return done(0, "")
        if verb == "rm":
            for name in args[2:]:
                if name in self.containers:
                    box = self.containers.pop(name)
                    kept = self.root / "removed" / f"{name}-{len(self.calls)}"
                    kept.parent.mkdir(exist_ok=True)
                    box["repo"] = box["repo"].rename(kept)  # kept for the tests to look at
                    self.removed[name] = box
            return done()
        return done()  # start, update, info

    # -- SweBenchDocker.popen ----------------------------------------------------------------
    def popen(self, args, stdout):
        self.calls.append(list(args))
        box = self.containers[args[1]]
        instance = next((i for i in IDS if box["instance"].endswith(i.lower())), "")
        mode = self.modes.get(instance, self.default_mode)
        prompt = args[-1]
        resume = "resume" in args
        session = args[args.index("resume") + 1] if resume else f"session-{len(self.calls)}"
        stdout.write((json.dumps({"type": "thread.started", "thread_id": session}) + "\n").encode())
        stdout.flush()
        say = lambda text: (box["scratch"] / "last.txt").write_text(text)
        if "This is the first of two steps" in prompt:
            # The refine arm's first step: writes the description, unless told to write nothing;
            # `refine_edits` also changes the tree it was told to leave alone.
            if mode != "refine_silent":
                (box["scratch"] / "refined.md").write_text(f"1. Intent: widget() returns 2 ({instance}).\n")
            if mode == "refine_edits":
                (box["repo"] / "widget.py").write_text("def widget():\n    return 99  # EXPLORING\n")
                (box["repo"] / "stray.py").write_text("print('scratch')\n")
            stdout.write((json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 300, "cached_input_tokens": 0, "output_tokens": 30}}) + "\n").encode())
            stdout.flush()
            say("Wrote the description.")
            return FakeProcess(0)
        if mode == "hang":
            (box["repo"] / "partial.py").write_text("# half done\n")
            process = FakeProcess(0, hang=True)
            self.processes.append(process)
            return process
        if mode == "error":
            return FakeProcess(1)
        if "ling-code" in box["env"].get("PATH", ""):
            # An agent that has the index uses it once before it edits.
            stdout.write((json.dumps({"type": "item.completed", "item": {
                "type": "command_execution", "command": "/bin/bash -lc 'ling-code refs widget'", "exit_code": 0}}) + "\n").encode())
        stdout.write((json.dumps({"type": "item.completed", "item": {
            "type": "command_execution", "command": "/bin/bash -lc 'grep -rn widget .'", "exit_code": 0}}) + "\n").encode())
        stdout.write((json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 1000, "cached_input_tokens": 900, "output_tokens": 50}}) + "\n").encode())
        stdout.flush()
        if mode == "midwork":
            # Edits, then ends its turn mid-reasoning; finishes when told to.
            if prompt == COMPLETION_NUDGE:
                (box["repo"] / "widget.py").write_text("def widget():\n    return 2  # FIX\n")
                say("Finished: widget() returns 2, and the widget tests pass.")
            else:
                (box["repo"] / "widget.py").write_text("def widget():\n    return 2  # HALF\n")
                say("Changed widget.py.\n\nThe caller in api.py needs the same. Let me check what calls widget():")
            return FakeProcess(0)
        if mode == "summary_with_plan":
            # A finished summary that mentions a plan early on and ends with "Let me know".
            (box["repo"] / "widget.py").write_text("def widget():\n    return 2  # FIX\n")
            say("I'll describe the fix. " + "widget() now returns 2 as the issue asks. " * 12
                + "\n\nLet me know if anything else is needed.")
            return FakeProcess(0)
        if mode in ("with_tests", "tests_only"):
            if mode == "with_tests":
                (box["repo"] / "widget.py").write_text("def widget():\n    return 2  # FIX\n")
            (box["repo"] / "tests").mkdir(exist_ok=True)
            (box["repo"] / "tests" / "test_widget.py").write_text("def test_widget():\n    assert True  # FIX\n")
            say("Fixed widget() and added a test.")
            return FakeProcess(0)
        acts = mode in ("change", "binary", "refine_edits", "refine_silent") or \
            (mode == "stall_then_act" and prompt == NUDGE)
        if acts:
            (box["repo"] / "widget.py").write_text("def widget():\n    return 2  # FIX\n")
            if mode == "binary":
                (box["repo"] / "__pycache__").mkdir(exist_ok=True)
                (box["repo"] / "__pycache__" / "widget.cpython-39.pyc").write_bytes(b"\x00\x01\x02\xff" * 40)
            say("Fixed widget(). Files changed: widget.py")
        elif mode in ("stall", "stall_then_act"):
            say("I'll now fix widget() in widget.py.")
        else:
            say("The code already behaves as the issue asks.")
        return FakeProcess(0)


class FakeHarness:
    """Plays `swebench eval`: writes the per-instance reports the real one writes."""

    def __init__(self):
        self.calls = []
        self.version = swe_bench_settings.HARNESS_VERSION
        self.bad_gold = set()
        self.noop_resolves = set()

    def __call__(self, command, **kwargs):
        done = lambda out="": subprocess.CompletedProcess(command, 0, out, "")
        if "-c" in command and "importlib.metadata" in command[-1]:
            return done(self.version + "\n")
        if "eval" not in command:
            return done()
        self.calls.append(list(command))
        work = Path(kwargs["cwd"])
        run_id = command[command.index("--run-id") + 1]
        ids = [command[i + 1] for i, word in enumerate(command) if word == "-i"]
        dataset = [json.loads(line) for line in Path(command[2]).read_text().splitlines()]
        assert all(entry["image"].startswith(REPOSITORY) for entry in dataset)
        if "--gold" in command:
            verdicts = {i: ("gold", i not in self.bad_gold, True) for i in ids}
        else:
            predictions = [json.loads(line) for line in Path(command[command.index("-p") + 1]).read_text().splitlines()]
            verdicts = {}
            for prediction in predictions:
                if prediction["instance_id"] not in ids:
                    continue
                assert prediction["model_patch"].strip(), "the harness is never handed an empty patch"
                resolved = "FIX" in prediction["model_patch"] or prediction["instance_id"] in self.noop_resolves
                verdicts[prediction["instance_id"]] = (prediction["model_name_or_path"], resolved, True)
        for instance_id, (model, resolved, applied) in verdicts.items():
            directory = work / "logs/run_evaluation" / run_id / model.replace("/", "__") / instance_id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "report.json").write_text(json.dumps(
                {instance_id: {"resolved": resolved, "patch_successfully_applied": applied}}))
        return done()


class QuietMachine:
    """Night Shift's admission and start checks, on a machine with nothing else going on."""
    ignore_sessions = False
    refuse = None
    blocker = None

    @classmethod
    def admit(cls, vllm_host, mightling_bin, idle_minutes, end):
        return cls.refuse

    @classmethod
    def start_blocker(cls, vllm_host, mightling_bin, active, settings):
        return cls.blocker


class FakeHost:
    @classmethod
    def served_model(cls, host, timeout=3.0):
        return ("test-model", 262144)

    @classmethod
    def metrics(cls, host, timeout=3.0):
        return {"running": 0.0, "served": 1.0, "kv_pool": 150000.0}

    @classmethod
    def parallelism(cls, max_parallel, kv_pool, task_context):
        return 2


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A machine after `setup`: dataset snapshot, image tags, runtime, and the stand-ins."""
    docker = FakeDocker(tmp_path)
    harness = FakeHarness()
    monkeypatch.setattr(SweBenchDocker, "run", classmethod(lambda cls, *a, **k: docker.run(*a, **k)))
    monkeypatch.setattr(SweBenchDocker, "popen", classmethod(lambda cls, *a, **k: docker.popen(*a, **k)))
    monkeypatch.setattr(SweBenchHarness, "execute", staticmethod(harness))
    monkeypatch.setattr(SweBenchImages, "fetch_tags", classmethod(
        lambda cls: [i.replace("__", "-") for i in IDS if i != "beta__gadget-9"]))
    monkeypatch.setattr(SweBenchRuntime, "installed_mightling", classmethod(lambda cls: "/opt/none/ling"))
    monkeypatch.setattr(SweBenchRuntime, "ensure", classmethod(lambda cls, ling, patchelf: "runtime-hash"))
    index_calls = []

    def fake_mightling_code(command, **kwargs):
        index_calls.append((list(command), kwargs.get("cwd"), dict(kwargs.get("env") or {})))
        if "index" in command and kwargs["env"].get("MIGHTLING_CODE_LAYERS") == "exact":
            environment = kwargs["env"]
            assert Path(kwargs["cwd"], "widget.py").is_file(), "indexed in the copy of /testbed"
            assert "MIGHTLING_CODE_INDEXERS_DIR" not in environment, "the real SCIP indexers run"
            assert 'mightling_code_layers = "exact"' in Path(environment["DREAMFERENCE_CONFIG_PATH"]).read_text()
            scip = Path(environment["MIGHTLING_CODE_STATE_DIR"]) / "scip"
            scip.mkdir(parents=True, exist_ok=True)
            (scip / "scip-python-acme.db").write_text("store")
            runs = {"scip-python:acme": {"indexer": "scip-python", "root": "acme", "path_prefix": "acme/",
                                         "status": "ok", "store": "scip-python-acme.db", "peak_rss_mb": 1700},
                    "scip-typescript:web": {"indexer": "scip-typescript", "root": "web", "path_prefix": "web/",
                                            "status": "failed: heap", "store": "", "peak_rss_mb": 0}}
            (scip / "manifest.json").write_text(json.dumps({"runs": runs}))
            return subprocess.CompletedProcess(command, 0, "scip-python: ok\n", "")
        if "index" in command:
            environment = kwargs["env"]
            assert Path(kwargs["cwd"], "widget.py").is_file(), "indexed in the copy of /testbed"
            assert list(Path(environment["MIGHTLING_CODE_INDEXERS_DIR"]).iterdir()) == [], "universal layer only"
            (Path(environment["CBM_CACHE_DIR"]) / "_config.db").write_text("")
            (Path(environment["CBM_CACHE_DIR"]) / "host-path-testbed.db").write_text("graph")
        return subprocess.CompletedProcess(command, 0, "mightling-index-x-codebase-memory: ok\n", "")

    installed = tmp_path / "installed" / "ling-code"
    installed.parent.mkdir()
    installed.write_text("binary")
    monkeypatch.setattr(SweBenchCodeIndex, "execute", staticmethod(fake_mightling_code))
    monkeypatch.setattr(SweBenchCodeIndex, "host_binary", classmethod(lambda cls: str(installed)))
    monkeypatch.setattr(SweBenchRuntime, "host_libraries", classmethod(lambda cls, binary: (str(installed), [])))
    monkeypatch.setattr(SweBenchRunner, "admission", QuietMachine)
    monkeypatch.setattr(SweBenchRunner, "host", FakeHost)
    monkeypatch.setattr(SweBenchRunner, "sleep", staticmethod(lambda seconds: time.sleep(0.01)))
    monkeypatch.setattr(QuietMachine, "refuse", None)
    # The disk reserve is judged against 2 TiB free, not the disk of the machine running the suite.
    monkeypatch.setattr(shutil, "disk_usage", lambda path: types.SimpleNamespace(total=4 << 40, used=2 << 40, free=2 << 40))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    assert str(swe_bench_settings.CACHE_DIR).startswith(os.environ["HOME"]), "the suite must not see the real cache"
    python = Path(SweBenchHarness.tool("python"))  # "installed": the version query is the stand-in's
    python.parent.mkdir(parents=True)
    python.write_text("")
    snapshot = SweBenchHarness.snapshot_path("verified")
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("".join(json.dumps(row(i)) + "\n" for i in IDS))
    snapshot.with_suffix(".meta.json").write_text(json.dumps(
        {"revision": "rev-1", "rows": len(IDS), "dataset": "SWE-bench/SWE-bench_Verified"}))
    SweBenchRunner.smoke_path().write_text(json.dumps({"passed": True, "harness": swe_bench_settings.HARNESS_VERSION}))
    return {"docker": docker, "harness": harness, "settings": SweBenchSettings({"task_timeout": "5s"}),
            "index_calls": index_calls}


def run(bench, **arguments):
    arguments.setdefault("name", "r1")
    arguments.setdefault("settings", bench["settings"])
    return SweBenchRunner.run(**arguments)


# -- the prompt and what the container is given ---------------------------------------------------

def test_only_the_code_index_arm_is_told_to_use_the_code_tools():
    issue = "The widget is broken."
    without, with_index = (SweBenchInstanceRun.compose_prompt(issue, index) for index in (False, True))
    assert "code_" not in without
    assert "`code_search`" in with_index and "`code_impact`" in with_index
    # The hint is the only difference, and the issue still ends the prompt.
    assert with_index.replace(CODE_INDEX_HINT, "") == without
    assert with_index.endswith(issue)


def test_the_prompt_holds_the_issue_and_nothing_else_from_the_row(bench):
    assert run(bench, instances=["acme__widget-1"]) == 0
    docker = bench["docker"]
    exec_call = next(call for call in docker.calls if call[0] == "exec" and "ling" in call[2])
    prompt = exec_call[-1]
    assert prompt == SweBenchInstanceRun.compose_prompt("The widget is broken in acme__widget-1.")
    assert "There is no network." in prompt
    everything_the_container_gets = json.dumps([call for call in docker.calls if call[0] in ("run", "exec")])
    source = row("acme__widget-1")
    for field in FORBIDDEN_FIELDS:
        assert str(source[field]).strip("[]'") not in everything_the_container_gets, field
    assert "SECRET" not in everything_the_container_gets
    scratch = SweBenchRunStore("r1").directory / "scratch" / "acme__widget-1"
    assert "SECRET" not in "".join(p.read_text(errors="replace") for p in scratch.rglob("*") if p.is_file())


# -- the refine arm ---------------------------------------------------------------------------------

def mightling_prompts(docker):
    """Every `ling exec` call's prompt and whether it resumed a session, in order."""
    calls = [call for call in docker.calls if call[0] == "exec" and len(call) > 2 and call[2].endswith("/ling")]
    return [(call[-1], "resume" in call) for call in calls]


def prediction(name, instance_id):
    store = SweBenchRunStore(name)
    entries = [json.loads(line) for line in store.predictions_path.read_text().splitlines()]
    return next(entry["model_patch"] for entry in entries if entry["instance_id"] == instance_id)


def test_the_fix_prompt_keeps_the_issue_first_and_says_it_is_authoritative():
    issue = "The widget is broken."
    prompt = SweBenchInstanceRun.compose_fix_prompt(issue, "1. Intent: widget() returns 2.")
    assert prompt.index(issue) < prompt.index("1. Intent: widget() returns 2.")
    assert "the issue is authoritative" in prompt and "acceptance check" in prompt
    assert "code_" not in prompt
    assert CODE_INDEX_HINT in SweBenchInstanceRun.compose_fix_prompt(issue, "x", code_index=True)
    assert NO_REFINED in SweBenchInstanceRun.compose_fix_prompt(issue, "  \n")
    refine = SweBenchInstanceRun.compose_refine_prompt(issue)
    assert "/mightling-scratch/refined.md" in refine and "Do not fix anything yet" in refine and refine.endswith(issue)
    assert REFINE_CODE_INDEX_HINT in SweBenchInstanceRun.compose_refine_prompt(issue, code_index=True)


def test_refine_runs_two_fresh_sessions_and_hands_the_description_over(bench):
    assert run(bench, instances=["acme__widget-1"], refine=True) == 0
    prompts = mightling_prompts(bench["docker"])
    issue = "The widget is broken in acme__widget-1."
    described = "1. Intent: widget() returns 2 (acme__widget-1).\n"
    assert prompts == [(SweBenchInstanceRun.compose_refine_prompt(issue), False),
                       (SweBenchInstanceRun.compose_fix_prompt(issue, described), False)]
    store = SweBenchRunStore("r1")
    assert store.manifest()["refine"] is True
    state = store.states()["acme__widget-1"]
    assert state["status"] == "done"
    assert state["refine"]["refined"] == described and state["refine"]["refine_edited"] is False
    assert state["refine"]["refine_exec"] == "ok" and "fix_s" in state["refine"]
    assert "FIX" in prediction("r1", "acme__widget-1")


def test_refine_discards_what_the_first_step_changed_and_records_it(bench):
    bench["docker"].modes["acme__widget-1"] = "refine_edits"
    assert run(bench, instances=["acme__widget-1"], refine=True) == 0
    patch = prediction("r1", "acme__widget-1")
    assert "FIX" in patch
    assert "EXPLORING" not in patch and "stray.py" not in patch
    assert SweBenchRunStore("r1").states()["acme__widget-1"]["refine"]["refine_edited"] is True


def test_a_first_step_that_wrote_nothing_leaves_the_fix_step_the_issue_alone(bench):
    bench["docker"].modes["acme__widget-1"] = "refine_silent"
    assert run(bench, instances=["acme__widget-1"], refine=True) == 0
    (fix_prompt, resumed), = mightling_prompts(bench["docker"])[1:]
    assert NO_REFINED in fix_prompt and not resumed
    assert SweBenchRunStore("r1").states()["acme__widget-1"]["refine"]["refined_bytes"] == 0
    assert "FIX" in prediction("r1", "acme__widget-1")


def test_the_report_counts_the_refine_steps_apart(bench):
    ids = ["acme__widget-1", "acme__widget-2"]
    assert run(bench, instances=ids, refine=True, evaluate=True) == 0
    assert run(bench, instances=ids, name="r2", evaluate=True) == 0
    summary = SweBenchReport.summary(SweBenchRunStore("r1"))
    assert summary["refine"]["instances"] == 2
    # The first step's tokens alone: the stand-in spends 300 in and 30 out on it, 1000 and 50 on a fix.
    assert summary["refine"]["refine_tokens"] == {"input_tokens": 600, "cached_input_tokens": 0, "output_tokens": 60}
    assert summary["tokens"]["input_tokens"] == 2 * (300 + 1000)
    text = SweBenchReport.render(SweBenchRunStore("r1"))
    assert "Refine first        on:" in text and "changed the tree in 0 of 2" in text
    assert "Refine first" not in SweBenchReport.render(SweBenchRunStore("r2"))
    compared = SweBenchReport.against(SweBenchRunStore("r1"), SweBenchRunStore("r2"))
    assert "differs: refine: True | False" in compared


def test_the_container_is_capped_isolated_and_runs_as_the_user(bench):
    run(bench, instances=["acme__widget-1"])
    created = next(call for call in bench["docker"].calls if call[0] == "run")
    assert created[created.index("--network") + 1] == swe_bench_settings.NETWORK_NAME
    assert created[created.index("--memory") + 1] == created[created.index("--memory-swap") + 1] == "8G"
    assert created[created.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert any(mount.endswith(":/opt/ling:ro") for mount in created)
    env = dict(a.split("=", 1) for i, a in enumerate(created) if created[i - 1] == "-e")
    assert env["DREAMFERENCE_VLLM_HOST"] == "http://172.30.0.1:8000"
    assert env["PATH"].startswith("/opt/miniconda3/envs/testbed/bin:")
    assert env["MIGHTLING_NIGHT_RUN"] == "1"
    assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == ("safe.directory", "/testbed")
    agent = next(call for call in bench["docker"].calls if call[0] == "exec" and "ling" in call[2])
    assert "--dangerously-bypass-approvals-and-sandbox" in agent
    assert "model_auto_compact_token_limit=49152" in agent
    # The image is the arm64 one, and the container is removed afterwards.
    assert created[-3] == f"{REPOSITORY}:acme-widget-1"
    assert ["rm", "-f", SweBenchInstanceRun.container_name("r1", "acme__widget-1")] in bench["docker"].calls


def test_refs_that_reach_the_fix_are_removed_before_the_agent_starts(bench):
    run(bench, instances=["acme__widget-1"])
    box = next(iter(bench["docker"].removed.values()))
    assert git(box["repo"], "tag").stdout == ""
    assert git(box["repo"], "reflog").stdout == ""
    # The scrub ran as root (the image's .git belongs to root); everything else as the user.
    scrub = next(call for call in bench["docker"].calls if call[0] == "exec" and "gc --prune=now" in call[-1])
    assert scrub[1:5] == ["-u", "0", "-e", "HOME=/root"]
    others = [call for call in bench["docker"].calls if call[0] == "exec" and call is not scrub]
    assert others and all("-u" not in call[:4] for call in others)


# -- predictions ----------------------------------------------------------------------------------

def test_a_change_becomes_a_prediction_in_the_harness_format(bench):
    assert run(bench, instances=["acme__widget-1"]) == 0
    store = SweBenchRunStore("r1")
    [prediction] = store.predictions()
    assert set(prediction) == {"instance_id", "model_name_or_path", "model_patch"}
    assert prediction["model_name_or_path"].startswith("mightling-") and prediction["model_name_or_path"].endswith("/test-model")
    assert "+    return 2  # FIX" in prediction["model_patch"]
    state = store.state("acme__widget-1")
    assert state["status"] == "done" and state["session"] and state["base_tree_equal"]
    assert store.log_path("acme__widget-1").read_text().startswith('{"type": "thread.started"')


@pytest.mark.parametrize("mode,status", [("error", "error"), ("empty", "empty"), ("stall", "stalled")])
def test_an_agent_that_fails_or_changes_nothing_writes_an_empty_prediction(bench, mode, status):
    bench["docker"].default_mode = mode
    assert run(bench, instances=["acme__widget-1"]) == 0
    store = SweBenchRunStore("r1")
    [prediction] = store.predictions()
    assert prediction["model_patch"] == ""
    assert store.state("acme__widget-1")["status"] == status


def test_a_stall_is_nudged_and_a_nudge_that_works_is_a_change(bench):
    bench["docker"].default_mode = "stall_then_act"
    run(bench, instances=["acme__widget-1"])
    store = SweBenchRunStore("r1")
    assert store.state("acme__widget-1")["status"] == "done"
    assert store.state("acme__widget-1")["nudges"] == 1
    turns = [call for call in bench["docker"].calls if call[0] == "exec" and "ling" in call[2]]
    assert len(turns) == 2 and turns[1][-3] == "resume" and turns[1][-1] == NUDGE


def test_a_timeout_stops_the_container_and_still_submits_what_was_changed(bench):
    bench["docker"].default_mode = "hang"
    bench["settings"] = SweBenchSettings({"task_timeout": "0.2s"})
    assert run(bench, instances=["acme__widget-1"]) == 0
    store = SweBenchRunStore("r1")
    assert store.state("acme__widget-1")["status"] == "timeout"
    assert "partial.py" in store.predictions()[0]["model_patch"]
    assert any(call[0] == "stop" for call in bench["docker"].calls)


def test_binary_files_are_left_out_of_the_patch_and_named(bench):
    bench["docker"].default_mode = "binary"
    run(bench, instances=["acme__widget-1"])
    store = SweBenchRunStore("r1")
    patch = store.predictions()[0]["model_patch"]
    assert "widget.py" in patch and "Binary files" not in patch and ".pyc" not in patch
    assert store.state("acme__widget-1")["binary_files"] == ["__pycache__/widget.cpython-39.pyc"]


def test_the_collected_patch_applies_to_a_fresh_checkout(tmp_path):
    # The real scripts, against a real repository: what the runner collects must be what
    # `git apply` in the harness's container accepts.
    repo, scratch, fresh = tmp_path / "repo", tmp_path / "scratch", tmp_path / "fresh"
    for directory in (repo, scratch):
        directory.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n")
    (repo / "untracked-from-the-image.txt").write_text("left by the image build\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "base")
    subprocess.run(["git", "clone", "-q", str(repo), str(fresh)], check=True)
    (fresh / "untracked-from-the-image.txt").write_text("left by the image build\n")
    environment = dict(os.environ, TESTBED=str(repo), SCRATCH=str(scratch),
                       BASE_COMMIT=git(repo, "rev-parse", "HEAD").stdout.strip())
    git(repo, "tag", "v2-with-the-fix")
    assert subprocess.run(["bash", "-c", SCRUB_SCRIPT], env=environment, capture_output=True).returncode == 0
    assert git(repo, "tag").stdout == ""
    prepared = subprocess.run(["bash", "-c", PREPARE_SCRIPT], env=environment, capture_output=True, text=True)
    assert prepared.returncode == 0 and "tree: equal" in prepared.stdout, prepared.stderr
    assert git(repo, "status", "--porcelain").stdout.strip() == "?? untracked-from-the-image.txt"
    (repo / "a.py").write_text("x = 2\n")
    (repo / "new file.py").write_text("y = 3\n")
    (repo / "blob.bin").write_bytes(bytes(range(256)))
    assert subprocess.run(["bash", "-c", COLLECT_SCRIPT], env=environment).returncode == 0
    patch = (scratch / "patch.diff").read_text()
    assert (scratch / "binary-files").read_text() == "blob.bin\n"
    # What the image already had untracked is not part of the agent's patch.
    assert "untracked-from-the-image" not in patch and "blob.bin" not in patch
    applied = subprocess.run(["git", "-C", str(fresh), "apply", "-v", "-"], input=patch, capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    assert (fresh / "a.py").read_text() == "x = 2\n" and (fresh / "new file.py").read_text() == "y = 3\n"


# -- resuming -------------------------------------------------------------------------------------

def test_a_resumed_run_skips_finished_instances_and_appends(bench):
    run(bench, instances=["acme__widget-1"])
    store = SweBenchRunStore("r1")
    store.write_manifest  # the manifest is fixed: a resumed run reads it and ignores new selections
    manifest = store.manifest()
    manifest["instances"].append("acme__widget-2")
    manifest["images"]["acme__widget-2"] = {"image": f"{REPOSITORY}:acme-widget-2", "digest": "d"}
    store.manifest_path.write_text(json.dumps(manifest))
    before = len([call for call in bench["docker"].calls if call[0] == "run"])
    assert run(bench) == 0
    started = [call for call in bench["docker"].calls if call[0] == "run"][before:]
    assert len(started) == 1 and started[0][-3].endswith("acme-widget-2")
    assert store.finished() == ["acme__widget-1", "acme__widget-2"]


def test_an_interrupted_instance_leaves_no_prediction_and_runs_again(bench, monkeypatch):
    bench["docker"].default_mode = "hang"
    bench["settings"] = SweBenchSettings({"task_timeout": "60s"})
    ticks = {"n": 0}

    def sleep_then_interrupt(seconds):
        ticks["n"] += 1
        time.sleep(0.05)
        if ticks["n"] == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(SweBenchRunner, "sleep", staticmethod(sleep_then_interrupt))
    assert run(bench, instances=["acme__widget-1"]) == 130
    store = SweBenchRunStore("r1")
    assert store.predictions() == []
    assert store.state("acme__widget-1")["status"] == "interrupted"
    bench["docker"].default_mode = "change"
    monkeypatch.setattr(SweBenchRunner, "sleep", staticmethod(lambda seconds: time.sleep(0.01)))
    assert run(bench) == 0
    assert store.state("acme__widget-1")["status"] == "done"


def test_a_torn_last_line_in_predictions_is_ignored_and_the_file_stays_readable(bench):
    store = SweBenchRunStore("torn")
    store.directory.mkdir(parents=True)
    store.predictions_path.write_text('{"instance_id": "a", "model_name_or_path": "m", "model_patch": ""}\n{"instance_id": "b", "mod')
    assert store.finished() == ["a"]
    store.append_prediction("c", "m", "diff")
    assert store.finished() == ["a", "c"]


def test_a_run_resumed_with_another_model_or_build_is_refused(bench, monkeypatch, capsys):
    run(bench, instances=["acme__widget-1"])
    monkeypatch.setattr(FakeHost, "served_model", classmethod(lambda cls, host, timeout=3.0: ("other-model", 1)))
    assert run(bench) == 1
    assert "one configuration" in capsys.readouterr().out


# -- validation and exclusion ---------------------------------------------------------------------

def test_only_validated_instances_run_and_the_rest_are_excluded_not_unresolved(bench):
    bench["harness"].bad_gold = {"acme__widget-2"}
    assert run(bench) == 0
    store = SweBenchRunStore("r1")
    manifest = store.manifest()
    assert manifest["instances"] == ["acme__widget-1", "beta__gadget-7"]
    assert manifest["excluded"] == {
        "acme__widget-2": "the reference patch does not resolve it here",
        "beta__gadget-9": "no " + SweBenchSettings.architecture() + " image",
    }
    assert sorted(store.finished()) == ["acme__widget-1", "beta__gadget-7"]
    SweBenchEvaluator.grade(store, bench["settings"])
    report = SweBenchReport.render(store)
    assert "Resolved            2 / 2 validated   100.0%" in report
    assert "Not evaluable       2 of 4 selected (4 in the dataset)" in report


def test_an_instance_a_noop_patch_resolves_is_not_validated(bench):
    bench["harness"].noop_resolves = {"acme__widget-1"}
    outcome = SweBenchEvaluator.validate("verified", ["acme__widget-1", "acme__widget-2"], bench["settings"])
    assert outcome == {"acme__widget-1": "a patch that fixes nothing resolves it here", "acme__widget-2": None}
    assert list(SweBenchImages.validated()) == ["acme__widget-2"]
    assert SweBenchImages.rejected() == {"acme__widget-1": "a patch that fixes nothing resolves it here"}
    # The no-op stands in for the empty patch, which the harness never runs.
    noop = (SweBenchEvaluator.validation_dir() / "noop.jsonl").read_text()
    assert json.loads(noop.splitlines()[0])["model_patch"] == NOOP_PATCH


def test_validation_is_not_repeated_and_force_repeats_it(bench):
    SweBenchEvaluator.validate("verified", ["acme__widget-1"], bench["settings"])
    calls = len(bench["harness"].calls)
    SweBenchEvaluator.validate("verified", ["acme__widget-1"], bench["settings"])
    assert len(bench["harness"].calls) == calls
    SweBenchEvaluator.validate("verified", ["acme__widget-1"], bench["settings"], force=True)
    assert len(bench["harness"].calls) == calls + 2


def test_an_image_that_cannot_be_pulled_is_not_recorded_as_rejected(bench):
    bench["docker"].unpullable = {f"{REPOSITORY}:acme-widget-1"}
    outcome = SweBenchEvaluator.validate("verified", ["acme__widget-1"], bench["settings"])
    assert outcome["acme__widget-1"].startswith("not validated yet")
    assert SweBenchImages.rejected() == {}


def test_validation_decides_from_the_two_verdicts():
    problem = SweBenchEvaluator.validation_problem
    assert problem({"resolved": True}, {"resolved": False}) is None
    assert "does not resolve" in problem({"resolved": False}, {"resolved": False})
    assert "fixes nothing" in problem({"resolved": True}, {"resolved": True})
    assert "could not be graded" in problem(None, {"resolved": False})
    assert "could not be graded" in problem({"resolved": True}, None)


# -- grading --------------------------------------------------------------------------------------

def test_eval_grades_only_the_ungraded_and_never_hands_the_harness_an_empty_patch(bench):
    bench["docker"].modes = {"acme__widget-2": "empty"}
    run(bench, instances=["acme__widget-1", "acme__widget-2"])
    store = SweBenchRunStore("r1")
    validation_calls = len(bench["harness"].calls)
    results = SweBenchEvaluator.grade(store, bench["settings"])
    assert results["acme__widget-1"]["resolved"] is True
    assert results["acme__widget-2"] == {"resolved": False, "empty": True}
    [call] = bench["harness"].calls[validation_calls:]
    assert call[call.index("--run-id") + 1] == "r1-1"
    assert [call[i + 1] for i, word in enumerate(call) if word == "-i"] == ["acme__widget-1"]
    # Nothing new: no harness call at all.
    SweBenchEvaluator.grade(store, bench["settings"])
    assert len(bench["harness"].calls) == validation_calls + 1


def test_a_changed_grader_starts_a_new_grading_of_every_instance(bench):
    run(bench, instances=["acme__widget-1"])
    store = SweBenchRunStore("r1")
    SweBenchEvaluator.grade(store, bench["settings"])
    assert store.gradings() == [1]
    bench["harness"].version = "5.0.3"
    results = SweBenchEvaluator.grade(store, bench["settings"])
    assert store.gradings() == [1, 2] and "acme__widget-1" in results
    assert bench["harness"].calls[-1][bench["harness"].calls[-1].index("--run-id") + 1] == "r1-2"
    assert store.grading(2)["grader"]["harness"] == "5.0.3"


def test_an_instance_the_harness_gave_no_verdict_for_stays_ungraded(bench, monkeypatch):
    run(bench, instances=["acme__widget-1"])
    store = SweBenchRunStore("r1")
    monkeypatch.setattr(SweBenchHarness, "instance_report", classmethod(lambda cls, *a: None))
    assert SweBenchEvaluator.grade(store, bench["settings"]) == {}
    report = SweBenchReport.render(store)
    assert "INCOMPLETE          0 not run yet, 1 run but not graded yet" in report


def test_remove_images_works_one_repository_at_a_time(bench):
    assert run(bench, evaluate=True, keep_images=False) == 0
    docker, harness = bench["docker"], bench["harness"]
    graded = [sorted(call[i + 1] for i, word in enumerate(call) if word == "-i")
              for call in harness.calls if "-p" in call and "to-grade.jsonl" in call[call.index("-p") + 1]]  # finish order varies
    assert graded == [["acme__widget-1", "acme__widget-2"], ["beta__gadget-7"]]
    assert docker.present == set()
    # A repository's images go only after it is graded, and before the next repository starts.
    order = [call for call in docker.calls if call[0] in ("rmi", "run")]
    names = [call[-1] if call[0] == "rmi" else call[-3] for call in order]
    assert [call[0] for call in order] == ["run", "run", "rmi", "rmi", "run", "rmi"], names
    assert SweBenchReport.summary(SweBenchRunStore("r1"))["resolved"] == 3


def test_eval_without_cycling_grades_everything_in_one_call(bench):
    assert run(bench, evaluate=True) == 0
    calls = [call for call in bench["harness"].calls if "-p" in call and "to-grade.jsonl" in call[call.index("-p") + 1]]
    assert len(calls) == 1 and len(bench["docker"].present) == 3


# -- admission ------------------------------------------------------------------------------------

def test_run_refuses_without_a_passed_smoke(bench, capsys):
    SweBenchRunner.smoke_path().unlink()
    assert run(bench, instances=["acme__widget-1"]) == 1
    assert "swe-bench smoke" in capsys.readouterr().out
    SweBenchRunner.smoke_path().write_text(json.dumps({"passed": True, "harness": "4.0.0"}))
    assert run(bench, instances=["acme__widget-1"]) == 1  # a harness upgrade invalidates the pass


def test_run_refuses_while_a_night_run_holds_the_lock_and_names_the_holder(bench, capsys):
    with NightShiftQueue.runner_lock(NightShiftQueue.night_dir()) as held:
        assert held
        assert run(bench, instances=["acme__widget-1"]) == 1
    assert "a Night Shift run holds the runner lock" in capsys.readouterr().out


def test_other_commands_are_told_a_swe_bench_run_holds_the_lock(bench, capsys):
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
    with NightShiftQueue.runner_lock(NightShiftQueue.night_dir(), holder="a SWE-bench run") as held:
        assert held and NightShiftQueue.runner_holder() == "a SWE-bench run"
        # A second taker must not erase the holder's name.
        with NightShiftQueue.runner_lock(NightShiftQueue.night_dir(), holder="someone else") as second:
            assert not second
        assert NightShiftQueue.runner_holder() == "a SWE-bench run"
        with pytest.raises(SystemExit):
            DreamferenceCLIController._refuse_during_night_run("codex build")
    assert "A SWE-bench run is in progress, so `ling-admin codex build` waits" in capsys.readouterr().out
    assert NightShiftQueue.runner_holder() is None


def test_admission_refuses_on_the_disk_reserve(bench, capsys):
    bench["settings"] = SweBenchSettings({"disk_reserve": "100000T"})
    assert run(bench, instances=["acme__widget-1"]) == 1
    assert "reserve" in capsys.readouterr().out
    assert bench["docker"].containers == {} and bench["docker"].removed == {}


def test_night_shifts_admission_decides_whether_the_run_starts(bench, monkeypatch, capsys):
    monkeypatch.setattr(QuietMachine, "refuse", "a Mightling session is open (interactive use wins)")
    assert run(bench, instances=["acme__widget-1"]) == 1
    assert "interactive use wins" in capsys.readouterr().out
    assert not SweBenchRunStore("r1").manifest_path.exists()


def test_until_stops_new_starts_and_the_run_resumes_later(bench, monkeypatch):
    from datetime import datetime, timedelta
    monkeypatch.setattr(SweBenchRunner, "until_time", classmethod(
        lambda cls, until: datetime.now().astimezone() - timedelta(minutes=1) if until else None))
    assert run(bench, instances=["acme__widget-1", "acme__widget-2"], until="07:00") == 0
    store = SweBenchRunStore("r1")
    assert store.finished() == []
    assert run(bench) == 0
    assert sorted(store.finished()) == ["acme__widget-1", "acme__widget-2"]


# -- the manifest and reports ---------------------------------------------------------------------

def test_the_manifest_records_what_was_measured(bench):
    run(bench, instances=["acme__widget-1"])
    manifest = SweBenchRunStore("r1").manifest()
    for field in ("started", "dataset", "dataset_revision", "harness", "architecture", "image_source",
                  "images", "instances", "excluded", "model_name_or_path", "served_model", "served_context",
                  "codex_tag", "runtime_hash", "cave_mode", "airgapped", "task_context", "task_timeout_s",
                  "nudges", "parallelism", "repository_commit"):
        assert field in manifest, field
    assert manifest["dataset_revision"] == "rev-1" and manifest["served_model"] == "test-model"
    assert manifest["images"]["acme__widget-1"]["image"] == f"{REPOSITORY}:acme-widget-1"
    with pytest.raises(FileExistsError):
        SweBenchRunStore("r1").write_manifest({})


def test_the_report_counts_add_up_and_says_what_the_number_is_not(bench):
    bench["docker"].modes = {"acme__widget-2": "stall", "beta__gadget-7": "error"}
    run(bench)
    store = SweBenchRunStore("r1")
    SweBenchEvaluator.grade(store, bench["settings"])
    summary = SweBenchReport.summary(store)
    assert (summary["validated"], summary["excluded"], summary["finished"], summary["graded"]) == (3, 1, 3, 3)
    assert summary["resolved"] == 1 and summary["statuses"] == {"done": 1, "stalled": 1, "error": 1}
    text = SweBenchReport.write(store)
    assert "Resolved            1 / 3 validated   33.3%" in text
    assert "Stalled 1" in text and "Agent error 1" in text
    assert "acme/widget                  1 / 2" in text and "beta/gadget                  0 / 1" in text
    assert "Not comparable with published SWE-bench scores" in text
    assert (store.directory / "report.md").read_text() == text


def test_against_compares_instance_by_instance_and_refuses_mismatched_sets(bench):
    run(bench, name="a", instances=["acme__widget-1", "acme__widget-2"])
    bench["docker"].modes = {"acme__widget-2": "empty"}
    run(bench, name="b", instances=["acme__widget-1", "acme__widget-2"])
    run(bench, name="c", instances=["acme__widget-1"])
    for name in "abc":
        SweBenchEvaluator.grade(SweBenchRunStore(name), bench["settings"])
    text = SweBenchReport.against(SweBenchRunStore("a"), SweBenchRunStore("b"))
    assert "Resolved only by a (1): acme__widget-2" in text and "Resolved only by b (0): none" in text
    assert "No measurable difference." in text  # one discordant pair proves nothing
    refused = SweBenchReport.against(SweBenchRunStore("a"), SweBenchRunStore("c"))
    assert refused.startswith("Not compared") and "different instances" in refused


def test_mcnemar_and_the_paired_interval():
    assert SweBenchReport.mcnemar(0, 0) == 1.0
    assert SweBenchReport.mcnemar(10, 0) == pytest.approx(2 / 1024)
    assert SweBenchReport.mcnemar(3, 3) == 1.0
    low, high = SweBenchReport.paired_interval(10, 0, 20)
    assert low > 0 and high > low


# -- the code-index arm ---------------------------------------------------------------------------

def test_without_the_code_index_the_container_gets_nothing_of_mightling_code(bench):
    run(bench, instances=["acme__widget-1"])
    created = next(call for call in bench["docker"].calls if call[0] == "run")
    assert "ling-code" not in json.dumps(created) and "MIGHTLING_CODE" not in json.dumps(created)
    assert bench["index_calls"] == []
    agent = next(call for call in bench["docker"].calls if call[0] == "exec" and "ling" in call[2])
    assert "mcp_servers" not in json.dumps(agent)
    store = SweBenchRunStore("r1")
    assert store.manifest()["code_index"] == "off" and "index" not in store.state("acme__widget-1")
    assert "Code index          off" in SweBenchReport.render(store)


def test_with_the_code_index_the_repository_is_indexed_on_the_host_and_mounted_read_only(bench):
    assert run(bench, instances=["acme__widget-1"], code_index="universal") == 0
    docker = bench["docker"]
    # Indexed from a copy of /testbed taken out of the image, before any agent container exists.
    order = [call[0] for call in docker.calls if call[0] in ("create", "cp", "run")]
    assert order == ["create", "cp", "run"]
    [(command, cwd, environment)] = [call for call in bench["index_calls"] if "index" in call[0]]
    assert command[1:] == ["index", "--wait"] and cwd.endswith("/testbed")
    directory = SweBenchCodeIndex.index_dir(row("acme__widget-1"))
    assert environment["MIGHTLING_CODE_STATE_DIR"] == str(directory / "state")
    assert not (directory / "testbed").exists()  # the copy is not kept
    created = next(call for call in docker.calls if call[0] == "run")
    mounts = [created[i + 1] for i, word in enumerate(created) if word == "-v"]
    assert f"{directory}:/mightling-index:ro" in mounts
    assert f"{SweBenchCodeIndex.runtime_dir()}:/opt/ling-code:ro" in mounts
    env = dict(a.split("=", 1) for i, a in enumerate(created) if created[i - 1] == "-e")
    assert env["MIGHTLING_CODE_BIN"] == "/opt/ling-code/bin/ling-code"
    assert env["MIGHTLING_CODE_STATE_DIR"] == "/mightling-index/state"
    assert env["MIGHTLING_CODE_GRAPH_DB"] == "/mightling-index/cbm/host-path-testbed.db"
    assert env["MIGHTLING_CODE_PROJECT"] == "host-path-testbed"
    assert env["PATH"].startswith("/opt/ling-code/bin:/opt/miniconda3/envs/testbed/bin:")
    # The agent's ling declares the index's MCP server itself, as a required one, so the first
    # request waits for its tools instead of going out without them.
    agent = next(call for call in docker.calls if call[0] == "exec" and "ling" in call[2])
    overrides = [agent[i + 1] for i, word in enumerate(agent) if word == "-c"]
    assert "mcp_servers.ling_code.required=true" in overrides
    assert 'mcp_servers.ling_code.command="/opt/ling-code/bin/ling-code"' in overrides
    assert 'mcp_servers.ling_code.args=["mcp"]' in overrides
    forwarded = next(o for o in overrides if o.startswith("mcp_servers.ling_code.env_vars="))
    assert '"MIGHTLING_CODE_GRAPH_DB"' in forwarded and '"MIGHTLING_CODE_PROJECT"' in forwarded
    store = SweBenchRunStore("r1")
    state = store.state("acme__widget-1")
    assert store.manifest()["code_index"] == "universal"
    assert state["index"]["layers"] == ["universal"] and state["index"]["cached"] is False
    assert store.log_stats("acme__widget-1") == {
        "commands": 2, "puffin_code_calls": 1, "input_tokens": 1000, "cached_input_tokens": 900, "output_tokens": 50}
    report = SweBenchReport.render(store)
    assert "Code index          universal" in report and "called ling-code 1 time(s), in 1 of 1 instance(s)" in report


def test_an_index_is_cached_by_repository_and_commit_and_its_time_is_not_the_agents(bench, monkeypatch):
    run(bench, name="a", instances=["acme__widget-1"], code_index="universal")
    builds = len([call for call in bench["index_calls"] if "index" in call[0]])
    run(bench, name="b", instances=["acme__widget-1"], code_index="universal")
    assert len([call for call in bench["index_calls"] if "index" in call[0]]) == builds
    state = SweBenchRunStore("b").state("acme__widget-1")
    assert state["index"]["cached"] is True
    # The agent's clock starts at its container, after every index is built.
    record = SweBenchCodeIndex.record(row("acme__widget-1"))
    record["seconds"] = 5000.0
    (SweBenchCodeIndex.index_dir(row("acme__widget-1")) / "index.json").write_text(json.dumps(record))
    run(bench, name="c", instances=["acme__widget-1"], code_index="universal")
    state = SweBenchRunStore("c").state("acme__widget-1")
    assert state["index"]["seconds"] == 5000.0 and state["wall_s"] < 60


def test_a_run_whose_index_cannot_be_built_does_not_start(bench, monkeypatch, capsys):
    monkeypatch.setattr(SweBenchCodeIndex, "execute", staticmethod(
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1 if "index" in command else 0, "", "boom")))
    assert run(bench, instances=["acme__widget-1"], code_index="universal") == 1
    assert "a run measures one arm" in capsys.readouterr().out
    assert not any(call[0] == "run" for call in bench["docker"].calls)
    assert run(bench, name="x", instances=["acme__widget-1"], code_index="bogus") == 1


def test_the_exact_arm_mounts_scip_stores_alone_and_names_what_did_not_finish(bench, monkeypatch):
    monkeypatch.setattr(SweBenchCodeIndex, "sleep", staticmethod(lambda seconds: None))
    assert run(bench, instances=["acme__widget-1"], code_index="exact") == 0
    [(command, cwd, environment)] = [call for call in bench["index_calls"] if "index" in call[0]]
    assert command[1:] == ["index", "--wait"] and environment["MIGHTLING_CODE_LAYERS"] == "exact"
    directory = SweBenchCodeIndex.index_dir(row("acme__widget-1"), "exact")
    assert directory != SweBenchCodeIndex.index_dir(row("acme__widget-1")), "a cache of its own"
    assert not (directory / "testbed").exists()
    created = next(call for call in bench["docker"].calls if call[0] == "run")
    mounts = [created[i + 1] for i, word in enumerate(created) if word == "-v"]
    assert f"{directory}:/mightling-index:ro" in mounts
    env = dict(a.split("=", 1) for i, a in enumerate(created) if created[i - 1] == "-e")
    assert env["MIGHTLING_CODE_LAYERS"] == "exact" and env["MIGHTLING_CODE_STATE_DIR"] == "/mightling-index/state"
    assert "MIGHTLING_CODE_GRAPH_DB" not in env
    # Codex hands the MCP server only the variables it is told to: the layer setting is one.
    agent = next(call for call in bench["docker"].calls if call[0] == "exec" and "ling" in call[2])
    overrides = [agent[i + 1] for i, word in enumerate(agent) if word == "-c"]
    assert '"MIGHTLING_CODE_LAYERS"' in next(o for o in overrides if o.startswith("mcp_servers.ling_code.env_vars="))
    store = SweBenchRunStore("r1")
    index = store.state("acme__widget-1")["index"]
    assert index["layers"] == ["exact"] and index["stores"] == ["scip-python:acme"] and index["peak_mb"] == 1700
    assert index["failed"] == {"scip-typescript:web": "failed: heap"}
    report = SweBenchReport.render(store)
    assert "Code index          exact (SCIP stores only, no graph; peak 1700 MiB" in report
    assert "1 instance(s) with an indexer that did not finish, 0 with no store at all" in report
    # A second run reuses the stores.
    run(bench, name="again", instances=["acme__widget-1"], code_index="exact")
    assert len([call for call in bench["index_calls"] if "index" in call[0]]) == 1
    assert SweBenchRunStore("again").state("acme__widget-1")["index"]["cached"] is True


def test_the_exact_arm_retries_a_run_the_busy_model_deferred(bench, monkeypatch):
    monkeypatch.setattr(SweBenchCodeIndex, "sleep", staticmethod(lambda seconds: None))
    attempts = []

    def deferred_then_ok(command, **kwargs):
        if "index" in command:
            attempts.append(command)
            scip = Path(kwargs["env"]["MIGHTLING_CODE_STATE_DIR"]) / "scip"
            scip.mkdir(parents=True, exist_ok=True)
            (scip / "s.db").write_text("store")
            status = "deferred: busy" if len(attempts) == 1 else "ok"
            (scip / "manifest.json").write_text(json.dumps(
                {"runs": {"scip-python:acme": {"status": status, "store": "" if len(attempts) == 1 else "s.db"}}}))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(SweBenchCodeIndex, "execute", staticmethod(deferred_then_ok))
    assert run(bench, instances=["acme__widget-1"], code_index="exact") == 0
    assert len(attempts) == 2
    assert SweBenchRunStore("r1").state("acme__widget-1")["index"]["stores"] == ["scip-python:acme"]


def test_a_mightling_code_under_test_gets_a_runtime_of_its_own(monkeypatch, tmp_path):
    monkeypatch.delenv("DREAMFERENCE_SWE_BENCH_MIGHTLING_CODE", raising=False)
    installed = SweBenchCodeIndex.runtime_dir()
    monkeypatch.setenv("DREAMFERENCE_SWE_BENCH_MIGHTLING_CODE", str(tmp_path / "ling-code"))
    assert SweBenchCodeIndex.runtime_dir() != installed


def test_a_resumed_run_keeps_its_arm(bench):
    run(bench, instances=["acme__widget-1"], code_index="universal")
    store = SweBenchRunStore("r1")
    manifest = store.manifest()
    manifest["instances"].append("acme__widget-2")
    manifest["images"]["acme__widget-2"] = {"image": f"{REPOSITORY}:acme-widget-2", "digest": "d"}
    store.manifest_path.write_text(json.dumps(manifest))
    assert run(bench) == 0  # no --code-index on the resume
    assert store.state("acme__widget-2")["index"]["layers"] == ["universal"]


def test_against_sets_the_two_arms_side_by_side(bench):
    ids = ["acme__widget-1", "acme__widget-2", "beta__gadget-7"]
    bench["docker"].modes = {"acme__widget-2": "empty"}
    run(bench, name="without", instances=ids)
    bench["docker"].modes = {"beta__gadget-7": "empty"}
    run(bench, name="with", instances=ids, code_index="universal")
    for name in ("with", "without"):
        SweBenchEvaluator.grade(SweBenchRunStore(name), bench["settings"])
    text = SweBenchReport.against(SweBenchRunStore("with"), SweBenchRunStore("without"))
    assert "differs: code_index: universal | off" in text
    assert "Side by side, on the 3 instance(s) graded in both (one run per arm is one sample of each):" in text
    lines = {line.split()[0] + " " + line.split()[1]: line for line in text.splitlines() if len(line.split()) > 2}
    assert lines["code index"].split()[-2:] == ["universal", "off"]
    assert lines["resolved 2"].split()[1:] == ["2", "(66.7%)", "2", "(66.7%)"]
    assert lines["ling-code calls"].split()[-2:] == ["3", "0"]
    assert lines["instances using"].split()[-2:] == ["3", "0"]
    assert lines["input tokens"].split()[-2:] == ["3,000", "3,000"]
    assert "Resolved in both: 1, only with: 1, only without: 1, neither: 0" in text
    assert "In with the agent called ling-code in 3 of 3 instances" in text
    assert "No measurable difference." in text
    row_line = next(line for line in text.splitlines() if line.strip().startswith("acme__widget-2"))
    assert "only with" in row_line and "1 ling-code" in row_line


def test_naming_mightling_code_is_not_calling_it(bench):
    store = SweBenchRunStore("counts")
    store.log_path("x").parent.mkdir(parents=True)
    command = lambda text: json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": text}})
    store.log_path("x").write_text("Reading additional input from stdin...\n" + "\n".join([
        command("/bin/bash -lc 'ls /opt/ling-code/bin'"), command("/bin/bash -lc 'which ling-code'"),
        command("/bin/bash -lc 'cd /testbed && ling-code refs Widget'"),
        command("/bin/bash -lc 'ling-code   callers a.b | head'")]) + "\n")
    assert store.log_stats("x")["commands"] == 4 and store.log_stats("x")["puffin_code_calls"] == 2


def test_an_index_question_asked_as_a_tool_counts_as_one(bench):
    # Since 2026-10-02 the launcher gives the model the index as `code_*` tools; `ling exec`
    # reports such a call as an `mcp_tool_call` item, not as a command.
    store = SweBenchRunStore("tools")
    store.log_path("x").parent.mkdir(parents=True)
    tool = lambda server, name: json.dumps({"type": "item.completed", "item": {
        "type": "mcp_tool_call", "server": server, "tool": name, "arguments": {}}})
    store.log_path("x").write_text("\n".join([
        tool("ling_code", "code_search"), tool("ling_code", "code_show"),
        tool("codex", "list_mcp_resources"), tool("other", "decode_refs"),
        json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}})]) + "\n")
    assert store.log_stats("x")["commands"] == 5 and store.log_stats("x")["puffin_code_calls"] == 2


def test_an_arm_that_never_used_the_index_is_said_to_prove_nothing(bench, monkeypatch):
    run(bench, name="without", instances=["acme__widget-1"])
    run(bench, name="with", instances=["acme__widget-1"], code_index="universal")
    for name in ("with", "without"):
        SweBenchEvaluator.grade(SweBenchRunStore(name), bench["settings"])
    monkeypatch.setattr(SweBenchRunStore, "log_stats", lambda self, instance_id: {
        "commands": 3, "puffin_code_calls": 0, "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1})
    text = SweBenchReport.against(SweBenchRunStore("with"), SweBenchRunStore("without"))
    assert "In with the agent never called ling-code: this comparison says nothing about the index." in text


# -- setup, smoke, the runtime, the command line --------------------------------------------------

def test_smoke_validates_the_fixed_set_runs_the_agent_and_records_the_pass(bench, monkeypatch):
    smoke_ids = ("acme__widget-1", "acme__widget-2", "beta__gadget-7")
    monkeypatch.setattr(swe_bench_settings, "SMOKE_INSTANCES", smoke_ids)
    SweBenchRunner.smoke_path().unlink()
    assert SweBenchCommand.smoke() == 0
    record = json.loads(SweBenchRunner.smoke_path().read_text())
    assert record["passed"] and record["harness"] == swe_bench_settings.HARNESS_VERSION and record["agent_resolved"]
    assert SweBenchRunner.smoke_passed()


def test_smoke_fails_when_the_grader_cannot_fail(bench, monkeypatch, capsys):
    monkeypatch.setattr(swe_bench_settings, "SMOKE_INSTANCES", ("acme__widget-1",))
    bench["harness"].noop_resolves = {"acme__widget-1"}
    SweBenchRunner.smoke_path().unlink()
    assert SweBenchCommand.smoke() == 1
    assert not SweBenchRunner.smoke_passed()
    assert "cannot be trusted" in capsys.readouterr().out


def test_the_runtime_names_every_library_mightling_is_linked_against(monkeypatch):
    ldd = ("\tlinux-vdso.so.1 (0x0000)\n\tlibgcc_s.so.1 => /lib/aarch64-linux-gnu/libgcc_s.so.1 (0x1)\n"
           "\tlibm.so.6 => /lib/aarch64-linux-gnu/libm.so.6 (0x2)\n\tlibc.so.6 => /lib/aarch64-linux-gnu/libc.so.6 (0x3)\n"
           "\t/lib/ld-linux-aarch64.so.1 (0x4)\n")
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0, ldd, "")
                        if command[0] == "ldd" else real_run(command, **kw))
    loader, libraries = SweBenchRuntime.host_libraries("/x/ling")
    assert loader == "/lib/ld-linux-aarch64.so.1" and len(libraries) == 3
    ldd += "\tliblzma.so.5 => /lib/aarch64-linux-gnu/liblzma.so.5 (0x6)\n"
    loader, libraries = SweBenchRuntime.host_libraries("/x/ling")
    assert "/lib/aarch64-linux-gnu/liblzma.so.5" in libraries and len(libraries) == 4
    ldd += "\tlibssl.so.3 => /lib/aarch64-linux-gnu/libssl.so.3 (0x5)\n"
    with pytest.raises(ValueError, match="libssl.so.3"):
        SweBenchRuntime.host_libraries("/x/ling")


def test_the_dataset_file_for_the_harness_names_this_machines_images(tmp_path):
    path = tmp_path / "dataset.jsonl"
    written = SweBenchHarness.write_dataset_file(path, [row("acme__widget-1"), row("acme__widget-2")],
                                                 {"acme__widget-1": "repo:acme-widget-1"})
    assert written == ["acme__widget-1"]
    [entry] = [json.loads(line) for line in path.read_text().splitlines()]
    assert entry["image"] == "repo:acme-widget-1" and entry["problem_statement"]


def test_selection_is_sorted_limited_and_rejects_unknown_ids(bench, tmp_path):
    assert SweBenchRunner.select("verified", None, 2, None) == ["acme__widget-1", "acme__widget-2"]
    subset = tmp_path / "ids.txt"
    subset.write_text("beta__gadget-7\n# a comment\nacme__widget-2\n")
    assert SweBenchRunner.select("verified", None, None, str(subset)) == ["acme__widget-2", "beta__gadget-7"]
    with pytest.raises(ValueError, match="nope"):
        SweBenchRunner.select("verified", ["nope"], None, None)


def test_settings_defaults_and_dataset_names():
    settings = SweBenchSettings({})
    assert (settings.task_timeout_s, settings.max_parallel, settings.task_context) == (45 * 60, 3, 49152)
    assert SweBenchSettings({"task_timeout": "2h", "eval_workers": 2}).task_timeout_s == 7200
    assert SweBenchSettings.dataset_id("lite") == "SWE-bench/SWE-bench_Lite"
    assert SweBenchSettings.dataset_id("org/Custom") == "org/Custom"


def test_the_command_line_parses_every_subcommand():
    import argparse
    parser = argparse.ArgumentParser()
    SweBenchCommand.add_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["swe-bench", "run", "--instances", "a,b", "--limit", "3", "--name", "x", "--eval", "--until", "07:00"])
    assert (args.swe_bench_command, args.instances, args.limit, args.name, args.eval, args.until) == ("run", "a,b", 3, "x", True, "07:00")
    for words in (["setup", "--validate", "--limit", "5"], ["smoke"], ["eval", "r"], ["report", "r", "--against", "s"],
                  ["status"], ["clean", "--images"]):
        assert parser.parse_args(["swe-bench", *words]).swe_bench_command == words[0]
    assert SweBenchCommand.dispatch(parser.parse_args(["swe-bench"])) == 2


def test_status_and_clean_touch_only_the_benchmarks_own_things(bench, capsys):
    run(bench, instances=["acme__widget-1"])
    assert SweBenchCommand.status() == 0
    out = capsys.readouterr().out
    assert "r1: 1 of 1 run, 0 graded, 0 resolved" in out and "Smoke: passed" in out
    assert SweBenchCommand.clean("r1", images=True) == 0
    assert not (SweBenchRunStore("r1").directory / "scratch").exists()
    assert bench["docker"].present == set()
    assert ["ps", "-aq", "--filter", "label=ling.swe-bench.run=r1"] in bench["docker"].calls


# -- the system prompt (specs/DREAMFERENCE_MIGHTLING_PROMPT.md §6.2) ---------------------------------

def container_env(bench):
    created = next(call for call in bench["docker"].calls if call[0] == "run")
    env = dict(a.split("=", 1) for i, a in enumerate(created) if created[i - 1] == "-e")
    mounts = [created[i + 1] for i, word in enumerate(created) if word == "-v"]
    return env, mounts


def test_without_prompt_the_run_records_and_passes_the_configured_one(bench, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_PROMPT", raising=False)
    assert run(bench, instances=["acme__widget-1"]) == 0
    env, mounts = container_env(bench)
    assert env["DREAMFERENCE_MIGHTLING_PROMPT"] == "default"
    assert not any("system-prompts" in mount for mount in mounts)
    manifest = SweBenchRunStore("r1").manifest()
    assert (manifest["prompt"], manifest["prompt_sha256"]) == ("default", None)
    assert "Prompt default; cave mode" in SweBenchReport.render(SweBenchRunStore("r1"))


def test_masking_is_off_unless_asked_and_two_runs_are_told_apart_by_it(bench, monkeypatch):
    # specs/DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md §4.1: the container's launcher reads the switch.
    assert run(bench, name="plain", instances=["acme__widget-1"]) == 0
    assert run(bench, name="masked", instances=["acme__widget-1"], mask="on") == 0
    runs = [call for call in bench["docker"].calls if call[0] == "run"]
    assert "DREAMFERENCE_MIGHTLING_MASK=off" in runs[0]
    assert "DREAMFERENCE_MIGHTLING_MASK=on" in runs[-1]
    assert SweBenchRunStore("masked").manifest()["masking"] == "on"
    for name in ("plain", "masked"):
        SweBenchEvaluator.grade(SweBenchRunStore(name), bench["settings"])
    text = SweBenchReport.against(SweBenchRunStore("plain"), SweBenchRunStore("masked"))
    assert "differs: masking: off | on" in text


def test_the_launchers_own_refine_mode_is_off_in_the_container_in_both_arms(bench, monkeypatch):
    # specs/DREAMFERENCE_MIGHTLING_REFINE.md §5.4: the runner orchestrates `--refine` itself, so the
    # container's launcher must never add a study step of its own, whatever the host's setting.
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_REFINE", "on")
    assert run(bench, name="plain", instances=["acme__widget-1"]) == 0
    assert run(bench, name="refined", instances=["acme__widget-1"], refine=True) == 0
    runs = [call for call in bench["docker"].calls if call[0] == "run"]
    assert len(runs) == 2 and all("DREAMFERENCE_MIGHTLING_REFINE=off" in call for call in runs)


def test_a_built_in_prompt_needs_no_file_and_two_prompts_are_told_apart(bench, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_PROMPT", raising=False)
    assert run(bench, name="a", instances=["acme__widget-1"]) == 0
    assert run(bench, name="b", instances=["acme__widget-1"], prompt="high-swe") == 0
    runs = [call for call in bench["docker"].calls if call[0] == "run"]
    assert "-e" in runs[-1] and "DREAMFERENCE_MIGHTLING_PROMPT=high-swe" in runs[-1]
    assert "DREAMFERENCE_MIGHTLING_PROMPT=default" in runs[0]
    for name in "ab":
        SweBenchEvaluator.grade(SweBenchRunStore(name), bench["settings"])
    text = SweBenchReport.against(SweBenchRunStore("a"), SweBenchRunStore("b"))
    assert "differs: prompt: default | high-swe" in text
    # A manifest from before named prompts ran the default, and is compared as such.
    manifest_path = SweBenchRunStore("a").manifest_path
    old = json.loads(manifest_path.read_text())
    del old["prompt"], old["prompt_sha256"]
    manifest_path.write_text(json.dumps(old))
    assert "differs: prompt: default | high-swe" in SweBenchReport.against(SweBenchRunStore("a"), SweBenchRunStore("b"))


def test_a_custom_prompt_is_mounted_read_only_and_its_text_is_recorded(bench, monkeypatch, tmp_path):
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_PROMPT", raising=False)
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert run(bench, instances=["acme__widget-1"], prompt="mine") == 1, "no such file: not started"
    assert not SweBenchRunStore("r1").manifest_path.exists()
    assert run(bench, instances=["acme__widget-1"], prompt="Mine") == 1, "not a prompt's name"
    (home / "system-prompts").mkdir(parents=True)
    text = "<!-- ling: blocks=code -->\nFix it.\n"
    (home / "system-prompts" / "mine.md").write_text(text)
    assert run(bench, instances=["acme__widget-1"], prompt="mine") == 0
    env, mounts = container_env(bench)
    assert env["DREAMFERENCE_MIGHTLING_PROMPT"] == "mine"
    expected = f"{home}/system-prompts/mine.md:/mightling-scratch/codex-home/system-prompts/mine.md:ro"
    assert expected in mounts
    assert SweBenchRunStore("r1").manifest()["prompt_sha256"] == hashlib.sha256(text.encode()).hexdigest()


# -- a replica's model server (specs/DREAMFERENCE_MIGHTLING_NODE.md §12.3) ---------------------------

class BusyHere(QuietMachine):
    """This machine's server is busy; a replica's is free."""

    @classmethod
    def start_blocker(cls, vllm_host, mightling_bin, active, settings, local=True):
        return "the model server is serving a request that is not the night run's" if local else None


def test_instances_go_to_a_replica_through_a_relay_on_the_gateway(bench, monkeypatch):
    replica = {"name": "spark-2", "node": "2222-bbbb", "host": "http://192.168.50.106:8000", "parallel": 1, "budget": None}
    monkeypatch.setattr(SweBenchRunner, "admission", BusyHere)
    monkeypatch.setattr(SweBenchRunner, "lanes", classmethod(
        lambda cls, vllm_host, served, settings, parallel: (
            [{"name": "this machine", "node": None, "host": vllm_host, "parallel": parallel, "budget": None},
             dict(replica)], ["Also using spark-2's model server"])))
    opened = []

    def relays(cls, gateway, lanes):
        for lane in lanes:
            lane["model_url"] = f"http://{gateway}:40001"
            opened.append((gateway, lane["host"]))
        return []
    monkeypatch.setattr(SweBenchRunner, "open_relays", classmethod(relays))
    assert run(bench, instances=["acme__widget-1", "acme__widget-2"]) == 0
    assert opened == [("172.30.0.1", "http://192.168.50.106:8000")]
    created = [call for call in bench["docker"].calls if call[0] == "run"]
    hosts = {dict(a.split("=", 1) for i, a in enumerate(call) if call[i - 1] == "-e")["DREAMFERENCE_VLLM_HOST"]
             for call in created}
    assert hosts == {"http://172.30.0.1:40001"}                         # the relay, never the LAN address
    store = SweBenchRunStore("r1")
    assert all("model server: spark-2 (a replica of this machine's model)" in store.state(i)["notes"]
               for i in ("acme__widget-1", "acme__widget-2"))


def test_without_a_paired_node_a_benchmark_asks_nobody(bench, monkeypatch):
    from dreamference.node import NodePairing
    monkeypatch.setattr(NodePairing, "run", classmethod(lambda cls, *args, **kwargs: pytest.fail("no SSH")))
    lanes, notes = SweBenchRunner.lanes("http://127.0.0.1:8000", ("m", 1), bench["settings"], 2)
    assert [(lane["host"], lane["parallel"]) for lane in lanes] == [("http://127.0.0.1:8000", 2)] and notes == []


def test_the_relay_forwards_to_its_one_target_and_closes():
    import socket
    import threading
    from dreamference.swe_bench import SweBenchRelay
    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)

    def answer():
        connection, _ = upstream.accept()
        request = connection.recv(1024)
        connection.sendall(b"HTTP/1.0 200 OK\r\n\r\n" + request.split()[1])
        connection.close()
    threading.Thread(target=answer, daemon=True).start()
    relay = SweBenchRelay("127.0.0.1", upstream.getsockname())
    port = relay.start()
    assert port != upstream.getsockname()[1]
    with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
        client.sendall(b"GET /v1/models HTTP/1.0\r\n\r\n")
        reply = b""
        while chunk := client.recv(1024):
            reply += chunk
    assert reply.endswith(b"/v1/models")
    relay.close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).recv(1)
    upstream.close()


def test_a_named_mightling_code_build_replaces_the_installed_one(tmp_path, monkeypatch):
    from dreamference.swe_bench.swe_bench_code_index import MIGHTLING_CODE_OVERRIDE_ENV
    build = tmp_path / "ling-code"
    build.write_text("")
    monkeypatch.setenv(MIGHTLING_CODE_OVERRIDE_ENV, str(build))
    assert SweBenchCodeIndex.host_binary() == str(build)
    # A name that does not exist is not silently replaced by the installed binary.
    monkeypatch.setenv(MIGHTLING_CODE_OVERRIDE_ENV, str(tmp_path / "missing"))
    assert SweBenchCodeIndex.host_binary() is None


# -- the issue with the fix's names taken out (`--strip-names`) --------------------------------------

GOLD = """diff --git a/acme/db/sql/compiler.py b/acme/db/sql/compiler.py
--- a/acme/db/sql/compiler.py
+++ b/acme/db/sql/compiler.py
@@ -10,7 +10,8 @@ def pre_sql_setup(self):
         query = self.query
-        query.add_fields([pk])
+        query.add_fields(fields)
@@ -40,3 +41,6 @@ class SQLUpdateCompiler(SQLCompiler):
+    def related_updates(self):
+        return []
diff --git a/acme/runserver.py b/acme/runserver.py
--- a/acme/runserver.py
+++ b/acme/runserver.py
@@ -1,2 +1,2 @@ def update(self):
-    pass
+    return 1
"""


@pytest.fixture
def english(monkeypatch):
    """A fixed word list, so the tests do not depend on the machine's /usr/share/dict."""
    monkeypatch.setattr(SweBenchNameStripper, "_english", frozenset({"update", "query", "compiler", "the"}))


def test_the_names_come_from_the_gold_patch_alone(english):
    found = {name: kind for name, kind, _ in SweBenchNameStripper.names(GOLD)}
    assert found["acme/db/sql/compiler.py"] == found["sql/compiler.py"] == found["compiler.py"] == "file"
    assert found["acme.db.sql.compiler"] == found["compiler"] == "module"
    assert found["pre_sql_setup"] == "function" and found["related_updates"] == "function"
    assert found["SQLUpdateCompiler"] == "class" and found["update"] == "function"
    # A context line's `query` is not a name the fix touches, and longer names come first, so a
    # path is replaced before the file name inside it.
    assert "query" not in found
    names = [name for name, _, _ in SweBenchNameStripper.names(GOLD)]
    assert names.index("acme/db/sql/compiler.py") < names.index("compiler.py")


def test_strip_replaces_each_name_with_one_numbered_phrase(english):
    issue = ("SQLUpdateCompiler.pre_sql_setup() selects the wrong ids; see acme/db/sql/compiler.py, "
             "which is sql/compiler.py in a traceback, and `import acme.db.sql.compiler`.\n"
             "pre_sql_setup is called twice. The error is: ValueError: bad ids")
    text, replaced = SweBenchNameStripper.strip(issue, GOLD)
    assert "pre_sql_setup" not in text and "SQLUpdateCompiler" not in text and "compiler.py" not in text
    # The same function is the same phrase everywhere; a file and its module share a number.
    assert text.count("[function 1]") == 2 and "[class 1]" in text
    assert "[file 1]" in text and "[module 1]" in text
    # Behaviour and error messages stay.
    assert "selects the wrong ids" in text and "ValueError: bad ids" in text
    assert replaced["pre_sql_setup"] == "[function 1]"


def test_an_english_word_is_replaced_only_where_it_reads_as_code(english):
    issue = ("Please update the docs. Calling obj.update() fails, `update` too, and in a traceback:\n"
             '  File "x.py", line 3, in update\n'
             "and the runserver command breaks, though format=\"runserver\" is a value.")
    text, _ = SweBenchNameStripper.strip(issue, GOLD)
    assert text.startswith("Please update the docs.")
    assert "obj.[function 1]()" in text and "`[function 1]`" in text and "in [function 1]" in text
    # Not an English word: code wherever it stands, except as a quoted value.
    assert "the [module 1] command" in text and 'format="runserver"' in text


def test_a_windows_path_is_stripped_and_an_issue_naming_nothing_is_left_alone(english):
    text, replaced = SweBenchNameStripper.strip("File C:\\py\\acme\\db\\sql\\compiler.py, line 2", GOLD)
    assert "compiler.py" not in text and "[file 1]" in text
    assert SweBenchNameStripper.strip("The widget is broken.", GOLD) == ("The widget is broken.", {})
    assert SweBenchNameStripper.strip("pre_sql_setup", "not a diff") == ("pre_sql_setup", {})


def test_without_a_word_list_every_plain_name_counts_as_english(monkeypatch):
    monkeypatch.setattr(SweBenchNameStripper, "_english", frozenset())
    text, _ = SweBenchNameStripper.strip("the runserver command; acme.runserver.main()", GOLD)
    assert text.startswith("the runserver command")


def test_a_stripped_run_gives_the_agent_the_stripped_issue_and_records_it(bench, english):
    snapshot = SweBenchHarness.snapshot_path("verified")
    rows = [dict(row(i)) for i in IDS]
    rows[0].update(problem_statement="SQLUpdateCompiler.pre_sql_setup() breaks in acme/db/sql/compiler.py",
                   patch=GOLD)
    snapshot.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert run(bench, name="plain", instances=["acme__widget-1"]) == 0
    assert run(bench, name="stripped", instances=["acme__widget-1"], strip_names=True) == 0
    prompts = [call[-1] for call in bench["docker"].calls if call[0] == "exec" and "ling" in call[2]]
    assert "pre_sql_setup" in prompts[0]
    expected = "[class 1].[function 1]() breaks in [file 1]"
    assert prompts[-1] == SweBenchInstanceRun.compose_prompt(expected)
    assert "diff --git" not in json.dumps(bench["docker"].calls), "the gold patch never reaches a container"
    manifest = SweBenchRunStore("stripped").manifest()
    assert manifest["issue_text"] == "names stripped"
    assert manifest["stripped_issues"]["acme__widget-1"]["text"] == expected
    assert SweBenchRunStore("plain").manifest()["issue_text"] == "verbatim"
    state = json.loads((SweBenchRunStore("stripped").directory / "instances" / "acme__widget-1.json").read_text())
    assert state["issue"]["text"] == expected and state["issue"]["replaced"]["pre_sql_setup"] == "[function 1]"
    for name in ("plain", "stripped"):
        SweBenchEvaluator.grade(SweBenchRunStore(name), bench["settings"])
    assert "names stripped: the files, modules, functions and classes" in SweBenchReport.render(SweBenchRunStore("stripped"))
    text = SweBenchReport.against(SweBenchRunStore("plain"), SweBenchRunStore("stripped"))
    assert "differs: issue_text: verbatim | names stripped" in text


def test_strip_names_is_a_command_line_switch(monkeypatch):
    import argparse
    seen = {}
    monkeypatch.setattr(SweBenchRunner, "run", classmethod(lambda cls, **kwargs: seen.update(kwargs) or 0))
    parser = argparse.ArgumentParser()
    SweBenchCommand.add_parser(parser.add_subparsers(dest="command"))
    assert SweBenchCommand.dispatch(parser.parse_args(["swe-bench", "run", "--strip-names", "--name", "x"])) == 0
    assert seen["strip_names"] is True
    assert SweBenchCommand.dispatch(parser.parse_args(["swe-bench", "run", "--name", "y"])) == 0
    assert seen["strip_names"] is False


# -- the failure analysis's harness fixes (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md §5, §6) --

# The eval script's trace around the test patch in django 16877 of `im100-default`, verbatim: the
# agent had created the file the test patch adds.
REJECTED_16877 = """+ git checkout 98f6ada0e2058d67d91fb6c16482411ec2ca0967 tests/template_tests/filter_tests/test_escapeseq.py
error: pathspec 'tests/template_tests/filter_tests/test_escapeseq.py' did not match any file(s) known to git
+ git apply -v -
Checking patch tests/template_tests/filter_tests/test_escapeseq.py...
error: tests/template_tests/filter_tests/test_escapeseq.py: already exists in working directory
+ : '>>>>> Start Test Output'
+ ./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1 template_tests.filter_tests.test_escapeseq
+ : '>>>>> End Test Output'
+ git checkout 98f6ada0e2058d67d91fb6c16482411ec2ca0967 tests/template_tests/filter_tests/test_escapeseq.py
error: pathspec 'tests/template_tests/filter_tests/test_escapeseq.py' did not match any file(s) known to git
"""

# And django 13837's: the reset aborted, so the agent's edit stayed and the hunk did not match.
REJECTED_13837 = """+ git checkout 415f50298f97fb17f841a9df38d995ccf347dfcc tests/utils_tests/test_autoreload.py tests/utils_tests/test_module/__main__.py
error: pathspec 'tests/utils_tests/test_module/__main__.py' did not match any file(s) known to git
+ git apply -v -
Checking patch tests/utils_tests/test_autoreload.py...
error: while searching for:

+ : '>>>>> Start Test Output'
"""

# A test patch that applied, after a reset that failed: the reset's error is not the patch's.
APPLIED_AFTER_PATHSPEC_ERROR = """+ git checkout 415f50298f97fb17f841a9df38d995ccf347dfcc tests/a.py tests/new.py
error: pathspec 'tests/new.py' did not match any file(s) known to git
+ git apply -v -
Checking patch tests/a.py...
Applied patch tests/a.py cleanly.
+ : '>>>>> Start Test Output'
ERROR: test_something (a.Tests) error: the test's own output may say error:
+ : '>>>>> End Test Output'
+ git checkout 415f50298f97fb17f841a9df38d995ccf347dfcc tests/a.py tests/new.py
error: pathspec 'tests/new.py' did not match any file(s) known to git
"""


def test_every_git_apply_refusal_of_the_test_patch_counts_and_nothing_else_does(tmp_path):
    assert SweBenchHarness.test_patch_rejected(REJECTED_16877)
    assert SweBenchHarness.test_patch_rejected(REJECTED_13837)
    assert not SweBenchHarness.test_patch_rejected(APPLIED_AFTER_PATHSPEC_ERROR)
    assert SweBenchHarness.test_patch_rejected("error: patch failed: a.py:3\n")  # the phrases still count
    # Read from where the harness writes it.
    model = "mightling-x/test-model"
    for instance_id, text in (("django__django-16877", REJECTED_16877), ("ok-1", APPLIED_AFTER_PATHSPEC_ERROR)):
        directory = tmp_path / "logs/run_evaluation" / "r1-1" / model.replace("/", "__") / instance_id
        directory.mkdir(parents=True)
        (directory / "test_output.txt").write_text(text)
    assert SweBenchHarness.test_patch_failed(tmp_path, "r1-1", model, "django__django-16877") is True
    assert SweBenchHarness.test_patch_failed(tmp_path, "r1-1", model, "ok-1") is False
    assert SweBenchHarness.test_patch_failed(tmp_path, "r1-1", model, "absent") is False


def eval_script(base, files):
    """The shape of an upstream eval script: reset, apply the test patch, test, reset."""
    reset = f"git checkout {base} {' '.join(files)}"
    return "\n".join(["#!/bin/bash", "set -uxo pipefail", "cd /testbed", "git status", reset,
                      "git apply -v - <<'EOF_1'", "PATCH", "EOF_1", ": '>>>>> Start Test Output'",
                      "./tests/runtests.py a", ": '>>>>> End Test Output'", reset])


def test_the_dataset_file_resets_each_test_file_on_its_own(tmp_path):
    source = dict(row("acme__widget-1"), base_commit="0123abc",
                  eval_script=eval_script("0123abc", ["tests/a.py", "tests/new.py"]))
    path = tmp_path / "dataset.jsonl"
    SweBenchHarness.write_dataset_file(path, [source], {"acme__widget-1": "repo:acme-widget-1"})
    [entry] = [json.loads(line) for line in path.read_text().splitlines()]
    lines, original = entry["eval_script"].split("\n"), source["eval_script"].split("\n")
    assert len(lines) == len(original)
    changed = [index for index, (new, old) in enumerate(zip(lines, original)) if new != old]
    assert changed == [4, 11], "both resets, and nothing else"
    assert lines[4] == lines[11] and lines[4].startswith("for f in tests/a.py tests/new.py; do ")
    # A row without a base commit or script is passed through as it is.
    assert SweBenchHarness.per_file_reset("git checkout other tests/a.py", "0123abc") == "git checkout other tests/a.py"


def test_the_per_file_reset_restores_edited_tests_and_removes_added_ones_where_the_upstream_one_does_not(tmp_path):
    repo = tmp_path / "testbed"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "tests").mkdir()
    (repo / "tests" / "a.py").write_text("def test_a():\n    assert 1 == 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()

    def agent_edits():
        # The agent rewrote an existing assertion and created the file the test patch adds.
        (repo / "tests" / "a.py").write_text("def test_a():\n    assert 2 == 2  # AGENT\n")
        (repo / "tests" / "new.py").write_text("# AGENT\n")

    files = ["tests/a.py", "tests/new.py"]
    upstream = f"git checkout {base} {' '.join(files)}"
    agent_edits()
    subprocess.run(["bash", "-c", upstream], cwd=repo, capture_output=True)
    assert "AGENT" in (repo / "tests" / "a.py").read_text(), "the upstream reset aborts and resets nothing"

    fixed = SweBenchHarness.per_file_reset(upstream, base)
    result = subprocess.run(["bash", "-c", fixed], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (repo / "tests" / "a.py").read_text() == "def test_a():\n    assert 1 == 1\n"
    assert not (repo / "tests" / "new.py").exists()
    # The benchmark's test patch now applies: it adds tests/new.py and changes tests/a.py.
    test_patch = ("diff --git a/tests/a.py b/tests/a.py\n--- a/tests/a.py\n+++ b/tests/a.py\n@@ -1,2 +1,2 @@\n"
                  " def test_a():\n-    assert 1 == 1\n+    assert 1 == 1  # BENCHMARK\n"
                  "diff --git a/tests/new.py b/tests/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/tests/new.py\n"
                  "@@ -0,0 +1 @@\n+# BENCHMARK\n")
    applied = subprocess.run(["git", "apply", "-v", "-"], cwd=repo, input=test_patch, capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    assert "BENCHMARK" in (repo / "tests" / "new.py").read_text()


def test_the_grader_names_the_reset_so_a_run_graded_before_it_is_graded_again(bench):
    run(bench, instances=["acme__widget-1"])
    store = SweBenchRunStore("r1")
    SweBenchEvaluator.grade(store, bench["settings"])
    assert store.grading(1)["grader"]["eval_reset"] == "per-file"
    # A grading made with the upstream reset.
    record = store.grading(1)
    del record["grader"]["eval_reset"]
    store.write_grading(1, record)
    SweBenchEvaluator.grade(store, bench["settings"])
    assert store.gradings() == [1, 2]


def test_a_turn_that_changed_the_tree_and_stopped_mid_work_is_nudged_to_finish(bench):
    bench["docker"].default_mode = "midwork"
    assert run(bench, instances=["acme__widget-1"]) == 0
    store = SweBenchRunStore("r1")
    state = store.state("acme__widget-1")
    assert state["status"] == "done" and state["nudges"] == 1 and state["nudge_kinds"] == ["completion"]
    turns = mightling_prompts(bench["docker"])
    assert turns[1] == (COMPLETION_NUDGE, True)
    assert "FIX" in prediction("r1", "acme__widget-1") and "HALF" not in prediction("r1", "acme__widget-1")
    SweBenchEvaluator.grade(store, bench["settings"])
    assert "Nudges              stall 0     completion 1" in SweBenchReport.render(store)


def test_a_finished_summary_is_not_nudged_and_a_stall_keeps_its_own_nudge(bench):
    bench["docker"].modes = {"acme__widget-1": "summary_with_plan", "acme__widget-2": "stall_then_act"}
    assert run(bench, instances=["acme__widget-1", "acme__widget-2"]) == 0
    store = SweBenchRunStore("r1")
    assert store.state("acme__widget-1")["nudges"] == 0 and store.state("acme__widget-1")["nudge_kinds"] == []
    assert store.state("acme__widget-2")["nudge_kinds"] == ["stall"]


def test_what_counts_as_stopping_mid_work():
    stopped = SweBenchInstanceRun.stopped_mid_work
    # The two mid-work stops of `im100-default` (django 11885 and 15563), as they ended.
    assert stopped("`WhereNode` doesn't have `__or__`. Let me check what happens when we pass a `WhereNode` "
                   "as a positional arg to `filter()`:\n\nThe `Q.__init__` does `children=[*args]`, so `Q(w)` "
                   "creates a `Q` with `[w]` as children. Then `add_q` iterates children. For `W")
    assert stopped("compiler.py`.\n\nLet me do it now.\n\nI'll run the Python script.\n\nOK, running it now.\n\nI ")
    assert not stopped("Fixed widget() in widget.py; the widget tests pass.")
    assert not stopped("I'll summarise. " + "The fix is in widget.py. " * 30 + "Let me know if you need more.")
    assert not stopped("")


def test_the_test_discipline_rules_are_an_arm_and_the_plain_prompt_is_unchanged(bench):
    issue = "The widget is broken."
    plain = SweBenchInstanceRun.compose_prompt(issue)
    assert SweBenchInstanceRun.compose_prompt(issue, task_rules=[]) == plain
    with_rules = SweBenchInstanceRun.compose_prompt(issue, task_rules=["tests"])
    assert with_rules.replace(TASK_RULES["tests"], "") == plain and with_rules.endswith(issue)
    for words in ("Never change an existing test", "in /tmp", "compare the failing tests by name"):
        assert words in TASK_RULES["tests"]
    assert TASK_RULES["tests"] in SweBenchInstanceRun.compose_fix_prompt(issue, "x", task_rules=["tests"])
    with pytest.raises(KeyError):
        SweBenchInstanceRun.compose_prompt(issue, task_rules=["contract-not-built"])

    assert run(bench, name="plain", instances=["acme__widget-1"]) == 0
    assert run(bench, name="rules", instances=["acme__widget-1"], task_rules=["tests"]) == 0
    prompts = [prompt for prompt, _ in mightling_prompts(bench["docker"])]
    assert prompts == [SweBenchInstanceRun.compose_prompt("The widget is broken in acme__widget-1."),
                       SweBenchInstanceRun.compose_prompt("The widget is broken in acme__widget-1.", task_rules=["tests"])]
    assert SweBenchRunStore("plain").manifest()["task_rules"] == []
    assert SweBenchRunStore("rules").manifest()["task_rules"] == ["tests"]
    # A resumed run keeps the rules it started with.
    assert run(bench, name="rules", instances=["acme__widget-2"]) == 0
    for name in ("plain", "rules"):
        SweBenchEvaluator.grade(SweBenchRunStore(name), bench["settings"])
    text = SweBenchReport.against(SweBenchRunStore("rules"), SweBenchRunStore("plain"))
    assert "differs: task_rules: ['tests'] | []" in text
    assert "Task rules          tests" in SweBenchReport.render(SweBenchRunStore("rules"))


def test_an_unknown_task_rule_is_refused_and_the_option_reaches_the_runner(bench, monkeypatch, capsys):
    assert run(bench, instances=["acme__widget-1"], task_rules=["everything"]) == 1
    assert "--task-rules takes: tests" in capsys.readouterr().out
    assert not SweBenchRunStore("r1").manifest_path.exists()
    import argparse
    seen = {}
    monkeypatch.setattr(SweBenchRunner, "run", classmethod(lambda cls, **kwargs: seen.update(kwargs) or 0))
    parser = argparse.ArgumentParser()
    SweBenchCommand.add_parser(parser.add_subparsers(dest="command"))
    assert SweBenchCommand.dispatch(parser.parse_args(["swe-bench", "run", "--task-rules", "tests", "--name", "x"])) == 0
    assert seen["task_rules"] == ["tests"]
    assert SweBenchCommand.dispatch(parser.parse_args(["swe-bench", "run", "--name", "y"])) == 0
    assert seen["task_rules"] is None


def test_which_paths_are_test_files():
    is_test = SweBenchPatchFilter.is_test_path
    for path in ("tests/test_a.py", "pkg/tests/sub/helpers.py", "testing/util.py", "sympy/testing/runtests.py",
                 "test_widget.py", "pkg/widget_test.py", "app/tests.py", "conftest.py", "pkg/conftest.py",
                 "tests/fixtures/data.json"):
        assert is_test(path), path
    for path in ("django/test/client.py", "widget.py", "attest.py", "contest.py", "pkg/testsuite.py",
                 "pkg/tests_helpers/x.py", "doc/testing.rst"):
        assert not is_test(path), path


TEST_AND_SOURCE = """diff --git a/acme/widget.py b/acme/widget.py
index 1111111..2222222 100644
--- a/acme/widget.py
+++ b/acme/widget.py
@@ -1,2 +1,2 @@
 def widget():
-    return 1
+    return 2
diff --git a/tests/test_widget.py b/tests/test_widget.py
index 3333333..4444444 100644
--- a/tests/test_widget.py
+++ b/tests/test_widget.py
@@ -1 +1 @@
-assert widget() == 1
+assert widget() == 2
diff --git a/tests/__init__.py b/tests/__init__.py
new file mode 100644
index 0000000..e69de29
diff --git a/acme/old_test.py b/acme/moved.py
similarity index 100%
rename from acme/old_test.py
rename to acme/moved.py
diff --git a/acme/gone.py b/acme/gone.py
deleted file mode 100644
index 5555555..0000000
--- a/acme/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
"""


def test_dropping_test_hunks_keeps_every_source_section_byte_for_byte():
    patch, dropped = SweBenchPatchFilter.drop_test_hunks(TEST_AND_SOURCE)
    assert dropped == ["tests/test_widget.py", "tests/__init__.py", "acme/moved.py"]
    sections = SweBenchPatchFilter.sections(TEST_AND_SOURCE)
    assert "".join(sections) == TEST_AND_SOURCE
    assert patch == sections[0] + sections[4]
    assert SweBenchPatchFilter.drop_test_hunks(sections[1]) == ("", ["tests/test_widget.py"])
    assert SweBenchPatchFilter.drop_test_hunks("") == ("", [])


def test_the_regrade_drops_test_files_in_a_series_of_its_own_and_leaves_the_plain_grading_alone(bench, monkeypatch):
    from dreamference.night_shift.night_shift_host import NightShiftHost
    monkeypatch.setattr(NightShiftHost, "mem_available_bytes", classmethod(lambda cls: 1 << 50))
    bench["docker"].modes = {"acme__widget-1": "with_tests", "acme__widget-2": "tests_only"}
    run(bench, instances=["acme__widget-1", "acme__widget-2", "beta__gadget-7"], evaluate=True)
    store = SweBenchRunStore("r1")
    plain = json.loads((store.grading_dir(1) / "grading.json").read_text())
    calls = len(bench["harness"].calls)

    assert SweBenchCommand.evaluate("r1", DROP_TEST_HUNKS) == 0
    [call] = bench["harness"].calls[calls:]
    assert call[call.index("--run-id") + 1] == "r1-drop-test-hunks-1"
    assert [call[i + 1] for i, word in enumerate(call) if word == "-i"] == ["acme__widget-1"]
    graded = [json.loads(line) for line in Path(call[call.index("-p") + 1]).read_text().splitlines()]
    assert "widget.py" in graded[0]["model_patch"] and "tests/" not in graded[0]["model_patch"]
    results = store.grading(1, DROP_TEST_HUNKS)["results"]
    assert results["acme__widget-1"]["resolved"] is True
    assert results["acme__widget-1"]["dropped"] == ["tests/test_widget.py"]
    # A patch of test files alone is empty once they go, and is never handed to the harness.
    assert results["acme__widget-2"] == {"resolved": False, "empty": True, "dropped": ["tests/test_widget.py"]}
    # A patch without test files is the one the plain grading graded: its verdict is taken over.
    assert results["beta__gadget-7"]["reused"] == "plain grading 1" and results["beta__gadget-7"]["resolved"] is True
    assert results["beta__gadget-7"]["dropped"] == []
    # The run's predictions and its plain grading are untouched.
    assert "tests/test_widget.py" in prediction("r1", "acme__widget-1")
    assert json.loads((store.grading_dir(1) / "grading.json").read_text()) == plain
    assert store.gradings() == [1] and store.gradings(DROP_TEST_HUNKS) == [1]
    assert store.grading(1, DROP_TEST_HUNKS)["grader"]["patch"] == "test files dropped"
    # Grading the plain series again neither regrades it nor the other.
    SweBenchEvaluator.grade(store, bench["settings"])
    assert len(bench["harness"].calls) == calls + 1 and store.gradings(DROP_TEST_HUNKS) == [1]

    text = SweBenchReport.render(store, DROP_TEST_HUNKS)
    assert "Test files dropped" in text and "2 patch(es) changed, 1 left empty" in text
    SweBenchReport.write(store, DROP_TEST_HUNKS)
    assert (store.directory / "report-drop-test-hunks.md").is_file()
    compared = SweBenchReport.against(store, store, DROP_TEST_HUNKS)
    assert compared.startswith("r1 [drop-test-hunks] against r1: 3 instance(s) graded in both")
    assert "Resolved only by r1 (1): acme__widget-2" in compared


def test_eval_and_report_take_the_drop_test_hunks_switch(monkeypatch):
    import argparse
    seen = []
    monkeypatch.setattr(SweBenchCommand, "evaluate", classmethod(lambda cls, *a: seen.append(("eval", a)) or 0))
    monkeypatch.setattr(SweBenchCommand, "report", classmethod(lambda cls, *a: seen.append(("report", a)) or 0))
    parser = argparse.ArgumentParser()
    SweBenchCommand.add_parser(parser.add_subparsers(dest="command"))
    for words in (["eval", "r", "--drop-test-hunks", "--remove-images"], ["eval", "r"],
                  ["report", "r", "--drop-test-hunks", "--against", "r"]):
        assert SweBenchCommand.dispatch(parser.parse_args(["swe-bench", *words])) == 0
    assert seen == [("eval", ("r", DROP_TEST_HUNKS, True)), ("eval", ("r", None, False)),
                    ("report", ("r", "r", DROP_TEST_HUNKS))]


def test_eval_with_remove_images_grades_one_repository_at_a_time_and_removes_what_it_pulled(bench, monkeypatch):
    from dreamference.night_shift.night_shift_host import NightShiftHost
    monkeypatch.setattr(NightShiftHost, "mem_available_bytes", classmethod(lambda cls: 1 << 50))
    bench["docker"].modes = {"acme__widget-1": "with_tests", "beta__gadget-7": "with_tests"}
    run(bench, instances=["acme__widget-1", "beta__gadget-7"], evaluate=True)
    docker, harness = bench["docker"], bench["harness"]
    docker.present.clear()
    calls = len(harness.calls)
    assert SweBenchCommand.evaluate("r1", DROP_TEST_HUNKS, remove_images=True) == 0
    graded = [[call[i + 1] for i, word in enumerate(call) if word == "-i"] for call in harness.calls[calls:]]
    assert sorted(graded) == [["acme__widget-1"], ["beta__gadget-7"]]  # predictions are in finish order
    assert docker.present == set()
    assert sorted(SweBenchRunStore("r1").grading(1, DROP_TEST_HUNKS)["results"]) == ["acme__widget-1", "beta__gadget-7"]


def test_with_cycled_images_the_index_pass_removes_what_it_pulled(bench):
    docker = bench["docker"]
    # Validated ahead of the run, with the images removed afterwards (scripts/swe_bench_fresh.py).
    SweBenchEvaluator.validate("verified", IDS, bench["settings"])
    docker.present.clear()
    before = len(docker.calls)
    assert run(bench, code_index="universal", evaluate=True, keep_images=False) == 0
    image = f"{REPOSITORY}:acme-widget-1"
    events = [call[0] for call in docker.calls[before:]
              if (call[0] in ("pull", "rmi") and call[-1] == image) or (call[0] == "run" and image in call)]
    # Pulled to index and removed at once; pulled again when the instance starts, removed after grading.
    assert events == ["pull", "rmi", "pull", "run", "rmi"], events
    assert docker.present == set()


def test_without_cycling_the_index_pass_keeps_the_images(bench):
    assert run(bench, code_index="universal") == 0
    assert not any(call[0] == "rmi" for call in bench["docker"].calls)
    assert len(bench["docker"].present) == 3
