"""`ling-admin matrix` (specs/DREAMFERENCE_MIGHTLING_CHAT.md §5) with Docker, systemd, Tailscale and the
homeserver replaced: `_run` and `_http` are the module's only ways out, and both are stand-ins here.
"""

import json
import os
import pathlib
import subprocess
from typing import Any

import pytest

from dreamference.chat import matrix_homeserver as mh
from dreamference.chat.matrix_homeserver import MatrixHomeserver


class Machine:
    """A scripted Docker, systemd, Tailscale and tuwunel."""

    def __init__(self, tailnet_name: str = "node.tail1234.ts.net") -> None:
        self.tailnet_name = tailnet_name
        self.runs: list[list[str]] = []
        self.environments: list[dict[str, str] | None] = []
        self.http: list[tuple[str, str, dict[str, Any] | None]] = []
        self.network = False
        self.container = False
        self.registered: list[str] = []

    def run(
        self, argv: list[str], timeout: int = 120, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        self.runs.append(argv)
        self.environments.append(env)
        out, code = "", 0
        if argv[:3] == ["tailscale", "status", "--json"]:
            if self.tailnet_name:
                out = json.dumps(
                    {"BackendState": "Running", "Self": {"DNSName": self.tailnet_name + "."}}
                )
            else:
                code = 1
        elif argv[:4] == ["docker", "network", "inspect", mh.MATRIX_NETWORK]:
            code = 0 if self.network else 1
        elif argv[:3] == ["docker", "network", "create"]:
            self.network = True
        elif argv[:2] == ["docker", "inspect"] and argv[-1] == mh.MATRIX_CONTAINER:
            if not self.container:
                code = 1
            elif "-f" in argv and "IPAddress" in argv[3]:
                out = mh.MATRIX_ADDRESS + " "
            elif "-f" in argv:
                out = "true"
        elif argv[:2] == ["docker", "run"]:
            self.container = True
        elif argv[:3] == ["docker", "rm", "-f"]:
            self.container = False
        return subprocess.CompletedProcess(argv, code, out, "")

    def request(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self.http.append((method, url, body))
        assert url.startswith(mh.PROXY_URL), url
        if url.endswith("/versions"):
            return 200, {"versions": ["v1.11"]}
        if url.endswith("/whoami"):
            return (
                (200, {"user_id": f"@mightling:{self.tailnet_name}"})
                if token == "bot-token"
                else (401, {})
            )
        if url.endswith("/register"):
            auth = (body or {}).get("auth")
            if not auth:
                return 401, {
                    "session": "s1",
                    "flows": [{"stages": ["m.login.registration_token"]}],
                    "completed": [],
                }
            assert auth["type"] == "m.login.registration_token" and auth["session"] == "s1"
            if (
                auth["token"]
                != json.load(pathlib.Path(MatrixHomeserver._path("matrix-admin.json")).open())[
                    "registration_token"
                ]
            ):
                return 401, {"errcode": "M_FORBIDDEN"}
            localpart = body["username"]
            self.registered.append(localpart)
            token = "bot-token" if localpart == mh.BOT_LOCALPART else f"{localpart}-token"
            return 200, {"user_id": f"@{localpart}:{self.tailnet_name}", "access_token": token}
        return 404, {}

    def argv(self, *prefix: str) -> list[list[str]]:
        return [argv for argv in self.runs if argv[: len(prefix)] == list(prefix)]


@pytest.fixture
def machine(monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    fake = Machine()
    monkeypatch.setattr(
        MatrixHomeserver,
        "_run",
        classmethod(lambda cls, argv, timeout=120, env=None: fake.run(argv, timeout, env)),
    )
    monkeypatch.setattr(
        MatrixHomeserver,
        "_http",
        classmethod(
            lambda cls, method, url, body=None, token=None: fake.request(method, url, body, token)
        ),
    )
    monkeypatch.setattr(
        "dreamference.node.node_identity.NodeIdentity.read", classmethod(lambda cls: "node-1")
    )
    monkeypatch.setattr(mh.time, "sleep", lambda seconds: None)
    # The sidecar network is ensured with docker calls of its own (not `_run`): on a machine that has
    # the network it is a read, on CI it is `docker network create`, which conftest refuses (2026-10-10).
    monkeypatch.setattr(
        "dreamference.chat.sidecar_network.SidecarNetwork.ensure", classmethod(lambda cls: True)
    )
    return fake


def test_start_builds_a_homeserver_with_no_route_out_and_a_bot_account(machine, capsys):
    assert MatrixHomeserver.start() == 0
    created = machine.argv("docker", "network", "create")[0]
    assert "--internal" in created and mh.MATRIX_SUBNET in created
    run = machine.argv("docker", "run")[0]
    assert run[run.index("--network") + 1] == mh.MATRIX_NETWORK
    assert run[run.index("--ip") + 1] == mh.MATRIX_ADDRESS
    assert "--memory=1g" in run and "--memory-swap=1g" in run
    env = [run[i + 1] for i, arg in enumerate(run) if arg == "-e"]
    assert "TUWUNEL_SERVER_NAME=node.tail1234.ts.net" in env
    assert "TUWUNEL_ALLOW_FEDERATION=false" in env
    # The registration token reaches the container through docker's environment, never its argv:
    # any local user can read a process's argv in /proc.
    assert "TUWUNEL_REGISTRATION_TOKEN" in env
    token = machine.environments[machine.runs.index(run)]["TUWUNEL_REGISTRATION_TOKEN"]
    assert len(token) > 30
    assert not [arg for argv in machine.runs for arg in argv if token in arg]
    assert run[-1] == mh.MATRIX_IMAGE and "@sha256:" in mh.MATRIX_IMAGE
    # The loopback proxy points at the container.
    service = (
        pathlib.Path(os.path.join(MatrixHomeserver.unit_dir(), mh.PROXY_SERVICE_UNIT)).open().read()
    )
    assert f"ExecStart={mh.SOCKET_PROXYD} {mh.MATRIX_ADDRESS}:{mh.MATRIX_PORT}" in service
    socket = (
        pathlib.Path(os.path.join(MatrixHomeserver.unit_dir(), mh.PROXY_SOCKET_UNIT)).open().read()
    )
    assert "ListenStream=127.0.0.1:6167" in socket
    # tailscale serve fronts the proxy, never the container's address.
    assert machine.argv("tailscale", "serve")[0] == [
        "tailscale",
        "serve",
        "--bg",
        "--https=443",
        mh.PROXY_URL,
    ]
    config = json.load(pathlib.Path(MatrixHomeserver._path("matrix.json")).open())
    assert config == {
        "user_id": "@mightling:node.tail1234.ts.net",
        "access_token": "bot-token",
        "homeserver": mh.PROXY_URL,
        "server_name": "node.tail1234.ts.net",
        "allowed": [],
    }
    assert os.stat(MatrixHomeserver._path("matrix.json")).st_mode & 0o777 == 0o600
    assert os.stat(MatrixHomeserver.chat_dir()).st_mode & 0o777 == 0o700
    assert "add-user" in capsys.readouterr().out


def test_a_second_start_reuses_the_container_and_the_bot(machine):
    assert MatrixHomeserver.start() == 0
    assert MatrixHomeserver.start() == 0
    assert len(machine.argv("docker", "run")) == 1
    assert machine.argv("docker", "start", mh.MATRIX_CONTAINER)
    assert machine.registered == [mh.BOT_LOCALPART]


def test_start_refuses_without_a_node_or_without_tailscale(machine, monkeypatch, capsys):
    machine.tailnet_name = ""
    assert MatrixHomeserver.start() == 1
    assert "tailscale up" in capsys.readouterr().out
    assert not machine.argv("docker", "run")
    monkeypatch.setattr(
        "dreamference.node.node_identity.NodeIdentity.read", classmethod(lambda cls: None)
    )
    machine.tailnet_name = "node.tail1234.ts.net"
    assert MatrixHomeserver.start() == 1
    assert "node" in capsys.readouterr().out
    assert not machine.argv("docker", "run")


def test_the_server_name_is_chosen_once_and_a_renamed_machine_is_refused(machine, capsys):
    assert MatrixHomeserver.start() == 0
    machine.tailnet_name = "renamed.tail1234.ts.net"
    machine.runs.clear()
    capsys.readouterr()
    assert MatrixHomeserver.start() == 1
    out = capsys.readouterr().out
    assert "node.tail1234.ts.net" in out and "renamed.tail1234.ts.net" in out
    assert not machine.argv("docker")


def test_add_user_prints_the_password_once_and_stores_none(machine, capsys):
    assert MatrixHomeserver.start() == 0
    capsys.readouterr()
    assert MatrixHomeserver.add_user("owner") == 0
    out = capsys.readouterr().out
    register = [
        body
        for method, url, body in machine.http
        if url.endswith("/register") and body and body.get("username") == "owner"
    ]
    password = register[-1]["password"]
    assert password in out
    for name in os.listdir(MatrixHomeserver.chat_dir()):
        assert password not in pathlib.Path(MatrixHomeserver._path(name)).open().read(), name
    assert json.load(pathlib.Path(MatrixHomeserver._path("matrix.json")).open())["allowed"] == [
        "@owner:node.tail1234.ts.net"
    ]
    assert MatrixHomeserver.add_user("Bad Name") == 2


def test_push_moves_the_container_to_the_sidecar_network(machine, capsys):
    assert MatrixHomeserver.start() == 0
    machine.runs.clear()
    assert MatrixHomeserver.set_push(True) == 0
    assert machine.argv("docker", "rm", "-f", mh.MATRIX_CONTAINER)
    run = machine.argv("docker", "run")[0]
    assert run[run.index("--network") + 1] == "dreamference-sidecars"
    assert "--ip" not in run
    assert "push" in capsys.readouterr().out.lower()


def test_remove_needs_yes_and_then_deletes_everything(machine):
    assert MatrixHomeserver.start() == 0
    machine.runs.clear()
    assert MatrixHomeserver.remove(False) == 1
    assert not machine.runs
    assert MatrixHomeserver.remove(True) == 0
    assert machine.argv("docker", "volume", "rm", mh.MATRIX_VOLUME)
    assert machine.argv("docker", "network", "rm", mh.MATRIX_NETWORK)
    assert not pathlib.Path(MatrixHomeserver._path("matrix.json")).exists()
    assert not pathlib.Path(MatrixHomeserver._path("matrix-admin.json")).exists()


def test_stop_turns_it_off_until_the_next_start(machine):
    # Off means off across a reboot: the socket is disabled, not only stopped, and the tailnet no
    # longer offers the name. The accounts stay.
    assert MatrixHomeserver.start() == 0
    machine.runs.clear()
    assert MatrixHomeserver.stop() == 0
    assert machine.argv("tailscale", "serve", "--https=443", "off")
    assert machine.argv("systemctl", "--user", "disable", "--now", mh.PROXY_SOCKET_UNIT)
    assert machine.argv("docker", "stop", mh.MATRIX_CONTAINER)
    assert not any("volume" in argv for argv in machine.runs)
    assert pathlib.Path(MatrixHomeserver._path("matrix-admin.json")).exists()


def test_the_cli_routes_matrix_commands(machine, monkeypatch):
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

    called = []
    monkeypatch.setattr(
        MatrixHomeserver, "add_user", classmethod(lambda cls, name: called.append(name) or 0)
    )
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["matrix", "add-user", "owner"])
    assert exit_info.value.code == 0
    assert called == ["owner"]


# --- the seams themselves ------------------------------------------------------------------


def test_a_missing_program_is_a_failed_step_not_an_exception(monkeypatch):
    def gone(*args, **kwargs):
        raise FileNotFoundError("tailscale")

    monkeypatch.setattr(mh.subprocess, "run", gone)
    result = MatrixHomeserver._run(["tailscale", "status"])
    assert result.returncode == 127 and "tailscale" in result.stderr


class _Reply:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_an_http_request_carries_the_bearer_token_and_json_body(monkeypatch):
    seen = {}

    def urlopen(request, timeout=None):
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = request.data
        return _Reply(200, b"")

    monkeypatch.setattr(mh.urllib.request, "urlopen", urlopen)
    assert MatrixHomeserver._http("POST", mh.PROXY_URL + "/x", {"a": 1}, token="t0k") == (200, {})
    assert seen["auth"] == "Bearer t0k" and json.loads(seen["body"]) == {"a": 1}


def test_an_http_error_with_a_json_body_returns_the_body(monkeypatch):
    import io
    import urllib.error

    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 403, "no", {}, io.BytesIO(b'{"errcode":"M_FORBIDDEN"}')
        )

    monkeypatch.setattr(mh.urllib.request, "urlopen", urlopen)
    assert MatrixHomeserver._http("GET", mh.PROXY_URL + "/x") == (403, {"errcode": "M_FORBIDDEN"})


def test_an_http_error_with_an_html_body_returns_an_empty_answer(monkeypatch):
    import io
    import urllib.error

    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 502, "bad", {}, io.BytesIO(b"<html>bad gateway")
        )

    monkeypatch.setattr(mh.urllib.request, "urlopen", urlopen)
    assert MatrixHomeserver._http("GET", mh.PROXY_URL + "/x") == (502, {})


