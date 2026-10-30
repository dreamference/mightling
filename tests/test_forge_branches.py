"""Branch coverage for `dreamference.forge`: what the other forge tests leave unreached.

Refusals, fallbacks and exit codes. Nothing here runs docker, ssh or Forgejo: every call that
would leave the process is a stand-in.
"""

import io
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from dreamference.forge.forge_anonymity import ForgeAnonymity
from dreamference.forge.forge_backup import ForgeBackup
from dreamference.forge.forge_mirrors import ForgeMirrors
from dreamference.forge.forge_move import ForgeMove
from dreamference.forge.forge_serve import REFUSED, ForgeServe
from tests.test_forge import LAN, commit, hooked_repository, push  # noqa: F401 - a fixture

# -- forge serve [key]


@pytest.fixture
def handlers(monkeypatch):
    """Every forge operation's handler records what it was asked and answers 0."""
    seen = []

    def record(name):
        return classmethod(lambda cls, *arguments: seen.append((name, *arguments)) or 0)

    for owner, name in [
        (ForgeMirrors, "serve_plan"),
        (ForgeMirrors, "serve_apply"),
        (ForgeBackup, "receive"),
        (ForgeMove, "serve_restore"),
        (ForgeMove, "serve_promote"),
        (ForgeMove, "serve_hand_over"),
        (ForgeMove, "freeze"),
        (ForgeMove, "serve_become_mirror"),
    ]:
        monkeypatch.setattr(owner, name, record(name))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"plan text")))
    return seen


@pytest.mark.parametrize(
    ("operation", "arguments", "called"),
    [
        ("forge-mirror-apply", [], ("serve_apply", "plan text")),
        ("forge-restore", ["set-1"], ("serve_restore", "set-1", None)),
        ("forge-restore", ["set-1", "node-2"], ("serve_restore", "set-1", "node-2")),
        ("forge-promote", [], ("serve_promote",)),
        ("forge-hand-over", [], ("serve_hand_over",)),
        ("forge-unfreeze", [], ("freeze", False)),
        (
            "forge-become-mirror",
            ["node-2", "forge-b"],
            ("serve_become_mirror", "node-2", "forge-b"),
        ),
    ],
)
def test_each_forge_operation_reaches_only_its_own_handler(handlers, operation, arguments, called):
    assert ForgeServe.serve(operation, arguments) == 0
    assert handlers == [called]


def test_a_backup_set_is_received_from_the_raw_input(handlers):
    assert ForgeServe.serve("forge-backup-receive", ["set-1"]) == 0
    assert handlers[0][:2] == ("receive", "set-1")
    assert handlers[0][2] is sys.stdin.buffer


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("forge-mirror-apply", ["extra"]),
        ("forge-restore", []),
        ("forge-restore", ["set-1", "node-2", "extra"]),
        ("forge-promote", ["extra"]),
        ("forge-hand-over", ["extra"]),
        ("forge-unfreeze", ["extra"]),
        ("forge-become-mirror", ["node-2", "forge-b", "extra"]),
    ],
)
def test_a_forge_operation_with_the_wrong_arguments_is_refused_and_runs_nothing(
    handlers, operation, arguments, capsys
):
    assert ForgeServe.serve(operation, arguments) == REFUSED
    assert handlers == []
    assert "forge operations are" in capsys.readouterr().err


# -- forge anonymity hook [key]


def rehook(bare, patterns, scope, check_roots):
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(ForgeAnonymity.hook_script(scope, [], check_roots, directory=str(patterns)))


def orphan(work, branch):
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "--orphan", branch], check=True)


def test_with_the_check_off_the_hook_scans_nothing_but_still_refuses_a_foreign_root(
    hooked_repository,  # noqa: F811 - the fixture imported above
):
    bare, work, patterns = hooked_repository
    rehook(bare, patterns, "off", True)
    commit(work, "a.txt", "fine\n")
    assert push(work, bare).returncode == 0
    commit(work, "b.txt", f"reach {LAN}.7.105\n")
    assert push(work, bare).returncode == 0, "off scans no content"
    orphan(work, "old")
    commit(work, "e.txt", "old history\n")
    refused = push(work, bare, "old")
    assert refused.returncode != 0 and "root commit" in refused.stderr
    (patterns / "frozen").write_text("1")
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"], check=True)
    commit(work, "f.txt", "later\n")
    assert "frozen" in push(work, bare).stderr


