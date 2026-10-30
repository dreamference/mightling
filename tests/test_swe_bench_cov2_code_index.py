"""Tests of the code index arm of the SWE-bench runner.

Which `ling-code` it finds, how the relocated copy for the container is built and kept, which
cached records are usable, how the testbed is copied out of an image, and how the universal and
exact indexes are built, time out and retry.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from dreamference.swe_bench import swe_bench_code_index, swe_bench_settings
from dreamference.swe_bench.swe_bench_code_index import (
    EXACT_ATTEMPTS,
    MIGHTLING_CODE_OVERRIDE_ENV,
    SweBenchCodeIndex,
)
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_runtime import SweBenchRuntime

ROW = {"instance_id": "acme__widget-1", "repo": "acme/widget", "base_commit": "a" * 40}


def done(command: Any, code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(command, code, out, err)


def fake_docker(monkeypatch: pytest.MonkeyPatch, create=(0, "cid\n"), cp=0) -> list[list]:
    calls: list[list] = []

    def run(cls, args, **kwargs):
        calls.append(list(args))
        if args[0] == "create":
            return done(args, create[0], create[1])
        if args[0] == "cp":
            return done(args, cp, "", "boom" if cp else "")
        return done(args)

    monkeypatch.setattr(SweBenchDocker, "run", classmethod(run))
    return calls


def binary_file(tmp_path: Path) -> Path:
    binary = tmp_path / "host" / "ling-code"
    binary.parent.mkdir()
    binary.write_bytes(b"ling-code bytes")
    return binary


def with_binary(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    monkeypatch.setattr(SweBenchCodeIndex, "host_binary", classmethod(lambda cls: str(binary)))


def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    pauses: list[float] = []
    monkeypatch.setattr(SweBenchCodeIndex, "sleep", staticmethod(pauses.append))
    return pauses


def test_host_binary_is_none_when_no_ling_is_installed(monkeypatch):
    monkeypatch.delenv(MIGHTLING_CODE_OVERRIDE_ENV, raising=False)
    monkeypatch.setattr(SweBenchRuntime, "installed_mightling", classmethod(lambda cls: None))

    assert SweBenchCodeIndex.host_binary() is None


def test_host_binary_is_the_ling_code_beside_the_installed_ling(monkeypatch, tmp_path):
    monkeypatch.delenv(MIGHTLING_CODE_OVERRIDE_ENV, raising=False)
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "ling").write_text("")
    (tmp_path / "bin" / "ling-code").write_text("")
    ling = str(tmp_path / "bin" / "ling")
    monkeypatch.setattr(SweBenchRuntime, "installed_mightling", classmethod(lambda cls: ling))

    assert SweBenchCodeIndex.host_binary() == str(tmp_path / "bin" / "ling-code")


def test_host_binary_is_none_when_no_ling_code_sits_beside_the_ling(monkeypatch, tmp_path):
    monkeypatch.delenv(MIGHTLING_CODE_OVERRIDE_ENV, raising=False)
    (tmp_path / "ling").write_text("")
    ling = str(tmp_path / "ling")
    monkeypatch.setattr(SweBenchRuntime, "installed_mightling", classmethod(lambda cls: ling))

    assert SweBenchCodeIndex.host_binary() is None


def test_ensure_runtime_without_a_binary_says_so_and_returns_none(monkeypatch, capsys):
    monkeypatch.setattr(SweBenchCodeIndex, "host_binary", classmethod(lambda cls: None))

    assert SweBenchCodeIndex.ensure_runtime("patchelf") is None
    assert "ling-code is not installed" in capsys.readouterr().out


def libraries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loader = tmp_path / "libs" / "ld-linux.so"
    loader.parent.mkdir()
    loader.write_text("loader")
    monkeypatch.setattr(
        SweBenchRuntime, "host_libraries", classmethod(lambda cls, binary: (str(loader), []))
    )


def test_ensure_runtime_rebuilds_when_the_stamp_does_not_match_the_binary(monkeypatch, tmp_path):
    binary = binary_file(tmp_path)
    with_binary(monkeypatch, binary)
    libraries(monkeypatch, tmp_path)
    target = SweBenchCodeIndex.runtime_dir()
    target.mkdir(parents=True)
    (target / "source-hash").write_text("stale\n")
    commands: list[list] = []
    monkeypatch.setattr(
        SweBenchCodeIndex,
        "execute",
        staticmethod(lambda command, **kw: commands.append(command) or done(command)),
    )

    wanted = hashlib.sha256(binary.read_bytes()).hexdigest()

    assert SweBenchCodeIndex.ensure_runtime("patchelf") == wanted
    assert (target / "source-hash").read_text().strip() == wanted
    assert (target / "bin" / "ling-code").is_file()
    assert len(commands) == 1 and commands[0][0] == "patchelf"


def test_ensure_runtime_with_a_matching_stamp_returns_the_hash_without_building(
    monkeypatch, tmp_path
):
    binary = binary_file(tmp_path)
    with_binary(monkeypatch, binary)
    wanted = hashlib.sha256(binary.read_bytes()).hexdigest()
    target = SweBenchCodeIndex.runtime_dir()
    target.mkdir(parents=True)
    (target / "source-hash").write_text(wanted + "\n")

    def refuse(command, **kwargs):
        raise AssertionError("nothing may run")

    monkeypatch.setattr(SweBenchCodeIndex, "execute", staticmethod(refuse))

    assert SweBenchCodeIndex.ensure_runtime("patchelf") == wanted


def test_ensure_runtime_failed_patchelf_returns_none_and_keeps_the_previous_runtime(
    monkeypatch, tmp_path, capsys
):
    binary = binary_file(tmp_path)
    with_binary(monkeypatch, binary)
    libraries(monkeypatch, tmp_path)
    target = SweBenchCodeIndex.runtime_dir()
    (target / "bin").mkdir(parents=True)
    (target / "bin" / "ling-code").write_text("earlier build")
    (target / "source-hash").write_text("earlier\n")
    monkeypatch.setattr(
        SweBenchCodeIndex,
        "execute",
        staticmethod(lambda command, **kw: done(command, 1, "", "cannot patch\n")),
    )

    assert SweBenchCodeIndex.ensure_runtime("patchelf") is None

    assert (target / "bin" / "ling-code").read_text() == "earlier build"
    assert (target / "source-hash").read_text() == "earlier\n"
    assert list(target.parent.glob(".runtime-code.*.tmp")) == []
    assert "cannot patch" in capsys.readouterr().out


def test_record_of_a_json_that_is_not_an_object_is_none(monkeypatch):
    directory = SweBenchCodeIndex.index_dir(ROW)
    directory.mkdir(parents=True)
    (directory / "index.json").write_text("[1, 2]")

    assert SweBenchCodeIndex.record(ROW) is None


def test_record_of_a_universal_index_whose_graph_file_is_missing_is_none():
    directory = SweBenchCodeIndex.index_dir(ROW)
    (directory / "cbm").mkdir(parents=True)
    (directory / "index.json").write_text(json.dumps({"layers": ["universal"], "graph_db": "x.db"}))

    assert SweBenchCodeIndex.record(ROW) is None

    (directory / "cbm" / "x.db").write_text("graph")

    assert SweBenchCodeIndex.record(ROW)["graph_db"] == "x.db"


def test_record_of_an_exact_index_whose_layers_are_not_exact_is_none():
    directory = SweBenchCodeIndex.index_dir(ROW, "exact")
    directory.mkdir(parents=True)
    (directory / "index.json").write_text(json.dumps({"layers": ["universal"]}))

    assert SweBenchCodeIndex.record(ROW, "exact") is None


@pytest.mark.parametrize("create", [(1, "cid\n"), (0, "  \n")])
def test_copy_testbed_fails_when_no_container_can_be_created(monkeypatch, tmp_path, capsys, create):
    calls = fake_docker(monkeypatch, create=create)

    assert SweBenchCodeIndex.copy_testbed(ROW, "img", tmp_path / "testbed") is False
    assert [call[0] for call in calls] == ["create"]
    assert "could not create a container" in capsys.readouterr().out


def test_copy_testbed_failed_cp_returns_false_and_still_removes_the_container(
    monkeypatch, tmp_path, capsys
):
    calls = fake_docker(monkeypatch, cp=1)

    assert SweBenchCodeIndex.copy_testbed(ROW, "img", tmp_path / "testbed") is False
    assert calls[-1] == ["rm", "-f", "cid"]
    assert "could not copy /testbed" in capsys.readouterr().out


def test_ensure_without_a_cache_or_a_binary_is_none(monkeypatch, capsys):
    monkeypatch.setattr(SweBenchCodeIndex, "host_binary", classmethod(lambda cls: None))

    assert SweBenchCodeIndex.ensure(ROW, "img") is None
    assert "ling-code is not installed" in capsys.readouterr().out


def test_ensure_returns_none_when_the_testbed_cannot_be_copied(monkeypatch, tmp_path):
    with_binary(monkeypatch, binary_file(tmp_path))
    fake_docker(monkeypatch, cp=1)

    assert SweBenchCodeIndex.ensure(ROW, "img") is None


def test_ensure_timeout_is_logged_and_the_testbed_copy_is_removed(monkeypatch, tmp_path):
    with_binary(monkeypatch, binary_file(tmp_path))
    fake_docker(monkeypatch)

    def hang(command, **kwargs):
        Path(kwargs["cwd"]).mkdir(parents=True, exist_ok=True)
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(SweBenchCodeIndex, "execute", staticmethod(hang))

    assert SweBenchCodeIndex.ensure(ROW, "img") is None

    directory = SweBenchCodeIndex.index_dir(ROW)
    assert (directory / "index.log").read_text() == "timed out"
    assert not (directory / "testbed").exists()


def exact_execute(
    monkeypatch: pytest.MonkeyPatch, manifest: Any, effects: list[Any] | None = None
) -> list[list]:
    commands: list[list] = []

    def execute(command, **kwargs):
        commands.append(command)
        effect = effects[len(commands) - 1] if effects else None
        if isinstance(effect, BaseException):
            raise effect
        if manifest is not None:
            scip = Path(kwargs["env"]["MIGHTLING_CODE_STATE_DIR"]) / "scip"
            scip.mkdir(parents=True, exist_ok=True)
            (scip / "manifest.json").write_text(json.dumps(manifest))
        return done(command, 0, "indexed\n")

    monkeypatch.setattr(SweBenchCodeIndex, "execute", staticmethod(execute))
    return commands


def test_ensure_exact_returns_none_when_the_testbed_cannot_be_copied(monkeypatch, tmp_path):
    binary = binary_file(tmp_path)
    fake_docker(monkeypatch, cp=1)

    assert SweBenchCodeIndex.ensure_exact(ROW, "img", str(binary)) is None


def test_ensure_exact_logs_a_timed_out_attempt_and_goes_on(monkeypatch, tmp_path):
    binary = binary_file(tmp_path)
    fake_docker(monkeypatch)
    no_sleep(monkeypatch)
    attempts: list[int] = []

    def execute(command, **kwargs):
        attempts.append(1)
        scip = Path(kwargs["env"]["MIGHTLING_CODE_STATE_DIR"]) / "scip"
        scip.mkdir(parents=True, exist_ok=True)
        if len(attempts) == 1:
            runs = {"scip-python:acme": {"status": "deferred: busy", "store": ""}}
            (scip / "manifest.json").write_text(json.dumps({"runs": runs}))
            raise subprocess.TimeoutExpired(command, 1)
        (scip / "a.db").write_text("store")
        runs = {"scip-python:acme": {"status": "ok", "store": "a.db", "peak_rss_mb": 5}}
        (scip / "manifest.json").write_text(json.dumps({"runs": runs}))
        return done(command, 0, "indexed\n")

    monkeypatch.setattr(SweBenchCodeIndex, "execute", staticmethod(execute))

    record = SweBenchCodeIndex.ensure_exact(ROW, "img", str(binary))

    log = (SweBenchCodeIndex.index_dir(ROW, "exact") / "index.log").read_text()
    assert "--- attempt 1\ntimed out" in log and "--- attempt 2\nindexed" in log
    assert record["stores"] == ["scip-python:acme"] and record["failed"] == {}


def test_ensure_exact_without_a_readable_manifest_records_no_store_and_warns(
    monkeypatch, tmp_path, capsys
):
    binary = binary_file(tmp_path)
    fake_docker(monkeypatch)
    commands = exact_execute(monkeypatch, None)

    record = SweBenchCodeIndex.ensure_exact(ROW, "img", str(binary))

    assert len(commands) == 1
    assert record["stores"] == [] and record["failed"] == {} and record["peak_mb"] == 0
    assert record["cached"] is False
    assert "no SCIP store was built" in capsys.readouterr().out


def test_ensure_exact_run_still_deferred_on_the_last_attempt_is_listed_as_failed(
    monkeypatch, tmp_path
):
    binary = binary_file(tmp_path)
    fake_docker(monkeypatch)
    pauses = no_sleep(monkeypatch)
    commands = exact_execute(
        monkeypatch, {"runs": {"scip-python:acme": {"status": "deferred: busy", "store": ""}}}
    )

    record = SweBenchCodeIndex.ensure_exact(ROW, "img", str(binary))

    assert len(commands) == EXACT_ATTEMPTS
    assert pauses == [swe_bench_code_index.EXACT_RETRY_PAUSE_S] * (EXACT_ATTEMPTS - 1)
    assert record["stores"] == []
    assert record["failed"] == {"scip-python:acme": "deferred: busy"}


def test_cached_without_an_index_directory_is_empty():
    assert not (swe_bench_settings.CACHE_DIR / "index").exists()
    assert SweBenchCodeIndex.cached() == []


def test_cached_lists_the_index_names_sorted():
    root = swe_bench_settings.CACHE_DIR / "index"
    for name in ("b@2", "a@1"):
        (root / name).mkdir(parents=True)

    assert SweBenchCodeIndex.cached() == ["a@1", "b@2"]


def test_the_suite_does_not_see_the_real_cache():
    assert str(swe_bench_settings.CACHE_DIR).startswith(os.environ["HOME"])