def test_an_unreachable_or_non_json_homeserver_answers_status_zero(monkeypatch):
    import urllib.error

    def refused(request, timeout=None):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(mh.urllib.request, "urlopen", refused)
    assert MatrixHomeserver._http("GET", mh.PROXY_URL + "/x") == (0, {})
    monkeypatch.setattr(
        mh.urllib.request, "urlopen", lambda r, timeout=None: _Reply(200, b"<html>")
    )
    assert MatrixHomeserver._http("GET", mh.PROXY_URL + "/x") == (0, {})


def test_a_corrupt_or_non_object_config_file_reads_as_empty(machine):
    pathlib.Path(MatrixHomeserver.chat_dir()).mkdir(exist_ok=True, parents=True)
    for text in ("{broken", "[1, 2]", ""):
        with pathlib.Path(MatrixHomeserver._path("matrix.json")).open("w") as handle:
            handle.write(text)
        assert MatrixHomeserver._load("matrix.json") == {}


def test_a_saved_file_replaces_the_old_one_whole_and_leaves_no_staging_file(machine):
    MatrixHomeserver._save("matrix.json", {"a": 1})
    MatrixHomeserver._save("matrix.json", {"b": 2})
    assert MatrixHomeserver._load("matrix.json") == {"b": 2}
    assert [p.name for p in pathlib.Path(MatrixHomeserver.chat_dir()).iterdir()] == ["matrix.json"]


