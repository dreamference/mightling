"""The 1.9.0 transition in release material: the older asset names and the installer's move.

Nothing here touches the real machine: the workflow step runs in a scratch folder and the installer
in the test's own HOME against the stand-in release of test_release_install.
"""

import gzip
import hashlib
import subprocess
from pathlib import Path

import pytest
import yaml

from tests import test_release_install as base

binaries, run_install = base.binaries, base.run_install
# Fixtures of the installer's tests, used here as they are (the signing keys are an autouse one).
release_keys, release_server = base.release_keys, base.release_server

ROOT = Path(__file__).resolve().parent.parent
LINUX = "aarch64-unknown-linux-gnu"
MAC = "aarch64-apple-darwin"
STEP = "Add the older asset names"


def older_names_step() -> dict:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    return next(step for step in steps if step.get("name") == STEP)


def sums(names, target):
    return "".join(f"{hashlib.sha256(name.encode()).hexdigest()}  {name}\n" for name in names)


def artifacts_of_a_release(root: Path) -> Path:
    """The folder `release.yml` has after it downloads the build artifacts: two targets."""
    art = root / "artifacts"
    art.mkdir()
    linux_names = [
        f"{name}-{LINUX}.gz"
        for name in (
            "ragon",
            "codex-code-mode-host",
            "ragon-search",
            "ragon-fetch",
            "ragon-code",
            "ragon-docs",
            "ragon-signal",
        )
    ]
    for name in linux_names:
        (art / name).write_bytes(gzip.compress(name.encode()))
    (art / f"ragon-{LINUX}.sha256sums").write_text(sums(linux_names, LINUX))
    mac_names = [f"ragon-{MAC}.gz", f"ragon-search-{MAC}.gz"]
    for name in mac_names:
        (art / name).write_bytes(gzip.compress(name.encode()))
    (art / f"ragon-{MAC}.sha256sums").write_text(sums(mac_names, MAC))
    (art / "Ragon-Setup-1.9.0-x64.exe").write_bytes(b"exe")
    return art


def run_step(root: Path) -> None:
    subprocess.run(
        ["bash", "-euo", "pipefail", "-c", older_names_step()["run"]], cwd=root, check=True
    )


def test_the_older_asset_names_are_published_for_1_9_0_only():
    assert older_names_step()["if"] == "${{ inputs.version == '1.9.0' }}"


def test_1_8_gets_the_ling_names_and_1_4_the_five_puffin_names_over_the_same_bytes(tmp_path):
    art = artifacts_of_a_release(tmp_path)
    before = {path.name: path.read_bytes() for path in art.iterdir()}
    run_step(tmp_path)
    names = {path.name for path in art.iterdir()}
    for name in before:
        assert name in names, "the Ragon assets are untouched"
        assert (art / name).read_bytes() == before[name]
    ling = {n for n in names if n.startswith("ling-")}
    assert ling == {
        f"ling-{LINUX}.gz",
        f"ling-search-{LINUX}.gz",
        f"ling-fetch-{LINUX}.gz",
        f"ling-code-{LINUX}.gz",
        f"ling-docs-{LINUX}.gz",
        f"ling-signal-{LINUX}.gz",
        f"ling-{LINUX}.sha256sums",
        f"ling-{MAC}.gz",
        f"ling-search-{MAC}.gz",
        f"ling-{MAC}.sha256sums",
    }
    assert (art / f"ling-{LINUX}.gz").read_bytes() == before[f"ragon-{LINUX}.gz"]
    puffin = {n for n in names if n.startswith("puffin")}
    assert puffin == {
        f"puffin-{LINUX}.gz",
        f"puffin-search-{LINUX}.gz",
        f"puffin-fetch-{LINUX}.gz",
        f"puffin-code-{LINUX}.gz",
        f"puffin-{LINUX}.sha256sums",
    }, "1.4.x was Linux only and asks for these names, nothing from a Mac"
    assert not any(name.startswith(("ling-Ragon", "ling-Setup")) for name in names)


