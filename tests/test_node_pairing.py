"""Pairing with a node, and the forced command a paired key runs.

The refusals, the failures half way through a pairing, and what a machine on the network may report
(its id, its answers).
"""

import json
import subprocess

import pytest

from dreamference.node.node_identity import NodeIdentity
from dreamference.node.node_pairing import NodePairing
from dreamference.node.node_serve import NodeServe

NODE = "11111111-2222-4333-8444-555555555555"


def answer(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class Session:
    """An open provisioning session as `pair_over_session` sees it."""

    def __init__(
        self, id_answer, authorize=None, host_keys=("ssh-ed25519 AAAAone",), host="spark-1.local"
    ):
        self.host, self.user, self.ssh_port = host, "owner", 22
        self.id_answer, self.authorize = id_answer, authorize or answer()
        self.keys = list(host_keys)
        self.commands = []

    def run(self, command, input_text=None):
        self.commands.append((command, input_text))
        return self.id_answer if command.endswith("hostname") else self.authorize

    def host_key_lines(self):
        return self.keys


@pytest.fixture
def pairing(monkeypatch):
    """No key generation and no ssh: the key exists, and the node's answers are given per test."""
    monkeypatch.setattr(
        NodePairing, "ensure_key", classmethod(lambda cls: "ssh-ed25519 AAAAsender mightling-node")
    )
    monkeypatch.setattr(
        NodePairing, "ssh_config", classmethod(lambda cls, host: (None, NodePairing.key_path()))
    )
    answers = {"info": answer(0, json.dumps({"node": NODE}))}
    monkeypatch.setattr(
        NodePairing, "run", classmethod(lambda cls, record, request, **_: answers[request])
    )
    return answers


# -- what the other machine reports --------------------------------------------------------------------


@pytest.mark.parametrize("reported", ["../../../escaped", "a/b", ".hidden", "-oProxyCommand=x"])
def test_a_node_id_reported_over_a_login_that_is_not_an_id_is_refused_and_nothing_is_written(
    pairing, reported, tmp_path, capsys
):
    pairing["info"] = answer(0, json.dumps({"node": reported}))  # the machine keeps up the story
    session = Session(answer(0, f"{reported}\nspark-1\n"))
    assert NodePairing.pair_over_session(session) is None
    assert "did not report a node id" in capsys.readouterr().out
    assert not NodePairing.known_hosts().exists()
    assert list(tmp_path.rglob("*.json")) == []
    assert not any("authorize" in command for command, _ in session.commands)


def test_a_node_advertising_something_that_is_not_a_node_id_is_not_found_by_a_browse(monkeypatch):
    # The id is a TXT record anyone on the LAN can send, under any node's name.
    from dreamference.node.node_browser import NodeBrowser

    monkeypatch.setattr(
        NodeBrowser,
        "browse",
        classmethod(
            lambda cls, timeout=6: [
                {"node": "../../escaped", "name": "spark-1", "address": "192.0.2.7"},
                {"node": NODE, "name": "spark-2", "address": "192.0.2.8"},
            ]
        ),
    )
    assert NodePairing.discover("spark-1") is None
    assert NodePairing.discover("spark-2")["node"] == NODE
    assert NodePairing.discover("111") is None  # an id is found by four characters or more
    assert NodePairing.discover("1111")["node"] == NODE


def test_a_record_is_never_named_by_something_that_is_not_a_node_id():
    for bad in ("../x", f"{NODE}/../../x", f"{NODE}.json"):  # the whole name is the id
        with pytest.raises(ValueError):
            NodePairing.record_path(bad)
    assert NodePairing.record_path(NODE).name == f"{NODE}.json"


def test_pairing_over_a_session_pins_the_host_key_authorises_the_key_and_saves_the_node(pairing):
    session = Session(answer(0, f"{NODE}\nspark-1\n"))
    record = NodePairing.pair_over_session(session)
    assert record == {
        "node": NODE,
        "name": "spark-1",
        "address": "spark-1.local",
        "user": "owner",
        "ssh_port": 22,
    }
    assert json.loads(NodePairing.record_path(NODE).read_text()) == record
    assert f"mightling-node-{NODE} ssh-ed25519 AAAAone" in NodePairing.known_hosts().read_text()
    command, key = session.commands[-1]
    assert command.endswith("node authorize") and key == "ssh-ed25519 AAAAsender mightling-node\n"


def test_a_machine_that_reports_no_node_id_is_not_paired(pairing, capsys):
    assert NodePairing.pair_over_session(Session(answer(127, "", "not found"))) is None
    assert "did not report a node id" in capsys.readouterr().out
    assert not NodePairing.nodes_dir().exists()


def test_a_session_that_recorded_no_host_key_is_not_paired(pairing, capsys):
    assert (
        NodePairing.pair_over_session(Session(answer(0, f"{NODE}\nspark-1\n"), host_keys=()))
        is None
    )
    assert "no host key" in capsys.readouterr().out
    assert not NodePairing.record_path(NODE).exists()


def test_a_node_that_refuses_the_key_is_not_paired(pairing, capsys):
    session = Session(answer(0, f"{NODE}\nspark-1\n"), authorize=answer(1, "", "permission denied"))
    assert NodePairing.pair_over_session(session) is None
    assert "did not authorise the key: permission denied" in capsys.readouterr().out
    assert not NodePairing.record_path(NODE).exists()


@pytest.mark.parametrize(
    "info",
    [
        answer(255, "", "refused"),
        answer(0, "not json"),
        answer(0, json.dumps({"node": "99999999-2222-4333-8444-555555555555"})),
    ],
)
def test_a_pairing_the_node_does_not_confirm_through_the_new_key_is_removed(pairing, info, capsys):
    pairing["info"] = info
    assert NodePairing.pair_over_session(Session(answer(0, f"{NODE}\nspark-1\n"))) is None
    assert "pairing removed" in capsys.readouterr().out
    assert not NodePairing.record_path(NODE).exists()


def test_a_name_in_the_overlay_domain_is_kept_as_the_nodes_overlay_name(pairing):
    host = f"spark-1.{NodePairing.overlay_domain()}"
    record = NodePairing.pair_over_session(Session(answer(0, f"{NODE}\nspark-1\n"), host=host))
    assert record["overlay_name"] == host


# -- pairing through a browse ------------------------------------------------------------------------


@pytest.fixture
def advertised(pairing, monkeypatch):
    monkeypatch.setattr(
        NodePairing,
        "discover",
        classmethod(lambda cls, name: {"node": NODE, "name": "spark-1", "address": "192.0.2.7"}),
    )
    authorised = []
    monkeypatch.setattr(
        NodePairing,
        "authorize_on_node",
        classmethod(lambda cls, record, key, identity=None: authorised.append(record) or True),
    )
    return authorised


def test_pairing_through_a_browse_is_undone_when_another_machine_answers_the_new_key(
    advertised, pairing, capsys
):
    pairing["info"] = answer(0, json.dumps({"node": "99999999-2222-4333-8444-555555555555"}))
    assert not NodePairing.add("spark-1", user="owner")
    assert "not the node that was advertised" in capsys.readouterr().out
    assert not NodePairing.record_path(NODE).exists()


def test_pairing_through_a_browse_fails_when_the_new_key_gets_no_answer(
    advertised, pairing, capsys
):
    pairing["info"] = answer(255, "", "Connection refused")
    assert not NodePairing.add("spark-1", user="owner")
    assert "did not answer through the new key: Connection refused" in capsys.readouterr().out
    assert not NodePairing.record_path(NODE).exists()


@pytest.mark.parametrize("info", [answer(0, "not json"), answer(0, json.dumps({"version": "1"}))])
def test_pairing_through_a_browse_is_undone_when_the_new_key_gets_no_node_id_back(
    advertised, pairing, info, capsys
):
    # As in a pairing over a session: only the node's own id through the new key confirms it.
    pairing["info"] = info
    assert not NodePairing.add("spark-1", user="owner")
    assert "pairing removed" in capsys.readouterr().out
    assert not NodePairing.record_path(NODE).exists()


def test_pairing_through_a_browse_stops_when_the_node_does_not_authorise_the_key(
    pairing, monkeypatch, capsys
):
    monkeypatch.setattr(
        NodePairing,
        "discover",
        classmethod(lambda cls, name: {"node": NODE, "name": "spark-1", "address": "192.0.2.7"}),
    )
    monkeypatch.setattr(
        NodePairing, "authorize_on_node", classmethod(lambda cls, record, key, identity=None: False)
    )
    assert not NodePairing.add("spark-1", user="owner")
    assert "did not authorise" in capsys.readouterr().out
    assert not NodePairing.record_path(NODE).exists()


def test_pairing_through_a_browse_offers_the_key_ssh_config_gives_the_name_typed(
    pairing, monkeypatch
):
    # The browse answers with an address; `~/.ssh/config` names the machine by its name, and the
    # user it gives that name is used, so the key it gives that name must be offered with it.
    from pathlib import Path

    monkeypatch.setattr(
        NodePairing,
        "discover",
        classmethod(lambda cls, name: {"node": NODE, "name": "spark-1", "address": "192.0.2.7"}),
    )
    keys = {"spark-1": ("owner", Path("/keys/spark")), "192.0.2.7": (None, Path("/keys/default"))}
    monkeypatch.setattr(NodePairing, "ssh_config", classmethod(lambda cls, host: keys[host]))
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: calls.append(argv) or answer(0))
    assert NodePairing.add("spark-1")
    assert calls[0][calls[0].index("-i") + 1] == "/keys/spark"
    assert "owner@192.0.2.7" in calls[0]


