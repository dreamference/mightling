"""SafeGit: host-side git in a checkout the agent can write runs none of the programs its config names.

Mirrors the Rust tests in `ling-rs/safegit`: a control proves plain git runs the hostile config's
programs, then none run through the helper or the Python callers that use it. Repository hooks DO
run (owner's choice, 2026-10-28), and a test pins that.
"""

import os
import re
import stat
import subprocess
from pathlib import Path

from dreamference.night_shift.night_shift_task_run import NightShiftTaskRun
from dreamference.node.node_job import NodeJob
from dreamference.runner.safe_git import FIXED_CONFIG, SafeGit

# A developer's global config (a signing key, say) must not change what the controls prove.
PLAIN_ENV = dict(
    os.environ,
    GIT_CONFIG_NOSYSTEM="1",
    GIT_CONFIG_GLOBAL=os.devnull,
    GIT_PAGER="",
    GIT_TERMINAL_PROMPT="0",
)


def _plain(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        env=PLAIN_ENV,
    )


def _script(directory: Path, name: str, marker: Path) -> str:
    path = directory / name
    path.write_text(f"#!/bin/sh\necho ran-{name} >> '{marker}'\ncat\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _hostile(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    tools = tmp_path / "tools"
    repo.mkdir()
    tools.mkdir()
    marker = tmp_path / "marker"
    _plain(repo, "init", "-q")
    (repo / "a.txt").write_text("one\n")
    (repo / ".gitattributes").write_text("*.txt filter=evil diff=evil\n")
    _plain(repo, "add", "-A")
    _plain(repo, "commit", "-q", "-m", "init")
    s = {
        name: _script(tools, name, marker)
        for name in (
            "fsmonitor",
            "pager",
            "external",
            "textconv",
            "clean",
            "smudge",
            "ssh",
            "cred",
            "uploadpack",
        )
    }
    (repo / ".git" / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n"
        f"\tfsmonitor = {s['fsmonitor']}\n\tpager = {s['pager']}\n\tsshCommand = {s['ssh']}\n"
        f"[diff]\n\texternal = {s['external']}\n"
        f'[diff "evil"]\n\ttextconv = {s["textconv"]}\n\tcommand = {s["external"]}\n'
        f'[filter "evil"]\n\tclean = {s["clean"]}\n\tsmudge = {s["smudge"]}\n'
        f"[credential]\n\thelper = {s['cred']}\n"
        f'[remote "origin"]\n\turl = {repo}\n\tuploadpack = {s["uploadpack"]}\n\treceivepack = {s["uploadpack"]}\n'
    )
    (repo / "a.txt").write_text("one\ntwo changed\n")
    return repo, marker


def _ran(marker: Path) -> str:
    return marker.read_text() if marker.exists() else ""


def test_the_hostile_config_does_run_programs_without_the_hardening(tmp_path):
    repo, marker = _hostile(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, env=PLAIN_ENV
    )
    subprocess.run(["git", "-C", str(repo), "diff"], capture_output=True, env=PLAIN_ENV)
    assert _ran(marker), "the control run should have executed a configured program"


def test_no_configured_program_runs_for_the_read_and_write_commands(tmp_path):
    repo, marker = _hostile(tmp_path)
    for args in (
        ["status", "--porcelain", "--untracked-files=all"],
        ["diff"],
        ["diff", "--cached"],
        ["show", "HEAD"],
        ["log", "-p", "-1"],
        ["rev-parse", "--verify", "HEAD"],
        ["ls-files", "-m"],
        ["add", "-A"],
        ["write-tree"],
        ["diff", "--stat", "HEAD"],
    ):
        result = SafeGit.run(args, repo)
        assert result.returncode == 0, (args, result.stderr)
    assert _ran(marker) == ""


def test_fetch_ls_remote_and_push_run_neither_the_remote_nor_ssh_programs(tmp_path):
    repo, marker = _hostile(tmp_path)
    for args in (["fetch", "origin"], ["ls-remote", "origin"]):
        result = SafeGit.run(args, repo)
        assert result.returncode == 0, result.stderr
    SafeGit.run(["push", "origin", "HEAD:refs/heads/probe"], repo)
    assert _ran(marker) == ""


def test_the_callers_that_use_it_run_no_program_either(tmp_path):
    repo, marker = _hostile(tmp_path)
    run = object.__new__(NightShiftTaskRun)
    assert run._git(repo, "status", "--porcelain")[0] == 0
    assert run._git(repo, "diff", "--cached", "--binary")[0] == 0
    assert NodeJob._git(["-C", str(repo), "status", "--porcelain"]).returncode == 0
    assert (
        NodeJob._git(
            ["--git-dir", str(repo / ".git"), "cat-file", "-e", "HEAD^{commit}"]
        ).returncode
        == 0
    )
    assert _ran(marker) == ""


def test_hooks_run_by_the_owners_choice(tmp_path):
    # Owner decision 2026-10-28 ("allow all for now"): repository hooks run from host-side git.
    # The known risk (an agent-written hook runs on the host) is in docs/dev/host-safety.md.
    repo = tmp_path / "repo"
    repo.mkdir()
    _plain(repo, "init", "-q")
    marker = tmp_path / "marker"
    _script(repo / ".git" / "hooks", "pre-commit", marker)
    (repo / "a.txt").write_text("x\n")
    SafeGit.run(["add", "-A"], repo)
    result = SafeGit.run(
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "m"], repo
    )
    assert result.returncode == 0, result.stderr
    assert "ran-pre-commit" in _ran(marker)
    assert not any(pair.startswith("core.hooksPath") for pair in FIXED_CONFIG)


