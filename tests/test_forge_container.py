"""`ForgeContainer`: docker commands, the API on loopback, and the waits and listings built on it.

No docker command runs and nothing is sent over the network: `subprocess.run` and the URL opener
are stand-ins that record what they were given.
"""

import http.client
import io
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from dreamference.forge import forge_container
from dreamference.forge.forge_container import LABEL, ForgeContainer


@pytest.fixture
def docker(monkeypatch):
    """Docker answering each command by its first words, recording every argv and timeout."""
    state = {"calls": [], "answers": {}}

    def run(argv, timeout=None, **_):
        state["calls"].append((argv[1:], timeout))
        for words, answer in state["answers"].items():
            if tuple(argv[1 : 1 + len(words)]) == words:
                return SimpleNamespace(returncode=answer[0], stdout=answer[1], stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(forge_container.subprocess, "run", run)
    return state


@pytest.mark.parametrize(
    "error", [FileNotFoundError("docker"), subprocess.TimeoutExpired(["docker"], 30)]
)
def test_docker_that_cannot_run_or_hangs_reads_as_exit_127_with_the_reason(monkeypatch, error):
    def broken(*_, **__):
        raise error

    monkeypatch.setattr(forge_container.subprocess, "run", broken)
    result = ForgeContainer.docker(["ps"])
    assert result.returncode == 127 and result.args == ["docker", "ps"]
    assert result.stderr == str(error)


def test_an_extra_mount_is_read_only_and_the_image_comes_last():
    argv = ForgeContainer.run_arguments(
        Path("/data"), [], extra_mounts=[("/host/patterns", "/etc/patterns")]
    )
    assert argv[argv.index("/host/patterns:/etc/patterns:ro") - 1] == "-v"
    assert argv[-1] == ForgeContainer.image


def test_the_forge_network_is_created_with_its_label_only_when_missing(docker):
    assert ForgeContainer.ensure_network()
    assert [argv[:2] for argv, _ in docker["calls"]] == [["network", "inspect"]]

    docker["calls"].clear()
    docker["answers"][("network", "inspect")] = (1, "")
    assert ForgeContainer.ensure_network()
    assert docker["calls"][1][0] == ["network", "create", "--label", LABEL, ForgeContainer.network]

    docker["answers"][("network", "create")] = (1, "")
    assert ForgeContainer.ensure_network() is False


def test_the_image_is_pulled_only_when_missing_and_a_failed_pull_fails(docker, capsys):
    assert ForgeContainer.ensure_image()
    assert not any(argv[0] == "pull" for argv, _ in docker["calls"])

    docker["answers"][("image", "inspect")] = (1, "")
    assert ForgeContainer.ensure_image()
    assert docker["calls"][-1] == (["pull", ForgeContainer.image], 1800)
    assert "Pulling the forge's image" in capsys.readouterr().out

    docker["answers"][("pull",)] = (1, "")
    assert ForgeContainer.ensure_image() is False


@pytest.mark.parametrize(
    ("answer", "running"), [((0, "true\n"), True), ((0, "false\n"), False), ((1, "true"), False)]
)
def test_the_container_runs_only_when_docker_says_true(docker, answer, running):
    docker["answers"][("inspect",)] = answer
    assert ForgeContainer.running() is running


def test_forgejo_runs_inside_the_named_container(docker):
    ForgeContainer.forgejo(["admin", "user", "list"], timeout=60)
    assert docker["calls"] == [
        (["exec", ForgeContainer.name, "forgejo", "admin", "user", "list"], 60)
    ]


# -- the API


class Response:
    def __init__(self, status, raw):
        self.status, self.raw = status, raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.raw


@pytest.fixture
def opener(monkeypatch):
    """The URL opener: records each request and the handlers it was built with."""
    state = {"requests": [], "handlers": [], "reply": Response(200, b"")}

    class Opener:
        def open(self, request, timeout):
            state["requests"].append(request)
            if isinstance(state["reply"], BaseException):
                raise state["reply"]
            return state["reply"]

    def build(*handlers):
        state["handlers"].append(handlers)
        return Opener()

    monkeypatch.setattr(forge_container.urllib.request, "build_opener", build)
    return state


def test_the_api_goes_to_loopback_past_any_proxy_with_the_token_only_when_given(opener):
    opener["reply"] = Response(201, b'{"id": 1}')

    assert ForgeContainer.api("POST", "/user/repos", "secret", {"name": "tools"}) == (
        201,
        {"id": 1},
    )

    request = opener["requests"][0]
    assert request.full_url == f"http://127.0.0.1:{ForgeContainer.http_port}/api/v1/user/repos"
    assert request.get_header("Authorization") == "token secret"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"name": "tools"}
    (handlers,) = opener["handlers"]
    assert [type(h) for h in handlers] == [urllib.request.ProxyHandler]
    assert handlers[0].proxies == {}, "a proxy from the environment would see the token"

    ForgeContainer.api("GET", "/version", None)
    anonymous = opener["requests"][1]
    assert anonymous.get_header("Authorization") is None
    assert anonymous.get_header("Content-type") is None and anonymous.data is None


