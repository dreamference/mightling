"""Moving the forge of record off the happy path.

What a paired node asks for is checked, and every failed move leaves the record in place and
unfrozen. Systemd, ssh and the backups are mocked.
"""

import io
import json
import os
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

from dreamference.forge import ForgeBackup, ForgeSettings
from dreamference.forge.forge_move import ForgeMove
from dreamference.forge.forge_service import ForgeService
from dreamference.forge.forge_settings import ROLE_MIRROR, ROLE_RECORD
from dreamference.node.node_pairing import NodePairing

NAME = "forge-20261027-063000"
TARGET = {"node": "node-b-1a2b", "name": "node-b"}


@pytest.fixture(autouse=True)
def _no_systemd(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ForgeService,
        "systemctl",
        classmethod(lambda cls, *a: calls.append(a) or subprocess.CompletedProcess(a, 0, "", "")),
    )
    monkeypatch.setattr(ForgeService, "host_name", classmethod(lambda cls: "node-a"))
    return calls


def answered(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


# -- what a paired node may ask for ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["../forge-20261027-063000", "forge-1", NAME])
def test_restoring_a_set_this_node_does_not_hold_is_refused(name, monkeypatch, capsys):
    monkeypatch.setattr(
        ForgeMove, "restore_here", classmethod(lambda cls, *a: pytest.fail("restored"))
    )
    assert ForgeMove.serve_restore(name, None) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "no such backup set here"


@pytest.mark.parametrize("previous", ["n;rm", "n 1", "../n", "n.1"])
def test_restoring_with_a_previous_record_that_is_not_a_node_id_is_refused(
    previous, monkeypatch, capsys
):
    (ForgeBackup.received_dir() / NAME).mkdir(parents=True)
    monkeypatch.setattr(
        ForgeMove, "restore_here", classmethod(lambda cls, *a: pytest.fail("restored"))
    )
    assert ForgeMove.serve_restore(NAME, previous) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "not a node id"


def test_a_restore_that_fails_answers_one_with_its_reason(monkeypatch, capsys):
    (ForgeBackup.received_dir() / NAME).mkdir(parents=True)
    monkeypatch.setattr(
        ForgeMove, "restore_here", classmethod(lambda cls, f, p: {"ok": False, "error": "x"})
    )
    assert ForgeMove.serve_restore(NAME, "node-a-9f8e") == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "x"}


def test_a_set_that_does_not_restore_restarts_the_forge_and_keeps_the_role(
    monkeypatch, _no_systemd
):
    ForgeSettings.update_state(role=ROLE_MIRROR)
    monkeypatch.setattr(ForgeBackup, "install_data", classmethod(lambda cls, f: (None, None)))
    result = ForgeMove.restore_here(Path("/nonexistent") / NAME, "node-a-9f8e")
    assert result == {"ok": False, "error": "the set did not restore"}
    assert _no_systemd[-1][0] == "start" and ForgeSettings.role() == ROLE_MIRROR


def test_a_hand_over_whose_set_is_not_taken_unfreezes(monkeypatch):
    ForgeSettings.update_state(role=ROLE_RECORD)
    monkeypatch.setattr(ForgeBackup, "take", classmethod(lambda cls: None))
    assert ForgeMove.serve_hand_over() == 1
    assert not ForgeMove.frozen_flag().exists()


# -- moving the role away --------------------------------------------------------------------------


def test_a_mirror_cannot_move_the_role_to_another_node(capsys):
    ForgeSettings.update_state(role=ROLE_MIRROR)
    assert ForgeMove.move("node-c") == 1
    assert "not the forge of record" in capsys.readouterr().out


def test_moving_back_without_a_previous_record_says_so(capsys):
    ForgeSettings.update_state(role=ROLE_RECORD)
    assert ForgeMove.move(None, back=True) == 1
    assert "never moved here" in capsys.readouterr().out
    assert not ForgeMove.frozen_flag().exists()


def test_moving_to_a_node_that_is_not_paired_freezes_nothing(monkeypatch, capsys):
    ForgeSettings.update_state(role=ROLE_RECORD)
    monkeypatch.setattr(NodePairing, "find", classmethod(lambda cls, name, browse=True: None))
    assert ForgeMove.move("node-c") == 1
    assert "not a paired node" in capsys.readouterr().out
    assert not ForgeMove.frozen_flag().exists()