def test_pairing_warns_when_lingering_is_off_on_the_node(advertised, pairing, capsys):
    pairing["info"] = answer(0, json.dumps({"node": NODE, "linger": False}))
    assert NodePairing.add("spark-1", user="owner")
    assert "loginctl enable-linger" in capsys.readouterr().out


def test_pairing_stops_when_ssh_keygen_cannot_make_the_key(monkeypatch, capsys):
    monkeypatch.setattr(
        NodePairing,
        "discover",
        classmethod(lambda cls, name: {"node": NODE, "name": "spark-1", "address": "192.0.2.7"}),
    )
    monkeypatch.setattr(NodePairing, "ensure_key", classmethod(lambda cls: None))
    monkeypatch.setattr(
        NodePairing, "ssh_config", classmethod(lambda cls, host: (None, NodePairing.key_path()))
    )
    assert not NodePairing.add("spark-1", user="owner")
    assert "ssh-keygen could not create" in capsys.readouterr().out


def test_a_half_written_key_pair_is_replaced_and_a_failing_ssh_keygen_is_reported(monkeypatch):
    key = NodePairing.key_path()
    key.parent.mkdir(parents=True)
    key.write_text("private, without its public half")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: calls.append(argv) or answer(1))
    assert NodePairing.ensure_key() is None
    assert not key.exists()
    assert calls[0][0] == "ssh-keygen"


