"""Decisions in the node package that no other test reached: refusals, guards and fallbacks.

Nothing here reaches another machine or changes this one: sockets stay in the test's own run
folder, and every ssh, plug or subprocess call is a stand-in.
"""

import os
import socket
import struct
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dreamference.node import NodeJob, node_job
from dreamference.node.fleet_askpass import FleetAskpass
from dreamference.node.fleet_session import FleetSession

SECRET = "pa55-for-the-test"


# -- askpass: the peer check


def test_askpass_gives_another_user_nothing_and_keeps_answering(monkeypatch, capsys):
    run_dir = Path(FleetSession.new_run_dir())
    askpass = FleetAskpass(run_dir, {"spark-1": SECRET})
    same_user = [False]
    monkeypatch.setattr(
        FleetAskpass, "_same_user", classmethod(lambda cls, connection: same_user[0])
    )
    askpass.start("/usr/bin/false")
    try:
        for key, value in askpass.environment("spark-1").items():
            monkeypatch.setenv(key, value)
        assert FleetAskpass.ask() == 1
        assert capsys.readouterr().out == ""
        # The refused peer did not stop the server: the next connection is answered.
        same_user[0] = True
        assert FleetAskpass.ask() == 0
        assert capsys.readouterr().out == SECRET + "\n"
    finally:
        askpass.stop()
        run_dir.rmdir()