@pytest.fixture
def record_moving(monkeypatch):
    """This node is the record and the target is paired; the backups are mocked."""
    ForgeSettings.update_state(role=ROLE_RECORD)
    monkeypatch.setattr(NodePairing, "find", classmethod(lambda cls, name, browse=True: TARGET))
    monkeypatch.setattr(NodePairing, "paired", classmethod(lambda cls: [TARGET]))
    monkeypatch.setattr(ForgeBackup, "take", classmethod(lambda cls: Path("/sets") / NAME))
    monkeypatch.setattr(ForgeBackup, "send", classmethod(lambda cls, folder, record: True))
    monkeypatch.setattr(
        ForgeMove, "become_mirror", classmethod(lambda cls, *a: pytest.fail("became a mirror"))
    )


def test_a_move_whose_set_is_not_taken_unfreezes(record_moving, monkeypatch):
    monkeypatch.setattr(ForgeBackup, "take", classmethod(lambda cls: None))
    assert ForgeMove.move("node-b") == 1
    assert not ForgeMove.frozen_flag().exists() and ForgeSettings.role() == ROLE_RECORD


def test_a_move_whose_set_does_not_reach_the_target_unfreezes(record_moving, monkeypatch, capsys):
    monkeypatch.setattr(ForgeBackup, "send", classmethod(lambda cls, folder, record: False))
    monkeypatch.setattr(NodePairing, "run", classmethod(lambda cls, *a, **k: pytest.fail("ran")))
    assert ForgeMove.move("node-b") == 1
    assert "did not reach node-b" in capsys.readouterr().out
    assert not ForgeMove.frozen_flag().exists() and ForgeSettings.role() == ROLE_RECORD


def test_a_move_the_target_does_not_restore_unfreezes_and_keeps_the_role(
    record_moving, monkeypatch, capsys
):
    requests = []
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(
            lambda cls, record, request, **k: (
                requests.append(request) or answered('{"ok": false, "error": "3 refs differ"}\n')
            )
        ),
    )
    assert ForgeMove.move("node-b") == 1
    assert requests == [f"forge-restore {NAME} {ForgeBackup.node_id()}"]
    assert "3 refs differ" in capsys.readouterr().out
    assert not ForgeMove.frozen_flag().exists() and ForgeSettings.role() == ROLE_RECORD


@pytest.mark.parametrize(
    "result, error",
    [
        (answered("", ""), "no answer"),
        (answered("Traceback\n", "boom\n", 1), "boom"),
        (answered("not json\n"), "not json"),
    ],
)
def test_a_target_answer_that_is_not_json_reads_as_a_failure(result, error, monkeypatch):
    monkeypatch.setattr(NodePairing, "run", classmethod(lambda cls, *a, **k: result))
    assert ForgeMove.restore_on(TARGET, Path(NAME)) == {"ok": False, "error": error}


# -- pulling the role to this mirror ---------------------------------------------------------------


def test_pulling_from_a_record_this_node_is_not_paired_with_says_so(monkeypatch, capsys):
    ForgeSettings.update_state(role=ROLE_MIRROR, record_node="node-b-1a2b")
    monkeypatch.setattr(NodePairing, "find", classmethod(lambda cls, name, browse=True: None))
    assert ForgeMove.move("here") == 1
    assert "not paired with the forge of record" in capsys.readouterr().out


def test_a_pull_that_brings_no_set_unfreezes_the_record(monkeypatch):
    ForgeSettings.update_state(role=ROLE_MIRROR, record_node="node-b-1a2b")
    monkeypatch.setattr(NodePairing, "find", classmethod(lambda cls, name, browse=True: TARGET))
    monkeypatch.setattr(ForgeMove, "receive_hand_over", classmethod(lambda cls, record: None))
    requests = []
    monkeypatch.setattr(
        NodePairing, "run", classmethod(lambda cls, r, request, **k: requests.append(request))
    )
    assert ForgeMove.move(None, back=True) == 1
    assert requests == ["forge-unfreeze"] and ForgeSettings.role() == ROLE_MIRROR