def test_a_hung_command_times_out_with_exit_124(monkeypatch):
    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    result = SafeGit.run(["status"], timeout=0.1)
    assert result.returncode == 124
    assert "timed out" in result.stderr


def test_flags_follow_the_subcommand_only():
    assert SafeGit.safe_flags(["push", "origin"]) == [
        "push",
        "--receive-pack=git-receive-pack",
        "origin",
    ]
    assert SafeGit.safe_flags(["-C", "x", "fetch"]) == [
        "-C",
        "x",
        "fetch",
        "--upload-pack=git-upload-pack",
    ]
    assert SafeGit.safe_flags(["-c", "a=b", "log", "-1"]) == [
        "-c",
        "a=b",
        "log",
        "--no-ext-diff",
        "--no-textconv",
        "-1",
    ]
    assert SafeGit.safe_flags(["status", "--porcelain"]) == ["status", "--porcelain"]
    assert SafeGit.safe_flags(["rev-parse", "diff"]) == ["rev-parse", "diff"]


def test_the_environment_drops_program_variables_and_keeps_the_callers_ssh():
    env = SafeGit.environment(
        {"GIT_EXTERNAL_DIFF": "x", "GIT_PROXY_COMMAND": "y", "GIT_SSH_COMMAND": "ssh -i k"}
    )
    assert "GIT_EXTERNAL_DIFF" not in env
    assert env["GIT_PROXY_COMMAND"] == ""
    assert env["GIT_SSH_COMMAND"] == "ssh -i k"
    assert env["GIT_PAGER"] == "cat"


def test_the_environment_moves_no_config_file_and_adds_no_config():
    env = SafeGit.environment(
        {
            "GIT_CONFIG_SYSTEM": "/x",
            "GIT_CONFIG_GLOBAL": "/x",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "/x",
            "GIT_CONFIG_PARAMETERS": "'core.fsmonitor'='/x'",
            "PATH": "/usr/bin",
        }
    )
    assert not [name for name in env if name.startswith("GIT_CONFIG_")], env
    assert env["PATH"] == "/usr/bin"


