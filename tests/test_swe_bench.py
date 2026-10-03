"""`puffin-admin swe-bench` (specs/DREAMFERENCE_PUFFIN_SWE_BENCH.md §7.2).

A stand-in plays `docker`: a "container" is a scratch git repository on the host, the scripts the
runner executes in a container run against it with bash, and `puffin exec` is a scripted agent
that edits it. Another stand-in plays the upstream harness. Nothing here starts a container,
pulls an image, installs a package or reaches the network.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from dreamference.night_shift import NightShiftQueue
from dreamference.night_shift.night_shift_task_run import NUDGE
from dreamference.swe_bench import (
    SweBenchCodeIndex, SweBenchCommand, SweBenchDocker, SweBenchEvaluator, SweBenchHarness, SweBenchImages,
    SweBenchInstanceRun, SweBenchReport, SweBenchRunStore, SweBenchRunner, SweBenchRuntime,
    SweBenchSettings,
)
from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_harness import FORBIDDEN_FIELDS, NOOP_PATCH
from dreamference.swe_bench.swe_bench_instance_run import (CODE_INDEX_HINT, COLLECT_SCRIPT, PREPARE_SCRIPT,
                                                          SCRUB_SCRIPT)

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
    """The `docker exec … puffin exec` client: finished at once, or hanging until stopped."""

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
            scratch = next(a.split(":")[0] for a in args if a.endswith(":/puffin-scratch"))
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
        if mode == "hang":
            (box["repo"] / "partial.py").write_text("# half done\n")
            process = FakeProcess(0, hang=True)
            self.processes.append(process)
            return process
        if mode == "error":
            return FakeProcess(1)
        if "puffin-code" in box["env"].get("PATH", ""):
            # An agent that has the index uses it once before it edits.
            stdout.write((json.dumps({"type": "item.completed", "item": {
                "type": "command_execution", "command": "/bin/bash -lc 'puffin-code refs widget'", "exit_code": 0}}) + "\n").encode())
        stdout.write((json.dumps({"type": "item.completed", "item": {
            "type": "command_execution", "command": "/bin/bash -lc 'grep -rn widget .'", "exit_code": 0}}) + "\n").encode())
        stdout.write((json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 1000, "cached_input_tokens": 900, "output_tokens": 50}}) + "\n").encode())
        stdout.flush()
        acts = mode in ("change", "binary") or (mode == "stall_then_act" and prompt == NUDGE)
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
    def admit(cls, vllm_host, puffin_bin, idle_minutes, end):
        return cls.refuse

    @classmethod
    def start_blocker(cls, vllm_host, puffin_bin, active, settings):
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
    monkeypatch.setattr(SweBenchRuntime, "installed_puffin", classmethod(lambda cls: "/opt/none/puffin"))
    monkeypatch.setattr(SweBenchRuntime, "ensure", classmethod(lambda cls, puffin, patchelf: "runtime-hash"))
    index_calls = []

    def fake_puffin_code(command, **kwargs):
        index_calls.append((list(command), kwargs.get("cwd"), dict(kwargs.get("env") or {})))
        if "index" in command:
            environment = kwargs["env"]
            assert Path(kwargs["cwd"], "widget.py").is_file(), "indexed in the copy of /testbed"
            assert list(Path(environment["PUFFIN_CODE_INDEXERS_DIR"]).iterdir()) == [], "universal layer only"
            (Path(environment["CBM_CACHE_DIR"]) / "_config.db").write_text("")
            (Path(environment["CBM_CACHE_DIR"]) / "host-path-testbed.db").write_text("graph")
        return subprocess.CompletedProcess(command, 0, "puffin-index-x-codebase-memory: ok\n", "")

    installed = tmp_path / "installed" / "puffin-code"
    installed.parent.mkdir()
    installed.write_text("binary")
    monkeypatch.setattr(SweBenchCodeIndex, "execute", staticmethod(fake_puffin_code))
    monkeypatch.setattr(SweBenchCodeIndex, "host_binary", classmethod(lambda cls: str(installed)))
    monkeypatch.setattr(SweBenchRuntime, "host_libraries", classmethod(lambda cls, binary: (str(installed), [])))
    monkeypatch.setattr(SweBenchRunner, "admission", QuietMachine)
    monkeypatch.setattr(SweBenchRunner, "host", FakeHost)
    monkeypatch.setattr(SweBenchRunner, "sleep", staticmethod(lambda seconds: time.sleep(0.01)))
    monkeypatch.setattr(QuietMachine, "refuse", None)
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
    exec_call = next(call for call in docker.calls if call[0] == "exec" and "puffin" in call[2])
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


def test_the_container_is_capped_isolated_and_runs_as_the_user(bench):
    run(bench, instances=["acme__widget-1"])
    created = next(call for call in bench["docker"].calls if call[0] == "run")
    assert created[created.index("--network") + 1] == swe_bench_settings.NETWORK_NAME
    assert created[created.index("--memory") + 1] == created[created.index("--memory-swap") + 1] == "8G"
    assert created[created.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert any(mount.endswith(":/opt/puffin:ro") for mount in created)
    env = dict(a.split("=", 1) for i, a in enumerate(created) if created[i - 1] == "-e")
    assert env["DREAMFERENCE_VLLM_HOST"] == "http://172.30.0.1:8000"
    assert env["PATH"].startswith("/opt/miniconda3/envs/testbed/bin:")
    assert env["PUFFIN_NIGHT_RUN"] == "1"
    assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == ("safe.directory", "/testbed")
    agent = next(call for call in bench["docker"].calls if call[0] == "exec" and "puffin" in call[2])
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
    assert prediction["model_name_or_path"].startswith("puffin-") and prediction["model_name_or_path"].endswith("/test-model")
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
    turns = [call for call in bench["docker"].calls if call[0] == "exec" and "puffin" in call[2]]
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
    assert "A SWE-bench run is in progress, so `puffin-admin codex build` waits" in capsys.readouterr().out
    assert NightShiftQueue.runner_holder() is None


def test_admission_refuses_on_the_disk_reserve(bench, capsys):
    bench["settings"] = SweBenchSettings({"disk_reserve": "100000T"})
    assert run(bench, instances=["acme__widget-1"]) == 1
    assert "reserve" in capsys.readouterr().out
    assert bench["docker"].containers == {} and bench["docker"].removed == {}


def test_night_shifts_admission_decides_whether_the_run_starts(bench, monkeypatch, capsys):
    monkeypatch.setattr(QuietMachine, "refuse", "a puffin session is open (interactive use wins)")
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

def test_without_the_code_index_the_container_gets_nothing_of_puffin_code(bench):
    run(bench, instances=["acme__widget-1"])
    created = next(call for call in bench["docker"].calls if call[0] == "run")
    assert "puffin-code" not in json.dumps(created) and "PUFFIN_CODE" not in json.dumps(created)
    assert bench["index_calls"] == []
    agent = next(call for call in bench["docker"].calls if call[0] == "exec" and "puffin" in call[2])
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
    assert environment["PUFFIN_CODE_STATE_DIR"] == str(directory / "state")
    assert not (directory / "testbed").exists()  # the copy is not kept
    created = next(call for call in docker.calls if call[0] == "run")
    mounts = [created[i + 1] for i, word in enumerate(created) if word == "-v"]
    assert f"{directory}:/puffin-index:ro" in mounts
    assert f"{SweBenchCodeIndex.runtime_dir()}:/opt/puffin-code:ro" in mounts
    env = dict(a.split("=", 1) for i, a in enumerate(created) if created[i - 1] == "-e")
    assert env["PUFFIN_CODE_BIN"] == "/opt/puffin-code/bin/puffin-code"
    assert env["PUFFIN_CODE_STATE_DIR"] == "/puffin-index/state"
    assert env["PUFFIN_CODE_GRAPH_DB"] == "/puffin-index/cbm/host-path-testbed.db"
    assert env["PUFFIN_CODE_PROJECT"] == "host-path-testbed"
    assert env["PATH"].startswith("/opt/puffin-code/bin:/opt/miniconda3/envs/testbed/bin:")
    # The agent's puffin declares the index's MCP server itself, as a required one, so the first
    # request waits for its tools instead of going out without them.
    agent = next(call for call in docker.calls if call[0] == "exec" and "puffin" in call[2])
    overrides = [agent[i + 1] for i, word in enumerate(agent) if word == "-c"]
    assert "mcp_servers.puffin_code.required=true" in overrides
    assert 'mcp_servers.puffin_code.command="/opt/puffin-code/bin/puffin-code"' in overrides
    assert 'mcp_servers.puffin_code.args=["mcp"]' in overrides
    forwarded = next(o for o in overrides if o.startswith("mcp_servers.puffin_code.env_vars="))
    assert '"PUFFIN_CODE_GRAPH_DB"' in forwarded and '"PUFFIN_CODE_PROJECT"' in forwarded
    store = SweBenchRunStore("r1")
    state = store.state("acme__widget-1")
    assert store.manifest()["code_index"] == "universal"
    assert state["index"]["layers"] == ["universal"] and state["index"]["cached"] is False
    assert store.log_stats("acme__widget-1") == {
        "commands": 2, "puffin_code_calls": 1, "input_tokens": 1000, "cached_input_tokens": 900, "output_tokens": 50}
    report = SweBenchReport.render(store)
    assert "Code index          universal" in report and "called puffin-code 1 time(s), in 1 of 1 instance(s)" in report


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
    assert run(bench, name="x", instances=["acme__widget-1"], code_index="exact") == 1


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
    assert lines["puffin-code calls"].split()[-2:] == ["3", "0"]
    assert lines["instances using"].split()[-2:] == ["3", "0"]
    assert lines["input tokens"].split()[-2:] == ["3,000", "3,000"]
    assert "Resolved in both: 1, only with: 1, only without: 1, neither: 0" in text
    assert "In with the agent called puffin-code in 3 of 3 instances" in text
    assert "No measurable difference." in text
    row_line = next(line for line in text.splitlines() if line.strip().startswith("acme__widget-2"))
    assert "only with" in row_line and "1 puffin-code" in row_line


def test_naming_puffin_code_is_not_calling_it(bench):
    store = SweBenchRunStore("counts")
    store.log_path("x").parent.mkdir(parents=True)
    command = lambda text: json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": text}})
    store.log_path("x").write_text("Reading additional input from stdin...\n" + "\n".join([
        command("/bin/bash -lc 'ls /opt/puffin-code/bin'"), command("/bin/bash -lc 'which puffin-code'"),
        command("/bin/bash -lc 'cd /testbed && puffin-code refs Widget'"),
        command("/bin/bash -lc 'puffin-code   callers a.b | head'")]) + "\n")
    assert store.log_stats("x")["commands"] == 4 and store.log_stats("x")["puffin_code_calls"] == 2


def test_an_index_question_asked_as_a_tool_counts_as_one(bench):
    # Since 2026-10-02 the launcher gives the model the index as `code_*` tools; `puffin exec`
    # reports such a call as an `mcp_tool_call` item, not as a command.
    store = SweBenchRunStore("tools")
    store.log_path("x").parent.mkdir(parents=True)
    tool = lambda server, name: json.dumps({"type": "item.completed", "item": {
        "type": "mcp_tool_call", "server": server, "tool": name, "arguments": {}}})
    store.log_path("x").write_text("\n".join([
        tool("puffin_code", "code_search"), tool("puffin_code", "code_show"),
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
    assert "In with the agent never called puffin-code: this comparison says nothing about the index." in text


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


def test_the_runtime_names_every_library_puffin_is_linked_against(monkeypatch):
    ldd = ("\tlinux-vdso.so.1 (0x0000)\n\tlibgcc_s.so.1 => /lib/aarch64-linux-gnu/libgcc_s.so.1 (0x1)\n"
           "\tlibm.so.6 => /lib/aarch64-linux-gnu/libm.so.6 (0x2)\n\tlibc.so.6 => /lib/aarch64-linux-gnu/libc.so.6 (0x3)\n"
           "\t/lib/ld-linux-aarch64.so.1 (0x4)\n")
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0, ldd, "")
                        if command[0] == "ldd" else real_run(command, **kw))
    loader, libraries = SweBenchRuntime.host_libraries("/x/puffin")
    assert loader == "/lib/ld-linux-aarch64.so.1" and len(libraries) == 3
    ldd += "\tlibssl.so.3 => /lib/aarch64-linux-gnu/libssl.so.3 (0x5)\n"
    with pytest.raises(ValueError, match="libssl.so.3"):
        SweBenchRuntime.host_libraries("/x/puffin")


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
    assert ["ps", "-aq", "--filter", "label=puffin.swe-bench.run=r1"] in bench["docker"].calls


def test_a_named_puffin_code_build_replaces_the_installed_one(tmp_path, monkeypatch):
    from dreamference.swe_bench.swe_bench_code_index import PUFFIN_CODE_OVERRIDE_ENV
    build = tmp_path / "puffin-code"
    build.write_text("")
    monkeypatch.setenv(PUFFIN_CODE_OVERRIDE_ENV, str(build))
    assert SweBenchCodeIndex.host_binary() == str(build)
    # A name that does not exist is not silently replaced by the installed binary.
    monkeypatch.setenv(PUFFIN_CODE_OVERRIDE_ENV, str(tmp_path / "missing"))
    assert SweBenchCodeIndex.host_binary() is None