class PeerConnection:
    """A connection whose SO_PEERCRED names the given uid, or fails."""

    def __init__(self, uid=None):
        self.uid = uid

    def getsockopt(self, level, option, size):
        assert (level, option, size) == (
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        if self.uid is None:
            raise OSError("no credentials")
        return struct.pack("3i", 4242, self.uid, 0)


def test_askpass_peer_check_wants_this_uid_and_fails_closed():
    assert FleetAskpass._same_user(PeerConnection(os.getuid()))
    assert not FleetAskpass._same_user(PeerConnection(os.getuid() + 1))
    assert not FleetAskpass._same_user(PeerConnection(None))


def test_askpass_stop_before_start_removes_what_is_there(tmp_path):
    askpass = FleetAskpass(tmp_path, {})
    askpass.script_path.write_text("#!/bin/sh\n")
    askpass.stop()
    askpass.stop()
    assert not askpass.script_path.exists() and not askpass.socket_path.exists()


# -- the job store: links, clean-up and settling


def job_record(job_id="20261002-1400-aaa", **fields):
    record = {
        "id": job_id,
        "status": "done",
        "repo": "project",
        "time": "1h",
        "memory": "8G",
        "submitted": "2026-10-02T14:00:00+00:00",
        "finished": "2026-10-02T14:05:00+00:00",
    }
    record.update(fields)
    return record


def test_a_folder_at_the_marker_is_removed_and_a_link_to_one_is_not_followed(tmp_path):
    marker = tmp_path / "env" / ".complete"
    marker.mkdir(parents=True)
    (marker / "left-by-setup").write_text("x")
    NodeJob._mark_complete(marker, "pip install -e .")
    assert marker.is_file() and marker.read_text() == "pip install -e .\n"

    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("mine")
    marker.unlink()
    marker.symlink_to(victim, target_is_directory=True)
    NodeJob._mark_complete(marker, "true")
    assert not marker.is_symlink() and marker.read_text() == "true\n"
    assert (victim / "keep.txt").read_text() == "mine"


def test_an_out_folder_that_is_a_link_or_missing_keeps_nothing(tmp_path):
    tree, directory = tmp_path / "tree", tmp_path / "job"
    (tree / "real").mkdir(parents=True)
    (tree / "real" / "result.txt").write_text("r")
    directory.mkdir()
    (tree / "out").symlink_to(tree / "real", target_is_directory=True)
    # Even a link that stays inside the tree is refused: only a real folder is moved out.
    assert NodeJob._keep_out({"out": "out"}, tree, directory) == 0
    assert NodeJob._keep_out({"out": "absent"}, tree, directory) == 0
    assert not (directory / "out").exists()
    assert (tree / "out").is_symlink() and (tree / "real" / "result.txt").is_file()


def test_fetching_the_out_files_of_a_job_that_kept_none_fails(capsys):
    NodeJob.write(job_record())
    assert NodeJob.send_out("20261002-1400-aaa") == 1
    assert "kept no --out files" in capsys.readouterr().err


def test_the_store_lists_only_job_folders_with_a_readable_record():
    NodeJob.write(job_record())
    stray = NodeJob.jobs_dir() / "not-a-job"
    stray.mkdir()
    (stray / "record.json").write_text("{}")
    broken = NodeJob.jobs_dir() / "20261002-1401-bbb"
    broken.mkdir()
    (broken / "record.json").write_text("not json")
    assert [record["id"] for record in NodeJob.records()] == ["20261002-1400-aaa"]


def test_a_due_job_whose_repository_is_gone_or_misnamed_is_removed_without_git(monkeypatch):
    calls = []
    monkeypatch.setattr(
        NodeJob, "_git", classmethod(lambda cls, arguments: calls.append(arguments))
    )
    NodeJob.write(job_record("20261002-1400-aaa", repo="gone"))
    NodeJob.write(job_record("20261002-1401-bbb", repo="../escape"))
    later = datetime(2026, 11, 30, tzinfo=UTC)
    assert NodeJob.prune(later) == ["20261002-1400-aaa", "20261002-1401-bbb"]
    assert calls == []
    assert not NodeJob.job_dir("20261002-1400-aaa").exists()
    assert not NodeJob.job_dir("20261002-1401-bbb").exists()


def test_a_due_job_with_a_tree_left_has_its_worktree_removed_first(monkeypatch):
    calls = []
    monkeypatch.setattr(
        NodeJob, "_git", classmethod(lambda cls, arguments: calls.append(arguments))
    )
    NodeJob.write(job_record(status="cancelled"))
    NodeJob.repo_path("project").mkdir(parents=True)
    (NodeJob.job_dir("20261002-1400-aaa") / "tree").mkdir()
    assert NodeJob.prune(datetime(2026, 11, 30, tzinfo=UTC)) == ["20261002-1400-aaa"]
    repo = str(NodeJob.repo_path("project"))
    tree = str(NodeJob.job_dir("20261002-1400-aaa") / "tree")
    assert calls[0] == ["--git-dir", repo, "worktree", "remove", "--force", tree]
    assert ["--git-dir", repo, "branch", "-D", "job/20261002-1400-aaa"] in calls
    assert NodeJob.repo_path("project").is_dir()


@pytest.mark.parametrize("text", ["../../somewhere", "gitdir"])
def test_a_tree_whose_git_file_names_no_gitdir_has_no_admin_folder(tmp_path, text):
    tree, repo = tmp_path / "tree", tmp_path / "repo.git"
    tree.mkdir()
    (repo / "worktrees" / "tree").mkdir(parents=True)
    (tree / ".git").write_text(text + "\n")
    assert NodeJob._admin_dir(tree, repo) is None
    (tree / ".git").write_text(f"gitdir: {repo / 'worktrees' / 'tree'}\n")
    assert NodeJob._admin_dir(tree, repo) == (repo / "worktrees" / "tree").resolve()


def test_a_running_job_whose_unit_is_active_or_just_finished_is_not_settled_as_failed(monkeypatch):
    monkeypatch.setattr(NodeJob, "USE_UNIT", True)
    NodeJob.write(job_record(status="running", finished=None))
    monkeypatch.setattr(NodeJob, "unit_active", classmethod(lambda cls, job_id: True))
    assert NodeJob.reconcile("20261002-1400-aaa")["status"] == "running"
    assert NodeJob.read("20261002-1400-aaa")["status"] == "running"

    # The job wrote its result between the first read and the unit check.
    def finished_meanwhile(cls, job_id):
        NodeJob.write(job_record(status="done"))
        return False

    monkeypatch.setattr(NodeJob, "unit_active", classmethod(finished_meanwhile))
    assert NodeJob.reconcile("20261002-1400-aaa")["status"] == "done"
    assert "note" not in NodeJob.read("20261002-1400-aaa")


def test_cancelling_a_job_under_a_unit_stops_that_unit(monkeypatch):
    monkeypatch.setattr(NodeJob, "USE_UNIT", True)
    seen = []

    def fake_run(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(node_job.subprocess, "run", fake_run)
    NodeJob.write(job_record(status="running", finished=None))
    assert NodeJob.cancel("20261002-1400-aaa")
    unit = NodeJob.unit_name("20261002-1400-aaa")
    assert seen == [["systemctl", "--user", "stop", f"{unit}.service"]]
    assert NodeJob.read("20261002-1400-aaa")["status"] == "cancelled"
    assert not NodeJob.cancel("20261002-1400-aaa")
    assert len(seen) == 1
