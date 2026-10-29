"""The forge's backups off the happy path.

What a paired node sends is checked before it is kept, a set that does not restore never replaces
the data, and sending is mocked (no ssh, no tar).
"""

import io
import json
import sqlite3
import subprocess
import tarfile
from pathlib import Path

import pytest
from test_forge import make_set, repositories  # noqa: F401  (a fixture)

from dreamference.forge import ForgeBackup, ForgeSettings
from dreamference.forge.forge_settings import BACKUP_COPIES, ROLE_MIRROR, ROLE_RECORD
from dreamference.node.node_pairing import NodePairing

NAME = "forge-20261027-063000"


def tar_stream(members: dict[str, bytes]) -> io.BytesIO:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    stream.seek(0)
    return stream


# -- receiving a set from a paired node ------------------------------------------------------------


def test_a_stream_without_a_manifest_is_refused_and_leaves_nothing(capsys):
    assert ForgeBackup.receive(NAME, tar_stream({f"{NAME}/dump.tar.gz": b"x"})) == 2
    assert "no backup set" in capsys.readouterr().out
    assert list(ForgeBackup.received_dir().iterdir()) == []


def test_a_stream_that_is_not_a_tar_is_refused(capsys):
    assert ForgeBackup.receive(NAME, io.BytesIO(b"not a tar stream at all" * 40)) == 2
    assert "refused" in capsys.readouterr().out
    assert list(ForgeBackup.received_dir().iterdir()) == []


def test_a_stream_reaching_outside_the_received_folder_is_refused(tmp_path):
    outside = ForgeBackup.received_dir().parent / "escaped"
    stream = tar_stream(
        {f"{NAME}/manifest.json": b"{}", f"{NAME}/../../escaped": b"x", "/tmp/abs-escape": b"x"}
    )
    assert ForgeBackup.receive(NAME, stream) == 2
    assert not outside.exists()
    assert list(ForgeBackup.received_dir().iterdir()) == []


def test_receiving_a_set_again_replaces_the_copy_held(tmp_path, repositories):  # noqa: F811
    source = tmp_path / "out" / NAME
    make_set(source, repositories)
    for marker in (b"first", b"second"):
        (source / "note").write_bytes(marker)
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            archive.add(source, arcname=NAME)
        stream.seek(0)
        assert ForgeBackup.receive(NAME, stream) == 0
    assert (ForgeBackup.received_dir() / NAME / "note").read_bytes() == b"second"


# -- checking and installing a set -----------------------------------------------------------------


def test_check_without_a_set_says_so(capsys):
    assert ForgeBackup.check() == 1
    assert "no backup set" in capsys.readouterr().out


@pytest.mark.parametrize("manifest", ["[]", '{"set": "x"}', '{"refs": ["owner/demo"]}'])
def test_a_set_whose_manifest_lists_no_refs_does_not_restore(
    tmp_path,
    repositories,  # noqa: F811
    manifest,
    capsys,
):
    folder = tmp_path / "sets" / NAME
    make_set(folder, repositories)
    (folder / "manifest.json").write_text(manifest)
    assert ForgeBackup.check(folder) == 1
    assert "does not restore" in capsys.readouterr().out
    assert ForgeBackup.install_data(folder) == (None, None)


def test_a_dump_without_a_database_does_not_restore(tmp_path, capsys):
    folder = tmp_path / "sets" / NAME
    folder.mkdir(parents=True)
    dump = io.BytesIO()
    with tarfile.open(fileobj=dump, mode="w:gz") as archive:
        info = tarfile.TarInfo("app.ini")
        archive.addfile(info, io.BytesIO(b""))
    (folder / "dump.tar.gz").write_bytes(dump.getvalue())
    (folder / "manifest.json").write_text('{"refs": {}}')
    (folder / "secrets.json").write_text("{}")
    assert ForgeBackup.check(folder) == 1
    assert "holds no database" in capsys.readouterr().out


def test_a_set_that_does_not_restore_leaves_the_current_data_in_place(tmp_path, capsys):
    data = ForgeSettings.data_dir()
    (data / "git").mkdir(parents=True)
    (data / "git" / "keep").write_text("current")
    folder = tmp_path / "sets" / NAME
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text('{"refs": {}}')
    (folder / "dump.tar.gz").write_bytes(b"not gzip")
    assert ForgeBackup.install_data(folder) == (None, None)
    assert (data / "git" / "keep").read_text() == "current"
    assert [p.name for p in data.parent.iterdir() if p.name.startswith("data.")] == []


def test_installing_a_set_moves_the_current_data_aside(tmp_path, repositories, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ForgeBackup, "host_name", classmethod(lambda cls: "node-b"))
    data = ForgeSettings.data_dir()
    data.mkdir(parents=True)
    (data / "old").write_text("before")
    folder = tmp_path / "sets" / NAME
    manifest = make_set(folder, repositories)
    installed, aside = ForgeBackup.install_data(folder)
    assert installed == manifest and aside == _aside(data)
    assert (aside / "old").read_text() == "before"
    assert (data / "git" / "repositories" / "owner" / "demo.git").is_dir()
    assert ForgeSettings.secret_path("owner-password").read_text().strip() == "p" * 24


def _aside(data: Path) -> Path:
    return next(p for p in data.parent.iterdir() if p.name.startswith("data.before-"))


