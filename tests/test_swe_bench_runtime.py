"""The relocated `ling` runtime off the happy path: a build that fails keeps the runtime there was,
and a library `ldd` names that the runtime does not carry is refused. ldd and patchelf are mocked."""

import subprocess

import pytest

from dreamference.swe_bench import swe_bench_runtime
from dreamference.swe_bench.swe_bench_runtime import STAMP_NAME, SweBenchRuntime

LDD = """\
\tlinux-vdso.so.1 (0x0000ffff)
\tlibc.so.6 => {lib}/libc.so.6 (0x0000ffff)
\tlibm.so.6 => {lib}/libm.so.6 (0x0000ffff)
\tlibgcc_s.so.1 => {lib}/libgcc_s.so.1 (0x0000ffff)
\t{lib}/ld-linux-aarch64.so.1 (0x0000ffff)
"""


@pytest.fixture
def host(tmp_path, monkeypatch):
    """An installed `ling`, its libraries, and the runtime directory, all in `tmp_path`."""
    lib = tmp_path / "lib"
    lib.mkdir()
    for name in ("libc.so.6", "libm.so.6", "libgcc_s.so.1", "ld-linux-aarch64.so.1"):
        (lib / name).write_bytes(b"\x7fELF " + name.encode())
    ling = tmp_path / "bin" / "ling"
    ling.parent.mkdir()
    ling.write_bytes(b"\x7fELF ling")
    monkeypatch.setattr(SweBenchRuntime, "directory", classmethod(lambda cls: tmp_path / "cache" / "runtime"))
    (tmp_path / "cache").mkdir()
    return {"ling": str(ling), "ldd": LDD.format(lib=lib), "root": tmp_path}


def tools(monkeypatch, ldd, patchelf_returncode=0):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "ldd":
            return subprocess.CompletedProcess(argv, 0, ldd, "")
        if patchelf_returncode:
            raise subprocess.CalledProcessError(patchelf_returncode, argv, "", "no space")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(swe_bench_runtime.subprocess, "run", run)
    return calls


def test_a_runtime_made_from_the_installed_binaries_is_not_rebuilt(host, monkeypatch):
    target = SweBenchRuntime.directory()
    target.mkdir()
    (target / STAMP_NAME).write_text(SweBenchRuntime.source_hash(host["ling"]) + "\n")
    monkeypatch.setattr(swe_bench_runtime.subprocess, "run", lambda *a, **k: pytest.fail("rebuilt"))
    assert SweBenchRuntime.ensure(host["ling"], "patchelf") == SweBenchRuntime.source_hash(host["ling"])


def test_a_build_points_the_copies_at_the_mounted_loader_and_libraries(host, monkeypatch):
    calls = tools(monkeypatch, host["ldd"])
    wanted = SweBenchRuntime.ensure(host["ling"], "patchelf")
    target = SweBenchRuntime.directory()
    assert wanted and (target / STAMP_NAME).read_text().strip() == wanted
    assert sorted(p.name for p in (target / "lib").iterdir()) == [
        "ld-linux-aarch64.so.1", "libc.so.6", "libgcc_s.so.1", "libm.so.6"
    ]
    patchelf = calls[-1]
    assert patchelf[:3] == ["patchelf", "--set-interpreter", "/opt/ling/lib/ld-linux-aarch64.so.1"]
    assert patchelf[3:5] == ["--set-rpath", "/opt/ling/lib"] and patchelf[5].endswith("/bin/ling")
    assert (target / "bin" / "ling").read_bytes() == b"\x7fELF ling", "the copy is patched, not the installed binary"
    assert not list(host["root"].joinpath("cache").glob(".runtime.*")), "the staging folder is renamed"


def test_a_build_that_fails_keeps_the_runtime_there_was(host, monkeypatch, capsys):
    target = SweBenchRuntime.directory()
    (target / "bin").mkdir(parents=True)
    (target / "bin" / "ling").write_text("the old runtime")
    tools(monkeypatch, host["ldd"], patchelf_returncode=1)
    assert SweBenchRuntime.ensure(host["ling"], "patchelf") is None
    assert (target / "bin" / "ling").read_text() == "the old runtime"
    assert not list(host["root"].joinpath("cache").glob(".runtime.*"))
    assert "Could not build the ling runtime" in capsys.readouterr().out


def test_a_library_the_runtime_does_not_carry_stops_the_build(host, monkeypatch, capsys):
    tools(monkeypatch, host["ldd"] + "\tlibssl.so.3 => /usr/lib/libssl.so.3 (0x0000ffff)\n")
    assert SweBenchRuntime.ensure(host["ling"], "patchelf") is None
    assert "libssl.so.3" in capsys.readouterr().out
    assert not SweBenchRuntime.directory().exists()


def test_an_optional_library_is_carried_when_ldd_names_it(host, monkeypatch):
    extra = host["root"] / "lib" / "liblzma.so.5"
    extra.write_bytes(b"lzma")
    tools(monkeypatch, host["ldd"] + f"\tliblzma.so.5 => {extra} (0x0000ffff)\n")
    loader, libraries = SweBenchRuntime.host_libraries(host["ling"])
    assert loader.endswith("ld-linux-aarch64.so.1") and str(extra) in libraries


@pytest.mark.parametrize("drop", ["ld-linux", "libm.so.6"])
def test_ldd_output_without_the_loader_or_a_core_library_is_refused(drop, host, monkeypatch):
    ldd = "".join(line + "\n" for line in host["ldd"].splitlines() if drop not in line)
    tools(monkeypatch, ldd)
    with pytest.raises(ValueError, match="unexpected"):
        SweBenchRuntime.host_libraries(host["ling"])


def test_the_code_mode_host_is_copied_only_when_it_is_installed(host):
    ling = host["ling"]
    assert SweBenchRuntime._binaries(ling) == [ling]
    hosted = host["root"] / "bin" / swe_bench_runtime.CODE_MODE_HOST_NAME
    hosted.write_bytes(b"host")
    assert SweBenchRuntime._binaries(ling) == [ling, str(hosted)]


def test_an_unreadable_stamp_reads_as_no_runtime(host):
    assert SweBenchRuntime.current_hash() is None
    SweBenchRuntime.directory().mkdir()
    (SweBenchRuntime.directory() / STAMP_NAME).write_text("\n")
    assert SweBenchRuntime.current_hash() is None
