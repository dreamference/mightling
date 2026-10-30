"""Removing what the retired Onyx web chat left, in two separately confirmed steps
(specs/DREAMFERENCE_MIGHTLING_ASK.md §10, Phase C). Docker is a recorder; nothing is removed."""

import pathlib
import subprocess

import pytest

from dreamference.chat import retired_web_chat
from dreamference.chat.retired_web_chat import RetiredWebChat
from dreamference.chat.sidecar_network import SIDECAR_NETWORK


def _image_id(name):
    return "id-" + name.replace("/", "-").replace(":", "-")


class FakeDocker:
    """The retired deployment as Docker would report it, shrinking as commands remove things."""

    def __init__(self):
        self.containers = ["puffin-api_server-1", "puffin-relational_db-1", "puffin-nginx-1"]
        self.volumes = ["onyx_db_volume", "onyx_file-system"]
        self.images = ["onyxdotapp/onyx-backend:v4.5", "onyxdotapp/onyx-web-server:v4.5", "postgres:15.2-alpine"]
        self.networks = ["onyx_default"]
        self.sidecars = {"dreamference-image-search": "onyx_default", "dreamference-stt": SIDECAR_NETWORK}
        # Containers on Onyx's network besides the project's own: name -> the network it was created on.
        self.on_network = {"dreamference-image-search": "onyx_default"}
        # Stopped containers on it: Docker's network inspect does not list them.
        self.stopped = {}
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
            with_id = "{{.ID}}" in argv[-1]
            out = "\n".join(f"{i} {_image_id(i)}" if with_id else i for i in self.images)
        elif argv[:2] == ["docker", "inspect"] and "NetworkSettings" in argv[-1]:
            if argv[2] not in self.sidecars:
                return subprocess.CompletedProcess(argv, 1, "", "no such container\n")
            joined = {self.sidecars[argv[2]], self.on_network.get(argv[2]), self.stopped.get(argv[2])}
            out = " ".join(sorted(n for n in joined if n))
        elif argv[:2] == ["docker", "inspect"]:
            mode = self.sidecars.get(argv[2])
            return subprocess.CompletedProcess(argv, 0 if mode else 1, (mode or "") + "\n", "")
        elif argv[:3] == ["docker", "network", "inspect"]:
            out = " ".join(name for name in self.on_network if argv[3] in self.networks)
        elif argv[:3] == ["docker", "network", "disconnect"]:
            assert self.sidecars.get(argv[4]) != argv[3], "disconnected from the network it was created on"
            self.on_network.pop(argv[4], None)
            self.stopped.pop(argv[4], None)
        elif argv[:2] == ["docker", "compose"]:
            assert "-v" not in argv and "--volumes" not in argv and "--rmi" not in argv
            self.containers = []
        elif argv[:3] == ["docker", "rm", "-f"]:
            for name in argv[3:]:
                self.sidecars.pop(name, None)
                self.on_network.pop(name, None)
                self.stopped.pop(name, None)
                self.containers = [c for c in self.containers if c != name]
        elif argv[:3] == ["docker", "volume", "rm"]:
            self.volumes = [v for v in self.volumes if v not in argv[3:]]
        elif argv[:2] == ["docker", "rmi"]:
            self.images = [i for i in self.images if _image_id(i) not in argv[2:]
                           and (i not in argv[2:] or i.endswith(":<none>"))]
            if any(arg.endswith(":<none>") for arg in argv[2:]):
                return subprocess.CompletedProcess(argv, 1, "", "invalid reference format\n")
        elif argv[:3] == ["docker", "network", "rm"]:
            if any(n in argv[3:] for n in self.on_network.values()) or (self.on_network and "onyx_default" in argv[3:]):
                return subprocess.CompletedProcess(argv, 1, "", "error: network has active endpoints\n")
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


def test_mightlings_own_containers_are_taken_off_onyxs_network_so_it_can_go(deployment, monkeypatch, capsys):
    # Onyx's `configure` joined SearXNG to its network and created the Google service on it; Docker
    # refuses to remove a network a container is still on.
    from dreamference.chat.google_service import GOOGLE_CONTAINER_NAME, GoogleService

    _, fake = deployment
    fake.sidecars.update({"dreamference-searxng": SIDECAR_NETWORK, GOOGLE_CONTAINER_NAME: "onyx_default"})
    fake.on_network.update({"dreamference-searxng": "onyx_default", GOOGLE_CONTAINER_NAME: "onyx_default"})
    recreated = []

    def start(cls):
        recreated.append(GOOGLE_CONTAINER_NAME)
        fake.sidecars[GOOGLE_CONTAINER_NAME] = SIDECAR_NETWORK
        return True

    monkeypatch.setattr(GoogleService, "start", classmethod(start))
    assert RetiredWebChat.remove(yes=True, delete_data=True) == 0
    assert ["docker", "network", "disconnect", "onyx_default", "dreamference-searxng"] in fake.commands
    assert ["docker", "rm", "-f", GOOGLE_CONTAINER_NAME] in fake.commands and recreated == [GOOGLE_CONTAINER_NAME]
    assert fake.sidecars["dreamference-searxng"] == SIDECAR_NETWORK          # SearXNG itself stays
    assert fake.networks == [] and "networks are deleted" in capsys.readouterr().out