def _hostile_system(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A repository with a changed file and a system-config file naming programs."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    marker = tmp_path / "marker"
    _plain(repo, "init", "-q")
    (repo / "a.txt").write_text("one\n")
    _plain(repo, "add", "-A")
    _plain(repo, "commit", "-q", "-m", "init")
    (repo / "a.txt").write_text("one\ntwo changed\n")
    s = {
        name: _script(tmp_path, name, marker)
        for name in ("fsmonitor", "ssh", "pager", "cred", "external")
    }
    system = tmp_path / "system-gitconfig"
    system.write_text(
        f"[core]\n\tfsmonitor = {s['fsmonitor']}\n\tsshCommand = {s['ssh']}\n\tpager = {s['pager']}\n"
        f"[credential]\n\thelper = {s['cred']}\n[diff]\n\texternal = {s['external']}\n"
    )
    return repo, system, marker


def test_the_system_config_is_read_but_a_program_it_names_loses_to_the_pins(tmp_path):
    """Git for Windows keeps core.autocrlf=true in its system config: the helper reads it."""
    repo, system, marker = _hostile_system(tmp_path)
    plain_env = {k: v for k, v in PLAIN_ENV.items() if k != "GIT_CONFIG_NOSYSTEM"}
    subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        env=dict(plain_env, GIT_CONFIG_SYSTEM=str(system)),
    )
    assert "ran-fsmonitor" in _ran(marker), "the control should run the system fsmonitor"

    repo, system, marker = _hostile_system(tmp_path / "proof")
    env = dict(SafeGit.environment(), GIT_CONFIG_SYSTEM=str(system))
    assert "GIT_CONFIG_NOSYSTEM" not in SafeGit.environment({})
    for args in (["status", "--porcelain"], ["diff"], ["log", "-p", "-1"]):
        done = subprocess.run(SafeGit.command(args, repo), capture_output=True, text=True, env=env)
        assert done.returncode == 0, done.stderr
    for key, pin in (
        ("core.fsmonitor", "false"),
        ("core.sshCommand", "ssh"),
        ("core.pager", "cat"),
        ("credential.helper", ""),
        ("diff.external", ""),
    ):
        done = subprocess.run(
            SafeGit.command(["config", "--get", key], repo), capture_output=True, text=True, env=env
        )
        assert done.stdout.rstrip("\n") == pin, key
    (system.parent / "autocrlf").write_text("[core]\n\tautocrlf = true\n")
    done = subprocess.run(
        SafeGit.command(["config", "--get", "core.autocrlf"], repo),
        capture_output=True,
        text=True,
        env=dict(SafeGit.environment(), GIT_CONFIG_SYSTEM=str(system.parent / "autocrlf")),
    )
    assert done.stdout.strip() == "true"
    assert _ran(marker) == ""


def test_driver_names_come_from_the_checkouts_own_config(tmp_path):
    repo, _ = _hostile(tmp_path)
    pairs = SafeGit.driver_overrides(repo)
    for want in (
        "filter.evil.clean=",
        "filter.evil.smudge=",
        "diff.evil.textconv=",
        "diff.evil.command=",
    ):
        assert want in pairs, pairs
    command = SafeGit.command(["diff", "HEAD"], repo)
    assert command[:2] == ["git", "--no-pager"]
    assert command[-4:] == ["diff", "--no-ext-diff", "--no-textconv", "HEAD"][-4:]
    assert "core.fsmonitor=false" in command


def test_a_git_proxy_and_a_signing_key_command_in_the_checkout_never_run(tmp_path):
    """core.gitProxy is first-value-wins, so `-c core.gitProxy=` alone lets the checkout's run."""
    marker = tmp_path / "marker"
    proxy = _script(tmp_path, "proxy", marker)
    key = _script(tmp_path, "keycommand", marker)

    def make(name: str) -> Path:
        repo = tmp_path / name
        repo.mkdir()
        _plain(repo, "init", "-q")
        for k, v in (
            ("core.gitProxy", proxy),
            ("gpg.format", "ssh"),
            ("commit.gpgSign", "true"),
            ("gpg.ssh.defaultKeyCommand", key),
            ("user.name", "t"),
            ("user.email", "t@t"),
        ):
            _plain(repo, "config", k, v)
        return repo

    control = make("control")
    for args in (
        ["-c", "core.gitProxy=", "ls-remote", "git://127.0.0.1:1/x"],
        ["commit", "-q", "--allow-empty", "-m", "m"],
    ):
        subprocess.run(
            ["git", "-C", str(control), *args],
            capture_output=True,
            env={k: v for k, v in PLAIN_ENV.items() if k != "GIT_PROXY_COMMAND"},
        )
    assert "ran-proxy" in _ran(marker) and "ran-keycommand" in _ran(marker)
    marker.unlink()

    repo = make("repo")
    SafeGit.run(["ls-remote", "git://127.0.0.1:1/x"], repo)
    SafeGit.run(["commit", "-q", "--allow-empty", "-m", "m"], repo)
    assert _ran(marker) == ""


def test_the_rust_helper_and_the_desktop_app_pin_the_same_keys():
    """The three copies (Rust helper, this module, the desktop app's git.ts) stay in step."""
    root = Path(__file__).resolve().parent.parent
    rust = (root / "ling-rs" / "safegit" / "src" / "lib.rs").read_text()
    rust_block = rust.split("pub const FIXED_CONFIG", 1)[1].split("];", 1)[0]
    assert set(re.findall(r'"([^"]+=[^"]*)"', rust_block)) == set(FIXED_CONFIG)
    assert '("GIT_PROXY_COMMAND", "")' in rust
    ts = (root / "desktop" / "electron" / "src" / "git.ts").read_text()
    ts_block = ts.split("export const SAFE_CONFIG", 1)[1].split("];", 1)[0]
    ts_keys = {pair.split("=", 1)[0] for pair in re.findall(r'"([^"]+=[^"]*)"', ts_block)}
    program_keys = {
        pair.split("=", 1)[0]
        for pair in FIXED_CONFIG
        if re.search(
            r"program|Command|gitProxy|external|helper|fsmonitor|pager|askPass|editor|ext\.allow",
            pair,
        )
    }
    assert program_keys <= ts_keys, program_keys - ts_keys
    assert 'env.GIT_PROXY_COMMAND = "";' in ts