# --- Tailscale and Docker answers ----------------------------------------------------------


def _stub_run(monkeypatch, stdout="", returncode=0):
    monkeypatch.setattr(
        MatrixHomeserver,
        "_run",
        classmethod(
            lambda cls, argv, timeout=120: subprocess.CompletedProcess(argv, returncode, stdout, "")
        ),
    )


def test_a_tailscale_that_is_stopped_or_unreadable_gives_no_name(monkeypatch):
    for out in (
        "not json",
        json.dumps({"BackendState": "Stopped", "Self": {"DNSName": "n.ts.net."}}),
        json.dumps({"BackendState": "Running", "Self": None}),
        json.dumps({"BackendState": "Running", "Self": {"DNSName": "."}}),
    ):
        _stub_run(monkeypatch, out)
        assert MatrixHomeserver.tailscale_name() is None


def test_a_tailscale_that_cannot_run_gives_no_name(monkeypatch):
    def broken(cls, argv, timeout=120):
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(MatrixHomeserver, "_run", classmethod(broken))
    assert MatrixHomeserver.tailscale_name() is None


def test_the_container_address_is_none_when_docker_says_nothing(monkeypatch):
    _stub_run(monkeypatch, "  \n")
    assert MatrixHomeserver.container_address() is None
    _stub_run(monkeypatch, "172.20.0.2 ", returncode=1)
    assert MatrixHomeserver.container_address() is None
    _stub_run(monkeypatch, "172.20.0.2 ")
    assert MatrixHomeserver.container_address() == "172.20.0.2"