def test_a_login_that_fails_leaves_no_run_folder(monkeypatch, capsys):
    from dreamference.node import fleet_session

    opened = []

    class FailingSession:
        def __init__(self, host, user, run_dir, ssh_port=22, identity=None):
            self.run_dir, self.last_error = run_dir, "Permission denied (publickey,password)."
            run_dir.mkdir(parents=True, exist_ok=True)
            opened.append(run_dir)

        @staticmethod
        def new_run_dir():
            return NodePairing.nodes_dir() / "run"

        def open(self):
            return False

    monkeypatch.setattr(fleet_session, "FleetSession", FailingSession)
    monkeypatch.setattr(
        NodePairing, "ssh_config", classmethod(lambda cls, host: (None, NodePairing.key_path()))
    )
    assert not NodePairing.add_by_login("192.0.2.7", user="owner")
    assert "Could not log in to owner@192.0.2.7" in capsys.readouterr().out
    assert opened and not opened[0].exists()


# -- ssh's own configuration and failures ------------------------------------------------------------


def test_ssh_config_that_times_out_gives_no_user_and_the_default_key(monkeypatch):
    def slow(argv, **_):
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(subprocess, "run", slow)
    user, identity = NodePairing.ssh_config("spark-1")
    assert user is None and str(identity).endswith(".ssh/id_ed25519")


