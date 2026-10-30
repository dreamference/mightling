"""Decisions in the node package that no other test reached: refusals, guards and fallbacks.

Nothing here reaches another machine or changes this one: sockets stay in the test's own run
folder, and every ssh, plug or subprocess call is a stand-in.
"""

import argparse
import io
import json
import os
import socket
import struct
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from dreamference.hardware.adapter_verifier import AdapterVerifier
from dreamference.node import (
    NodeAdapterSync,
    NodeJob,
    NodeModelSync,
    NodePairing,
    NodePowerCycle,
    NodePowerSettings,
    NodeShellyPlug,
    node_adapter_sync,
    node_job,
    node_power_cycle,
    node_shelly_plug,
)
from dreamference.node.fleet_askpass import FleetAskpass
from dreamference.node.fleet_session import FleetSession
from tests.test_fleet_hub_fetch import FOLDER, authorised_lines, completed, lan_sessions

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


# -- power: the plug table's refusals, the plug's replies, the cycle's fallbacks


def test_a_plug_with_a_bad_name_channel_or_generation_is_not_saved():
    assert "not a plug name" in NodePowerSettings.set_plug("bad name!", "p.local")
    for channel, generation in ((-1, 2), (0, 3)):
        refused = NodePowerSettings.set_plug(
            "p1", "p.local", channel=channel, generation=generation
        )
        assert refused == "channel is 0 or more, generation is 1 or 2"
    assert NodePowerSettings.load()["plugs"] == {}


@pytest.mark.parametrize(("plug", "via"), [("bad/plug", None), ("p1", "bad via"), ("p1", "../x")])
def test_a_node_entry_with_a_bad_plug_or_neighbour_name_is_not_saved(plug, via):
    NodePowerSettings.set_plug("p1", "p.local")
    assert NodePowerSettings.set_node("house-1", plug, via=via) is not None
    assert NodePowerSettings.load()["nodes"] == {}


def power_arguments(**fields):
    arguments = {"plug": "p1", "host": "p.local", "kind": "shelly", "channel": 0, "generation": 2}
    arguments.update(name="house-1", via=None)
    arguments.update(fields)
    return argparse.Namespace(**arguments)


def test_power_config_exit_codes_say_usage_shown_refused_or_saved(capsys):
    assert NodePowerSettings.configure(None, power_arguments()) == 2
    assert "usage:" in capsys.readouterr().out
    assert NodePowerSettings.configure("plug", power_arguments(plug="bad name!")) == 1
    assert capsys.readouterr().out.startswith("❌")
    assert NodePowerSettings.load()["plugs"] == {}
    assert NodePowerSettings.configure("plug", power_arguments()) == 0
    assert NodePowerSettings.configure("node", power_arguments()) == 0
    assert capsys.readouterr().out.count("✅ Saved.") == 2
    assert NodePowerSettings.configure("show", power_arguments()) == 0
    assert json.loads(capsys.readouterr().out) == NodePowerSettings.load()


def test_power_dispatch_sends_config_to_the_table_and_cycle_to_the_plug(monkeypatch):
    cycles = []
    monkeypatch.setattr(
        NodePowerCycle,
        "cycle",
        classmethod(lambda cls, name, **kwargs: cycles.append((name, kwargs)) or 7),
    )
    shown = power_arguments(node_command="power-config", power_config_command="show")
    assert NodePowerSettings.dispatch(shown) == 0
    assert cycles == []
    cycled = power_arguments(node_command="power-cycle", off_seconds=9, force=True, wait_minutes=2)
    assert NodePowerSettings.dispatch(cycled) == 7
    assert cycles == [("house-1", {"off_seconds": 9, "force": True, "wait_minutes": 2})]