def test_without_the_root_check_the_hook_takes_an_unrelated_history_but_still_scans_it(
    hooked_repository,  # noqa: F811 - the fixture imported above
):
    bare, work, patterns = hooked_repository
    rehook(bare, patterns, "full", False)
    commit(work, "a.txt", "fine\n")
    assert push(work, bare).returncode == 0
    orphan(work, "old")
    commit(work, "e.txt", "old history\n")
    assert push(work, bare, "old").returncode == 0
    commit(work, "f.txt", f"reach {LAN}.7.105\n")
    refused = push(work, bare, "old")
    assert refused.returncode != 0 and "anonymity check" in refused.stderr


# -- forge service setup [key]


@pytest.fixture
def forge_setup(monkeypatch):
    """The real `ForgeService.setup` over stand-ins.

    The forge is on, docker is ready, the unit starts and answers, and the accounts are ready.
    Each test breaks one of them.
    """
    from conftest import REAL_FORGE_SETUP

    from dreamference.forge.forge_accounts import ForgeAccounts
    from dreamference.forge.forge_container import ForgeContainer
    from dreamference.forge.forge_service import ForgeService
    from dreamference.forge.forge_settings import ForgeSettings

    monkeypatch.setattr(ForgeService, "setup", classmethod(REAL_FORGE_SETUP.__func__))
    seen = {"systemctl": [], "accounts": 0, "advertised": []}
    state = {"enabled": True, "image": True, "network": True, "started": 0, "answering": True}
    state["ready"] = True

    def systemctl(cls, *arguments):
        seen["systemctl"].append(arguments)
        code = state["started"] if arguments[0] == "restart" else 0
        return subprocess.CompletedProcess(["systemctl", *arguments], code, "", "")

    def accounts(cls, *_args, **_kwargs):
        seen["accounts"] += 1
        return state["ready"], None

    monkeypatch.setattr(ForgeSettings, "enabled", classmethod(lambda cls: state["enabled"]))
    monkeypatch.setattr(ForgeContainer, "ensure_image", classmethod(lambda cls: state["image"]))
    monkeypatch.setattr(ForgeContainer, "ensure_network", classmethod(lambda cls: state["network"]))
    monkeypatch.setattr(
        ForgeContainer, "wait_until_answering", classmethod(lambda cls, _t: state["answering"])
    )
    monkeypatch.setattr(ForgeService, "decide_role", classmethod(lambda cls: "record"))
    monkeypatch.setattr(ForgeService, "install_units", classmethod(lambda cls: None))
    monkeypatch.setattr(ForgeService, "systemctl", classmethod(systemctl))
    monkeypatch.setattr(
        ForgeService, "advertise", classmethod(lambda cls, role: seen["advertised"].append(role))
    )
    monkeypatch.setattr(ForgeAccounts, "ensure", classmethod(accounts))
    return ForgeService, state, seen


def test_a_forge_turned_off_is_not_prepared_or_started(forge_setup, capsys):
    service, state, seen = forge_setup
    state["enabled"] = False
    assert service.setup() is True
    out = capsys.readouterr().out
    assert "The forge is off on this node" in out and "could not be prepared" not in out
    assert seen == {"systemctl": [], "accounts": 0, "advertised": []}


@pytest.mark.parametrize("broken", ["image", "network"])
def test_a_forge_whose_image_or_network_cannot_be_prepared_is_never_started(
    forge_setup, broken, capsys
):
    service, state, seen = forge_setup
    state[broken] = False
    assert service.setup() is False
    assert "could not be prepared" in capsys.readouterr().out
    assert seen["systemctl"] == [] and seen["accounts"] == 0


@pytest.mark.parametrize(("started", "answering"), [(1, True), (0, False)])
def test_a_forge_that_does_not_start_or_answer_gets_no_accounts_and_is_not_advertised(
    forge_setup, started, answering, capsys
):
    service, state, seen = forge_setup
    state.update(started=started, answering=answering)
    assert service.setup() is False
    assert "The forge did not start" in capsys.readouterr().out
    assert seen["accounts"] == 0 and seen["advertised"] == []
    assert not any(arguments[0] == "start" for arguments in seen["systemctl"])


def test_a_forge_whose_accounts_are_not_ready_starts_no_timers_and_is_not_advertised(forge_setup):
    service, state, seen = forge_setup
    state["ready"] = False
    assert service.setup() is False
    assert seen["accounts"] == 1 and seen["advertised"] == []
    assert not any(arguments[0] == "start" for arguments in seen["systemctl"])