def test_a_container_of_someone_elses_created_on_onyxs_network_keeps_it(deployment, capsys):
    _, fake = deployment
    fake.sidecars["their-app"] = "onyx_default"
    fake.on_network["their-app"] = "onyx_default"
    assert RetiredWebChat.remove(yes=True, delete_data=True) == 1
    assert fake.networks == ["onyx_default"] and "their-app" in fake.sidecars
    assert not any(c[:3] == ["docker", "network", "rm"] for c in fake.commands)
    assert not any(c[:3] == ["docker", "network", "disconnect"] for c in fake.commands)
    out = capsys.readouterr().out
    assert "onyx_default is kept: their-app was created on it" in out
    assert fake.volumes == [] and fake.images == ["postgres:15.2-alpine"]   # the rest still went


def test_stopped_containers_of_mightlings_own_are_released_too(deployment, monkeypatch):
    # A stopped container holds no endpoint, so the network does not list it and Docker would
    # remove the network under it; `docker start` would then fail for good.
    from dreamference.chat.google_service import GOOGLE_CONTAINER_NAME, GoogleService

    _, fake = deployment
    fake.sidecars.update({"dreamference-searxng": SIDECAR_NETWORK, GOOGLE_CONTAINER_NAME: "onyx_default"})
    fake.stopped.update({"dreamference-searxng": "onyx_default", GOOGLE_CONTAINER_NAME: "onyx_default"})
    recreated = []
    monkeypatch.setattr(GoogleService, "start", classmethod(lambda cls: recreated.append(True) or True))
    assert RetiredWebChat.remove(yes=True, delete_data=True) == 0
    assert ["docker", "network", "disconnect", "onyx_default", "dreamference-searxng"] in fake.commands
    assert ["docker", "rm", "-f", GOOGLE_CONTAINER_NAME] in fake.commands and recreated == [True]
    assert fake.networks == []


def test_an_onyx_image_that_lost_its_tag_is_removed_by_its_id(deployment):
    # A newer pull leaves the old image listed as `repo:<none>`, which `docker rmi` refuses.
    _, fake = deployment
    fake.images.append("onyxdotapp/onyx-backend:<none>")
    assert RetiredWebChat.remove(ask=lambda question: True) == 0
    assert fake.images == ["postgres:15.2-alpine"]
    assert RetiredWebChat.remove_data()