def test_a_stopped_container_is_not_running(monkeypatch):
    _stub_run(monkeypatch, "false\n")
    assert MatrixHomeserver.container_running() is False


def test_the_push_command_joins_the_sidecar_network_without_a_fixed_address():
    from dreamference.chat.sidecar_network import SIDECAR_NETWORK

    argv = MatrixHomeserver.run_command("n.ts.net", push=True)
    assert argv[argv.index("--network") + 1] == SIDECAR_NETWORK and "--ip" not in argv
    assert "TUWUNEL_ALLOW_FEDERATION=false" in argv


# --- account creation ----------------------------------------------------------------------


def _scripted_http(monkeypatch, answers):
    sent = []

    def request(cls, method, url, body=None, token=None):
        sent.append(body)
        return answers[min(len(sent), len(answers)) - 1]

    monkeypatch.setattr(MatrixHomeserver, "_http", classmethod(request))
    return sent


def test_registration_answers_the_dummy_stage_after_the_token_stage(monkeypatch):
    sent = _scripted_http(
        monkeypatch,
        [
            (401, {"session": "s"}),
            (401, {"session": "s", "completed": ["m.login.registration_token"]}),
            (200, {"user_id": "@a:x", "access_token": "t"}),
        ],
    )
    assert MatrixHomeserver.register("a", "pw", "reg-token")["access_token"] == "t"
    assert sent[1]["auth"] == {
        "type": "m.login.registration_token",
        "session": "s",
        "token": "reg-token",
    }
    assert sent[2]["auth"] == {"type": "m.login.dummy", "session": "s"}


