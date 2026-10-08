"""
The private Matrix homeserver behind `ling chat`'s Matrix adapter: `ling-admin matrix …`
(specs/DREAMFERENCE_MIGHTLING_CHAT.md §5).

tuwunel runs as the container `dreamference-matrix` on an **internal** Docker network, so it has no
route to anything but this host: no federation, no URL previews, no push gateway, because nothing
can be reached, not because a setting says so. The host reaches it through a loopback proxy (two
user units running systemd's own `systemd-socket-proxyd`), and the phone through `tailscale serve`
in front of that proxy, under the node's tailnet name. That name is the Matrix server name, which is
permanent (every account's id ends in it), so it is chosen once, kept in `matrix-admin.json`, and a
later `start` refuses when Tailscale reports another one.

The bridge reads `matrix.json` (the homeserver URL, the bot's token, the allow-list); this module
writes it. Everything that touches Docker, systemd, Tailscale or HTTP goes through `_run` and
`_http`, so the tests replace those two and nothing else.
"""

import json
import os
import re
import secrets
import string
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Final, List, Optional, Tuple

MATRIX_CONTAINER: Final[str] = "dreamference-matrix"
# tuwunel 1.9.3 (2026-09-25), the multi-architecture index, pinned by digest.
MATRIX_IMAGE: Final[str] = (
    "ghcr.io/matrix-construct/tuwunel:v1.9.3"
    "@sha256:678b7f5350e06a41614444497c587da9dddf66767e4068a27480402f3c1367d0"
)
MATRIX_VOLUME: Final[str] = "dreamference-matrix-data"
# The internal network: no route out. A fixed subnet so the container's address never moves.
MATRIX_NETWORK: Final[str] = "dreamference-matrix"
MATRIX_SUBNET: Final[str] = "172.31.231.0/24"
MATRIX_ADDRESS: Final[str] = "172.31.231.10"
MATRIX_PORT: Final[int] = 6167
# Where the host (the bridge, `tailscale serve`) reaches it: the loopback proxy.
PROXY_URL: Final[str] = f"http://127.0.0.1:{MATRIX_PORT}"
PROXY_SOCKET_UNIT: Final[str] = "mightling-matrix-proxy.socket"
PROXY_SERVICE_UNIT: Final[str] = "mightling-matrix-proxy.service"
SOCKET_PROXYD: Final[str] = "/usr/lib/systemd/systemd-socket-proxyd"
BOT_LOCALPART: Final[str] = "mightling"
MEMORY_LIMIT: Final[str] = "1g"
TAILSCALE_INSTALL: Final[str] = (
    "Matrix reaches your phone through Tailscale, which is not running here. Install it and sign in:\n"
    "    curl -fsSL https://tailscale.com/install.sh | sh\n"
    "    sudo tailscale up\n"
    "then install Tailscale on your phone, signed in to the same tailnet, and run this again.\n"
    "(Headscale, the self-hosted coordination server, works the same way.)"
)
LOCALPART: Final = re.compile(r"^[a-z0-9._=-]{1,64}$")