def test_removing_without_docker_says_so_instead_of_failing_with_a_traceback(monkeypatch, capsys):
    def no_docker(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    monkeypatch.setattr(subprocess, "run", no_docker)
    assert RetiredWebChat.remove(yes=True) == 1
    assert "Docker could not be run" in capsys.readouterr().out


def test_tagged_onyx_images_are_removed_by_name_and_only_untagged_ones_by_id(deployment):
    # By id, `docker rmi` would refuse an image another tag of which is still used.
    _, fake = deployment
    fake.images.append("onyxdotapp/onyx-backend:<none>")
    assert RetiredWebChat.images() == ["onyxdotapp/onyx-backend:v4.5", "onyxdotapp/onyx-web-server:v4.5",
                                       _image_id("onyxdotapp/onyx-backend:<none>")]


def test_a_former_sidecar_that_is_not_there_is_no_orphan(deployment):
    _, fake = deployment
    del fake.sidecars["dreamference-image-search"]
    assert RetiredWebChat.orphan_sidecars() == []


def test_containers_compose_could_not_remove_are_reported_and_the_data_is_not_offered(deployment, monkeypatch, capsys):
    _, fake = deployment

    def stuck(argv, **kwargs):
        result = fake(argv, **kwargs)
        fake.containers = ["puffin-api_server-1"]
        return result

    monkeypatch.setattr(subprocess, "run", stuck)
    asked = []
    assert RetiredWebChat.remove(yes=True, ask=lambda question: asked.append(question) or True) == 1
    assert "Some containers are still there: puffin-api_server-1" in capsys.readouterr().out
    assert asked == [] and fake.volumes


@pytest.mark.parametrize("left", ["images", "orphans", "networks"])
def test_each_kind_of_data_left_alone_is_still_offered_for_deletion(deployment, left):
    _, fake = deployment
    fake.containers, fake.volumes = [], []
    if left != "images":
        fake.images = ["postgres:15.2-alpine"]
    if left != "orphans":
        fake.sidecars["dreamference-image-search"] = SIDECAR_NETWORK
        fake.on_network = {}
    if left != "networks":
        fake.networks = []
    asked = []
    assert RetiredWebChat.remove(ask=lambda question: asked.append(question) or True) == 0
    assert len(asked) == 1
    assert fake.images == ["postgres:15.2-alpine"] and fake.networks == []
    if left == "orphans":
        assert fake.sidecars == {"dreamference-stt": SIDECAR_NETWORK}


def _at_terminal(monkeypatch, answer):
    monkeypatch.setattr(retired_web_chat.sys, "stdin", type("T", (), {"isatty": staticmethod(lambda: True)})())
    terminal = {"isatty": staticmethod(lambda: True), "write": lambda self, text: None, "flush": lambda self: None}
    monkeypatch.setattr(retired_web_chat.sys, "stdout", type("T", (), terminal)())
    monkeypatch.setattr("builtins.input", answer)


def test_without_a_terminal_the_answer_is_no_and_nothing_is_read(monkeypatch):
    monkeypatch.setattr(retired_web_chat.sys, "stdin", type("T", (), {"isatty": staticmethod(lambda: False)})())

    def no_input(question):
        raise AssertionError("read an answer without a terminal")

    monkeypatch.setattr("builtins.input", no_input)
    assert RetiredWebChat._ask_terminal("Remove? ") is False


@pytest.mark.parametrize(("typed", "yes"), [("y", True), (" YES\n", True), ("Y", True), ("", False), ("no", False)])
def test_only_y_or_yes_in_any_case_counts_as_yes(monkeypatch, typed, yes):
    _at_terminal(monkeypatch, lambda question: typed)
    assert RetiredWebChat._ask_terminal("Remove? ") is yes


def test_a_closed_terminal_answers_no(monkeypatch):
    def closed(question):
        raise EOFError

    _at_terminal(monkeypatch, closed)
    assert RetiredWebChat._ask_terminal("Remove? ") is False


@pytest.fixture
def offer(deployment, monkeypatch):
    from conftest import REAL_RETIRED_OFFER

    monkeypatch.setattr(RetiredWebChat, "offer", REAL_RETIRED_OFFER)
    asked = []

    def at_terminal(command, answers=("n", "n")):
        # In the test itself: pytest puts its own stdout back after the fixtures are set up.
        replies = iter(answers)
        _at_terminal(monkeypatch, lambda question: asked.append(question) or next(replies))
        RetiredWebChat.offer(command)
        return asked

    return deployment[1], at_terminal


def test_a_decision_file_that_does_not_say_declined_still_lets_the_offer_ask(offer):
    _, at_terminal = offer
    pathlib.Path(retired_web_chat.DECISION_FILE).write_text('{"declined": false}')
    assert len(at_terminal("status")) == 1


def test_chat_makes_its_own_offer(offer):
    fake, at_terminal = offer
    assert at_terminal("chat") == [] and fake.commands == []


def test_no_offer_without_the_deployment_folder_even_at_a_terminal(offer, monkeypatch, tmp_path):
    fake, at_terminal = offer
    monkeypatch.setattr(retired_web_chat, "ONYX_DEPLOYMENT_DIR", str(tmp_path / "absent"))
    assert at_terminal("status") == [] and fake.commands == []
    assert RetiredWebChat.describe()[-1] == "Deployment folder: none (left as it is)"


def test_no_offer_once_the_containers_are_gone(offer):
    # `chat remove` deletes the data the user keeps; the offer is about the running containers.
    fake, at_terminal = offer
    fake.containers = []
    assert at_terminal("status") == []


def test_an_offer_that_could_not_ask_is_made_again(offer, monkeypatch):
    fake, at_terminal = offer
    calls = []

    def docker_away_once(argv, **kwargs):
        if argv[:2] == ["docker", "ps"]:
            calls.append(argv)
            if len(calls) == 2:
                raise OSError("Docker is restarting")
        return fake(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", docker_away_once)
    assert at_terminal("status") == [] and not RetiredWebChat._declined()


def test_an_offer_taken_is_not_remembered_as_declined(offer):
    fake, at_terminal = offer
    assert len(at_terminal("status", answers=("y", "n"))) == 2
    assert fake.containers == [] and fake.volumes
    assert not RetiredWebChat._declined()
