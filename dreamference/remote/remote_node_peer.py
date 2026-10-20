"""The node as the overlay's first peer (REMOTE_ACCESS §3, step 2).

The node runs NetBird's own client like every other peer: the pinned release archive, checked
against its SHA-256 as the user before anything is installed as root; the node's CA in the system
trust store, the only store the client reads (§14.2, item 2); then `netbird up` with a one-off
setup key the node minted for itself. The key reaches the client in a file only the user can
read, never on a command line. Every root step is a `RemoteStep`, printed before it runs.
"""

from pathlib import Path

from dreamference.remote.netbird_pins import NETBIRD_VERSION, NetBirdPins
from dreamference.remote.remote_executor import RemoteExecutor
from dreamference.remote.remote_step import RemoteStep

NETBIRD = "/usr/local/bin/netbird"
CA_STORE_FILE = "/usr/local/share/ca-certificates/mightling-node-ca.crt"
ARCHIVE = "netbird.tar.gz"
UP_TIMEOUT_S = 180
LOADED = "loaded"


class RemoteNodePeer:
    """The plan that installs NetBird's client on the node and enrols it."""

    @classmethod
    def installed_version(cls) -> str:
        """The client's version, as `netbird version` prints it.

        Returns:
            str: The version, or "" when the client is not installed.
        """
        result = RemoteExecutor.run([NETBIRD, "version"])
        return result.stdout.strip() if result.returncode == 0 else ""

    @classmethod
    def service_installed(cls) -> bool:
        """Whether NetBird's system service exists.

        Returns:
            bool: True when systemd knows `netbird.service`.
        """
        result = RemoteExecutor.run(
            ["systemctl", "show", "-p", "LoadState", "--value", "netbird.service"]
        )
        return result.stdout.strip() == LOADED

    @classmethod
    def install_steps(cls, work: Path, system: str, machine: str) -> list[RemoteStep] | None:
        """Installs the pinned client, unless it is installed at that version.

        Args:
            work: A folder of the user's for the download.
            system: `uname -s`.
            machine: `uname -m`.

        Returns:
            list[RemoteStep] | None: The steps (empty when nothing is to do), or None when no
            archive is pinned for this machine.
        """
        steps: list[RemoteStep] = []
        if cls.installed_version() != NETBIRD_VERSION:
            pinned = NetBirdPins.client_archive(system, machine)
            if pinned is None:
                return None
            url, sha = pinned
            archive = str(work / ARCHIVE)
            steps += [
                RemoteStep.user(
                    f"Download NetBird {NETBIRD_VERSION}'s client",
                    "curl",
                    "-fsSL",
                    "-o",
                    archive,
                    url,
                ),
                RemoteStep.user(
                    "Check it against the pinned SHA-256",
                    "sh",
                    "-c",
                    f"echo '{sha}  {archive}' | sha256sum -c -",
                ),
                RemoteStep.user("Unpack it", "tar", "-xzf", archive, "-C", str(work), "netbird"),
                RemoteStep.sudo(
                    f"Install it as {NETBIRD}",
                    "install",
                    "-m",
                    "0755",
                    str(work / "netbird"),
                    NETBIRD,
                ),
            ]
        if not cls.service_installed():
            steps.append(
                RemoteStep.sudo("Install NetBird's system service", NETBIRD, "service", "install")
            )
        steps.append(RemoteStep.sudo("Start it", NETBIRD, "service", "start"))
        return steps

    @classmethod
    def trust_steps(cls, ca_cert: Path) -> list[RemoteStep]:
        """Puts the node's CA in the system trust store (Debian and Ubuntu).

        Args:
            ca_cert: The CA's certificate.

        Returns:
            list[RemoteStep]: The two root steps.
        """
        return [
            RemoteStep.sudo(
                "Trust the node's CA (name-constrained) system-wide",
                "install",
                "-m",
                "0644",
                str(ca_cert),
                CA_STORE_FILE,
            ),
            RemoteStep.sudo("Rebuild the system trust store", "update-ca-certificates"),
        ]

    @classmethod
    def untrust_steps(cls) -> list[RemoteStep]:
        """Takes the node's CA out of the system trust store again.

        Returns:
            list[RemoteStep]: The two root steps.
        """
        return [
            RemoteStep.sudo(
                "Remove the node's CA from the system trust store", "rm", "-f", CA_STORE_FILE
            ),
            RemoteStep.sudo("Rebuild the system trust store", "update-ca-certificates", "--fresh"),
        ]

    @classmethod
    def up_step(cls, dns_name: str, key_file: Path, hostname: str) -> RemoteStep:
        """Brings the node's client up on its own control plane.

        Args:
            dns_name: The box's name: the management URL every peer uses.
            key_file: The one-off setup key, in a file only the user can read.
            hostname: The peer's name, from which management derives its DNS label.

        Returns:
            RemoteStep: The root step.
        """
        return RemoteStep.sudo(
            "Join the overlay as its first peer",
            NETBIRD, "up", "--management-url", f"https://{dns_name}:443",
            "--setup-key-file", str(key_file), "--hostname", hostname,
        )  # fmt: skip

    @classmethod
    def reconnect_step(cls, dns_name: str) -> RemoteStep:
        """Points an enrolled node at a new box: the same peer and key, a new management URL.

        Args:
            dns_name: The new box's name.

        Returns:
            RemoteStep: The root step.
        """
        return RemoteStep.sudo(
            f"Reach the control plane through {dns_name} from now on",
            NETBIRD, "up", "--management-url", f"https://{dns_name}:443",
        )  # fmt: skip

    @classmethod
    def down_step(cls) -> RemoteStep:
        """Takes the node's client off the overlay; the client stays installed.

        Returns:
            RemoteStep: The root step.
        """
        return RemoteStep.sudo("Leave the overlay", NETBIRD, "down")
