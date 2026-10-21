"""`scripts/industry_evals/`: the runners for IFEval, IFBench, Aider Polyglot and Terminal-Bench.

specs/DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md §10. No model, no network, no Docker: downloads,
installs and scorers are stand-ins, and `ling` is the ask-evals suite's stand-in script.
"""

from __future__ import annotations

import importlib.util
import io
import json
import stat
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDUSTRY = ROOT / "scripts" / "industry_evals"
sys.path.insert(0, str(INDUSTRY))
sys.path.insert(0, str(ROOT / "scripts" / "ask_evals"))
sys.path.insert(0, str(ROOT / "tests"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"industry_{name}", INDUSTRY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SETUP = _load("setup_cache")
IFEVAL = _load("ifeval")

LOCK = """
version = 1

[[package]]
name = "absl-py"
version = "2.3.1"

[[package]]
name = "nltk"
version = "3.9.2"
"""


# T0: the cache.


def test_pins_come_from_the_lock_and_a_missing_one_stops_setup():
    assert SETUP.pins_from_lock(LOCK, ("absl-py", "nltk")) == ["absl-py==2.3.1", "nltk==3.9.2"]
    try:
        SETUP.pins_from_lock(LOCK, ("absl-py", "emoji"))
    except SystemExit as error:
        assert "emoji" in str(error)
    else:
        raise AssertionError("a package missing from the lock must stop setup")


def _tarball(lock_text: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in {"uv.lock": lock_text, "data/IFBench_test.jsonl": "{}\n"}.items():
            data = text.encode()
            info = tarfile.TarInfo(f"IFBench-{SETUP.IFBENCH_COMMIT}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_setup_fetches_pinned_sources_installs_the_locks_pins_and_writes_the_manifest(tmp_path):
    lock = "\n".join(f'[[package]]\nname = "{n}"\nversion = "1.0"\n' for n in SETUP.SCORER_PACKAGES)
    fetched, ran = [], []

    def download(url, path):
        fetched.append(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_tarball(lock) if url.endswith(SETUP.IFBENCH_COMMIT) else b"x")
        return path

    def run(command, check):
        ran.append(command)
        if command[1:3] == ["-m", "venv"]:
            python = Path(command[3]) / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("")

    manifest = SETUP.setup_ifeval(tmp_path, run=run, download=download)
    assert all(SETUP.IFEVAL_COMMIT in url or SETUP.IFBENCH_COMMIT in url for url in fetched)
    assert (
        tmp_path / "ifeval" / "instruction_following_eval" / "data" / "input_data.jsonl"
    ).exists()
    assert (tmp_path / "ifbench" / "uv.lock").exists()
    install = next(c for c in ran if "install" in c)
    assert install[-len(SETUP.SCORER_PACKAGES) :] == [f"{n}==1.0" for n in SETUP.SCORER_PACKAGES]
    assert manifest["ifeval"]["licence"].startswith("Apache-2.0")
    assert (
        json.loads((tmp_path / "manifest.json").read_text())["ifbench"]["revision"]
        == SETUP.IFBENCH_COMMIT
    )


# T1: the IFEval runner.


def test_accuracy_reads_prompt_and_instruction_level(tmp_path):
    results = [
        {"follow_all_instructions": True, "follow_instruction_list": [True, True]},
        {"follow_all_instructions": False, "follow_instruction_list": [True, False]},
    ]
    path = tmp_path / "eval_results_strict.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in results))
    assert IFEVAL.accuracy(path) == {"prompts": 2, "prompt_level": 0.5, "instruction_level": 0.75}


def test_a_shipped_level_sets_no_cave_and_a_named_one_does():
    row = {"key": 7, "prompt": "Write two words."}
    assert "cave" not in IFEVAL.scenario_for("ifeval", row, "shipped", "ask")
    named = IFEVAL.scenario_for("ifeval", row, "ultra", "default")
    assert named["cave"] == "ultra" and named["prompt"] == "default" and named["id"] == "ifeval-7"


FAKE_SCORER = r'''#!/usr/bin/env python3
"""Plays the reference scorer: an instruction is followed when the answer is non-empty."""
import json, sys
args = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--"))
responses = [json.loads(l) for l in open(args["input_response_data"])]
for kind in ("strict", "loose"):
    with open(f"{args['output_dir']}/eval_results_{kind}.jsonl", "w") as out:
        for r in responses:
            ok = bool(r["response"].strip())
            out.write(json.dumps({"prompt": r["prompt"], "follow_all_instructions": ok,
                                  "follow_instruction_list": [ok, True]}) + "\n")
'''


def _fake(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_the_ifeval_runner_answers_each_prompt_scores_it_and_writes_the_summary(
    tmp_path, monkeypatch
):
    from test_ask_evals import FAKE_LING

    cache = tmp_path / "cache"
    data = cache / IFEVAL.SETS["ifeval"]["data"]
    data.parent.mkdir(parents=True)
    data.write_text(
        "".join(json.dumps({"key": k, "prompt": f"Say hello {k}."}) + "\n" for k in (1, 2, 3))
    )
    (cache / "manifest.json").write_text(json.dumps({"ifeval": {"licence": "Apache-2.0"}}))
    monkeypatch.setenv("FAKE_MODE", "good")
    status = IFEVAL.main(
        [
            "--sets",
            "ifeval",
            "--cave",
            "shipped,ultra",
            "--cache",
            str(cache),
            "--runs",
            str(tmp_path / "runs"),
            "--ling",
            str(_fake(tmp_path / "ling", FAKE_LING)),
            "--scorer-python",
            str(_fake(tmp_path / "scorer", FAKE_SCORER)),
            "--parallel",
            "2",
            "--no-jail",
            "--no-model",
        ]
    )
    assert status == 0
    summary = json.loads((tmp_path / "runs" / "summary.json").read_text())
    for level in ("shipped", "ultra"):
        result = summary["results"]["ifeval"][level]
        assert result["strict"] == {"prompts": 3, "prompt_level": 1.0, "instruction_level": 1.0}
        assert result["empty_answers"] == 0
    assert summary["manifest"]["ifeval"]["licence"] == "Apache-2.0"
    responses = (
        (tmp_path / "runs" / "ifeval" / "ultra" / "responses.jsonl").read_text().splitlines()
    )
    assert [json.loads(r)["prompt"] for r in responses] == [
        "Say hello 1.",
        "Say hello 2.",
        "Say hello 3.",
    ]
    scenario = json.loads(
        (tmp_path / "runs" / "ifeval" / "ultra" / "ifeval-1" / "scenario.json").read_text()
    )
    assert scenario["cave"] == "ultra"


def test_the_ifeval_runner_refuses_an_empty_cache(tmp_path, capsys):
    assert IFEVAL.main(["--cache", str(tmp_path), "--no-model", "--no-jail"]) == 2
    assert "setup_cache.py ifeval" in capsys.readouterr().err
