"""The control plane's seams and refusals (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §2).

Nothing here reaches Docker, systemd or a socket: `subprocess.run`, the HTTPS connection and
`socket.create_connection` are replaced, and the node's folder is the test's own HOME.
"""

import http.client
import json
import ssl
import stat
import subprocess

import pytest

from dreamference.remote import RemoteControlPlane, RemoteSettings

BOX = "remote.example.org"


def _done(argv, out="", code=0, err=""):
    return subprocess.CompletedProcess(argv, code, out, err)


class _Answer:
    def __init__(self, status, raw):
        self.status = status
        self._raw = raw

    def read(self):
        return self._raw


def _connection(monkeypatch, status=200, raw=b"{}", fail=None, seen=None):
    """Replaces the HTTPS connection with one that answers `raw`, or raises `fail`."""

    class Fake:
        def __init__(self, port, server_name, context, timeout):
            if seen is not None:
                seen.update(port=port, server_name=server_name, timeout=timeout)
            self.closed = False

        def request(self, method, path, body=None, headers=None):
            if seen is not None:
                seen.update(method=method, path=path, body=body, headers=headers)
            if fail is not None:
                raise fail

        def getresponse(self):
            return _Answer(status, raw)

        def close(self):
            if seen is not None:
                seen["closed"] = True

    monkeypatch.setattr(RemoteControlPlane, "_Connection", Fake)


def _api(monkeypatch, replies):
    """Replaces `_request` with a scripted one; returns the calls it saw."""
    calls = []

    def fake(cls, dns_name, method, path, body=None, token=None):
        calls.append((method, path, body, token))
        return replies(method, path) if callable(replies) else replies

    monkeypatch.setattr(RemoteControlPlane, "_request", classmethod(fake))
    return calls


# -- the seams