def test_registration_gives_up_on_a_refusal_that_is_not_a_challenge(monkeypatch):
    _scripted_http(monkeypatch, [(400, {"errcode": "M_USER_IN_USE"})])
    assert MatrixHomeserver.register("a", "pw", "tok") is None
    _scripted_http(monkeypatch, [(401, {"errcode": "M_FORBIDDEN"})])  # 401 without a session
    assert MatrixHomeserver.register("a", "pw", "tok") is None
    _scripted_http(monkeypatch, [(0, {})])
    assert MatrixHomeserver.register("a", "pw", "tok") is None


def test_registration_stops_after_three_stages_that_never_finish(monkeypatch):
    sent = _scripted_http(monkeypatch, [(401, {"session": "s"})])
    assert MatrixHomeserver.register("a", "pw", "tok") is None
    assert len(sent) == 4  # the first request plus three answers


def test_a_generated_password_is_twenty_characters_in_four_groups():
    password = MatrixHomeserver.password()
    groups = password.split("-")
    assert len(groups) == 4 and all(len(group) == 5 and group.isalnum() for group in groups)


def test_waiting_for_the_homeserver_gives_up_at_the_deadline(monkeypatch):
    clock = iter(range(1000))
    monkeypatch.setattr(mh.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(mh.time, "sleep", lambda s: None)
    monkeypatch.setattr(MatrixHomeserver, "_http", classmethod(lambda cls, *a, **k: (0, {})))
    assert MatrixHomeserver.wait_until_answering(seconds=3) is False


# --- start's failure paths -----------------------------------------------------------------


def test_start_stops_when_the_internal_network_cannot_be_created(machine, monkeypatch, capsys):
    monkeypatch.setattr(MatrixHomeserver, "ensure_network", classmethod(lambda cls: False))
    assert MatrixHomeserver.start() == 1
    assert "internal Docker network" in capsys.readouterr().out
    assert not machine.argv("docker", "run")


def test_start_reports_why_the_container_did_not_start(machine, monkeypatch, capsys):
    real = machine.run

    def run(argv, timeout=120, env=None):
        if argv[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(argv, 125, "", "no space left on device")
        return real(argv, timeout, env)

    machine.run = run
    assert MatrixHomeserver.start() == 1
    assert "no space left" in capsys.readouterr().out


def test_start_stops_when_the_loopback_proxy_will_not_start(machine, monkeypatch, capsys):
    monkeypatch.setattr(MatrixHomeserver, "install_proxy", classmethod(lambda cls, target: False))
    assert MatrixHomeserver.start() == 1
    assert "loopback proxy" in capsys.readouterr().out
    assert not machine.argv("tailscale", "serve")


def test_start_stops_when_the_homeserver_never_answers(machine, monkeypatch, capsys):
    monkeypatch.setattr(
        MatrixHomeserver, "wait_until_answering", classmethod(lambda cls, seconds=60: False)
    )
    assert MatrixHomeserver.start() == 1
    assert "not answering" in capsys.readouterr().out


def test_start_stops_when_the_bot_account_cannot_be_made(machine, monkeypatch, capsys):
    monkeypatch.setattr(MatrixHomeserver, "register", classmethod(lambda cls, *a: None))
    assert MatrixHomeserver.start() == 1
    assert "bot account" in capsys.readouterr().out
    assert not pathlib.Path(MatrixHomeserver._path("matrix.json")).exists()


def test_start_makes_a_new_bot_when_the_stored_token_no_longer_works(machine):
    MatrixHomeserver._save(
        "matrix.json", {"access_token": "stale", "user_id": "@old:x", "allowed": ["@me:x"]}
    )
    assert MatrixHomeserver.start() == 0
    config = MatrixHomeserver._load("matrix.json")
    assert config["access_token"] == "bot-token" and config["allowed"] == ["@me:x"]


def test_start_warns_but_succeeds_when_tailscale_serve_is_refused(machine, capsys):
    real = machine.run

    def run(argv, timeout=120, env=None):
        if argv[:2] == ["tailscale", "serve"]:
            return subprocess.CompletedProcess(argv, 1, "", "HTTPS not enabled")
        return real(argv, timeout, env)

    machine.run = run
    assert MatrixHomeserver.start() == 0
    out = capsys.readouterr().out
    assert "HTTPS not enabled" in out and "is running as" in out


# --- the other commands --------------------------------------------------------------------


def test_add_user_refuses_names_that_are_not_plain_local_parts(machine, capsys):
    for bad in ("Owner", "a b", "", "x" * 65, "a:b", "../x"):
        assert MatrixHomeserver.add_user(bad) == 2
    assert not machine.http


def test_add_user_needs_a_homeserver_first(machine, capsys):
    assert MatrixHomeserver.add_user("owner") == 1
    assert "matrix start" in capsys.readouterr().out
    assert not machine.http


def test_add_user_reports_a_name_that_could_not_be_created(machine, monkeypatch, capsys):
    assert MatrixHomeserver.start() == 0
    monkeypatch.setattr(MatrixHomeserver, "register", classmethod(lambda cls, *a: None))
    assert MatrixHomeserver.add_user("owner") == 1
    assert "taken" in capsys.readouterr().out


def test_adding_the_same_user_twice_allows_it_once(machine):
    assert MatrixHomeserver.start() == 0
    assert MatrixHomeserver.add_user("owner") == 0
    assert MatrixHomeserver.add_user("owner") == 0
    assert MatrixHomeserver._load("matrix.json")["allowed"] == ["@owner:node.tail1234.ts.net"]


def test_push_needs_a_homeserver_first(machine, capsys):
    assert MatrixHomeserver.set_push(True) == 1
    assert not machine.runs


def test_status_before_setup_says_so(machine, capsys):
    assert MatrixHomeserver.status() == 0
    assert "not set up" in capsys.readouterr().out


def test_status_names_the_server_the_bot_and_who_is_allowed(machine, capsys):
    assert MatrixHomeserver.start() == 0
    assert MatrixHomeserver.add_user("owner") == 0
    capsys.readouterr()
    assert MatrixHomeserver.status() == 0
    out = capsys.readouterr().out
    assert "node.tail1234.ts.net" in out and "internal, no route out" in out
    assert "@owner:node.tail1234.ts.net" in out and "running" in out


def test_remove_still_finishes_when_the_files_are_already_gone(machine):
    assert MatrixHomeserver.remove(True) == 0
    assert machine.argv("docker", "rm", "-f", mh.MATRIX_CONTAINER)


def test_a_bot_token_the_homeserver_did_not_answer_for_is_kept(machine):
    # Only a 401 says the token is gone; a reply that never came must not re-register the bot,
    # which the homeserver refuses since the name is taken.
    assert MatrixHomeserver.start() == 0
    answer = machine.request

    def unreachable_whoami(method, url, body=None, token=None):
        return (0, {}) if url.endswith("/whoami") else answer(method, url, body, token)

    machine.request = unreachable_whoami
    assert MatrixHomeserver.start() == 0
    assert machine.registered == [mh.BOT_LOCALPART]


# --- what the mutation run found unpinned (2026-10-30) ---------------------------------------


def test_push_off_puts_the_container_back_on_the_internal_network(machine, capsys):
    # Push off is the way back to no route out: the setting is saved off, and the new container
    # joins the internal network at its fixed address again.
    assert MatrixHomeserver.start() == 0
    assert MatrixHomeserver.set_push(True) == 0
    machine.runs.clear()
    assert MatrixHomeserver.set_push(False) == 0
    assert MatrixHomeserver._load("matrix-admin.json")["push"] is False
    run = machine.argv("docker", "run")[0]
    assert run[run.index("--network") + 1] == mh.MATRIX_NETWORK
    assert run[run.index("--ip") + 1] == mh.MATRIX_ADDRESS
    capsys.readouterr()
    assert MatrixHomeserver.status() == 0
    assert "internal, no route out" in capsys.readouterr().out