def test_authorising_on_the_node_fails_cleanly_when_ssh_cannot_start(monkeypatch):
    def missing(argv, **_):
        raise OSError("no ssh")

    monkeypatch.setattr(subprocess, "run", missing)
    record = {"node": NODE, "address": "192.0.2.7", "user": "owner"}
    assert NodePairing.authorize_on_node(record, "ssh-ed25519 AAAA") is False


def test_a_request_to_a_node_that_is_not_on_the_network_answers_255_with_the_reason(monkeypatch):
    monkeypatch.setattr(NodePairing, "resolve", classmethod(lambda cls, record: None))
    result = NodePairing.run(
        {"node": NODE, "name": "spark-1", "address": "192.0.2.7", "user": "owner"}, "info"
    )
    assert result.returncode == 255 and "spark-1 is not on the network" in result.stderr


def test_a_corrupt_or_incomplete_node_record_is_skipped(tmp_path):
    folder = NodePairing.nodes_dir()
    folder.mkdir(parents=True)
    (folder / "broken.json").write_text("{not json")
    (folder / "partial.json").write_text(json.dumps({"node": "x"}))
    (folder / "escaped.json").write_text(json.dumps({"node": "../../x", "address": "192.0.2.9"}))
    (folder / f"{NODE}.json").write_text(
        json.dumps({"node": NODE, "address": "192.0.2.7", "name": "spark-1"})
    )
    assert [record["node"] for record in NodePairing.paired()] == [NODE]


def test_removing_a_pairing_forgets_the_node_even_when_it_does_not_confirm(monkeypatch, capsys):
    record = {"node": NODE, "name": "spark-1", "address": "192.0.2.7", "user": "owner"}
    NodePairing._save(record)
    monkeypatch.setattr(
        NodePairing, "run", classmethod(lambda cls, record, request, **_: answer(255))
    )
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: calls.append(argv) or answer())
    assert NodePairing.remove("spark-1")
    out = capsys.readouterr().out
    assert "did not confirm" in out and "no longer paired" in out
    assert not NodePairing.record_path(NODE).exists()
    assert calls == [
        ["ssh-keygen", "-q", "-R", f"mightling-node-{NODE}", "-f", str(NodePairing.known_hosts())]
    ]


def test_removing_a_node_that_is_not_paired_is_refused(capsys):
    assert not NodePairing.remove("spark-9")
    assert "No paired node named spark-9" in capsys.readouterr().out


# -- the forced command ------------------------------------------------------------------------------


def test_unpairing_through_the_forced_command_removes_only_the_connecting_keys_line(capsys):
    first, second = "ssh-ed25519 AAAAfirst a", "ssh-ed25519 AAAAsecond b"
    NodeIdentity.ensure()
    assert NodeServe.authorize(first) and NodeServe.authorize(second)
    assert NodeServe.serve("unpair", key_tag=NodeServe.key_tag(first)) == 0
    lines = NodeServe.authorized_keys().read_text()
    assert "AAAAfirst" not in lines and "AAAAsecond" in lines
    assert "unpaired" in capsys.readouterr().out


@pytest.mark.parametrize("tag", [None, "", "not-a-tag", "0123456789abcdef --key x"])
def test_unpairing_without_the_connecting_keys_tag_removes_nothing(tag, capsys):
    assert NodeServe.authorize("ssh-ed25519 AAAAfirst a")
    before = NodeServe.authorized_keys().read_text()
    assert NodeServe.serve("unpair", key_tag=tag) == 1
    assert NodeServe.authorized_keys().read_text() == before
    assert "no such key" in capsys.readouterr().out


def test_a_users_own_line_naming_the_same_tag_is_never_removed():
    tag = NodeServe.key_tag("ssh-ed25519 AAAAfirst a")
    path = NodeServe.authorized_keys()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"ssh-ed25519 AAAAown my laptop --key {tag}\n")
    assert not NodeServe.unauthorize(tag)
    assert "AAAAown" in path.read_text()


