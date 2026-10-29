"""Tests for `SweBenchDocker` and `SweBenchRuntime`.

`SweBenchDocker` is the one place the benchmark runs `docker`; `SweBenchRuntime` is the copy of
`ling` mounted into every instance container.

`subprocess.run` is replaced in each test (or `SweBenchDocker.run`, for the methods built on it),
so nothing here runs `docker`, `ldd` or `patchelf`.
"""

import json
import pathlib
import subprocess

import pytest

from dreamference.swe_bench import SweBenchDocker, SweBenchRuntime, swe_bench_docker


def fake_run(monkeypatch, outcome):
    """Makes `subprocess.run` in the docker module raise or return `outcome`; returns its calls."""
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(swe_bench_docker.subprocess, "run", run)
    return calls


def test_a_timed_out_docker_command_returns_its_partial_output_as_text(monkeypatch):
    # On POSIX `TimeoutExpired.stdout` is bytes even in text mode, so a timed-out `docker exec`
    # that had printed something handed bytes to callers that join it with text: a scrub script
    # that timed out became "runner error: can't concat str to bytes" instead of its 124.
    fake_run(monkeypatch, subprocess.TimeoutExpired(["docker"], 5, output=b"note: partial\n"))

    result = SweBenchDocker.run(["exec", "c", "bash", "-c", "true"], timeout=5)

    assert result.returncode == 124
    assert result.stdout == "note: partial\n"
    assert result.stderr == "timed out"
    assert (
        "tree: equal" not in result.stdout
    )  # what the instance run asks of it, without a TypeError


def test_a_timed_out_docker_command_with_no_output_returns_empty_text(monkeypatch):
    fake_run(monkeypatch, subprocess.TimeoutExpired(["docker"], 5))

    result = SweBenchDocker.run(["ps"], timeout=5)

    assert (result.returncode, result.stdout) == (124, "")


def test_a_missing_docker_is_return_code_127(monkeypatch):
    fake_run(monkeypatch, FileNotFoundError("docker"))

    result = SweBenchDocker.run(["ps"])

    assert result.returncode == 127 and "docker" in result.stderr


def test_standard_input_is_closed_unless_text_is_given(monkeypatch):
    calls = fake_run(monkeypatch, subprocess.CompletedProcess(["docker"], 0, "", ""))

    SweBenchDocker.run(["ps"])
    SweBenchDocker.run(["load"], input_text="data")

    (first, quiet), (_, fed) = calls
    assert first == ["docker", "ps"]
    assert quiet["stdin"] is subprocess.DEVNULL and quiet["input"] is None
    assert fed["stdin"] is None and fed["input"] == "data"


def test_an_image_is_named_by_its_repository_digest_or_else_its_id(monkeypatch):
    answers = iter(
        [
            subprocess.CompletedProcess([], 0, '["acme/widget@sha256:abc"] sha256:id1\n', ""),
            subprocess.CompletedProcess([], 0, "[] sha256:id2\n", ""),
            subprocess.CompletedProcess([], 0, "<no value> sha256:id3\n", ""),
            subprocess.CompletedProcess([], 1, "", "No such image"),
        ]
    )
    monkeypatch.setattr(
        SweBenchDocker, "run", classmethod(lambda cls, args, timeout=None: next(answers))
    )

    assert SweBenchDocker.image_digest("acme/widget") == "sha256:abc"
    assert SweBenchDocker.image_digest("built") == "sha256:id2"
    assert SweBenchDocker.image_digest("odd") == "sha256:id3"
    assert SweBenchDocker.image_digest("absent") is None


def docker_answering(monkeypatch, networks):
    """Plays `docker network inspect/create` over `networks` (name to record); returns the calls."""
    calls = []

    def run(cls, args, timeout=None):
        calls.append(args)
        if args[:2] == ["network", "inspect"]:
            record = networks.get(args[2])
            if record is None:
                return subprocess.CompletedProcess(args, 1, "", "not found")
            return subprocess.CompletedProcess(
                args, 0, record if isinstance(record, str) else json.dumps([record]), ""
            )
        if args[:3] == ["network", "create", "--internal"]:
            if "refuse" in networks:
                return subprocess.CompletedProcess(args, 1, "", "denied")
            networks[args[3]] = {
                "Internal": True,
                "IPAM": {"Config": [{"Subnet": "172.30.0.0/16", "Gateway": "172.30.0.1"}]},
            }
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected docker {args}")

    monkeypatch.setattr(SweBenchDocker, "run", classmethod(run))
    return calls


def test_the_network_is_created_internal_and_its_gateway_found(monkeypatch):
    calls = docker_answering(monkeypatch, {})

    assert SweBenchDocker.ensure_network("swebench") == "172.30.0.1"
    assert ["network", "create", "--internal", "swebench"] in calls
    assert SweBenchDocker.subnet("swebench") == "172.30.0.0/16"


def test_a_network_that_is_not_internal_is_never_used(monkeypatch):
    # The containers run an agent with the sandbox off: a network with a route out would let it
    # reach the internet, so an existing one of the same name that is not internal is refused.
    calls = docker_answering(
        monkeypatch,
        {"swebench": {"Internal": False, "IPAM": {"Config": [{"Gateway": "172.17.0.1"}]}}},
    )

    assert SweBenchDocker.ensure_network("swebench") is None
    assert not any(args[:2] == ["network", "create"] for args in calls)