def test_flags_follow_the_subcommand_after_any_global_option():
    assert SafeGit.safe_flags(["--no-pager", "-p", "diff", "HEAD"]) == [
        "--no-pager", "-p", "diff", "--no-ext-diff", "--no-textconv", "HEAD",
    ]
    assert SafeGit.safe_flags(["--git-dir", "x.git", "--work-tree", "w", "--namespace", "n", "show"]) == [
        "--git-dir", "x.git", "--work-tree", "w", "--namespace", "n", "show", "--no-ext-diff", "--no-textconv",
    ]
    assert SafeGit.safe_flags(["--config-env", "core.pager=P", "--git-dir=x.git", "pull"]) == [
        "--config-env", "core.pager=P", "--git-dir=x.git", "pull", "--upload-pack=git-upload-pack",
    ]
    # An option's value that looks like a subcommand is not one.
    assert SafeGit.safe_flags(["-C", "push", "status"]) == ["-C", "push", "status"]


def test_with_no_subcommand_nothing_is_added():
    assert SafeGit.safe_flags(["--version"]) == ["--version"]
    assert SafeGit.safe_flags(["-c", "a=b"]) == ["-c", "a=b"]
    assert SafeGit.safe_flags(["-C"]) == ["-C"]
    assert SafeGit.safe_flags([]) == []


def test_network_subcommands_get_the_longer_timeout(monkeypatch):
    from dreamference.runner import safe_git

    timeouts = {}

    def run(command, **kwargs):
        if "config" not in command:
            timeouts[next(word for word in command if word in ("fetch", "status", "push", "clone", "log"))] = \
                kwargs["timeout"]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    for subcommand in ("fetch", "status", "push", "clone"):
        SafeGit.run(["-c", "x=y", subcommand])
    SafeGit.run(["log"], timeout=3)

    assert timeouts == {"fetch": safe_git.NETWORK_TIMEOUT_S, "status": safe_git.DEFAULT_TIMEOUT_S,
                        "push": safe_git.NETWORK_TIMEOUT_S, "clone": safe_git.NETWORK_TIMEOUT_S,
                        "log": 3}


def test_a_missing_git_is_exit_127_in_text_or_bytes(monkeypatch):
    def missing(command, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(subprocess, "run", missing)
    as_text = SafeGit.run(["status"])
    as_bytes = SafeGit.run(["status"], text=False)

    assert (as_text.returncode, as_text.stdout) == (127, "") and "git" in as_text.stderr
    assert (as_bytes.returncode, as_bytes.stdout) == (127, b"") and b"git" in as_bytes.stderr


def test_driver_names_are_not_blanked_when_git_cannot_list_the_config(monkeypatch, tmp_path):
    answers = iter([OSError("exec format error"), subprocess.CompletedProcess([], 128, b"", b"not a repo")])

    def run(command, **kwargs):
        answer = next(answers)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(subprocess, "run", run)
    assert SafeGit.driver_overrides(tmp_path) == []
    assert SafeGit.driver_overrides(tmp_path) == []


def test_only_the_repositorys_own_driver_programs_are_blanked(monkeypatch, tmp_path):
    listing = b"\0".join([
        b"local", b"filter.lfs.smudge\ngit-lfs smudge",
        b"local", b"filter.lfs.required\ntrue",
        b"worktree", b"diff.pdf.textconv\npdftotext",
        b"global", b"diff.mine.command\nmy-diff",  # the owner's own config is theirs
        b"local", b"merge.ours.driver\ntrue",
        b"command", b"submodule.lib.update\n!sh run",
        b"local", b"core.pager\nless",  # not a driver
        b"local", b"filter.smudge\nx",  # no driver name
    ]) + b"\0"
    monkeypatch.setattr(subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0, listing, b""))

    assert SafeGit.driver_overrides(tmp_path) == [
        "diff.pdf.textconv=", "filter.lfs.required=false", "filter.lfs.smudge=",
        "merge.ours.driver=false", "submodule.lib.update=none",
    ]
