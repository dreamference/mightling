"""
`ling-admin node provision`: bringing more GB10s to a working node from this one
(specs/DREAMFERENCE_MIGHTLING_FLEET.md §5, §7, §9, §11).

After NVIDIA's first-boot wizard, one command run here installs Mightling on each new machine from a
bundle of this one, runs the root half there with one `sudo` (`node prepare`), advertises it,
copies the model and its images over the LAN, assigns the model, pairs, starts and verifies it.
Re-run, it is the fleet update: every step reads the machine first and does nothing when it is
already satisfied.

Two channels, kept apart (§8): the **provisioning session**, a password-authenticated SSH login
that lasts only while the command runs and is the only channel that installs anything or runs
anything as root; and the **pairing key**, used afterwards to start, read and check, never to
install. No persistent access is added: no NOPASSWD sudoers entry, no full-access key.

With several machines every question comes first (one password for all by default, the host-key
fingerprints), then the slow part runs unattended. A failure stops that machine at that step and
never the others; the summary names, for each machine, what is left and the command for it.
"""

import datetime
import getpass
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Final, List, Optional

from dreamference.node.fleet_askpass import FleetAskpass
from dreamference.node.fleet_bundle import FleetBundle
from dreamference.node.fleet_model_plan import GIB, FleetModelPlan
from dreamference.node.fleet_probe import FleetProbe
from dreamference.node.fleet_session import REMOTE_ADMIN, FleetSession
from dreamference.node.node_pairing import NodePairing

LOG_DIR: Final[str] = os.path.expanduser("~/.local/state/dreamference/fleet")

# Where a bundle is copied to on the node before `install.sh --from` reads it.
REMOTE_BUNDLE_DIR: Final[str] = "~/.cache/dreamference/fleet"

# How long a started model server may take to answer (FLEET §7.1, step 11).
DEFAULT_START_TIMEOUT_S: Final[int] = 20 * 60

SEARXNG_PORT: Final[int] = 8888