def test_a_missing_docker_or_a_timeout_is_a_failed_command_not_a_raise(monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", missing)
    result = RemoteControlPlane._run(["docker", "ps"])
    assert result.returncode == 127 and "docker" in result.stderr

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(subprocess, "run", slow)
    assert RemoteControlPlane._run(["docker", "ps"]).returncode == 127


def test_the_connection_checks_the_certificate_against_the_boxs_name_not_loopback(monkeypatch):
    seen = {}

    class Context:
        def wrap_socket(self, raw, server_hostname=None):
            seen["hostname"] = server_hostname
            return "wrapped"

    monkeypatch.setattr(
        "dreamference.remote.remote_control_plane.socket.create_connection",
        lambda address, timeout: seen.update(address=address) or "raw",
    )
    connection = RemoteControlPlane._Connection(33443, BOX, Context(), timeout=5)
    connection.connect()
    assert seen["address"] == ("127.0.0.1", 33443)
    assert seen["hostname"] == BOX
    assert connection.sock == "wrapped"


def test_a_request_is_json_with_the_token_and_the_connection_is_closed(monkeypatch):
    seen = {}
    _connection(monkeypatch, 200, b'{"ok": true}', seen=seen)
    status, body = RemoteControlPlane._request(BOX, "POST", "/api/x", {"a": 1}, token="tok")
    assert (status, body) == (200, {"ok": True})
    assert seen["server_name"] == BOX and seen["port"] == 33443
    assert seen["headers"]["Authorization"] == "Token tok"
    assert json.loads(seen["body"]) == {"a": 1}
    assert seen["closed"]


def test_a_request_without_a_token_sends_no_authorization(monkeypatch):
    seen = {}
    _connection(monkeypatch, 200, b"", seen=seen)
    assert RemoteControlPlane._request(BOX, "GET", "/api/instance") == (200, {})
    assert "Authorization" not in seen["headers"] and seen["body"] is None


@pytest.mark.parametrize(
    "fail",
    [
        ConnectionRefusedError("refused"),
        ssl.SSLError("bad certificate"),
        http.client.BadStatusLine("x"),
    ],
)
def test_an_unreachable_or_untrusted_management_is_status_zero_not_a_raise(monkeypatch, fail):
    seen = {}
    _connection(monkeypatch, fail=fail, seen=seen)
    status, body = RemoteControlPlane._request(BOX, "GET", "/api/instance")
    assert status == 0 and "error" in body
    assert seen["closed"], "the connection is closed after a failure too"


def test_a_reply_that_is_not_json_is_kept_as_a_short_error_text(monkeypatch):
    _connection(monkeypatch, 502, b"<html>" + b"x" * 1000 + b"\xff")
    status, body = RemoteControlPlane._request(BOX, "GET", "/api/instance")
    assert status == 502 and body["error"].startswith("<html>") and len(body["error"]) <= 300


# -- the container


def _commands(monkeypatch, fails=(), stderr=""):
    """Replaces `_run`; a command whose argv contains one of `fails`' words fails."""
    calls = []

    def fake(cls, argv, timeout=300):
        calls.append(argv)
        bad = any(all(word in argv for word in failing.split()) for failing in fails)
        return _done(argv, code=1 if bad else 0, err=stderr if bad else "")

    monkeypatch.setattr(RemoteControlPlane, "_run", classmethod(fake))
    return calls


def test_a_start_stops_when_the_internal_network_cannot_be_made(monkeypatch):
    calls = _commands(monkeypatch, fails=("network inspect", "network create"), stderr="boom")
    assert RemoteControlPlane.start() is False
    assert not any(argv[:2] == ["docker", "run"] for argv in calls), (
        "no container without its network"
    )


def test_a_start_stops_when_docker_run_fails(monkeypatch):
    monkeypatch.setattr(RemoteControlPlane, "geolocation_ready", classmethod(lambda cls: True))
    monkeypatch.setattr(
        RemoteControlPlane, "install_proxy", classmethod(lambda cls: pytest.fail("proxy"))
    )
    _commands(monkeypatch, fails=("run",), stderr="no image")
    assert RemoteControlPlane.start() is False


def test_a_start_without_the_loopback_proxy_is_a_failed_start(monkeypatch):
    monkeypatch.setattr(RemoteControlPlane, "geolocation_ready", classmethod(lambda cls: True))
    monkeypatch.setattr(RemoteControlPlane, "install_proxy", classmethod(lambda cls: False))
    _commands(monkeypatch)
    assert RemoteControlPlane.start() is False


def test_a_start_that_cannot_fetch_the_geolocation_databases_starts_nothing(monkeypatch):
    monkeypatch.setattr(RemoteControlPlane, "geolocation_ready", classmethod(lambda cls: False))
    monkeypatch.setattr(RemoteControlPlane, "prime_geolocation", classmethod(lambda cls: False))
    calls = _commands(monkeypatch)
    assert RemoteControlPlane.start() is False
    assert not any(argv[:2] == ["docker", "run"] for argv in calls)


def test_a_restart_that_docker_refuses_is_false(monkeypatch):
    _commands(monkeypatch, fails=("restart",), stderr="no such container")
    assert RemoteControlPlane.restart() is False


def test_the_geolocation_fetch_runs_on_the_default_bridge_without_a_setup_token(monkeypatch):
    calls = _commands(monkeypatch)
    ready = iter([False, True, True])
    monkeypatch.setattr(
        RemoteControlPlane, "geolocation_ready", classmethod(lambda cls: next(ready))
    )
    monkeypatch.setattr("dreamference.remote.remote_control_plane.time.sleep", lambda s: None)
    assert RemoteControlPlane.prime_geolocation(seconds=5) is True
    run = next(argv for argv in calls if argv[:2] == ["docker", "run"])
    assert run[run.index("--network") + 1] == "bridge"
    assert "--ip" not in run and "-p" not in run and "NB_SETUP_PAT_ENABLED=true" not in run
    assert run[run.index("--restart") + 1] == "no"
    assert calls[-1][:3] == ["docker", "rm", "-f"], "the container is removed once the files are in"


def test_a_geolocation_fetch_that_cannot_start_or_never_finishes_is_false(monkeypatch):
    _commands(monkeypatch, fails=("run",))
    assert RemoteControlPlane.prime_geolocation(seconds=0) is False
    calls = _commands(monkeypatch)
    monkeypatch.setattr(RemoteControlPlane, "geolocation_ready", classmethod(lambda cls: False))
    assert RemoteControlPlane.prime_geolocation(seconds=0) is False
    assert calls[-1][:3] == ["docker", "rm", "-f"], (
        "the container is removed even when nothing arrived"
    )


def test_the_proxy_install_is_false_when_any_systemctl_step_fails(monkeypatch):
    calls = _commands(monkeypatch, fails=("enable",))
    assert RemoteControlPlane.install_proxy() is False
    assert len(calls) == 3
    for name in RemoteControlPlane.proxy_units():
        assert (RemoteControlPlane.unit_dir() / name).is_file()


def test_removing_the_proxy_deletes_its_units_and_the_network(monkeypatch):
    _commands(monkeypatch)
    RemoteControlPlane.install_proxy()
    calls = _commands(monkeypatch)
    RemoteControlPlane.remove_proxy()
    for name in RemoteControlPlane.proxy_units():
        assert not (RemoteControlPlane.unit_dir() / name).exists()
    assert calls[-1][:3] == ["docker", "network", "rm"]
    RemoteControlPlane.remove_proxy()  # idempotent: no units, no raise


def test_stopping_an_absent_container_counts_as_stopped_but_another_error_does_not(monkeypatch):
    _commands(monkeypatch, fails=("rm",), stderr="Error: No such container: dreamference-remote")
    assert RemoteControlPlane.stop() is True
    _commands(monkeypatch, fails=("rm",), stderr="permission denied")
    assert RemoteControlPlane.stop() is False


def test_waiting_for_management_gives_none_after_the_deadline_and_reads_setup_required(monkeypatch):
    monkeypatch.setattr("dreamference.remote.remote_control_plane.time.sleep", lambda s: None)
    _api(monkeypatch, (0, {"error": "refused"}))
    assert RemoteControlPlane.wait_ready(BOX, seconds=0) is None
    _api(monkeypatch, (200, {"setup_required": True}))
    assert RemoteControlPlane.wait_ready(BOX, seconds=0) is True
    _api(monkeypatch, (200, {}))
    assert RemoteControlPlane.wait_ready(BOX, seconds=0) is False
    _api(monkeypatch, (200, ["not", "a", "dict"]))
    assert RemoteControlPlane.wait_ready(BOX, seconds=0) is None


# -- the management API


def test_a_refused_first_setup_gives_no_token_and_the_password_stays_private(monkeypatch):
    _api(monkeypatch, (403, {"error": "setup done"}))
    assert RemoteControlPlane.bootstrap(BOX, "node1") is None
    password = RemoteSettings.path("owner-password")
    assert stat.S_IMODE(password.stat().st_mode) == 0o600


def test_a_setup_reply_without_a_token_is_refused(monkeypatch):
    _api(monkeypatch, (200, {"user_id": "u1"}))
    assert RemoteControlPlane.bootstrap(BOX, "node1") is None


@pytest.mark.parametrize("body", [[], ["x"], "text", None, 5])
def test_a_reply_that_is_not_an_object_is_a_refusal_not_a_raise(monkeypatch, body):
    _api(monkeypatch, (200, body))
    assert RemoteControlPlane.bootstrap(BOX, "node1") is None
    assert RemoteControlPlane.renew_token(BOX, "tok", "u1") is None
    assert RemoteControlPlane.setup_key(BOX, "tok", "phone") is None


def test_a_setup_reply_missing_the_owners_id_is_refused_not_raised(monkeypatch):
    _api(monkeypatch, (200, {"personal_access_token": "pat"}))
    assert RemoteControlPlane.bootstrap(BOX, "node1") is None


def test_a_renewal_without_an_owner_in_the_user_list_gives_none(monkeypatch):
    _api(monkeypatch, (200, [{"id": "u2", "role": "admin"}, "junk", 3]))
    assert RemoteControlPlane.renew_token(BOX, "tok") is None
    _api(monkeypatch, (500, {"error": "x"}))
    assert RemoteControlPlane.renew_token(BOX, "tok") is None


def test_an_owner_entry_without_an_id_is_refused_not_raised(monkeypatch):
    _api(monkeypatch, (200, [{"role": "owner"}]))
    assert RemoteControlPlane.renew_token(BOX, "tok") is None


def test_a_refused_renewal_keeps_the_old_tokens(monkeypatch):
    calls = _api(monkeypatch, (401, {"error": "no"}))
    assert RemoteControlPlane.renew_token(BOX, "tok", "u1") is None
    assert [method for method, *_ in calls] == ["POST"], "nothing is deleted after a refusal"


def test_pruning_deletes_only_the_nodes_older_tokens_and_the_setup_token(monkeypatch):
    tokens = [
        {"id": "new", "name": "mightling-node-20261029"},
        {"id": "old", "name": "mightling-node-20250101"},
        {"id": "setup", "name": "setup-token"},
        {"id": "mine", "name": "my own automation"},
    ]
    calls = _api(monkeypatch, lambda method, path: (200, tokens if method == "GET" else {}))
    RemoteControlPlane.prune_tokens(BOX, "tok", "u1", "new")
    deleted = [path for method, path, *_ in calls if method == "DELETE"]
    assert deleted == ["/api/users/u1/tokens/old", "/api/users/u1/tokens/setup"]


def test_pruning_without_the_new_tokens_id_deletes_nothing(monkeypatch):
    calls = _api(monkeypatch, (200, [{"id": "a", "name": "mightling-node-1"}]))
    RemoteControlPlane.prune_tokens(BOX, "tok", "u1", None)
    assert calls == []


def test_a_token_list_that_is_not_a_list_is_left_alone(monkeypatch):
    calls = _api(monkeypatch, (200, {"error": "x"}))
    RemoteControlPlane.prune_tokens(BOX, "tok", "u1", "new")
    assert [method for method, *_ in calls] == ["GET"]


def test_a_token_list_with_junk_entries_is_pruned_without_a_raise(monkeypatch):
    calls = _api(
        monkeypatch, lambda method, path: (200, [None, "x", {"id": "o", "name": "setup-token"}])
    )
    RemoteControlPlane.prune_tokens(BOX, "tok", "u1", "new")
    assert [path for method, path, *_ in calls if method == "DELETE"] == ["/api/users/u1/tokens/o"]


def test_a_refused_setup_key_gives_none(monkeypatch):
    _api(monkeypatch, (403, {"error": "forbidden"}))
    assert RemoteControlPlane.setup_key(BOX, "tok", "phone") is None
    _api(monkeypatch, (200, {}))
    assert RemoteControlPlane.setup_key(BOX, "tok", "phone") is None


def test_a_setup_key_is_one_use_and_expires(monkeypatch):
    calls = _api(monkeypatch, (200, {"key": "K"}))
    assert RemoteControlPlane.setup_key(BOX, "tok", "phone", minutes=3) == "K"
    body = calls[0][2]
    assert body["usage_limit"] == 1 and body["type"] == "one-off" and body["expires_in"] == 180
    assert body["auto_groups"] == []


def test_the_peer_list_is_none_unless_management_answers_with_a_list(monkeypatch):
    _api(monkeypatch, (200, [{"id": "p"}]))
    assert RemoteControlPlane.peers(BOX, "tok") == [{"id": "p"}]
    _api(monkeypatch, (200, {"error": "x"}))
    assert RemoteControlPlane.peers(BOX, "tok") is None
    _api(monkeypatch, (0, {"error": "x"}))
    assert RemoteControlPlane.peers(BOX, "tok") is None


def test_a_peer_is_deleted_only_on_a_200(monkeypatch):
    calls = _api(monkeypatch, (200, {}))
    assert RemoteControlPlane.delete_peer(BOX, "tok", "p1") is True
    assert calls[0][:2] == ("DELETE", "/api/peers/p1")
    _api(monkeypatch, (404, {}))
    assert RemoteControlPlane.delete_peer(BOX, "tok", "p1") is False


def test_a_rewrite_without_the_secret_or_the_store_key_writes_nothing():
    assert RemoteControlPlane.rewrite_config(BOX) is False
    RemoteSettings.write_private("relay-secret", "s")
    assert RemoteControlPlane.rewrite_config(BOX) is False
    assert not RemoteSettings.path("server", "config.yaml").exists()