def test_an_http_error_is_its_status_and_body(opener):
    opener["reply"] = urllib.error.HTTPError(
        "http://x", 403, "Forbidden", {}, io.BytesIO(b'{"message": "no"}')
    )
    assert ForgeContainer.api("GET", "/admin/users", "t") == (403, {"message": "no"})


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError("refused"), ValueError("bad url"), http.client.BadStatusLine("x")],
)
def test_a_forge_that_does_not_answer_is_status_0(opener, error):
    opener["reply"] = error
    assert ForgeContainer.api("GET", "/version", None) == (0, None)


@pytest.mark.parametrize(("raw", "body"), [(b"", None), (b"not json", "not json")])
def test_an_empty_reply_is_none_and_text_that_is_not_json_is_kept(opener, raw, body):
    opener["reply"] = Response(200, raw)
    assert ForgeContainer.api("GET", "/version", None) == (200, body)


@pytest.fixture
def clock(monkeypatch):
    """A clock that moves only when the wait sleeps."""
    state = {"now": 0.0, "sleeps": 0}

    def sleep(seconds):
        state["now"] += seconds
        state["sleeps"] += 1

    monkeypatch.setattr(forge_container.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(forge_container.time, "sleep", sleep)
    return state


def test_a_refusal_from_the_application_counts_as_answering(monkeypatch, clock):
    statuses = iter([0, 502, 401])
    monkeypatch.setattr(
        ForgeContainer, "api", classmethod(lambda cls, *a, **k: (next(statuses), None))
    )
    assert ForgeContainer.wait_until_answering(seconds=60) is True
    assert clock["sleeps"] == 2


def test_a_forge_that_never_answers_is_given_up_at_the_deadline(monkeypatch, clock):
    monkeypatch.setattr(ForgeContainer, "api", classmethod(lambda cls, *a, **k: (500, None)))
    assert ForgeContainer.wait_until_answering(seconds=10) is False
    assert clock["sleeps"] == 5


# -- listings


@pytest.fixture
def pages(monkeypatch):
    state = {"paths": [], "replies": []}

    def api(cls, method, path, token, body=None, timeout=0):
        state["paths"].append(path)
        return state["replies"].pop(0)

    monkeypatch.setattr(ForgeContainer, "api", classmethod(api))
    return state


def test_every_page_is_read_until_a_short_one(pages):
    pages["replies"] = [(200, [{"n": 1}, {"n": 2}]), (200, [{"n": 3}, {"n": 4}]), (200, [])]
    assert ForgeContainer.paginate("/repos/search?q=a", "t", limit=2) == [
        {"n": n} for n in (1, 2, 3, 4)
    ]
    assert pages["paths"] == [f"/repos/search?q=a&limit=2&page={page}" for page in (1, 2, 3)]


@pytest.mark.parametrize("reply", [(500, None), (200, {"data": []}), (0, None)])
def test_a_page_that_cannot_be_read_fails_the_whole_listing(pages, reply):
    pages["replies"] = [(200, [{"n": 1}]), reply]
    with pytest.raises(RuntimeError, match="did not list /orgs"):
        ForgeContainer.paginate("/orgs", "t", limit=1)
    assert pages["paths"][0] == "/orgs?limit=1&page=1"
