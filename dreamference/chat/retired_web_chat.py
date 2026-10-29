"""What an install from before Onyx's retirement left behind, and removing it in two steps.

Specified in specs/DREAMFERENCE_MIGHTLING_ASK.md §10 (Phase C): the Onyx web chat is gone from the
code; on upgrade, `ling-admin` offers to stop and remove its containers, and its volumes are kept
until the user confirms separately. `ling web` and the desktop app replace it.

- **Step 1, the containers** (`docker compose -p onyx … down`, never `-v`): the API server, web
  server, PostgreSQL, nginx and the code interpreter. Nginx's `/puffin-images/` route goes with it;
  the images themselves stay in the image search store, served at `/images/` now.
- **Step 2, the data, asked again:** the compose project's volumes (saved chats and the Onyx
  accounts, in PostgreSQL), the image search, SigLIP and speech-to-text containers still on
  Onyx's network (`ling-admin images start` and `voice start` recreate them on the sidecar
  network), Onyx's own images (`onyxdotapp/*`, about 4.9 GB) and its networks. Docker refuses to
  remove a network a container is still on, and two of Mightling's own were put on Onyx's by its
  `configure`: SearXNG as a second network, which is disconnected, and the Google service, created
  on it, which is recreated on the sidecar network (its tokens are in the mounted folder, its
  secret is the same). Any other container still on it is named and left alone.

The deployment folder (`~/.config/onyx/deployment`, compose files and `.env`) is left as it is,
and nothing here deletes anything without a yes or the matching flag. The offer is made once, at
an interactive `ling-admin` run, only where that folder exists and Onyx's containers are found;
`ling-admin chat remove` makes it again.
"""

import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Final

from dreamference.chat.google_service import GOOGLE_CONTAINER_NAME, GoogleService
from dreamference.chat.searxng_sidecar import SEARXNG_CONTAINER_NAME
from dreamference.chat.sidecar_network import SIDECAR_NETWORK, SidecarNetwork

ONYX_DEPLOYMENT_DIR: Final[str] = os.path.expanduser("~/.config/onyx/deployment")
ONYX_COMPOSE_PROJECT: Final[str] = "onyx"
ONYX_COMPOSE_FILES: Final[tuple] = ("docker-compose.yml", "docker-compose.onyx-lite.yml")
PROJECT_LABEL: Final[str] = f"label=com.docker.compose.project={ONYX_COMPOSE_PROJECT}"
# Onyx's own images; generic ones it also ran (PostgreSQL, nginx) are left, others may use them.
ONYX_IMAGE_PREFIX: Final[str] = "onyxdotapp/"
# Sidecars `ling-admin chat configure` created on Onyx's network.
FORMER_SIDECARS: Final[tuple] = (
    "dreamference-image-search",
    "dreamference-siglip",
    "dreamference-stt",
)
# Mightling's own containers Onyx's `configure` put on its network (ONYX §6, §8), kept by `chat remove`.
OWN_CONTAINERS: Final[tuple] = (SEARXNG_CONTAINER_NAME, GOOGLE_CONTAINER_NAME)
# Where a declined offer is remembered, so it is made once.
DECISION_FILE: Final[str] = os.path.expanduser("~/.config/dreamference/web-chat-retired.json")

RETIRED_MESSAGE: Final[str] = (
    "The Onyx web chat was retired: Mightling's web UI is `ling web` (Ask and Work in a browser),\n"
    "and the desktop app (`ling app`) shows the same page. Image search and voice are\n"
    "`ling-admin images start` and `ling-admin voice start`.\n"
    "`ling-admin chat remove` removes what an older install left (asked twice: containers, then data)."
)


def _run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