def test_a_database_that_cannot_be_read_is_not_copied(tmp_path):
    target = tmp_path / "copy.db"
    assert ForgeBackup.snapshot_database(tmp_path / "missing.db", target) is False
    assert not target.exists()


def test_a_live_database_is_copied_as_a_snapshot(tmp_path):
    source = tmp_path / "live.db"
    with sqlite3.connect(source) as live:
        live.execute("create table t (x)")
        live.execute("insert into t values (7)")
    target = tmp_path / "copy.db"
    assert ForgeBackup.snapshot_database(source, target) is True
    assert sqlite3.connect(target).execute("select x from t").fetchone() == (7,)


def test_an_unreadable_status_reads_as_none():
    assert ForgeBackup.status() is None
    ForgeSettings.write_secret("backup-status.json", "{not json")
    assert ForgeBackup.status() is None


# -- sending a set ---------------------------------------------------------------------------------


class FakePacker:
    def __init__(self, argv, stdout=None):
        self.argv, self.stdout, self.returncode = argv, io.BytesIO(b"tar"), 0

    def wait(self):
        return self.returncode


def test_sending_to_a_node_without_an_address_starts_nothing(tmp_path, monkeypatch, capsys):
    def no_address(record, request):
        raise LookupError("node-b has no address")

    monkeypatch.setattr(NodePairing, "ssh_command", classmethod(lambda cls, r, q: no_address(r, q)))
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    assert ForgeBackup.send(tmp_path / NAME, {"name": "node-b"}) is False
    assert started == [] and "no address" in capsys.readouterr().out


def test_a_node_that_does_not_store_the_set_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        NodePairing, "ssh_command", classmethod(lambda cls, r, q: ["ssh", "node-b", q])
    )
    monkeypatch.setattr(subprocess, "Popen", FakePacker)
    commands = []

    def refused(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 2, "", "refused: no backup set\n")

    monkeypatch.setattr(subprocess, "run", refused)
    assert ForgeBackup.send(tmp_path / NAME, {"name": "node-b"}) is False
    assert commands == [["ssh", "node-b", f"forge-backup-receive {NAME}"]]
    assert "node-b did not store the backup: refused" in capsys.readouterr().out


def test_a_node_that_does_not_answer_in_time_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        NodePairing, "ssh_command", classmethod(lambda cls, r, q: ["ssh", "node-b", q])
    )
    monkeypatch.setattr(subprocess, "Popen", FakePacker)

    def hung(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hung)
    assert ForgeBackup.send(tmp_path / NAME, {"name": "node-b"}) is False
    assert "node-b did not store the backup in time" in capsys.readouterr().out


def test_a_mirror_node_takes_no_backup(monkeypatch):
    ForgeSettings.update_state(role=ROLE_MIRROR)
    monkeypatch.setattr(ForgeBackup, "take", classmethod(lambda cls: pytest.fail("took a set")))
    assert ForgeBackup.run() == 0


def test_a_failed_dump_is_recorded(monkeypatch):
    ForgeSettings.update_state(role=ROLE_RECORD)
    monkeypatch.setattr(ForgeBackup, "take", classmethod(lambda cls: None))
    assert ForgeBackup.run() == 1
    assert ForgeBackup.status()["error"] == "the dump failed"


def test_the_nightly_backup_stops_after_enough_nodes_and_fails_below_that(tmp_path, monkeypatch):
    ForgeSettings.update_state(role=ROLE_RECORD)
    folder = tmp_path / NAME
    monkeypatch.setattr(ForgeBackup, "take", classmethod(lambda cls: folder))
    nodes = [{"node": f"n{i}", "name": f"node-{i}"} for i in range(BACKUP_COPIES + 2)]
    monkeypatch.setattr(NodePairing, "paired", classmethod(lambda cls: nodes))
    tried = []
    monkeypatch.setattr(
        ForgeBackup, "send", classmethod(lambda cls, f, record: tried.append(record) or True)
    )
    assert ForgeBackup.run() == 0
    assert len(tried) == BACKUP_COPIES and ForgeBackup.status()["ok"] is True
    tried.clear()
    monkeypatch.setattr(
        ForgeBackup,
        "send",
        classmethod(lambda cls, f, record: tried.append(record) or record["node"] == "n0"),
    )
    assert ForgeBackup.run() == 1
    status = ForgeBackup.status()
    assert status["ok"] is False and status["sent_to"] == ["node-0"]
    assert status["error"] == f"reached 1 of {BACKUP_COPIES} nodes"
    assert json.dumps(status)


@pytest.mark.parametrize("secrets", [None, "not json"])
def test_a_set_without_its_secrets_does_not_restore(secrets, tmp_path, repositories, capsys):  # noqa: F811
    data = ForgeSettings.data_dir()
    data.mkdir(parents=True)
    (data / "old").write_text("before")
    folder = tmp_path / "sets" / NAME
    make_set(folder, repositories)
    if secrets is None:
        (folder / "secrets.json").unlink()
    else:
        (folder / "secrets.json").write_text(secrets)
    # Else the push hook's denylist stays behind, and this node's old tokens face the set's forge.
    assert ForgeBackup.install_data(folder) == (None, None)
    assert (data / "old").read_text() == "before"
    assert "secrets" in capsys.readouterr().out