@pytest.mark.parametrize(("role", "running"), [(None, True), ("record", False)])
def test_a_forge_without_a_role_or_not_running_is_never_rebound(monkeypatch, role, running):
    from dreamference.forge.forge_container import ForgeContainer
    from dreamference.forge.forge_service import ForgeService
    from dreamference.forge.forge_settings import ForgeSettings

    monkeypatch.setattr(ForgeSettings, "role", classmethod(lambda cls: role))
    monkeypatch.setattr(ForgeContainer, "running", classmethod(lambda cls: running))
    monkeypatch.setattr(ForgeService, "restart", classmethod(lambda cls: pytest.fail("restarted")))
    assert ForgeService.rebind() is False


# -- forge backup take [key]


def stub_dump(monkeypatch, refs):
    """`forgejo dump` writes its archive; the repositories' refs read as given, one per call."""
    from dreamference.forge.forge_container import ForgeContainer
    from dreamference.forge.forge_settings import ForgeSettings

    dumps = ForgeSettings.data_dir() / "dumps"

    def dump(cls, arguments, timeout=0):
        (dumps / Path(arguments[-1]).name).write_bytes(b"dump")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    seen = iter(refs)
    monkeypatch.setattr(ForgeContainer, "forgejo", classmethod(dump))
    monkeypatch.setattr(ForgeBackup, "repository_refs", classmethod(lambda cls, root: next(seen)))
    return dumps


@pytest.mark.parametrize("unreadable", ["before", "after"])
def test_a_dump_whose_refs_git_cannot_read_makes_no_set(monkeypatch, unreadable, capsys):
    refs = {"o/r": {"refs/heads/main": "a" * 40}}
    dumps = stub_dump(monkeypatch, [None, refs] if unreadable == "before" else [refs, None])
    assert ForgeBackup.take() is None
    assert "git could not read a repository" in capsys.readouterr().out
    assert list(dumps.iterdir()) == []
    assert not ForgeBackup.local_dir().exists()


def test_a_set_keeps_its_refs_and_private_secrets_bundles_small_repositories_and_replaces_older_sets(
    monkeypatch,
):
    from dreamference.forge import forge_backup
    from dreamference.forge.forge_anonymity import ForgeAnonymity
    from dreamference.forge.forge_settings import ForgeSettings
    from dreamference.forge.forge_storage import ForgeStorage

    head = {"refs/heads/main": "a" * 40}
    refs = {"o/small": head, "o/big": head, "o/empty": {}}
    stub_dump(monkeypatch, [refs, refs])
    local = ForgeBackup.local_dir()
    older, other = local / "forge-20261001-000000", local / "notes"
    older.mkdir(parents=True)
    other.mkdir()
    monkeypatch.setattr(
        ForgeBackup,
        "snapshot_database",
        classmethod(lambda cls, source, target: target.write_bytes(b"db") or True),
    )
    monkeypatch.setattr(ForgeBackup, "node_id", classmethod(lambda cls: "node-1"))
    monkeypatch.setattr(ForgeSettings, "read_secret", classmethod(lambda cls, key: f"secret-{key}"))
    monkeypatch.setattr(ForgeAnonymity, "read_denylist", classmethod(lambda cls: ""))
    big = forge_backup.BUNDLE_LIMIT_BYTES
    monkeypatch.setattr(
        ForgeStorage, "size", classmethod(lambda cls, path: big if "big" in path.name else 1)
    )
    bundled = []
    monkeypatch.setattr(
        forge_backup.subprocess,
        "run",
        lambda argv, **_: bundled.append(argv) or subprocess.CompletedProcess(argv, 0),
    )

    folder = ForgeBackup.take()
    assert folder is not None and folder.parent == local
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["refs"] == refs and manifest["node"] == "node-1"
    assert (folder / "dump.tar.gz").read_bytes() == b"dump"
    secrets = folder / "secrets.json"
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    assert stat.S_IMODE(local.stat().st_mode) == 0o700
    assert forge_backup.DENYLIST_KEY not in json.loads(secrets.read_text()), (
        "an empty value is left out"
    )
    assert [Path(argv[argv.index("create") + 2]).name for argv in bundled] == ["o__small.bundle"]
    assert not older.exists(), "an older set is replaced"
    assert other.is_dir(), "a folder that is not a set is left alone"