def test_the_older_sums_files_name_the_older_files_and_list_only_what_exists(tmp_path):
    art = artifacts_of_a_release(tmp_path)
    run_step(tmp_path)
    ling_sums = (art / f"ling-{LINUX}.sha256sums").read_text().splitlines()
    assert all("ragon" not in line for line in ling_sums)
    assert {line.split("  ")[1] for line in ling_sums} == {
        f"ling-{LINUX}.gz",
        f"codex-code-mode-host-{LINUX}.gz",
        f"ling-search-{LINUX}.gz",
        f"ling-fetch-{LINUX}.gz",
        f"ling-code-{LINUX}.gz",
        f"ling-docs-{LINUX}.gz",
        f"ling-signal-{LINUX}.gz",
    }
    puffin_sums = (art / f"puffin-{LINUX}.sha256sums").read_text().splitlines()
    assert {line.split("  ")[1] for line in puffin_sums} == {
        f"puffin-{LINUX}.gz",
        f"codex-code-mode-host-{LINUX}.gz",
        f"puffin-search-{LINUX}.gz",
        f"puffin-fetch-{LINUX}.gz",
        f"puffin-code-{LINUX}.gz",
    }
    # A line's hash is the one the Ragon file has, and the name now points at a file that exists.
    for line in ling_sums + puffin_sums:
        digest, name = line.split("  ")
        assert (art / name).is_file()
        if name.startswith(("ling-", "puffin-")):
            ragon = (
                name.replace("puffin", "ragon", 1)
                if name.startswith("puffin")
                else name.replace("ling", "ragon", 1)
            )
            assert digest == hashlib.sha256(ragon.encode()).hexdigest()


def old_install(home: Path, folder: str, binaries_named) -> Path:
    bin_dir = home / ".local/share/dreamference" / folder / "bin"
    bin_dir.mkdir(parents=True)
    for name in binaries_named:
        (bin_dir / name).write_text(f"#!/bin/sh\necho old {name}\n")
        (bin_dir / name).chmod(0o755)
    link_dir = home / ".local/bin"
    link_dir.mkdir(parents=True, exist_ok=True)
    for name in binaries_named:
        (link_dir / name).symlink_to(bin_dir / name)
    return bin_dir


@pytest.mark.parametrize(
    ("folder", "names"),
    [
        ("mightling", ("ling", "ling-search", "ling-fetch", "ling-code")),
        ("puffin", ("puffin", "puffin-search", "puffin-fetch", "puffin-code")),
    ],
)
def test_the_installer_moves_an_older_install_and_removes_its_links(
    tmp_path, release_server, folder, names
):
    old_install(tmp_path, folder, names)
    result = run_install(tmp_path, release_server(binaries()), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    share = tmp_path / ".local/share/dreamference"
    assert not (share / folder).exists()
    assert (share / "ragon/bin/ragon").is_file()
    assert "is now Ragon" in result.stdout
    link_dir = tmp_path / ".local/bin"
    assert (
        sorted(path.name for path in link_dir.iterdir() if path.is_symlink())
        == ["ragon", "ragon-fetch", "ragon-search"]
        or (link_dir / "ragon").is_symlink()
    )
    for name in names:
        assert not (link_dir / name).exists() and not (link_dir / name).is_symlink()
    out = subprocess.run([str(link_dir / "ragon")], capture_output=True, text=True).stdout
    assert out == "ragon from the release\n", "the release's binary replaced the moved one"


def test_the_installer_leaves_a_real_ling_file_alone(tmp_path, release_server):
    link_dir = tmp_path / ".local/bin"
    link_dir.mkdir(parents=True)
    (link_dir / "ling").write_text("#!/bin/sh\necho mine\n")
    result = run_install(tmp_path, release_server(binaries()), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (link_dir / "ling").read_text() == "#!/bin/sh\necho mine\n"


def test_a_ragon_install_is_not_overwritten_by_an_older_folder(tmp_path, release_server):
    old_install(tmp_path, "mightling", ("ling",))
    new = tmp_path / ".local/share/dreamference/ragon/bin"
    new.mkdir(parents=True)
    (new / "keep").write_text("x")
    result = run_install(tmp_path, release_server(binaries()), "--role", "client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (new / "keep").is_file()
    assert (tmp_path / ".local/share/dreamference/mightling").is_dir(), (
        "left for `ragon` to deal with"
    )
