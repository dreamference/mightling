"""The forge's anonymity check off the push path.

The pattern files, the hooks it installs, and the GitHub mirror's second layer (`added_text` and
`send_to_github`), with git run only in `tmp_path`.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from dreamference.forge import ForgeAnonymity, forge_anonymity
from dreamference.forge.forge_github_mirror import ForgeGithubMirror

LAN = "192.168"
GIT = shutil.which("git")


@pytest.fixture(autouse=True)
def _plain_git(monkeypatch):
    # The developer's global git config (a signing key, hooks) must not reach these repositories.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)


@pytest.fixture
def work(tmp_path):
    if GIT is None:
        pytest.skip("no git")
    folder = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(folder)], check=True)
    for key, value in (("user.name", "dgxcoder"), ("user.email", "dgxcoder@example.com")):
        subprocess.run(["git", "-C", str(folder), "config", key, value], check=True)
    return folder


def commit(work: Path, name: str, text: str, message: str | bytes = "m") -> str:
    path = work / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    if isinstance(message, bytes):
        subprocess.run(
            ["git", "-C", str(work), "commit", "-q", "-F", "-"], input=message, check=True
        )
    else:
        subprocess.run(["git", "-C", str(work), "commit", "-qm", message], check=True)
    return subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def write_denylist(text: str) -> None:
    path = Path(forge_anonymity.DENYLIST).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# -- the pattern files and the hooks ---------------------------------------------------------------


def test_a_repository_with_the_check_off_has_no_patterns_even_with_a_denylist():
    assert ForgeAnonymity.patterns("off", "node-q\n") == []
    assert ForgeAnonymity.patterns("denylist", None) == []


def test_a_denylist_entry_with_its_own_flags_still_checks_in_python():
    patterns = ForgeAnonymity.denylist_patterns("(?i)node-q")
    assert ForgeAnonymity.findings("pushed from NODE-Q today", patterns) == ["1: pattern #1"]


def test_a_node_without_a_denylist_reads_none():
    assert ForgeAnonymity.read_denylist() is None


def test_the_pattern_files_are_private_and_an_empty_scope_never_matches(tmp_path):
    ForgeAnonymity.write_patterns(tmp_path)
    full = ForgeAnonymity.pattern_file(tmp_path, "full")
    denylist = ForgeAnonymity.pattern_file(tmp_path, "denylist")
    for path in (full, denylist):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path
    assert denylist.read_text() == "(?!)\n", (
        "no denylist: grep still gets a pattern, never matching"
    )
    assert full.read_text().splitlines() == ForgeAnonymity.generic_patterns()
    assert not list(full.parent.glob(".*.tmp")), "the staging files are renamed into place"


def test_the_pattern_files_carry_the_nodes_denylist_into_both_scopes(tmp_path):
    write_denylist("node-q\n")
    ForgeAnonymity.write_patterns(tmp_path)
    for scope in ("full", "denylist"):
        patterns = ForgeAnonymity.pattern_file(tmp_path, scope).read_text().splitlines()
        assert ForgeAnonymity.findings("ssh node-q", patterns), scope
    assert ForgeAnonymity.findings(
        f"reach {LAN}.7.105",
        ForgeAnonymity.pattern_file(tmp_path, "full").read_text().splitlines(),
    )
    assert not ForgeAnonymity.findings(
        f"reach {LAN}.7.105",
        ForgeAnonymity.pattern_file(tmp_path, "denylist").read_text().splitlines(),
    ), "a fork checked by the denylist only keeps its upstream's addresses"


def test_rewriting_the_pattern_files_replaces_a_wider_mode_left_behind(tmp_path):
    full = ForgeAnonymity.pattern_file(tmp_path, "full")
    full.parent.mkdir(parents=True)
    full.write_text("old\n")
    full.chmod(0o644)
    ForgeAnonymity.write_patterns(tmp_path)
    assert stat.S_IMODE(full.stat().st_mode) == 0o600 and "old" not in full.read_text()


def test_install_writes_executable_hooks_and_removes_a_trigger_no_longer_wanted(tmp_path):
    repository = tmp_path / "o" / "r.git"
    ForgeAnonymity.install(repository, "full", ["tests/test_anonymity.py"], True, trigger=True)
    hook = repository / "hooks" / "pre-receive.d" / forge_anonymity.HOOK_NAME
    trigger = repository / "hooks" / "post-receive.d" / forge_anonymity.TRIGGER_HOOK_NAME
    assert stat.S_IMODE(hook.stat().st_mode) == 0o755 and "anonymity-full.pcre" in hook.read_text()
    assert stat.S_IMODE(trigger.stat().st_mode) == 0o755 and "pushed" in trigger.read_text()
    ForgeAnonymity.install(repository, "off", [], False, trigger=False)
    assert not trigger.exists(), "a repository no longer mirrored stops waking the mirror"
    assert hook.read_text().endswith("exit 0\n") and "pcre" not in hook.read_text()
    assert "frozen" in hook.read_text(), "an unchecked repository still refuses pushes while frozen"


def test_a_hook_rewrite_that_fails_leaves_the_old_hook_whole(tmp_path, monkeypatch):
    repository = tmp_path / "o" / "r.git"
    ForgeAnonymity.install(repository, "full", [], True, trigger=False)
    hook = repository / "hooks" / "pre-receive.d" / forge_anonymity.HOOK_NAME
    before = hook.read_text()
    monkeypatch.setattr(ForgeAnonymity, "hook_script", classmethod(lambda cls, *a: "\udc80"))
    with pytest.raises(UnicodeEncodeError):
        ForgeAnonymity.install(repository, "full", [], True, trigger=False)
    assert hook.read_text() == before, "a push meanwhile still runs the whole check"
    assert stat.S_IMODE(hook.stat().st_mode) == 0o755
    assert [path.name for path in hook.parent.iterdir()] == [hook.name]


def test_an_exempt_path_with_shell_characters_never_reaches_the_hook():
    hook = ForgeAnonymity.hook_script(
        "full", ["tests/test_anonymity.py", "x'; touch pwned #"], True
    )
    assert "tests/test_anonymity.py" in hook and "pwned" not in hook


# -- the GitHub mirror's second layer --------------------------------------------------------------


def test_the_mirror_recheck_reads_only_the_commits_the_destination_lacks(work):
    known = commit(work, "old.txt", f"reach {LAN}.7.105\n", message="old work")
    tip = commit(work, "new.txt", "fine\n", message="new work")
    text = ForgeAnonymity.added_text(work, [tip], [known], [])
    assert "new work" in text and "new.txt: fine" in text
    assert "old work" not in text and f"{LAN}.7.105" not in text, "known history is not re-read"
    assert ForgeAnonymity.added_text(work, [], [known], []) == ""


def test_the_mirror_recheck_sees_authors_messages_and_paths(work):
    tip = commit(work, "notes/" + "node-q.txt", "x\n", message="talk to node-q")
    text = ForgeAnonymity.added_text(work, [tip], [], [])
    assert len(ForgeAnonymity.findings(text, ForgeAnonymity.denylist_patterns("node-q"))) >= 3
    assert "dgxcoder@example.com" in text


def test_the_mirror_recheck_raises_when_git_cannot_read_the_tips(work):
    commit(work, "a.txt", "a\n")
    with pytest.raises(RuntimeError):
        ForgeAnonymity.added_text(work, ["0" * 40], [], [])


def crafted_commit(work: Path, message: bytes) -> str:
    """A commit whose header names an encoding git cannot convert, so `git log` prints it raw."""
    tree = subprocess.run(
        ["git", "-C", str(work), "write-tree"], capture_output=True, text=True, check=True
    ).stdout.strip()
    body = (
        f"tree {tree}\nauthor d <d@example.com> 1 +0000\ncommitter d <d@example.com> 1 +0000\n"
        "encoding not-an-encoding\n\n"
    ).encode() + message
    return (
        subprocess.run(
            ["git", "-C", str(work), "hash-object", "-t", "commit", "-w", "--stdin"],
            input=body,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


def test_the_mirror_recheck_reads_a_commit_message_that_is_not_utf8(work):
    tip = crafted_commit(work, b"caf\xff on node-q\n")
    text = ForgeAnonymity.added_text(work, [tip], [], [])
    assert ForgeAnonymity.findings(text, ForgeAnonymity.denylist_patterns("node-q"))


def test_a_commit_message_that_is_not_utf8_stops_the_mirror_cleanly(work, monkeypatch):
    write_denylist("node-q\n")
    tip = crafted_commit(work, b"caf\xff on node-q\n")
    monkeypatch.setattr(ForgeGithubMirror, "refs", classmethod(lambda cls, work, prefix: {}))
    pushed = []
    monkeypatch.setattr(
        ForgeGithubMirror,
        "push_each",
        classmethod(lambda cls, *a: pushed.append(a) or {"ok": True}),
    )
    result = ForgeGithubMirror.send_to_github(
        work, [f"{tip}:refs/heads/main"], "https://github.com/o/r.git", mirror_settings(), {}
    )
    assert result["stop"] and "identifiers" in result["error"] and pushed == []


def mirror_settings(scope="full"):
    return {"scope": scope, "exempt": ["tests/test_anonymity.py"]}


def test_the_mirror_pushes_nothing_when_the_recheck_finds_an_identifier(work, monkeypatch):
    tip = commit(work, "a.txt", f"reach {LAN}.7.105\n")
    monkeypatch.setattr(ForgeGithubMirror, "refs", classmethod(lambda cls, work, prefix: {}))
    pushed = []
    monkeypatch.setattr(
        ForgeGithubMirror,
        "push_each",
        classmethod(lambda cls, *a: pushed.append(a) or {"ok": True}),
    )
    result = ForgeGithubMirror.send_to_github(
        work, [f"{tip}:refs/heads/main"], "https://github.com/o/r.git", mirror_settings(), {}
    )
    assert result["stop"] and not result["ok"] and "nothing was pushed" in result["error"]
    assert f"{LAN}.7.105" not in result["error"], "the report never repeats the identifier"
    assert pushed == []


def test_the_mirror_pushes_nothing_when_the_recheck_cannot_run(work, monkeypatch):
    commit(work, "a.txt", "a\n")
    monkeypatch.setattr(ForgeGithubMirror, "refs", classmethod(lambda cls, work, prefix: {}))
    pushed = []
    monkeypatch.setattr(
        ForgeGithubMirror,
        "push_each",
        classmethod(lambda cls, *a: pushed.append(a) or {"ok": True}),
    )
    result = ForgeGithubMirror.send_to_github(
        work, [f"{'0' * 40}:refs/heads/main"], "https://github.com/o/r.git", mirror_settings(), {}
    )
    assert result["stop"] and "could not run" in result["error"] and pushed == []


def test_the_mirror_pushes_clean_commits_without_forcing(work, monkeypatch):
    tip = commit(work, "a.txt", "fine\n")
    monkeypatch.setattr(ForgeGithubMirror, "refs", classmethod(lambda cls, work, prefix: {}))
    pushed = []
    monkeypatch.setattr(
        ForgeGithubMirror,
        "push_each",
        classmethod(lambda cls, w, command, refspecs, env: pushed.append(command) or {"ok": True}),
    )
    result = ForgeGithubMirror.send_to_github(
        work, [f"{tip}:refs/heads/main"], "https://github.com/o/r.git", mirror_settings(), {}
    )
    assert result == {"ok": True}
    assert "push" in pushed[0] and "--force" not in pushed[0] and "-f" not in pushed[0]


def test_what_github_already_has_is_not_checked_again(work, monkeypatch):
    sent = commit(work, "a.txt", f"reach {LAN}.7.105\n")
    subprocess.run(
        ["git", "-C", str(work), "update-ref", "refs/github/heads/main", sent], check=True
    )
    tip = commit(work, "b.txt", "fine\n")
    pushed = []
    monkeypatch.setattr(
        ForgeGithubMirror,
        "push_each",
        classmethod(lambda cls, w, command, refspecs, env: pushed.append(refspecs) or {"ok": True}),
    )
    result = ForgeGithubMirror.send_to_github(
        work, [f"{tip}:refs/heads/main"], "https://github.com/o/r.git", mirror_settings(), {}
    )
    assert result == {"ok": True} and pushed == [[f"{tip}:refs/heads/main"]]