class RetiredWebChat:
    """Finds and removes the retired Onyx deployment's containers, and, asked again, its data."""

    @classmethod
    def deployed(cls) -> bool:
        """True if an Onyx deployment folder exists: a cheap check before asking Docker."""
        return pathlib.Path(ONYX_DEPLOYMENT_DIR).is_dir()

    @classmethod
    def containers(cls) -> list[str]:
        """The names of the compose project's containers, running or not."""
        result = _run(
            ["docker", "ps", "-a", "--filter", PROJECT_LABEL, "--format", "{{.Names}}"], timeout=30
        )
        return result.stdout.split() if result.returncode == 0 else []

    @classmethod
    def volumes(cls) -> list[str]:
        """The compose project's volumes: saved chats and accounts among them."""
        result = _run(["docker", "volume", "ls", "-q", "--filter", PROJECT_LABEL], timeout=30)
        return result.stdout.split() if result.returncode == 0 else []

    @classmethod
    def networks(cls) -> list[str]:
        """The networks.

        Returns:
            List[str]: The names of the compose project's networks (names, not ids: a container's
            `NetworkMode` names the network it was created on).
        """
        result = _run(
            ["docker", "network", "ls", "--filter", PROJECT_LABEL, "--format", "{{.Name}}"],
            timeout=30,
        )
        return result.stdout.split() if result.returncode == 0 else []

    @classmethod
    def attached(cls, network: str) -> list[str]:
        """The attached.

        Args:
            network (str): One of the project's networks.

        Returns:
            List[str]: The containers on it: the running ones, which the network lists, and
            Mightling's own stopped ones, which it does not (a stopped container holds no
            endpoint, so Docker would remove the network and leave it unable to start).
        """
        result = _run(
            [
                "docker",
                "network",
                "inspect",
                network,
                "--format",
                "{{range .Containers}}{{.Name}} {{end}}",
            ],
            timeout=30,
        )
        names = result.stdout.split() if result.returncode == 0 else []
        for name in OWN_CONTAINERS:
            if name not in names and network in cls.container_networks(name):
                names.append(name)
        return names

    @classmethod
    def container_networks(cls, name: str) -> list[str]:
        """The container networks.

        Args:
            name (str): A container's name.

        Returns:
            List[str]: The networks it is on, read from the container, so a stopped one counts.
        """
        result = _run(
            [
                "docker",
                "inspect",
                name,
                "--format",
                "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}",
            ],
            timeout=30,
        )
        return result.stdout.split() if result.returncode == 0 else []

    @classmethod
    def release_network(cls, network: str) -> list[str]:
        """Takes Mightling's own containers off one of Onyx's networks, so it can be removed.

        A container on it as a second network (SearXNG, joined by Onyx's `configure`) is
        disconnected. The Google service, which that `configure` created on it, is recreated on the
        sidecar network: its tokens are in the mounted credentials folder and its secret is
        `GoogleService.secret`, so nothing is lost. Any other container created on it is left.

        Args:
            network (str): One of the project's networks.

        Returns:
            List[str]: The containers still holding the network.
        """
        held = []
        for name in cls.attached(network):
            if SidecarNetwork.network_mode(name) != network:
                _run(["docker", "network", "disconnect", network, name], timeout=60)
            elif name == GOOGLE_CONTAINER_NAME:
                GoogleService.stop()
                if not GoogleService.start():
                    print(
                        f"⚠️  {GoogleService.problem or 'The Google service was not recreated.'} "
                        "`ling-admin google start` retries."
                    )
            else:
                held.append(name)
        return held

    @classmethod
    def images(cls) -> list[str]:
        """Onyx's own images (`onyxdotapp/*`), as `repository:tag`."""
        result = _run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=30)
        if result.returncode != 0:
            return []
        return [name for name in result.stdout.split() if name.startswith(ONYX_IMAGE_PREFIX)]

    @classmethod
    def orphan_sidecars(cls) -> list[str]:
        """The orphan sidecars.

        Returns:
            List[str]: The former web chat's sidecars that still sit on a network other than the
            sidecar network (Onyx's); `ling-admin images start` and `voice start` replace them.
        """
        orphans = []
        for name in FORMER_SIDECARS:
            mode = SidecarNetwork.network_mode(name)
            if mode and mode != SIDECAR_NETWORK:
                orphans.append(name)
        return orphans

    @classmethod
    def compose_down_command(cls) -> list[str] | None:
        """The compose down command.

        Returns:
            Optional[List[str]]: `docker compose … down` for the deployment, without `-v` (the
            volumes stay), or None when its compose files are gone.
        """
        files: list[str] = []
        for name in ONYX_COMPOSE_FILES:
            path = os.path.join(ONYX_DEPLOYMENT_DIR, name)
            if pathlib.Path(path).is_file():
                files += ["-f", path]
        if not files:
            return None
        return [
            "docker",
            "compose",
            *files,
            "-p",
            ONYX_COMPOSE_PROJECT,
            "--project-directory",
            ONYX_DEPLOYMENT_DIR,
            "down",
            "--remove-orphans",
        ]

    @classmethod
    def remove_containers(cls) -> bool:
        """Step 1: stops and removes the containers; volumes, images and the folder stay.

        Returns:
            bool: True if none of the project's containers is left.
        """
        command = cls.compose_down_command()
        if command is not None:
            _run(command, timeout=600)
        left = cls.containers()
        if left:
            # No compose files, or `down` failed part-way: the containers by name, still no volume.
            _run(["docker", "rm", "-f", *left], timeout=300)
        return not cls.containers()

    @classmethod
    def remove_data(cls) -> bool:
        """Step 2: deletes the volumes, the sidecars left on Onyx's network.

        Onyx's images and its networks. Only ever after step 1 and a second yes.

        Returns:
            bool: True if no volume, Onyx image or Onyx network is left.
        """
        orphans = cls.orphan_sidecars()
        if orphans:
            _run(["docker", "rm", "-f", *orphans], timeout=300)
        volumes = cls.volumes()
        if volumes:
            _run(["docker", "volume", "rm", *volumes], timeout=300)
        images = cls.images()
        if images:
            _run(["docker", "rmi", *images], timeout=600)
        for network in cls.networks():
            held = cls.release_network(network)
            if held:
                print(
                    f"⚠️  {network} is kept: {', '.join(held)} {'was' if len(held) == 1 else 'were'} created on it."
                )
                continue
            _run(["docker", "network", "rm", network], timeout=60)
        return not cls.volumes() and not cls.images() and not cls.networks()

    @classmethod
    def describe(cls) -> list[str]:
        """What is left, for `ling-admin chat status` and before each question."""
        lines = [f"Containers: {', '.join(cls.containers()) or 'none'}"]
        lines.append(f"Volumes (saved chats, accounts): {', '.join(cls.volumes()) or 'none'}")
        lines.append(
            f"Sidecars still on Onyx's network: {', '.join(cls.orphan_sidecars()) or 'none'}"
        )
        lines.append(f"Onyx images: {', '.join(cls.images()) or 'none'}")
        lines.append(f"Onyx networks: {', '.join(cls.networks()) or 'none'}")
        lines.append(
            f"Deployment folder: {ONYX_DEPLOYMENT_DIR if cls.deployed() else 'none'} (left as it is)"
        )
        return lines

    @classmethod
    def remove(
        cls, yes: bool = False, delete_data: bool = False, ask: Callable[[str], bool] | None = None
    ) -> int:
        """`ling-admin chat remove`: step 1, then step 2, each asked unless its flag says so.

        Args:
            yes (bool): Remove the containers without asking.
            delete_data (bool): Delete the data (step 2) without asking.
            ask (Optional[Callable[[str], bool]]): Asks a yes/no question; None asks on the
                terminal, and answers no where there is none.

        Returns:
            int: The exit code.
        """
        ask = ask or cls._ask_terminal
        containers = cls.containers()
        if containers:
            print(f"The retired Onyx web chat's containers: {', '.join(containers)}")
            if not (
                yes or ask("Stop and remove them? Saved chats stay in their Docker volumes. [y/N] ")
            ):
                print("Nothing was removed. `ling-admin chat remove` asks again.")
                return 0
            if not cls.remove_containers():
                print(f"❌ Some containers are still there: {', '.join(cls.containers())}")
                return 1
            print("✅ The Onyx containers are gone; its volumes and images are kept.")
        else:
            print("No Onyx container is left.")
        volumes, images, orphans, networks = (
            cls.volumes(),
            cls.images(),
            cls.orphan_sidecars(),
            cls.networks(),
        )
        if not (volumes or images or orphans or networks):
            print("No Onyx data is left either.")
            return 0
        print(
            f"Kept: volumes {', '.join(volumes) or 'none'}; images {', '.join(images) or 'none'}; "
            f"sidecars on Onyx's network {', '.join(orphans) or 'none'}; networks {', '.join(networks) or 'none'}."
        )
        if orphans:
            print(
                "💡 `ling-admin images start` and `ling-admin voice start` run image search and voice "
                "without them."
            )
        if not (
            delete_data
            or ask(
                "Delete these too? The saved chats and Onyx accounts cannot be recovered. [y/N] "
            )
        ):
            print("Kept. `ling-admin chat remove --delete-data` deletes them later.")
            return 0
        if not cls.remove_data():
            print(
                "❌ Some volumes, images or networks could not be removed (one may still be in use)."
            )
            return 1
        print("✅ The Onyx volumes, images and networks are deleted.")
        return 0

    @classmethod
    def _ask_terminal(cls, question: str) -> bool:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
        try:
            return input(question).strip().lower() in ("y", "yes")
        except EOFError:
            return False

    @classmethod
    def _declined(cls) -> bool:
        try:
            with pathlib.Path(DECISION_FILE).open() as handle:
                return bool(json.load(handle).get("declined"))
        except (OSError, ValueError, AttributeError):
            return False

    @classmethod
    def _remember_declined(cls) -> None:
        try:
            pathlib.Path(os.path.dirname(DECISION_FILE)).mkdir(exist_ok=True, parents=True)
            with pathlib.Path(DECISION_FILE).open("w") as handle:
                json.dump({"declined": True}, handle)
        except OSError:
            pass

    @classmethod
    def offer(cls, command: str | None) -> None:
        """The one-time offer at an interactive `ling-admin` run on an install that still has Onyx.

        Args:
            command (Optional[str]): The command being run; `chat` makes its own offer.
        """
        if command == "chat" or not cls.deployed() or cls._declined():
            return
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return
        try:
            if not cls.containers():
                return
        except (OSError, subprocess.SubprocessError):
            return
        print(RETIRED_MESSAGE)
        answered = {"asked": False}

        def ask(question: str) -> bool:
            answered["asked"] = True
            return cls._ask_terminal(question)

        cls.remove(ask=ask)
        if answered["asked"] and cls.containers():
            cls._remember_declined()
