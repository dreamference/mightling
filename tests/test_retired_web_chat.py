"""Removing what the retired Onyx web chat left, in two separately confirmed steps
(specs/DREAMFERENCE_MIGHTLING_ASK.md §10, Phase C). Docker is a recorder; nothing is removed."""

import subprocess

import pytest

from dreamference.chat import retired_web_chat
from dreamference.chat.retired_web_chat import RetiredWebChat
from dreamference.chat.sidecar_network import SIDECAR_NETWORK


class FakeDocker:
    """The retired deployment as Docker would report it, shrinking as commands remove things."""

    def __init__(self):
        self.containers = ["puffin-api_server-1", "puffin-relational_db-1", "puffin-nginx-1"]
        self.volumes = ["onyx_db_volume", "onyx_file-system"]
        self.images = ["onyxdotapp/onyx-backend:v4.5", "onyxdotapp/onyx-web-server:v4.5", "postgres:15.2-alpine"]
        self.networks = ["onyx_default"]
        self.sidecars = {"dreamference-image-search": "onyx_default", "dreamference-stt": SIDECAR_NETWORK}
        self.commands = []

    def __call__(self, argv, **kwargs):
        argv = [str(a) for a in argv]
        self.commands.append(argv)
        out = ""
        if argv[:2] == ["docker", "ps"]:
            out = "\n".join(self.containers)
        elif argv[:3] == ["docker", "volume", "ls"]:
            out = "\n".join(self.volumes)
        elif argv[:3] == ["docker", "network", "ls"]:
            out = "\n".join(self.networks)
        elif argv[:2] == ["docker", "images"]:
            out = "\n".join(self.images)
        elif argv[:2] == ["docker", "inspect"]:
            mode = self.sidecars.get(argv[2])
            return subprocess.CompletedProcess(argv, 0 if mode else 1, (mode or "") + "\n", "")
        elif argv[:2] == ["docker", "compose"]:
            assert "-v" not in argv and "--volumes" not in argv and "--rmi" not in argv
            self.containers = []
        elif argv[:3] == ["docker", "rm", "-f"]:
            for name in argv[3:]:
                self.sidecars.pop(name, None)
                self.containers = [c for c in self.containers if c != name]
        elif argv[:3] == ["docker", "volume", "rm"]:
            self.volumes = [v for v in self.volumes if v not in argv[3:]]
        elif argv[:2] == ["docker", "rmi"]:
            self.images = [i for i in self.images if i not in argv[2:]]
        elif argv[:3] == ["docker", "network", "rm"]:
            self.networks = [n for n in self.networks if n not in argv[3:]]
        return subprocess.CompletedProcess(argv, 0, out + "\n", "")

    def removing(self):
        return [c for c in self.commands if c[1] in ("compose", "rm", "rmi") or c[1:3] in (["volume", "rm"], ["network", "rm"])]


@pytest.fixture
def deployment(monkeypatch, tmp_path):
    folder = tmp_path / "onyx" / "deployment"
    folder.mkdir(parents=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    (folder / ".env").write_text('HOST_PORT="127.0.0.1:3000"\n')
    monkeypatch.setattr(retired_web_chat, "ONYX_DEPLOYMENT_DIR", str(folder))
    monkeypatch.setattr(retired_web_chat, "DECISION_FILE", str(tmp_path / "decision.json"))
    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    return folder, fake


def test_a_no_removes_nothing(deployment):
    _, fake = deployment
    assert RetiredWebChat.remove(ask=lambda question: False) == 0
    assert fake.removing() == []


def test_the_first_yes_removes_the_containers_and_keeps_every_byte_of_data(deployment, capsys):
    folder, fake = deployment
    answers = iter([True, False])
    assert RetiredWebChat.remove(ask=lambda question: next(answers)) == 0
    (down,) = [c for c in fake.commands if c[:2] == ["docker", "compose"]]
    assert down[-2:] == ["down", "--remove-orphans"] and ["-p", "onyx"] == down[down.index("-p"):down.index("-p") + 2]
    assert fake.containers == []
    assert fake.volumes and fake.images and fake.networks and "dreamference-image-search" in fake.sidecars
    assert (folder / ".env").exists()
    assert "chat remove --delete-data" in capsys.readouterr().out


def test_the_second_yes_deletes_the_data_onyx_images_and_the_sidecars_left_on_its_network(deployment):
    _, fake = deployment
    assert RetiredWebChat.remove(ask=lambda question: True) == 0
    assert fake.volumes == [] and fake.networks == []
    assert fake.images == ["postgres:15.2-alpine"]          # a generic image others may use stays
    assert fake.sidecars == {"dreamference-stt": SIDECAR_NETWORK}  # already moved: kept


def test_flags_answer_for_a_script(deployment):
    _, fake = deployment

    def never(question):
        raise AssertionError("asked although a flag answered")

    assert RetiredWebChat.remove(yes=True, delete_data=True, ask=never) == 0
    assert fake.containers == [] and fake.volumes == []


def test_without_compose_files_the_containers_go_by_name_still_without_volumes(deployment):
    folder, fake = deployment
    (folder / "docker-compose.yml").unlink()
    assert RetiredWebChat.remove(yes=True, ask=lambda question: False) == 0
    assert ["docker", "rm", "-f", "puffin-api_server-1", "puffin-relational_db-1", "puffin-nginx-1"] in fake.commands
    assert fake.volumes


def test_the_offer_is_made_once_and_only_at_a_terminal(deployment, monkeypatch):
    from conftest import REAL_RETIRED_OFFER

    _, fake = deployment
    monkeypatch.setattr(RetiredWebChat, "offer", REAL_RETIRED_OFFER)
    asked = []
    monkeypatch.setattr(RetiredWebChat, "_ask_terminal", classmethod(lambda cls, question: asked.append(question) or False))
    RetiredWebChat.offer("status")                         # no terminal: not even a docker call
    assert fake.commands == [] and asked == []

    monkeypatch.setattr(retired_web_chat.sys, "stdin", type("T", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.setattr(retired_web_chat.sys, "stdout", type("T", (), {"isatty": staticmethod(lambda: True), "write": lambda self, text: None, "flush": lambda self: None})())
    RetiredWebChat.offer("status")
    assert len(asked) == 1 and fake.removing() == []
    RetiredWebChat.offer("status")                         # declined once: not asked again
    assert len(asked) == 1
    RetiredWebChat.offer("chat")                           # `chat remove` asks for itself


def test_nothing_is_asked_where_onyx_never_was(monkeypatch, tmp_path):
    from conftest import REAL_RETIRED_OFFER

    monkeypatch.setattr(retired_web_chat, "ONYX_DEPLOYMENT_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(RetiredWebChat, "offer", REAL_RETIRED_OFFER)

    def no_docker(*args, **kwargs):
        raise AssertionError("asked Docker on a machine that never had Onyx")

    monkeypatch.setattr(subprocess, "run", no_docker)
    RetiredWebChat.offer("status")