class Reply(io.BytesIO):
    """An HTTP reply body that works as `urlopen`'s context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_plug_reply_that_is_not_a_json_object_is_an_oserror(monkeypatch):
    bodies = [b'{"output": true}', b"[1, 2]", b"not json"]
    monkeypatch.setattr(
        node_shelly_plug.urllib.request, "urlopen", lambda url, timeout: Reply(bodies.pop(0))
    )
    assert NodeShellyPlug._get("http://p.local/rpc/Switch.GetStatus?id=0") == {"output": True}
    for _ in range(2):
        with pytest.raises(OSError):
            NodeShellyPlug._get("http://p.local/rpc/Switch.GetStatus?id=0")


def test_a_plug_reply_without_a_true_or_false_state_is_an_oserror(monkeypatch):
    monkeypatch.setattr(NodeShellyPlug, "_get", classmethod(lambda cls, url: {"output": "on"}))
    with pytest.raises(OSError, match="names no switch state"):
        NodeShellyPlug.state("p.local")


def test_an_unknown_plug_generation_is_refused_before_any_request(monkeypatch):
    urls = []
    monkeypatch.setattr(NodeShellyPlug, "_get", classmethod(lambda cls, url: urls.append(url)))
    with pytest.raises(ValueError):
        NodeShellyPlug.switch("p.local", False, generation=3)
    assert urls == []


@pytest.fixture
def neighbour(monkeypatch):
    """`house-2` is paired and relays plug commands; `replies` decides each `power-plug` answer."""
    records = {"house-2": {"node": "n2", "name": "house-2", "user": "u", "address": "b"}}
    sent, replies = [], {}

    def run(cls, record, request, **kwargs):
        sent.append((record["name"], request))
        return subprocess.CompletedProcess([], *replies.get(request, (0, '{"on": true}\n', "")))

    monkeypatch.setattr(
        NodePairing, "find", classmethod(lambda cls, name, browse=True: records.get(name))
    )
    monkeypatch.setattr(NodePairing, "run", classmethod(run))
    monkeypatch.setattr(node_power_cycle.time, "sleep", lambda seconds: None)
    return sent, replies


def test_a_neighbour_that_is_not_paired_or_fails_never_gets_off(neighbour, capsys):
    sent, replies = neighbour
    NodePowerSettings.set_node("house-1", "p1", via="house-3")
    assert NodePowerCycle.cycle("house-1", force=True) == 1
    assert "house-3 is not a paired node" in capsys.readouterr().out
    assert sent == []
    NodePowerSettings.set_node("house-1", "p1", via="house-2")
    replies["power-plug p1 status"] = (255, "", "permission denied\n")
    assert NodePowerCycle.cycle("house-1", force=True) == 1
    assert "could not be reached: permission denied" in capsys.readouterr().out
    assert [request for _, request in sent] == ["power-plug p1 status"]


def test_a_node_that_is_not_paired_or_cannot_be_asked_does_not_answer(neighbour, monkeypatch):
    assert not NodePowerCycle._answers("house-9")

    def broken(cls, record, request, **kwargs):
        raise subprocess.TimeoutExpired("ssh", 5)

    monkeypatch.setattr(NodePairing, "run", classmethod(broken))
    assert not NodePowerCycle._answers("house-2")


def scripted_plug(monkeypatch, script):
    """`_plug` answers each action from `script[action]` in turn: a dict, or an OSError to raise."""
    actions = []

    def plug(cls, node, plug, via, action):
        actions.append(action)
        answer = script[action].pop(0)
        if isinstance(answer, OSError):
            raise answer
        return answer

    monkeypatch.setattr(NodePowerCycle, "_plug", classmethod(plug))
    monkeypatch.setattr(node_power_cycle.time, "sleep", lambda seconds: None)
    return actions


@pytest.mark.parametrize("second_status", [{"on": False}, OSError("still no answer")])
def test_a_lost_off_reply_still_turns_the_plug_on_again(monkeypatch, capsys, second_status):
    actions = scripted_plug(
        monkeypatch,
        {
            "status": [{"on": True, "power_w": 40.0}, second_status],
            "off": [OSError("timed out")],
            "on": [{"on": True}],
        },
    )
    assert NodePowerCycle._cut_and_restore("house-1", "p1", None, 5)
    assert actions == ["status", "off", "status", "on"]
    assert "reply to 'off' was lost (timed out)" in capsys.readouterr().out


def test_on_is_retried_until_the_plug_reports_on(monkeypatch, capsys):
    actions = scripted_plug(
        monkeypatch,
        {
            "status": [{"on": True}],
            "off": [{"on": False}],
            "on": [{"on": False}, OSError("busy"), {"on": True}],
        },
    )
    assert NodePowerCycle._cut_and_restore("house-1", "p1", None, 5)
    assert actions == ["status", "off", "on", "on", "on"]
    assert "did not take 'on' yet (busy)" in capsys.readouterr().out


def test_a_plug_that_never_comes_back_on_is_reported_off_after_five_tries(monkeypatch, capsys):
    actions = scripted_plug(
        monkeypatch,
        {"status": [{"on": True}], "off": [{"on": False}], "on": [OSError("gone")] * 5},
    )
    assert not NodePowerCycle._cut_and_restore("house-1", "p1", None, 5)
    assert actions.count("on") == node_power_cycle.RESTORE_TRIES
    assert "THE PLUG IS OFF" in capsys.readouterr().out


def test_a_node_that_stays_dark_after_the_cycle_fails_the_command(monkeypatch, capsys):
    NodePowerSettings.set_plug("p1", "p.local")
    NodePowerSettings.set_node("house-1", "p1")
    monkeypatch.setattr(
        NodePowerCycle, "_cut_and_restore", classmethod(lambda cls, *arguments: True)
    )
    monkeypatch.setattr(NodePowerCycle, "_answers", classmethod(lambda cls, node: False))
    assert NodePowerCycle.cycle("house-1", wait_minutes=0) == 1
    assert "did not answer in 0 min" in capsys.readouterr().out


# -- model and adapter sync: what is refused before anything is copied


def a_matrix_key():
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

    return next(iter(ModelMatrixRegistry.MATRIX))


PAIRED = {"node": "n2", "name": "house-2", "user": "u", "address": "house-2.local"}


def test_a_model_sync_to_an_unknown_key_or_node_copies_nothing(monkeypatch, capsys):
    asked = []
    monkeypatch.setattr(
        NodePairing, "find", classmethod(lambda cls, name, browse=True: asked.append(name))
    )
    assert NodeModelSync.sync("house-2", "no-such-model") == 1
    assert "not a key of the model matrix" in capsys.readouterr().out
    assert asked == []
    assert NodeModelSync.sync("house-2", a_matrix_key()) == 1
    assert "house-2 is not a paired node" in capsys.readouterr().out


def test_a_model_sync_to_a_given_address_pins_that_address(monkeypatch, tmp_path, capsys):
    reached = []

    def ssh_command(cls, record, request):
        reached.append(record)
        raise LookupError("stop here")

    monkeypatch.setattr(NodePairing, "find", classmethod(lambda cls, name, browse=True: PAIRED))
    monkeypatch.setattr(NodePairing, "ssh_command", classmethod(ssh_command))
    monkeypatch.setattr(NodeModelSync, "hub", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(NodeModelSync, "repos", classmethod(lambda cls, key: ["org/model"]))
    monkeypatch.setattr(NodeModelSync, "members", classmethod(lambda cls, hub, repo: []))
    assert NodeModelSync.sync("house-2", a_matrix_key(), address="house-2-qsfp.local") == 1
    assert reached[0]["address"] == "house-2-qsfp.local" and reached[0]["address_fixed"]
    assert "stop here" in capsys.readouterr().out
    assert PAIRED["address"] == "house-2.local"  # the stored record is not changed


def test_only_content_addressed_blobs_are_checked_against_their_name(tmp_path):
    blobs = tmp_path / "models--org--m" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / ("ab" * 20)).write_text("a git blob is named by sha1 and not hashed here")
    assert NodeModelSync._verify(tmp_path) is None
    (blobs / ("cd" * 32)).write_text("not what the name says")
    assert "does not match its checksum" in NodeModelSync._verify(tmp_path)


def test_adapters_go_only_to_a_paired_node_and_to_the_address_given(monkeypatch):
    sent = []
    monkeypatch.setattr(NodeAdapterSync, "signed_for", classmethod(lambda cls, key: []))
    assert NodeAdapterSync.send_to("house-2", "m") == (0, [])
    monkeypatch.setattr(NodeAdapterSync, "signed_for", classmethod(lambda cls, key: [{}]))
    monkeypatch.setattr(
        NodeAdapterSync,
        "send",
        classmethod(lambda cls, record, key: sent.append(record) or (0, [])),
    )
    monkeypatch.setattr(NodePairing, "find", classmethod(lambda cls, name, browse=True: None))
    assert NodeAdapterSync.send_to("house-2", "m") == (1, ["❌ house-2 is not a paired node."])
    assert sent == []
    monkeypatch.setattr(NodePairing, "find", classmethod(lambda cls, name, browse=True: PAIRED))
    assert NodeAdapterSync.send_to("house-2", "m", address="house-2-qsfp.local") == (0, [])
    assert NodeAdapterSync.send_to("house-2", "m") == (0, [])
    assert sent[0]["address"] == "house-2-qsfp.local" and sent[0]["address_fixed"]
    assert sent[1] == PAIRED


def test_an_adapter_too_big_for_the_disk_is_refused_before_staging(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(NodeModelSync, "hub", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        node_adapter_sync.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=1, used=1, free=0),
    )
    assert NodeAdapterSync.receive(["style", "org/style", "a" * 40, "1"]) == 2
    assert "too little free space" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_an_adapter_whose_manifest_names_another_is_refused(monkeypatch, tmp_path):
    class Manifest:
        name = "other"

    def check_files(cls, snapshot, signed):
        assert signed
        return Manifest()

    monkeypatch.setattr(AdapterVerifier, "check_files", classmethod(check_files))
    assert NodeAdapterSync._check(tmp_path, "style") == "the manifest names other, not style"
    assert NodeAdapterSync._check(tmp_path, "other") is None


# -- the LAN copy's key: never authorised unless it was made, is removable, and took


def test_a_lan_copy_whose_key_was_not_made_asks_the_donor_for_nothing(tmp_path):
    for made in (completed(1), completed(0, "/tmp/work\n")):
        fetch, recipient, donor = lan_sessions(tmp_path)
        answer = recipient.run
        recipient.run = lambda script, made=made, answer=answer, **kwargs: (
            made if "ssh-keygen" in script else answer(script, **kwargs)
        )
        assert not fetch.lan_copy(FOLDER, donor, "spark-1a2b.local", "owner", "/donor/hub")
        assert authorised_lines(donor) == []
        assert not any("ssh_host_ed25519" in command for command in donor.commands)


def test_a_lan_copy_key_with_another_comment_is_never_authorised(tmp_path, caplog):
    fetch, recipient, donor = lan_sessions(tmp_path)
    answer = recipient.run
    recipient.run = lambda script, **kwargs: (
        completed(0, "/tmp/work\nssh-ed25519 AAAAkey someone-else\n")
        if "ssh-keygen" in script
        else answer(script, **kwargs)
    )
    with caplog.at_level("WARNING"):
        assert not fetch.lan_copy(FOLDER, donor, "spark-1a2b.local", "owner", "/donor/hub")
    assert "not one it can remove" in caplog.text
    assert authorised_lines(donor) == []
    assert any("sed -i" in command for command in donor.commands)


def test_a_lan_copy_whose_key_the_donor_did_not_take_copies_nothing_and_cleans_up(tmp_path):
    fetch, recipient, donor = lan_sessions(tmp_path)
    answer = donor.run
    donor.run = lambda script, **kwargs: (
        completed(1)
        if "printf" in script and "authorized_keys" in script
        else answer(script, **kwargs)
    )
    assert not fetch.lan_copy(FOLDER, donor, "spark-1a2b.local", "owner", "/donor/hub")
    assert not any("rsync" in command for command in recipient.commands)
    assert any("sed -i" in command for command in donor.commands)
    assert any(command.startswith("rm -rf") for command in recipient.commands)


# -- the role's steps: no key, no listing, no pass


def role_steps():
    from dreamference.node.node_provisioner import NodeProvisioner
    from dreamference.node.node_role_provisioner import NodeRoleProvisioner

    provisioner = NodeProvisioner(["new-node"], {"user": "user", "role": "night"})
    return provisioner, NodeRoleProvisioner(provisioner, "night")


def test_authorising_without_a_key_here_fails_the_step_and_sends_nothing(capsys):
    provisioner, role = role_steps()
    session = SimpleNamespace(identity=None, run=lambda *a, **k: pytest.fail("ran on the node"))
    assert role.authorize_key("new-node", session) is False
    assert provisioner.results["new-node"]["failed"] == "authorise the key"
    assert provisioner.results["new-node"]["reason"] == "no key here"


def test_a_node_missing_from_the_list_after_pairing_fails_the_step(monkeypatch):
    provisioner, role = role_steps()
    provisioner.results["new-node"] = {"host": "new-node", "done": [], "node": "n-new"}
    paired = [{"node": "n-other", "name": "house-2"}]
    monkeypatch.setattr(NodePairing, "paired", classmethod(lambda cls: paired))
    assert role.listed("new-node", None) is False
    assert provisioner.results["new-node"]["failed"] == "node list"
    paired.append({"node": "n-new", "name": "new-node"})
    provisioner.results["new-node"] = {"host": "new-node", "done": [], "node": "n-new"}
    assert role.listed("new-node", None) is True
    assert "failed" not in provisioner.results["new-node"]


@pytest.mark.parametrize("text", ["[true]", "true", '"advertise"'])
def test_a_node_settings_file_that_is_not_an_object_keeps_every_bind_on_loopback(text):
    from dreamference.node.node_settings import NodeSettings

    NodeSettings.path().parent.mkdir(parents=True, exist_ok=True)
    NodeSettings.path().write_text(text)
    assert NodeSettings.load() == {"advertise": False, "web": False}
