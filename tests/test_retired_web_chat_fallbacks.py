"""The retired web chat's fallbacks: what removing and offering do when Docker fails or nothing is left."""

import pathlib
import subprocess

import pytest
from test_retired_web_chat import deployment, offer  # noqa: F401  (fixtures)

from dreamference.chat import retired_web_chat
from dreamference.chat.retired_web_chat import RetiredWebChat


def test_images_docker_could_not_list_are_none(deployment, monkeypatch):  # noqa: F811
    _, fake = deployment

    def images_fail(argv, **kwargs):
        if argv[:2] == ["docker", "images"]:
            return subprocess.CompletedProcess(
                argv, 1, "onyxdotapp/onyx-backend:v4.5 id-1\n", "daemon down\n"
            )
        return fake(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", images_fail)
    assert RetiredWebChat.images() == []


def test_with_no_data_left_nothing_is_asked_or_removed(deployment, capsys):  # noqa: F811
    _, fake = deployment
    fake.volumes, fake.images, fake.networks = [], [], []
    fake.sidecars = {}

    def ask(question):
        raise AssertionError("asked about data that is not there")

    assert RetiredWebChat._remove_data(False, ask) == 0
    assert fake.removing() == []
    assert "No Onyx data is left either." in capsys.readouterr().out


@pytest.mark.parametrize("failure", [OSError("no docker"), subprocess.TimeoutExpired("docker", 30)])
def test_no_offer_when_docker_cannot_list_the_containers(offer, monkeypatch, failure):  # noqa: F811
    fake, at_terminal = offer

    def docker_away(argv, **kwargs):
        if argv[:2] == ["docker", "ps"]:
            raise failure
        return fake(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", docker_away)
    assert at_terminal("status") == []
    assert fake.removing() == [] and not RetiredWebChat._declined()
    assert not pathlib.Path(retired_web_chat.DECISION_FILE).exists()


def test_a_decision_that_cannot_be_written_is_dropped_quietly(monkeypatch, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    monkeypatch.setattr(
        retired_web_chat, "DECISION_FILE", str(blocker / "dreamference" / "decision.json")
    )
    RetiredWebChat._remember_declined()
    assert not RetiredWebChat._declined() and blocker.read_text() == ""


def test_a_google_service_that_cannot_be_recreated_does_not_hold_the_network(
    deployment,  # noqa: F811
    monkeypatch,
    capsys,
):
    from dreamference.chat.google_service import GOOGLE_CONTAINER_NAME, GoogleService

    _, fake = deployment
    fake.sidecars[GOOGLE_CONTAINER_NAME] = "onyx_default"
    fake.on_network[GOOGLE_CONTAINER_NAME] = "onyx_default"
    monkeypatch.setattr(GoogleService, "problem", "Docker refused the port.", raising=False)
    monkeypatch.setattr(GoogleService, "start", classmethod(lambda cls: False))
    held = RetiredWebChat.release_network("onyx_default")
    assert GOOGLE_CONTAINER_NAME not in held
    assert ["docker", "rm", "-f", GOOGLE_CONTAINER_NAME] in fake.commands
    out = capsys.readouterr().out
    assert "Docker refused the port." in out and "`ling-admin google start` retries." in out