class MatrixHomeserver:
    """`ling-admin matrix start|stop|status|add-user|push|remove`."""

    # -- seams the tests replace --------------------------------------------------------------

    @classmethod
    def _run(cls, argv: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
        """Runs a command, capturing its output. A missing program (Tailscale removed since `start`,
        say) is a failed step, not an exception, so `stop` and `remove` still do the rest."""
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except FileNotFoundError as error:
            return subprocess.CompletedProcess(argv, 127, "", str(error))

    @classmethod
    def _http(cls, method: str, url: str, body: Optional[Dict[str, Any]] = None,
              token: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        """One JSON request to the homeserver through the loopback proxy."""
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as err:
            try:
                return err.code, json.loads(err.read() or b"{}")
            except ValueError:
                return err.code, {}
        except (urllib.error.URLError, OSError, ValueError):
            return 0, {}

    # -- files ----------------------------------------------------------------------------------

    @classmethod
    def chat_dir(cls) -> str:
        """`~/.mightling/chat` (or `$CODEX_HOME/chat`): where the bridge keeps its files."""
        from dreamference.runner.codex_installer import CodexInstaller
        return os.path.join(CodexInstaller.home_dir(), "chat")

    @classmethod
    def _path(cls, name: str) -> str:
        return os.path.join(cls.chat_dir(), name)

    @classmethod
    def _load(cls, name: str) -> Dict[str, Any]:
        try:
            with open(cls._path(name), encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @classmethod
    def _save(cls, name: str, value: Dict[str, Any]) -> None:
        """Writes a file readable only by the user, replacing it whole."""
        directory = cls.chat_dir()
        os.makedirs(directory, exist_ok=True)
        os.chmod(directory, 0o700)
        staging = cls._path(f"{name}.tmp-{os.getpid()}")
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
        os.replace(staging, cls._path(name))

    # -- Tailscale ------------------------------------------------------------------------------

    @classmethod
    def tailscale_name(cls) -> Optional[str]:
        """
        Returns:
            Optional[str]: This machine's tailnet name (`node.tail1234.ts.net`) when Tailscale is
            running and signed in, else None.
        """
        try:
            result = cls._run(["tailscale", "status", "--json"], timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        try:
            status = json.loads(result.stdout)
        except ValueError:
            return None
        if status.get("BackendState") != "Running":
            return None
        name = (status.get("Self") or {}).get("DNSName") or ""
        return name.rstrip(".") or None

    # -- Docker ---------------------------------------------------------------------------------

    @classmethod
    def run_command(cls, server_name: str, token: str, push: bool) -> List[str]:
        """
        Builds the `docker run` command.

        Args:
            server_name: The permanent server name.
            token: The registration token: no account can be created without it.
            push: True puts the container on the sidecar network (push notifications can leave,
                with event ids only); False, the default, on the internal network.

        Returns:
            List[str]: The argv.
        """
        from dreamference.chat.sidecar_network import SIDECAR_NETWORK
        network = ["--network", SIDECAR_NETWORK] if push else ["--network", MATRIX_NETWORK, "--ip", MATRIX_ADDRESS]
        env = {
            "TUWUNEL_SERVER_NAME": server_name,
            "TUWUNEL_DATABASE_PATH": "/var/lib/tuwunel",
            "TUWUNEL_ADDRESS": "0.0.0.0",
            "TUWUNEL_PORT": str(MATRIX_PORT),
            "TUWUNEL_ALLOW_FEDERATION": "false",
            "TUWUNEL_ALLOW_REGISTRATION": "true",
            "TUWUNEL_REGISTRATION_TOKEN": token,
            "TUWUNEL_TRUSTED_SERVERS": "[]",
            "TUWUNEL_URL_PREVIEW_DOMAIN_EXPLICIT_ALLOWLIST": "[]",
        }
        argv = ["docker", "run", "-d", "--name", MATRIX_CONTAINER, "--restart", "unless-stopped",
                *network, f"--memory={MEMORY_LIMIT}", f"--memory-swap={MEMORY_LIMIT}",
                "--stop-timeout", "600",
                "-v", f"{MATRIX_VOLUME}:/var/lib/tuwunel"]
        for key, value in env.items():
            argv += ["-e", f"{key}={value}"]
        return argv + [MATRIX_IMAGE]

    @classmethod
    def ensure_network(cls) -> bool:
        """Creates the internal network once. Returns True if it exists afterwards."""
        if cls._run(["docker", "network", "inspect", MATRIX_NETWORK], timeout=30).returncode == 0:
            return True
        created = cls._run(["docker", "network", "create", "--internal", "--subnet", MATRIX_SUBNET,
                            MATRIX_NETWORK], timeout=60)
        return created.returncode == 0

    @classmethod
    def container_exists(cls) -> bool:
        return cls._run(["docker", "inspect", MATRIX_CONTAINER], timeout=30).returncode == 0

    @classmethod
    def container_running(cls) -> bool:
        result = cls._run(["docker", "inspect", "-f", "{{.State.Running}}", MATRIX_CONTAINER], timeout=30)
        return result.returncode == 0 and result.stdout.strip() == "true"

    @classmethod
    def container_address(cls) -> Optional[str]:
        """The container's address on whichever network it is on."""
        result = cls._run(["docker", "inspect", "-f",
                           "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", MATRIX_CONTAINER], timeout=30)
        if result.returncode != 0:
            return None
        addresses = result.stdout.split()
        return addresses[0] if addresses else None

    # -- the loopback proxy -----------------------------------------------------------------------

    @classmethod
    def unit_dir(cls) -> str:
        return os.path.expanduser("~/.config/systemd/user")

    @classmethod
    def proxy_units(cls, target: str) -> Dict[str, str]:
        """
        Args:
            target: `address:port` of the container.

        Returns:
            Dict[str, str]: Unit file name → text.
        """
        socket = (
            "# Written by `ling-admin matrix start`: the homeserver on loopback (MIGHTLING_CHAT §5.2).\n"
            "[Unit]\nDescription=Mightling Matrix homeserver, on loopback\n\n"
            f"[Socket]\nListenStream=127.0.0.1:{MATRIX_PORT}\n\n"
            "[Install]\nWantedBy=sockets.target\n"
        )
        service = (
            "# Written by `ling-admin matrix start`.\n"
            "[Unit]\nDescription=Mightling Matrix homeserver proxy\n"
            f"Requires={PROXY_SOCKET_UNIT}\nAfter={PROXY_SOCKET_UNIT}\n\n"
            f"[Service]\nExecStart={SOCKET_PROXYD} {target}\n"
        )
        return {PROXY_SOCKET_UNIT: socket, PROXY_SERVICE_UNIT: service}

    @classmethod
    def install_proxy(cls, target: str) -> bool:
        """Writes the proxy units, then (re)starts them. Returns True on success."""
        directory = cls.unit_dir()
        os.makedirs(directory, exist_ok=True)
        for name, text in cls.proxy_units(target).items():
            with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
                handle.write(text)
        steps = [["systemctl", "--user", "daemon-reload"],
                 ["systemctl", "--user", "stop", PROXY_SERVICE_UNIT],
                 ["systemctl", "--user", "enable", "--now", PROXY_SOCKET_UNIT]]
        return all(cls._run(step, timeout=60).returncode == 0 for step in steps)

    # -- accounts -------------------------------------------------------------------------------

    @classmethod
    def wait_until_answering(cls, seconds: int = 60) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            status, _ = cls._http("GET", f"{PROXY_URL}/_matrix/client/versions")
            if status == 200:
                return True
            time.sleep(1)
        return False

    @classmethod
    def register(cls, localpart: str, password: str, token: str) -> Optional[Dict[str, Any]]:
        """
        Creates an account through the registration-token flow.

        Returns:
            Optional[Dict[str, Any]]: The server's answer (`user_id`, `access_token`), or None.
        """
        url = f"{PROXY_URL}/_matrix/client/v3/register"
        body: Dict[str, Any] = {"username": localpart, "password": password, "inhibit_login": False,
                                "initial_device_display_name": "ling-admin"}
        status, answer = cls._http("POST", url, body)
        for _ in range(3):
            if status == 200:
                return answer
            if status != 401 or "session" not in answer:
                return None
            completed = answer.get("completed") or []
            stage = "m.login.registration_token" if "m.login.registration_token" not in completed else "m.login.dummy"
            auth: Dict[str, Any] = {"type": stage, "session": answer["session"]}
            if stage == "m.login.registration_token":
                auth["token"] = token
            status, answer = cls._http("POST", url, {**body, "auth": auth})
        return answer if status == 200 else None

    @classmethod
    def password(cls) -> str:
        """A password to type once on a phone: 20 letters and digits in groups of five."""
        alphabet = string.ascii_letters + string.digits
        raw = "".join(secrets.choice(alphabet) for _ in range(20))
        return "-".join(raw[i:i + 5] for i in range(0, 20, 5))

    # -- commands -------------------------------------------------------------------------------

    @classmethod
    def start(cls) -> int:
        """
        Starts (or creates) the homeserver, the proxy, the bot account and `tailscale serve`.

        Returns:
            int: The exit code.
        """
        from dreamference.node.node_identity import NodeIdentity
        if not NodeIdentity.read():
            print("❌ The homeserver belongs on a node, which is always on: run this on the machine that serves "
                  "the model (`ling-admin node enable`).")
            return 1
        name = cls.tailscale_name()
        if not name:
            print(f"❌ {TAILSCALE_INSTALL}")
            return 1
        admin = cls._load("matrix-admin.json")
        if admin.get("server_name") and admin["server_name"] != name:
            print(f"❌ This homeserver is {admin['server_name']}, but Tailscale now names this machine {name}.\n"
                  "   A Matrix server name cannot change: every account's id ends in it. Rename the machine back in "
                  "Tailscale, or delete the homeserver and its accounts with `ling-admin matrix remove --yes` and "
                  "start again.")
            return 1
        if not admin:
            admin = {"server_name": name, "registration_token": secrets.token_hex(32), "push": False}
            cls._save("matrix-admin.json", admin)
        push = bool(admin.get("push"))
        if not push and not cls.ensure_network():
            print(f"❌ Could not create the internal Docker network {MATRIX_NETWORK}.")
            return 1
        if cls.container_exists():
            cls._run(["docker", "start", MATRIX_CONTAINER], timeout=60)
        else:
            if push:
                from dreamference.chat.sidecar_network import SidecarNetwork
                SidecarNetwork.ensure()
            created = cls._run(cls.run_command(name, admin["registration_token"], push), timeout=600)
            if created.returncode != 0:
                print(f"❌ The homeserver did not start: {created.stderr.strip()[:500]}")
                return 1
        address = cls.container_address() or MATRIX_ADDRESS
        if not cls.install_proxy(f"{address}:{MATRIX_PORT}"):
            print("❌ Could not start the loopback proxy (systemd user units).")
            return 1
        if not cls.wait_until_answering():
            print(f"❌ The homeserver is not answering on {PROXY_URL}; `docker logs {MATRIX_CONTAINER}` says why.")
            return 1
        config = cls._load("matrix.json")
        token = config.get("access_token")
        if token:
            status, _ = cls._http("GET", f"{PROXY_URL}/_matrix/client/v3/account/whoami", token=token)
            if status != 200:
                token = None
        if not token:
            answer = cls.register(BOT_LOCALPART, cls.password() + cls.password(), admin["registration_token"])
            if not answer or not answer.get("access_token"):
                print("❌ Could not create the bot account @mightling.")
                return 1
            config.update({"user_id": answer["user_id"], "access_token": answer["access_token"]})
        config.update({"homeserver": PROXY_URL, "server_name": name})
        config.setdefault("allowed", [])
        cls._save("matrix.json", config)
        served = cls._run(["tailscale", "serve", "--bg", "--https=443", PROXY_URL], timeout=60)
        if served.returncode != 0:
            print("⚠️  `tailscale serve` refused; if HTTPS is off for your tailnet, turn it on once in the Tailscale "
                  f"admin console (DNS → HTTPS Certificates), then run this again. ({served.stderr.strip()[:300]})")
        print(f"✅ The Matrix homeserver is running as https://{name} (no route out of this machine).")
        if not config["allowed"]:
            print("   Next: `ling-admin matrix add-user <name>` for your account, then `ling chat start`.")
        return 0

    @classmethod
    def add_user(cls, localpart: str) -> int:
        """Creates a user account, prints its password once, and allows it to talk to the bot."""
        if not LOCALPART.match(localpart):
            print("❌ A user name is lowercase letters, digits and . _ = - only.")
            return 2
        admin = cls._load("matrix-admin.json")
        if not admin:
            print("❌ No homeserver yet: run `ling-admin matrix start` first.")
            return 1
        password = cls.password()
        answer = cls.register(localpart, password, admin["registration_token"])
        if not answer or not answer.get("user_id"):
            print(f"❌ Could not create {localpart} (is the name taken, or the homeserver stopped?).")
            return 1
        config = cls._load("matrix.json")
        allowed = config.setdefault("allowed", [])
        if answer["user_id"] not in allowed:
            allowed.append(answer["user_id"])
        cls._save("matrix.json", config)
        print(f"✅ Created {answer['user_id']}. Its password, shown this once and stored nowhere:\n\n"
              f"    {password}\n\n"
              f"In Element X on your phone (with Tailscale on): sign in to https://{admin['server_name']} as "
              f"{localpart} with that password, then accept the invite from Mightling. "
              "`ling chat start` (or a restart of it) opens the room.")
        return 0

    @classmethod
    def stop(cls) -> int:
        """
        Turns the homeserver off until the next `start`: `tailscale serve` no longer offers it, the
        proxy socket is disabled (so a reboot does not listen again) and the container is stopped
        (`unless-stopped` keeps it stopped). The accounts and messages stay in the volume.
        """
        cls._run(["tailscale", "serve", "--https=443", "off"], timeout=60)
        cls._run(["systemctl", "--user", "disable", "--now", PROXY_SOCKET_UNIT], timeout=60)
        cls._run(["systemctl", "--user", "stop", PROXY_SERVICE_UNIT], timeout=60)
        cls._run(["docker", "stop", MATRIX_CONTAINER], timeout=660)
        print("✅ The Matrix homeserver is stopped; its accounts and messages are kept.")
        return 0

    @classmethod
    def status(cls) -> int:
        admin = cls._load("matrix-admin.json")
        config = cls._load("matrix.json")
        if not admin:
            print("Matrix: not set up (`ling-admin matrix start`).")
            return 0
        print(f"Server name: {admin['server_name']}")
        print(f"Container:   {'running' if cls.container_running() else 'stopped'} ({MATRIX_CONTAINER})")
        print(f"Network:     {'sidecar network, push notifications can leave' if admin.get('push') else 'internal, no route out'}")
        print(f"Tailscale:   {cls.tailscale_name() or 'not running'}")
        print(f"Bot:         {config.get('user_id', 'not created')}")
        print(f"Allowed:     {', '.join(config.get('allowed', [])) or 'nobody yet (`ling-admin matrix add-user <name>`)'}")
        return 0

    @classmethod
    def set_push(cls, on: bool) -> int:
        """Moves the container between the internal network and the sidecar network."""
        admin = cls._load("matrix-admin.json")
        if not admin:
            print("❌ No homeserver yet: run `ling-admin matrix start` first.")
            return 1
        admin["push"] = on
        cls._save("matrix-admin.json", admin)
        cls._run(["docker", "rm", "-f", MATRIX_CONTAINER], timeout=660)
        if on:
            print("⚠️  Push on: the homeserver can now reach Element's push gateway. A push carries an event id, "
                  "not the message, but it does say that a message arrived, and when.")
        return cls.start()

    @classmethod
    def remove(cls, confirmed: bool) -> int:
        """Deletes the homeserver, its volume (every account and message) and the bridge's Matrix files."""
        if not confirmed:
            print("This deletes the homeserver, every account on it and every message. Run it again with --yes.")
            return 1
        cls._run(["tailscale", "serve", "--https=443", "off"], timeout=60)
        cls._run(["systemctl", "--user", "disable", "--now", PROXY_SOCKET_UNIT], timeout=60)
        cls._run(["systemctl", "--user", "stop", PROXY_SERVICE_UNIT], timeout=60)
        cls._run(["docker", "rm", "-f", MATRIX_CONTAINER], timeout=660)
        cls._run(["docker", "volume", "rm", MATRIX_VOLUME], timeout=120)
        cls._run(["docker", "network", "rm", MATRIX_NETWORK], timeout=60)
        for name in ("matrix.json", "matrix-admin.json", "matrix-state.json"):
            try:
                os.remove(cls._path(name))
            except FileNotFoundError:
                pass
        for name in (PROXY_SOCKET_UNIT, PROXY_SERVICE_UNIT):
            try:
                os.remove(os.path.join(cls.unit_dir(), name))
            except FileNotFoundError:
                pass
        print("✅ The Matrix homeserver and its accounts are deleted.")
        return 0