def test_a_pull_that_does_not_restore_unfreezes_the_record_and_stays_a_mirror(monkeypatch):
    ForgeSettings.update_state(role=ROLE_MIRROR, record_node="node-b-1a2b")
    monkeypatch.setattr(NodePairing, "find", classmethod(lambda cls, name, browse=True: TARGET))
    monkeypatch.setattr(
        ForgeMove, "receive_hand_over", classmethod(lambda cls, record: Path("/sets") / NAME)
    )
    monkeypatch.setattr(
        ForgeMove, "restore_here", classmethod(lambda cls, f, p: {"ok": False, "error": "x"})
    )
    mirrored = []
    monkeypatch.setattr(ForgeMove, "become_mirror", classmethod(lambda cls, *a: mirrored.append(a)))
    requests = []
    monkeypatch.setattr(
        NodePairing, "run", classmethod(lambda cls, r, request, **k: requests.append(request))
    )
    assert ForgeMove.move("node-a") == 1
    assert requests == ["forge-unfreeze"]
    assert mirrored == [("node-b-1a2b", "node-b")], "this node goes back to mirroring the record"


def test_a_hand_over_from_a_record_without_an_address_runs_nothing(monkeypatch, capsys):
    def no_address(record, request):
        raise LookupError("node-b has no address")

    monkeypatch.setattr(NodePairing, "ssh_command", classmethod(lambda cls, r, q: no_address(r, q)))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ran ssh"))
    assert ForgeMove.receive_hand_over(TARGET) is None
    assert "no address" in capsys.readouterr().out


def hand_over_answer(monkeypatch, data: bytes, returncode: int = 0, stderr: bytes = b""):
    monkeypatch.setattr(
        NodePairing, "ssh_command", classmethod(lambda cls, r, q: ["ssh", "node-b", q])
    )

    def ssh(command, stdout, **kwargs):
        stdout.write(data)
        return subprocess.CompletedProcess(command, returncode, None, stderr)

    monkeypatch.setattr(subprocess, "run", ssh)


def test_a_hand_over_the_record_refuses_brings_nothing(monkeypatch, capsys):
    hand_over_answer(
        monkeypatch, b"", returncode=1, stderr=b"this node is not the forge of record\n"
    )
    assert ForgeMove.receive_hand_over(TARGET) is None
    assert "did not hand a set over: this node is not the forge" in capsys.readouterr().out


@pytest.mark.parametrize(
    "failure", [subprocess.TimeoutExpired(["ssh"], 7200), FileNotFoundError("ssh")]
)
def test_a_hand_over_that_never_finishes_brings_nothing(failure, monkeypatch, capsys):
    # pull_here unfreezes the record only when this returns; an escaping error left it frozen.
    hand_over_answer(monkeypatch, b"")

    def ssh(command, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", ssh)
    assert ForgeMove.receive_hand_over(TARGET) is None
    assert "did not hand a set over" in capsys.readouterr().out


@pytest.mark.parametrize("data", [b"not a tar stream" * 40, b""])
def test_a_hand_over_answer_that_is_not_a_set_brings_nothing(data, monkeypatch, capsys):
    hand_over_answer(monkeypatch, data)
    assert ForgeMove.receive_hand_over(TARGET) is None
    assert "not a backup set" in capsys.readouterr().out
    assert [p for p in ForgeBackup.received_dir().iterdir() if p.is_dir()] == []


def test_a_hand_over_named_outside_the_set_names_is_not_kept(monkeypatch):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo("evil/manifest.json")
        info.size = 2
        archive.addfile(info, io.BytesIO(b"{}"))
    hand_over_answer(monkeypatch, stream.getvalue())
    assert ForgeMove.receive_hand_over(TARGET) is None
    assert not (ForgeBackup.received_dir() / "evil").exists()


# -- the data a move set aside ---------------------------------------------------------------------


def test_set_aside_data_is_removed_only_after_the_back_window():
    parent = ForgeSettings.data_dir().parent
    old = parent / "data.record-20200901-000000"
    recent = parent / f"data.before-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
    for folder in (old, recent):
        (folder / "git").mkdir(parents=True)
    (parent / "data.notes").write_text("a file, not a data folder")
    month_ago = time.time() - 31 * 86400
    os.utime(old, (month_ago, month_ago))
    assert ForgeMove.prune_old_data() == [old.name]
    assert not old.exists() and recent.is_dir() and (parent / "data.notes").exists()


def test_data_set_aside_today_is_kept_although_the_folder_itself_is_old():
    # A rename keeps the folder's own time, which is when a file last went into it (set-up time).
    parent = ForgeSettings.data_dir().parent
    today = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    aside = parent / f"data.record-{today}"
    (aside / "git").mkdir(parents=True)
    months_ago = time.time() - 120 * 86400
    os.utime(aside, (months_ago, months_ago))
    assert ForgeMove.prune_old_data() == []
    assert aside.is_dir()