class NodeProvisioner:
    """One provisioning run over one or more machines."""

    def __init__(self, hosts: List[str], options: Dict[str, Any]) -> None:
        """
        Args:
            hosts: The machines named on the command line (names, addresses or paired nodes).
            options: The parsed flags: `all`, `user`, `model`, `source`, `per_host_password`,
                `mesh`, `web`, `no_start`, `restart`, `os_update`, `dry_run`, `via`, `match`,
                `start_timeout`.
        """
        self.hosts = hosts
        self.options = options
        self.user: str = options.get("user") or getpass.getuser()
        self.model: str = options.get("model") or self.configured_model()
        self.run_dir = FleetSession.new_run_dir()
        self.passwords: Dict[str, str] = {}
        self.askpass: Optional[FleetAskpass] = None
        self.sessions: Dict[str, FleetSession] = {}
        self.results: Dict[str, Dict[str, Any]] = {}
        self.bundle: Optional[Path] = None
        self.plan: Dict[str, Any] = {}

    # -- the run ----------------------------------------------------------------------------------

    def run(self) -> int:
        """
        Runs the whole provisioning.

        Returns:
            int: 0 when every machine is complete, 1 otherwise.
        """
        if not self._check_model() or not self._resolve_hosts():
            return 1
        if not self.options.get("dry_run"):
            self.bundle = FleetBundle.build(self.options.get("source") or "this")
            if self.bundle is None:
                return 1
        self.plan = FleetModelPlan.plan(self.model)
        try:
            self._questions_first()
            for host in list(self.sessions):
                self._provision(host)
        finally:
            self._close_all()
        for host in self.hosts:
            if host in self.results and self.results[host].get("paired_record"):
                self._start_and_verify(host)
        if self.options.get("mesh"):
            print(
                "⚠️  --mesh is not built yet: only this machine is paired with each node "
                "(run `ling-admin node add` on another node to manage the fleet from there)."
            )
        self.print_summary()
        return 0 if all(result.get("complete") for result in self.results.values()) else 1

    # -- choosing the machines --------------------------------------------------------------------

    def _check_model(self) -> bool:
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

        if ModelMatrixRegistry.get_spec(self.model) is None or not ModelMatrixRegistry.is_offered(
            self.model
        ):
            print(f"❌ {self.model} is not a model of the matrix (`ling-admin model list`).")
            return False
        return True

    def _resolve_hosts(self) -> bool:
        if self.options.get("all"):
            records = NodePairing.paired()
            if not records:
                print("❌ No paired nodes: provision new machines by name first.")
                return False
            self.hosts = [record["address"] for record in records]
            return True
        if self.hosts:
            return True
        return self._choose_from_network()

    def _choose_from_network(self) -> bool:
        from dreamference.node.node_browser import NodeBrowser

        found = NodeBrowser.unprovisioned(match=self.options.get("match"))
        if not found:
            print(
                "No unprovisioned GB10 answers on this network. Name the machines: "
                "ling-admin node provision <host>…"
            )
            return False
        print("Machines on this network that look like new GB10s:")
        for number, entry in enumerate(found, 1):
            print(f"  {number}. {entry['host']} ({entry['address']})")
        if not sys.stdin.isatty():
            print("Name the ones to provision: ling-admin node provision <host>…")
            return False
        answer = input("Provision which? (numbers, comma-separated; empty for none) ").strip()
        chosen = [
            found[int(part) - 1]["host"]
            for part in answer.replace(" ", "").split(",")
            if part.isdigit() and 0 < int(part) <= len(found)
        ]
        self.hosts = chosen
        return bool(chosen)

    # -- questions first (§9.2) -------------------------------------------------------------------

    def _questions_first(self) -> None:
        """Asks every password, opens every session, prints each host key, before any slow step."""
        if len(self.hosts) > 1:
            self._ask_passwords()
            self.askpass = FleetAskpass(self.run_dir, self.passwords)
            self.askpass.start(self._admin_executable())
        for host in self.hosts:
            session = self._session_for(host)
            if self._open(session):
                self.sessions[host] = session
                fingerprint = session.host_key_fingerprint()
                print(
                    f"🔐 {host}: host key SHA256:{fingerprint or '?'} (compare with "
                    f"`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the machine)."
                )
            else:
                self._fail(
                    host,
                    "connect",
                    "could not log in (wrong password, or SSH is off)",
                    f"ling-admin node provision {host}",
                )

    def _ask_passwords(self) -> None:
        if self.options.get("per_host_password"):
            for host in self.hosts:
                self.passwords[host] = getpass.getpass(f"Password for {self.user} on {host}: ")
            return
        shared = getpass.getpass(f"Password for {self.user} on all {len(self.hosts)} machines: ")
        self.passwords = {host: shared for host in self.hosts}

    def _session_for(self, host: str) -> FleetSession:
        address = (
            self.options.get("via") if self.options.get("via") and len(self.hosts) == 1 else host
        )
        safe = "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in host)
        log = Path(LOG_DIR) / f"{datetime.date.today().isoformat()}-{safe}.log"
        session = FleetSession(address, self.user, self.run_dir, log_path=log)
        if self.askpass is not None:
            session.askpass_env = self.askpass.environment(address)
        return session

    def _open(self, session: FleetSession) -> bool:
        if session.open():
            return True
        if self.askpass is None:
            return False
        # The shared password was refused here: this machine's own, asked once.
        self.passwords[session.host] = getpass.getpass(
            f"{session.host} refused that password. Its own password for {self.user}: "
        )
        return session.open()

    # -- one machine (§7.1) -----------------------------------------------------------------------

    def _provision(self, host: str) -> None:
        session = self.sessions[host]
        self.results.setdefault(host, {"host": host, "done": []})
        steps: List[Callable[[str, FleetSession], bool]] = [
            self._step_probe,
            self._step_os_update,
            self._step_install,
            self._step_prepare,
            self._step_advertise,
            self._step_models,
            self._step_assign,
            self._step_web,
            self._step_pair,
        ]
        for step in steps:
            if not step(host, session):
                return
            if self.options.get("dry_run") and step == self._step_probe:
                self._describe_dry_run(host)
                return

    def _step_probe(self, host: str, session: FleetSession) -> bool:
        answer = session.run(
            FleetProbe.script(
                self.plan["folders"], self.plan["copy_images"] + self.plan["pull_images"]
            )
        )
        if answer.returncode != 0:
            return self._fail(
                host,
                "read the state",
                answer.stderr.strip()[-200:] or "the probe failed",
                f"ling-admin node provision {host}",
            )
        state = FleetProbe.parse(answer.stdout)
        self.results[host].update(probe=state, node=state.get("node_id", ""))
        if not FleetProbe.is_gb10(state):
            return self._fail(
                host, "check the machine", "not a GB10 (no /etc/dgx-release, no GB10 GPU)", ""
            )
        return True

    def _step_os_update(self, host: str, session: FleetSession) -> bool:
        if not self.options.get("os_update"):
            return True
        password = self.passwords.get(session.host)
        if self._state(host).get("admin") == "1":
            session.run_admin(["server", "stop"], docker=True)
        print(f"⬆️  {host}: NVIDIA's OS and firmware update, then a reboot (§10.3).")
        update = session.sudo(
            "sh -c 'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y dist-upgrade "
            "&& fwupdmgr refresh --force; fwupdmgr upgrade -y --no-reboot-check; true'",
            password,
        )
        if update.returncode != 0:
            return self._fail(
                host,
                "OS update",
                "the update failed",
                f"ling-admin node provision {host} --os-update",
            )
        session.sudo("systemctl reboot", password)
        session.close()
        if not self._wait_for_ssh(session):
            return self._fail(
                host,
                "OS update",
                "the machine did not come back after the reboot",
                f"ling-admin node provision {host}",
            )
        self._done(host, "OS and firmware updated")
        return self._step_probe(host, session)

    def _step_install(self, host: str, session: FleetSession) -> bool:
        assert self.bundle is not None
        version = FleetBundle.version(self.bundle)
        if self._state(host).get("bundle") == version and self._state(host).get("admin") == "1":
            return True
        remote = f"{REMOTE_BUNDLE_DIR}/{self.bundle.name}"
        copied = session.rsync(self.bundle, remote)
        if copied.returncode != 0:
            return self._fail(
                host,
                "copy the bundle",
                copied.stderr.strip()[-200:],
                f"ling-admin node provision {host}",
            )
        installed = session.run(
            f"bash {remote}/install.sh --from {remote} --role node --no-advertise --no-host-setup --no-model",
            timeout=60 * 60,
        )
        if installed.returncode != 0:
            return self._fail(
                host,
                "install Mightling",
                installed.stderr.strip()[-200:],
                f"ling-admin node provision {host}",
            )
        self._done(host, f"Mightling {version} installed")
        return self._reprobe(host, session)

    def _step_prepare(self, host: str, session: FleetSession) -> bool:
        check = session.run_admin(["host", "check"])
        if FleetProbe.prepared(self._state(host)) and check.returncode == 0:
            self.results[host]["host_check"] = "pass"
            return True
        print(
            f"🔑 {host}: the root half, `sudo ling-admin node prepare` (it prints each change)."
        )
        prepared = session.sudo(f"{REMOTE_ADMIN} node prepare", self.passwords.get(session.host))
        if prepared.returncode != 0:
            return self._fail(
                host,
                "node prepare",
                (prepared.stdout or prepared.stderr or "").strip()[-300:],
                f"ssh {self.user}@{host} sudo {REMOTE_ADMIN} node prepare",
            )
        self._done(
            host,
            "root half applied (host settings, docker, linger, Avahi, sandbox, telemetry off)",
        )
        passed = session.run_admin(["host", "check"]).returncode == 0
        self.results[host]["host_check"] = "pass" if passed else "fail"
        return self._reprobe(host, session)

    def _step_advertise(self, host: str, session: FleetSession) -> bool:
        if self._state(host).get("advertised") == "1" and not self.options.get("web"):
            return True
        arguments = (
            ["node", "enable"] if self.options.get("web") else ["node", "enable", "--no-web"]
        )
        enabled = session.run_admin(arguments, docker=True)
        if enabled.returncode != 0:
            return self._fail(
                host,
                "advertise",
                enabled.stdout.strip()[-200:],
                f"ssh {self.user}@{host} ling-admin node enable --no-web",
            )
        self._done(host, "advertised on the LAN")
        return True

    def _step_models(self, host: str, session: FleetSession) -> bool:
        state = self._state(host)
        missing = [
            folder
            for folder in FleetProbe.missing_models(state, self.plan["folders"])
            if FleetModelPlan.local_folders([folder])
        ]
        needed = FleetModelPlan.bytes_needed(missing)
        if not FleetModelPlan.fits(float(state.get("free_gb") or 0), needed):
            return self._fail(
                host,
                "copy the model",
                f"not enough disk: {needed / GIB:.0f} GiB to copy, {state.get('free_gb')} GiB free",
                "free space there, then re-run",
            )
        started = time.monotonic()
        for folder in FleetModelPlan.local_folders(missing):
            copied = session.rsync(folder, f"{state.get('hub')}/{folder.name}")
            if copied.returncode != 0:
                return self._fail(
                    host,
                    "copy the model",
                    copied.stderr.strip()[-200:],
                    f"ling-admin node provision {host}",
                )
        if not self._images(host, session):
            return False
        self._record_copy(host, needed, time.monotonic() - started)
        if state.get("searxng") == "true":
            return True
        searxng = session.run_admin(["searxng", "start"], docker=True)
        if searxng.returncode != 0:
            return self._fail(
                host,
                "start SearXNG",
                searxng.stdout.strip()[-200:],
                f"ssh {self.user}@{host} ling-admin searxng start",
            )
        return True

    def _images(self, host: str, session: FleetSession) -> bool:
        state = self._state(host)
        for image in FleetProbe.missing_images(state, self.plan["copy_images"]):
            code = session.pipe_into(
                [["docker", "save", image], ["zstd", "-T0", "-3", "-c"]],
                FleetSession.with_docker_group("zstd -d -c | docker load"),
            )
            if code != 0:
                return self._fail(
                    host,
                    "copy an image",
                    f"docker save | load of {image} failed",
                    f"ling-admin node provision {host}",
                )
        for image in FleetProbe.missing_images(state, self.plan["pull_images"]):
            pulled = session.run(
                FleetSession.with_docker_group(f"docker pull {image}"), timeout=2 * 60 * 60
            )
            if pulled.returncode != 0:
                return self._fail(
                    host,
                    "pull an image",
                    pulled.stderr.strip()[-200:],
                    f"ssh {self.user}@{host} docker pull {image}",
                )
        return True

    def _step_assign(self, host: str, session: FleetSession) -> bool:
        from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS

        current = self._state(host).get("model") or DEFAULT_MODEL_ALIAS
        if current == self.model:
            self.results[host]["model"] = self.model
            return True
        assigned = session.run_admin(["main-model", "set", self.model])
        if assigned.returncode != 0:
            return self._fail(
                host,
                "assign the model",
                assigned.stdout.strip()[-200:],
                f"ling-admin node set {host} --model {self.model}",
            )
        self.results[host]["model"] = self.model
        return True

    def _step_web(self, host: str, session: FleetSession) -> bool:
        if not self.options.get("web"):
            return True
        for arguments in (["ling", "start"], ["ling", "configure"]):
            answer = session.run_admin(arguments, docker=True, timeout=60 * 60)
            if answer.returncode != 0:
                return self._fail(
                    host,
                    "the web UI",
                    answer.stdout.strip()[-200:],
                    f"ssh {self.user}@{host} ling-admin ling start",
                )
        self._done(host, "web UI installed")
        return True

    def _step_pair(self, host: str, session: FleetSession) -> bool:
        node_id = self._state(host).get("node_id", "")
        record = NodePairing.find(node_id, browse=False) if node_id else None
        if record is not None and NodePairing.run(record, "info").returncode == 0:
            self.results[host]["paired_record"] = record
        else:
            record = NodePairing.pair_over_session(session)
            if record is None:
                return self._fail(host, "pair", "pairing failed", f"ling-admin node add {host}")
            self._done(host, "paired")
        record["address"] = host
        record["provisioned"] = {
            "bundle": FleetBundle.version(self.bundle) if self.bundle else "",
            "model": self.model,
            "image": self.plan.get("image"),
            "date": datetime.date.today().isoformat(),
        }
        NodePairing._save(record)
        self.results[host].update(paired_record=record, node=record["node"])
        return True

    # -- after the session: start and verify through the key (§7.1, steps 11–12) ------------------

    def _start_and_verify(self, host: str) -> None:
        record = self.results[host]["paired_record"]
        info = self._info(record)
        port = int(info.get("model_port") or 8000)
        if self.options.get("no_start"):
            self.results[host].update(state="stopped", complete=True)
            return
        running = self._served_model(host, port)
        if running is None or self.options.get("restart"):
            NodePairing.run(record, "start", capture=True)
            running = self._wait_for_model(host, port)
        if running is None:
            self._fail(
                host,
                "start",
                "the model server did not answer in time",
                f"ling-admin node start {host}",
            )
            return
        self.results[host]["state"] = "ready"
        self.results[host]["verify"] = self._verify(host, port, running)
        self.results[host]["complete"] = self.results[host]["verify"] == "ok"
        if not self.results[host]["complete"]:
            self.results[host]["left"] = f"ling-admin node status {host}"

    def _info(self, record: Dict[str, Any]) -> Dict[str, Any]:
        answer = NodePairing.run(record, "info")
        try:
            return json.loads(answer.stdout) if answer.returncode == 0 else {}
        except ValueError:
            return {}

    def _served_model(self, host: str, port: int) -> Optional[str]:
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/v1/models", timeout=5) as response:
                data = json.load(response).get("data") or []
        except (OSError, ValueError):
            return None
        return data[0].get("id") if data else None

    def _wait_for_model(self, host: str, port: int) -> Optional[str]:
        deadline = time.monotonic() + int(
            self.options.get("start_timeout") or DEFAULT_START_TIMEOUT_S
        )
        while time.monotonic() < deadline:
            served = self._served_model(host, port)
            if served:
                return served
            time.sleep(10)
        return None

    def _verify(self, host: str, port: int, model_id: str) -> str:
        body = json.dumps(
            {
                "model": model_id,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "Say ok."}],
            }
        ).encode()
        request = urllib.request.Request(
            f"http://{host}:{port}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                if not json.load(response).get("choices"):
                    return "no completion"
            with urllib.request.urlopen(
                f"http://{host}:{SEARXNG_PORT}/search?q=ling&format=json", timeout=30
            ) as response:
                json.load(response)
        except (OSError, ValueError) as error:
            return f"failed: {error}"
        return "ok"

    # -- bookkeeping ------------------------------------------------------------------------------

    def _state(self, host: str) -> Dict[str, str]:
        return self.results.get(host, {}).get("probe", {})

    def _reprobe(self, host: str, session: FleetSession) -> bool:
        return self._step_probe(host, session)

    def _done(self, host: str, what: str) -> None:
        self.results[host]["done"].append(what)
        print(f"✅ {host}: {what}.")

    def _fail(self, host: str, step: str, reason: str, command: str) -> bool:
        result = self.results.setdefault(host, {"host": host, "done": []})
        result.update(failed=step, reason=reason, left=command, complete=False)
        print(f"❌ {host}: {step}: {reason}")
        return False

    def _record_copy(self, host: str, size: int, seconds: float) -> None:
        self.results[host]["copied_bytes"] = size
        if size and seconds > 0:
            self.results[host]["throughput"] = f"{size / seconds / 1e6:.0f} MB/s"

    def _wait_for_ssh(self, session: FleetSession, timeout: int = 15 * 60) -> bool:
        deadline = time.monotonic() + timeout
        time.sleep(30)
        while time.monotonic() < deadline:
            if session.open():
                return True
            time.sleep(15)
        return False

    def _close_all(self) -> None:
        for session in self.sessions.values():
            session.close()
        if self.askpass is not None:
            self.askpass.stop()
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def _describe_dry_run(self, host: str) -> None:
        state = self._state(host)
        version = "this machine's build"
        changes = []
        if state.get("admin") != "1":
            changes.append(f"install Mightling ({version})")
        if not FleetProbe.prepared(state):
            changes.append("sudo ling-admin node prepare")
        if state.get("advertised") != "1":
            changes.append("node enable --no-web")
        for folder in FleetProbe.missing_models(state, self.plan["folders"]):
            changes.append(f"copy {folder}")
        for image in FleetProbe.missing_images(
            state, self.plan["copy_images"] + self.plan["pull_images"]
        ):
            changes.append(f"image {image}")
        changes.append(f"main-model set {self.model}, pair, start")
        print(f"📋 {host} would change: " + "; ".join(changes))
        self.results[host].update(complete=True, left="(dry run)")

    @classmethod
    def configured_model(cls) -> str:
        """
        Returns:
            str: This machine's configured main model, which new nodes get by default.
        """
        from dreamference.config import DreamferenceConfig

        return DreamferenceConfig().model

    @classmethod
    def _admin_executable(cls) -> str:
        return shutil.which("ling-admin") or os.path.realpath(sys.argv[0])

    # -- the summary (§11) ------------------------------------------------------------------------

    def print_summary(self) -> None:
        """Prints one row per machine: what was done, its state and what is left."""
        from rich.console import Console
        from rich.table import Table

        table = Table(title="node provision")
        for column in ("host", "node", "model", "host check", "copied", "state", "verify", "left"):
            table.add_column(column)
        for host in self.hosts:
            result = self.results.get(host, {"host": host})
            copied = result.get("copied_bytes")
            table.add_row(
                host,
                (result.get("node") or "")[:8],
                result.get("model", ""),
                result.get("host_check", ""),
                f"{copied / GIB:.1f} GiB {result.get('throughput', '')}" if copied else "",
                result.get("state", ""),
                result.get("verify", result.get("failed", "")),
                result.get("left", "" if result.get("complete") else "re-run"),
            )
        Console().print(table)
        print(f"📝 Logs: {LOG_DIR}/ (each command and its output; never a password).")