def test_a_network_that_cannot_be_made_or_has_no_gateway_is_none(monkeypatch):
    docker_answering(monkeypatch, {"refuse": True})
    assert SweBenchDocker.ensure_network("swebench") is None

    docker_answering(
        monkeypatch,
        {"swebench": {"Internal": True, "IPAM": {"Config": [{"Subnet": "10.0.0.0/8"}]}}},
    )
    assert SweBenchDocker.ensure_network("swebench") is None

    docker_answering(monkeypatch, {"swebench": "not json", "empty": "[]"})
    assert SweBenchDocker.network("swebench") is None
    assert SweBenchDocker.network("empty") is None
    assert SweBenchDocker.subnet("swebench") is None


# -- SweBenchRuntime ---------------------------------------------------------------------------


@pytest.fixture
def host(tmp_path, monkeypatch):
    """An installed `ling` with its code-mode host, `ldd` libraries and a `patchelf` stand-in.

    The stand-in records its calls, or fails when `host.fail` is set.
    """
    from dreamference.swe_bench import swe_bench_runtime, swe_bench_settings

    monkeypatch.setattr(swe_bench_settings, "CACHE_DIR", tmp_path / "cache")
    install = tmp_path / "install"
    install.mkdir()
    (install / "ling").write_bytes(b"ling v1")
    (install / swe_bench_runtime.CODE_MODE_HOST_NAME).write_bytes(b"host v1")
    system = tmp_path / "lib"
    system.mkdir()
    for name in ("ld-linux-aarch64.so.1", "libc.so.6", "libm.so.6", "libgcc_s.so.1"):
        (system / name).write_bytes(name.encode())
    monkeypatch.setattr(
        SweBenchRuntime,
        "host_libraries",
        classmethod(
            lambda cls, binary: (
                str(system / "ld-linux-aarch64.so.1"),
                [str(system / name) for name in ("libc.so.6", "libm.so.6", "libgcc_s.so.1")],
            )
        ),
    )
    state = type("Host", (), {"ling": str(install / "ling"), "patchelf": [], "fail": False})()

    def run(command, **kwargs):
        if state.fail:
            raise subprocess.CalledProcessError(1, command, "", "patchelf: not an ELF")
        state.patchelf.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(swe_bench_runtime.subprocess, "run", run)
    return state


def test_the_runtime_is_built_once_per_installed_binaries(host):
    built = SweBenchRuntime.ensure(host.ling, "/venv/bin/patchelf")

    runtime = SweBenchRuntime.directory()
    assert built == SweBenchRuntime.current_hash() == SweBenchRuntime.source_hash(host.ling)
    assert sorted(p.name for p in (runtime / "bin").iterdir()) == ["codex-code-mode-host", "ling"]
    assert (runtime / "lib" / "libc.so.6").read_bytes() == b"libc.so.6"
    # Each copy is pointed at the loader and libraries under the container's mount.
    assert len(host.patchelf) == 2
    assert host.patchelf[0][1:5] == [
        "--set-interpreter",
        "/opt/ling/lib/ld-linux-aarch64.so.1",
        "--set-rpath",
        "/opt/ling/lib",
    ]

    assert SweBenchRuntime.ensure(host.ling, "/venv/bin/patchelf") == built
    assert len(host.patchelf) == 2  # unchanged binaries: nothing rebuilt


def test_a_new_build_of_ling_rebuilds_the_runtime(host):
    first = SweBenchRuntime.ensure(host.ling, "patchelf")
    with pathlib.Path(host.ling).open("wb") as handle:
        handle.write(b"ling v2")

    second = SweBenchRuntime.ensure(host.ling, "patchelf")

    assert second != first and SweBenchRuntime.current_hash() == second
    assert (SweBenchRuntime.directory() / "bin" / "ling").read_bytes() == b"ling v2"


def test_a_failed_build_keeps_the_runtime_that_was_there(host, capsys):
    first = SweBenchRuntime.ensure(host.ling, "patchelf")
    with pathlib.Path(host.ling).open("wb") as handle:
        handle.write(b"ling v2")
    host.fail = True

    assert SweBenchRuntime.ensure(host.ling, "patchelf") is None

    runtime = SweBenchRuntime.directory()
    assert SweBenchRuntime.current_hash() == first
    assert (runtime / "bin" / "ling").read_bytes() == b"ling v1"
    assert [p.name for p in runtime.parent.iterdir()] == ["runtime"]  # no staging left behind
    assert "Could not build the ling runtime" in capsys.readouterr().out


def test_ldd_output_without_a_loader_is_refused(monkeypatch):
    from dreamference.swe_bench import swe_bench_runtime

    ldd = (
        "\tlibc.so.6 => /lib/libc.so.6 (0x1)\n\tlibm.so.6 => /lib/libm.so.6 (0x2)\n"
        "\tlibgcc_s.so.1 => /lib/libgcc_s.so.1 (0x3)\n"
    )
    monkeypatch.setattr(
        swe_bench_runtime.subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(command, 0, ldd, ""),
    )

    with pytest.raises(ValueError, match="unexpected"):
        SweBenchRuntime.host_libraries("/x/ling")