@pytest.mark.parametrize("key", ["", "ssh-ed25519", "not a key at all"])
def test_authorising_something_that_is_not_a_public_key_writes_nothing(key, capsys):
    assert not NodeServe.authorize(key)
    assert not NodeServe.authorized_keys().exists()
    assert "not a public key" in capsys.readouterr().out


@pytest.mark.parametrize(
    "request_",
    [
        "",
        "bash",
        "'unterminated",
        "info extra",
        "status now",
        "set-model a b",
        "model-receive m notanumber",
        "git-upload-pack",
        "job-shell x",
    ],
)
def test_anything_but_a_known_operation_is_refused(request_, monkeypatch, capsys):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("refused requests run nothing")
    )
    assert NodeServe.serve(request_) == 2
    assert "serve-job" in capsys.readouterr().err


def test_a_job_repository_git_cannot_create_is_reported(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **_: calls.append(argv) or answer(1, "", "disk full")
    )
    assert NodeServe.serve("git-receive-pack 'jobs/new-job.git'") == 1
    assert "could not create the job repository: disk full" in capsys.readouterr().err
    assert calls[0][:3] == ["git", "init", "--quiet"] and len(calls) == 1


def test_a_job_repository_is_created_before_the_first_push_is_received(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: calls.append(argv) or answer())
    assert NodeServe.serve("git-receive-pack '/jobs/new-job.git'") == 0
    assert [argv[0] for argv in calls] == ["git", "git-receive-pack"]
    assert calls[1][1].endswith("new-job.git")


def test_job_logs_are_followed_from_the_offset_asked_for(monkeypatch):
    from dreamference.node.node_job import NodeJob

    followed = []
    monkeypatch.setattr(NodeJob, "read", classmethod(lambda cls, job_id: {"id": job_id}))
    monkeypatch.setattr(
        NodeJob,
        "follow",
        classmethod(lambda cls, job_id, offset=0: followed.append((job_id, offset)) or 0),
    )
    assert NodeServe.serve("job-logs 20261029-1102-abc 4096") == 0
    assert followed == [("20261029-1102-abc", 4096)]


def test_forge_and_adapter_requests_go_to_their_own_subsystems(monkeypatch):
    from dreamference.forge.forge_serve import ForgeServe
    from dreamference.node import node_serve
    from dreamference.node.node_adapter_sync import NodeAdapterSync

    seen = []
    monkeypatch.setattr(
        ForgeServe, "serve", classmethod(lambda cls, op, args: seen.append((op, args)) or 0)
    )
    monkeypatch.setattr(
        NodeAdapterSync,
        "receive",
        classmethod(lambda cls, args: seen.append(("adapter", args)) or 0),
    )
    assert NodeServe.serve("forge-status") == 0
    assert NodeServe.serve(f"{node_serve.RECEIVE_OPERATION} name 12") == 0
    assert seen == [("forge-status", []), ("adapter", ["name", "12"])]


@pytest.mark.parametrize("output, expected", [("yes\n", True), ("no\n", False), ("", None)])
def test_lingering_is_read_from_loginctl(output, expected, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: answer(0, output))
    assert NodeServe.linger() is expected


def test_lingering_is_unknown_when_loginctl_cannot_run(monkeypatch):
    def missing(argv, **_):
        raise OSError("no loginctl")

    monkeypatch.setattr(subprocess, "run", missing)
    assert NodeServe.linger() is None


def test_a_ling_admin_that_cannot_start_is_reported_as_a_failure(monkeypatch, capsys):
    def missing(argv, **_):
        raise OSError("not found")

    monkeypatch.setattr(subprocess, "run", missing)
    assert NodeServe.run_admin(["status"]) == 1
    assert "could not run ling-admin" in capsys.readouterr().err


def test_info_reports_unknown_memory_when_it_cannot_be_read(monkeypatch):
    from dreamference.night_shift.night_shift_host import NightShiftHost

    def unreadable(cls):
        raise OSError("no /proc")

    monkeypatch.setattr(NightShiftHost, "mem_available_bytes", classmethod(unreadable))
    monkeypatch.setattr(NodeServe, "linger", classmethod(lambda cls: None))
    info = NodeServe.info()
    assert info["mem_available"] is None and info["mem_total"] is None
