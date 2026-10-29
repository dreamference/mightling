"""Tests for the night shift's host probes: every request and command is a stand-in."""

import fcntl
import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from dreamference.night_shift import night_shift_host
from dreamference.night_shift.night_shift_host import GIB, NightShiftHost


def answering(body: bytes):
    """Returns an `urlopen` stand-in that answers every request with `body`."""
    return lambda url, timeout=None: io.BytesIO(body)


def refusing(url, timeout=None):
    """An `urlopen` stand-in for a server that is not there."""
    raise ConnectionRefusedError(url)


def test_the_served_model_is_its_id_and_context_length(monkeypatch):
    body = json.dumps({"data": [{"id": "qwen", "max_model_len": 131072}]}).encode()
    monkeypatch.setattr(night_shift_host.urllib.request, "urlopen", answering(body))
    assert NightShiftHost.served_model("http://model/") == ("qwen", 131072)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",
        b'{"data": []}',
        b'{"data": [{"max_model_len": 1}]}',
        b'{"data": [{"id": "q", "max_model_len": "x"}]}',
    ],
)
def test_a_model_server_that_answers_nonsense_serves_nothing(monkeypatch, body):
    monkeypatch.setattr(night_shift_host.urllib.request, "urlopen", answering(body))
    assert NightShiftHost.served_model("http://model") is None


def test_a_model_server_that_does_not_answer_serves_nothing_and_has_no_metrics(monkeypatch):
    monkeypatch.setattr(night_shift_host.urllib.request, "urlopen", refusing)
    assert NightShiftHost.served_model("http://model") is None
    assert NightShiftHost.metrics("http://model") is None


def test_metrics_add_running_and_queued_requests_and_read_the_kv_pool(monkeypatch):
    text = (
        b"# HELP sglang:num_running_reqs running\n"
        b'sglang:num_running_reqs{model="q"} 2\n'
        b'sglang:num_queue_reqs{model="q"} 3\n'
        b"sglang:prompt_tokens_total 1e3\n"
        b"sglang:max_total_num_tokens 500000\n"
        b"sglang:num_queue_reqs NaNish\n"
        b"garbage line\n"
    )
    monkeypatch.setattr(night_shift_host.urllib.request, "urlopen", answering(text))
    assert NightShiftHost.metrics("http://model") == {
        "running": 5.0,
        "served": 1000.0,
        "kv_pool": 500000.0,
    }


def test_vllm_metrics_take_the_kv_pool_from_blocks_times_block_size():
    text = (
        'vllm:num_requests_running{m="q"} 1\n'
        'vllm:num_requests_waiting{m="q"} 1\n'
        'vllm:cache_config_info{block_size="16",num_gpu_blocks="1000"} 1\n'
    )
    assert NightShiftHost.parse_metrics(text) == {"running": 2.0, "served": 0.0, "kv_pool": 16000.0}


def test_the_host_is_unsafe_when_the_model_servers_own_checks_exit(monkeypatch):
    from dreamference.vllm_server import VLLMServerManager

    monkeypatch.setattr(VLLMServerManager, "check_host_safety", classmethod(lambda cls: None))
    assert NightShiftHost.host_safety_ok() is True

    def unsafe(cls):
        raise SystemExit(1)

    monkeypatch.setattr(VLLMServerManager, "check_host_safety", classmethod(unsafe))
    assert NightShiftHost.host_safety_ok() is False


@pytest.mark.parametrize(
    ("size", "expected"),
    [("8G", 8 * GIB), ("512M", 512 * 1024**2), ("1.5GiB", 3 * GIB // 2), ("4096", 4096)],
)
def test_a_systemd_size_is_read_in_bytes(size, expected):
    assert NightShiftHost.parse_size(size) == expected


@pytest.mark.parametrize("size", ["", "lots", "8X", "-1G"])
def test_something_that_is_not_a_size_is_refused(size):
    with pytest.raises(ValueError, match="not a size"):
        NightShiftHost.parse_size(size)


@pytest.mark.parametrize(
    ("stdout", "expected"), [("123456\n", 123456), ("[not set]\n", 0), ("", 0)]
)
def test_a_scopes_memory_is_what_systemd_reports_or_0(monkeypatch, stdout, expected):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout))
    assert NightShiftHost.scope_memory_current("night-task.scope") == expected


def test_a_scopes_memory_is_0_when_systemctl_cannot_be_run(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("systemctl", 10)

    monkeypatch.setattr(subprocess, "run", fail)
    assert NightShiftHost.scope_memory_current("night-task.scope") == 0


@pytest.fixture
def quiet_units(monkeypatch, tmp_path):
    """No build lock and no active units; returns the list of units systemctl will report."""
    monkeypatch.setattr(night_shift_host, "BUILD_CACHE_DIR", str(tmp_path))
    units: list[str] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="\n".join(units)))
    return units


def test_a_held_build_lock_is_heavy_work_and_a_free_one_is_not(quiet_units, tmp_path):
    lock = tmp_path / ".build.lock"
    lock.touch()
    assert "a ling build holds the build lock" not in NightShiftHost.heavy_jobs()
    with lock.open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        assert "a ling build holds the build lock" in NightShiftHost.heavy_jobs()


def test_active_index_and_upstream_test_units_are_heavy_work(quiet_units):
    quiet_units += [
        "mightling-index-repo.service loaded active running Index",
        "",
        "codex-tests-42.scope loaded active running Tests",
    ]
    found = NightShiftHost.heavy_jobs()
    assert "an index run is active (mightling-index-repo.service)" in found
    assert "an upstream test run is active (codex-tests-42.scope)" in found


def test_heavy_work_is_still_reported_when_systemctl_cannot_be_run(monkeypatch, tmp_path):
    monkeypatch.setattr(night_shift_host, "BUILD_CACHE_DIR", str(tmp_path))

    def fail(*args, **kwargs):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(subprocess, "run", fail)
    assert not [job for job in NightShiftHost.heavy_jobs() if "is active (" in job]
