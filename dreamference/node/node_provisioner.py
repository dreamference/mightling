"""`ling-admin node provision`: bringing more GB10s to a working node from this one
(specs/DREAMFERENCE_MIGHTLING_FLEET.md §5, §7, §9, §11).

After NVIDIA's first-boot wizard, one command run here installs Mightling on each new machine from a
bundle of this one, runs the root half there with one `sudo` (`node prepare`), advertises it,
copies the model and its images over the LAN, assigns the model, pairs, starts and verifies it.
Re-run, it is the fleet update: every step reads the machine first and does nothing when it is
already satisfied.

Two channels, kept apart (§8): the **provisioning session**, a password-authenticated SSH login
that lasts only while the command runs and is the only channel that installs anything or runs
anything as root; and the **pairing key**, used afterwards to start, read and check, never to
install. No persistent access is added: no NOPASSWD sudoers entry, no full-access key. `--role`
is the deliberate exception: it adds both, and key-only SSH (node_role_provisioner.py).

With several machines every question comes first (one password for all by default, the host-key
fingerprints), then the slow part runs unattended. A failure stops that machine at that step and
never the others; the summary names, for each machine, what is left and the command for it.
"""

import datetime
import getpass
import json
import os
import re
import shutil
import socket
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from dreamference.node.fleet_askpass import FleetAskpass
from dreamference.node.fleet_bundle import FleetBundle
from dreamference.node.fleet_hub_fetch import FleetHubFetch
from dreamference.node.fleet_model_plan import GIB, FleetModelPlan
from dreamference.node.fleet_probe import FleetProbe
from dreamference.node.fleet_session import REMOTE_ADMIN, FleetSession
from dreamference.node.node_pairing import NodePairing
from dreamference.node.node_role_provisioner import NodeRoleProvisioner

LOG_DIR: Final[str] = os.path.expanduser("~/.local/state/dreamference/fleet")

# Where a bundle is copied to on the node before `install.sh --from` reads it.
REMOTE_BUNDLE_DIR: Final[str] = "~/.cache/dreamference/fleet"

# How long a started model server may take to answer (FLEET §7.1, step 11).
DEFAULT_START_TIMEOUT_S: Final[int] = 20 * 60

SEARXNG_PORT: Final[int] = 8888

# What the probe prints for a reading that holds, and docker for a running container.
PROBE_YES: Final[str] = "1"
CONTAINER_RUNNING: Final[str] = "true"

# How often a model copy is started again while its connection keeps dropping (a node at
# another site, reached over the internet), with a pause between: carrying on from the partial file.
COPY_ATTEMPTS: Final[int] = 30

# `--model-source` value: the node downloads the public weights itself.
HUB_SOURCE: Final[str] = "hf"

# A single-label host name (`spark-1a2b`), which this LAN resolves only as `<name>.local`.
BARE_NAME: Final[re.Pattern] = re.compile(r"[A-Za-z0-9-]+")


class NodeProvisioner:
    """One provisioning run over one or more machines."""

    def __init__(self, hosts: list[str], options: dict[str, Any]) -> None:
        """Set up one run.

        Args:
            hosts: The machines named on the command line (names, addresses or paired nodes).
            options: The parsed flags: `all`, `user`, `model`, `source`, `per_host_password`,
                `mesh`, `web`, `no_start`, `restart`, `os_update`, `dry_run`, `via`, `match`,
                `start_timeout`, `role`, `model_source` (`copy`, or `hf`: the node downloads the
                public weights itself and only the rest is sent from here).
        """
        self.hosts = hosts
        self.options = options
        self.user: str = options.get("user") or getpass.getuser()
        # What each host is connected to (its name resolved by mDNS, or a paired node's address
        # from a browse for its id) and as whom: never an address remembered from earlier.
        self.connect: dict[str, str] = {}
        self.users: dict[str, str] = {}
        self.model: str = options.get("model") or self.configured_model()
        self.run_dir = FleetSession.new_run_dir()
        self.passwords: dict[str, str] = {}
        self.askpass: FleetAskpass | None = None
        self.sessions: dict[str, FleetSession] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.bundle: Path | None = None
        self.plan: dict[str, Any] = {}
        # `--role`: the steps that leave a node as the last one was set up by hand (persistent
        # access FLEET decisions 3 and 6 rule out otherwise; node_role_provisioner.py).
        self.role: NodeRoleProvisioner | None = None
        if options.get("role"):
            self.role = NodeRoleProvisioner(self, options["role"])

    # -- the run ----------------------------------------------------------------------------------

    def run(self) -> int:
        """Runs the whole provisioning.

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
                if self.role is not None:
                    self.role.finish(host, self.sessions[host])
        finally:
            self._close_all()
        for host in self.hosts:
            if self.role is None and self.results.get(host, {}).get("paired_record"):
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
            self.hosts = [record.get("name") or record["node"][:8] for record in records]
            for host, record in zip(self.hosts, records, strict=True):
                self._place(host, record)
            return True
        if not self.hosts and not self._choose_from_network():
            return False
        for host in self.hosts:
            self._place(host, NodePairing.find(host, browse=False))
        return True

    def _place(self, host: str, record: dict[str, Any] | None) -> None:
        """Decides what to connect to for one host, by name, never by a remembered address.

        The name as given if it resolves, `<name>.local` (mDNS) for a bare name that does not, and
        for a paired node whose name resolves neither way, the address a browse for its id finds
        now (`NodePairing.resolve`). A paired node is logged into as its record's user unless
        `--user` says otherwise.

        Args:
            host: The name typed (or the paired node's name, under `--all`).
            record: The paired node's record, or None for a machine not paired here.
        """
        if record is not None and not self.options.get("user") and record.get("user"):
            self.users[host] = record["user"]
        candidates = [host]
        if BARE_NAME.fullmatch(host):
            # The overlay's DNS (remote access) first: a machine at another site is never on this
            # LAN, so mDNS cannot find it; `<name>.local` stays the LAN's way.
            candidates += [f"{host}.{NodePairing.overlay_domain()}", f"{host}.local"]
        if record is not None and record.get("overlay_name"):
            candidates.insert(1, record["overlay_name"])
        if record is not None and record.get("name") and record["name"] != host:
            candidates += [record["name"], f"{record['name']}.local"]
        for candidate in candidates:
            if self._resolves(candidate):
                self.connect[host] = candidate
                return
        address = NodePairing.resolve(record) if record is not None else None
        if address is not None:
            self.connect[host] = address
        elif record is not None:
            self._fail(
                host,
                "connect",
                "off the network: its name does not resolve and no browse shows its id "
                "(its remembered address was not tried)",
                f"ling-admin node provision {host}",
            )
        else:
            # Not resolvable here: ssh says why, at the connect step.
            self.connect[host] = candidates[-1]

    @classmethod
    def _resolves(cls, name: str) -> bool:
        """Whether this machine's resolver (mDNS for `.local`) finds the name now."""
        try:
            return bool(socket.getaddrinfo(name, 22, type=socket.SOCK_STREAM))
        except (OSError, UnicodeError):
            return False

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
        """Asks every password, opens every session, prints each host key, before any slow step.

        With `--role` the password is asked even for one machine: its root steps run unattended,
        through `sudo -S`, which has no terminal to prompt on.
        """
        if len(self.hosts) > 1 or self.role is not None:
            self._ask_passwords()
            self.askpass = FleetAskpass(self.run_dir, self.passwords)
            self.askpass.start(self._admin_executable())
        for host in self.hosts:
            if self.results.get(host, {}).get("failed"):
                continue
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
        # Keyed by what each session connects to: that is the name askpass and sudo look up.
        if self.options.get("per_host_password"):
            for host in self.hosts:
                self.passwords[self._address(host)] = getpass.getpass(
                    f"Password for {self._user(host)} on {host}: "
                )
            return
        where = self.hosts[0] if len(self.hosts) == 1 else f"all {len(self.hosts)} machines"
        shared = getpass.getpass(f"Password for {self.user} on {where}: ")
        self.passwords = {self._address(host): shared for host in self.hosts}

    def _address(self, host: str) -> str:
        if self.options.get("via") and len(self.hosts) == 1:
            return self.options["via"]
        return self.connect.get(host, host)

    def _user(self, host: str) -> str:
        return self.users.get(host, self.user)

    def _session_for(self, host: str) -> FleetSession:
        address = self._address(host)
        safe = "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in host)
        log = Path(LOG_DIR) / f"{datetime.date.today().isoformat()}-{safe}.log"
        session = FleetSession(address, self._user(host), self.run_dir, log_path=log)
        if self.role is not None:
            # The main node's key (`ssh -G`'s, or the default), the one the role authorises, so a
            # re-run still logs in once password SSH is off.
            session.identity = NodePairing.ssh_config(address)[1]
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
            f"{session.host} refused that password. Its own password for {session.user}: "
        )
        return session.open()

    # -- one machine (§7.1) -----------------------------------------------------------------------

    def _provision(self, host: str) -> None:
        session = self.sessions[host]
        self.results.setdefault(host, {"host": host, "done": []})
        steps: list[Callable[[str, FleetSession], bool]] = [
            self._step_probe,
            self._step_os_update,
            self._step_install,
            self._step_prepare,
            self._step_advertise,
            self._step_models,
            self._step_assign,
            self._step_pair,
        ]
        if self.role is not None:
            steps = self.role.steps(steps)
        for step in steps:
            if not step(host, session):
                return
            if self.options.get("dry_run") and step == self._step_probe:
                self._describe_dry_run(host)
                if self.role is not None:
                    self.role.describe(host, session)
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
            "&& { fwupdmgr refresh --force; fwupdmgr upgrade -y --no-reboot-check; true; }'",
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
        if not self._step_venv(host, session):
            return False
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
                (installed.stderr.strip() or installed.stdout.strip())[-200:],
                f"ling-admin node provision {host}",
            )
        self._done(host, f"Mightling {version} installed")
        return self._reprobe(host, session)

    def _step_venv(self, host: str, session: FleetSession) -> bool:
        """Installs python3-venv as root when the machine lacks it, before `install.sh` needs it.

        DGX OS ships python3 without it (a fresh GX10, 2026-10-08), and `install.sh` run over the
        session has no terminal for sudo, so it can only fail there. This is the one root command
        before `node prepare`, which needs the virtualenv this makes possible.
        """
        if self._state(host).get("venv", PROBE_YES) == PROBE_YES:
            return True
        installed = session.sudo(
            "env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 install -y "
            "python3-venv",
            self.passwords.get(session.host),
        )
        if installed.returncode != 0:
            return self._fail(
                host,
                "install python3-venv",
                (installed.stdout or installed.stderr or "").strip()[-200:],
                f"ssh -t {self._user(host)}@{host} sudo apt-get install -y python3-venv",
            )
        self._done(host, "python3-venv installed")
        return True

    def _step_prepare(self, host: str, session: FleetSession) -> bool:
        check = session.run_admin(["host", "check"])
        if FleetProbe.prepared(self._state(host)) and check.returncode == 0:
            self.results[host]["host_check"] = "pass"
            return True
        print(f"🔑 {host}: the root half, `sudo ling-admin node prepare` (it prints each change).")
        prepared = session.sudo(f"{REMOTE_ADMIN} node prepare", self.passwords.get(session.host))
        if prepared.returncode != 0:
            return self._fail(
                host,
                "node prepare",
                (prepared.stdout or prepared.stderr or "").strip()[-300:],
                f"ssh {self._user(host)}@{host} sudo {REMOTE_ADMIN} node prepare",
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
        if self.options.get("no_forge"):
            arguments.append("--no-forge")
        enabled = session.run_admin(arguments, docker=True)
        if enabled.returncode != 0:
            return self._fail(
                host,
                "advertise",
                enabled.stdout.strip()[-200:],
                f"ssh {self._user(host)}@{host} ling-admin node enable --no-web",
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
        if not self._fetch_models(host, session, missing):
            return False
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
                f"ssh {self._user(host)}@{host} ling-admin searxng start",
            )
        return True

    def _fetch_models(self, host: str, session: FleetSession, missing: list[str]) -> bool:
        """Brings the missing folders to the machine: rsync from here, or from Hugging Face (`hf`)."""
        if self.options.get("model_source") == HUB_SOURCE:
            return self._models_from_hub(host, session, missing)
        hub = self._state(host).get("hub")
        for folder in FleetModelPlan.local_folders(missing):
            copied = session.rsync(folder, f"{hub}/{folder.name}", attempts=COPY_ATTEMPTS)
            if copied.returncode != 0:
                return self._fail(
                    host,
                    "copy the model",
                    copied.stderr.strip()[-200:],
                    f"ling-admin node provision {host}",
                )
        return True

    def _models_from_hub(self, host: str, session: FleetSession, missing: list[str]) -> bool:
        """`--model-source hf`: the node downloads the public weights, this machine checks them.

        Each repository is fetched by the node from Hugging Face (resuming, retried), every file is
        compared with this machine's by SHA-256, and only what differs or is not on the Hub is sent
        from here. A download that keeps failing is replaced by a copy from another machine of this
        run that already has the model, over their shared LAN (`FleetHubFetch.lan_copy`).

        Args:
            host: The machine.
            session: Its provisioning session.
            missing: The folders it lacks.

        Returns:
            bool: True when every folder is there and checked (a failure is already recorded).
        """
        state = self._state(host)
        fetch = FleetHubFetch(session, str(state.get("hub")), FleetModelPlan.hub())
        for path in FleetModelPlan.local_folders(missing):
            folder = path.name
            sys.stdout.write(f"⏬ {host}: {folder} from Hugging Face (the node downloads it).\n")
            outcome = fetch.ensure(folder, attempts=COPY_ATTEMPTS)
            if not outcome["ok"] and outcome["hub_failed"]:
                outcome["ok"] = self._lan_fallback(host, fetch, folder)
            if not outcome["ok"]:
                return self._fail(
                    host,
                    "fetch the model",
                    f"{folder}: {outcome['why']}",
                    f"ling-admin node provision {host} --model-source hf",
                )
            if outcome["sent"]:
                self._done(
                    host, f"{folder}: {outcome['sent']} file(s) sent from here, not on the Hub"
                )
        self.results[host]["models_verified"] = True
        return True

    def _lan_fallback(self, host: str, fetch: FleetHubFetch, folder: str) -> bool:
        """Copies a folder from a machine of this run that holds it, then checks it.

        Args:
            host: The machine whose download failed.
            fetch: Its fetch.
            folder: The folder.

        Returns:
            bool: True when a donor gave it and every file checks out.
        """
        for donor in self.sessions:
            if donor == host or not self.results.get(donor, {}).get("models_verified"):
                continue
            donor_state = self._state(donor)
            lan_name = f"{donor_state.get('hostname')}.local"
            sys.stdout.write(
                f"🏠 {host}: the download failed; copying {folder} from {donor} over their LAN.\n"
            )
            if fetch.lan_copy(
                folder,
                self.sessions[donor],
                lan_name,
                self._user(donor),
                str(donor_state.get("hub")),
            ) and fetch.verify(folder):
                self._done(host, f"{folder}: copied from {donor} over the LAN")
                return True
        return False

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
                    f"ssh {self._user(host)}@{host} docker pull {image}",
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

    def _step_pair(self, host: str, session: FleetSession) -> bool:
        node_id = self._state(host).get("node_id", "")
        record = NodePairing.find(node_id, browse=False) if node_id else None
        # The id is what the machine says; its host key must be the one that node's pairing pinned.
        if record is not None and not NodePairing.pin_host_keys(node_id, session.host_key_lines()):
            return self._fail(
                host,
                "pair",
                "its host key is not the paired node's",
                f"ling-admin node remove {record['name']}",
            )
        if record is not None and NodePairing.run(record, "info").returncode == 0:
            self.results[host]["paired_record"] = record
        else:
            record = NodePairing.pair_over_session(session)
            if record is None:
                return self._fail(host, "pair", "pairing failed", f"ling-admin node add {host}")
            self._done(host, "paired")
        record["address"] = self.connect.get(host, host)
        if NodePairing.is_overlay_name(record["address"]):
            record["overlay_name"] = record["address"]
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
        address = self.connect.get(host, host)
        running = self._served_model(address, port)
        if running is None or self.options.get("restart"):
            NodePairing.run(record, "start", capture=True)
            running = self._wait_for_model(address, port)
        if running is None:
            self._fail(
                host,
                "start",
                "the model server did not answer in time",
                f"ling-admin node start {host}",
            )
            return
        self.results[host]["state"] = "ready"
        self.results[host]["verify"] = self._verify(address, port, running)
        self.results[host]["complete"] = self.results[host]["verify"] == "ok"
        if not self.results[host]["complete"]:
            self.results[host]["left"] = f"ling-admin node status {host}"

    def _info(self, record: dict[str, Any]) -> dict[str, Any]:
        answer = NodePairing.run(record, "info")
        try:
            return json.loads(answer.stdout) if answer.returncode == 0 else {}
        except ValueError:
            return {}

    def _served_model(self, host: str, port: int) -> str | None:
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/v1/models", timeout=5) as response:
                data = json.load(response).get("data") or []
            model = data[0].get("id") if data else None
        except (OSError, ValueError, AttributeError, LookupError):
            # The node answers over the network: a list of another shape used to raise out of the
            # whole run.
            return None
        return model

    def _wait_for_model(self, host: str, port: int) -> str | None:
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
        except (OSError, ValueError, AttributeError) as error:
            return f"failed: {error}"
        return "ok"

    # -- bookkeeping ------------------------------------------------------------------------------

    def _state(self, host: str) -> dict[str, str]:
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
            if self.role is not None:
                session.run("sudo -k")  # the root steps ran unattended: drop sudo's timestamp
            session.close()
        if self.askpass is not None:
            self.askpass.stop()
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def _describe_dry_run(self, host: str) -> None:
        state = self._state(host)
        changes = [*self._dry_run_install(state), *self._dry_run_models(state)]
        node_id = state.get("node_id", "")
        if not node_id or NodePairing.find(node_id, browse=False) is None:
            changes.append("pair (node add over the session)")
        if not self.options.get("no_start"):
            changes.append(
                "start the model server and verify"
                + (" (restarted)" if self.options.get("restart") else " (unless it serves)")
            )
        print(f"📋 {host} would change: " + "; ".join(changes))
        self.results[host].update(complete=True, left="(dry run)")

    def _dry_run_install(self, state: dict[str, str]) -> list[str]:
        """The install, root and advertising changes a dry run names for one probed machine."""
        source = self.options.get("source") or "this"
        try:
            expected = FleetBundle.expected_version(source)
        except ValueError as error:
            return [f"nothing, {error}"]
        changes = []
        if state.get("venv", PROBE_YES) != PROBE_YES:
            changes.append("sudo apt-get install python3-venv (DGX OS lacks it)")
        if state.get("admin") != PROBE_YES:
            changes.append(f"install Mightling ({expected or source})")
        elif expected is None or state.get("bundle") != expected:
            changes.append(
                f"replace Mightling {state.get('bundle') or '(no version recorded)'} with "
                f"{expected or 'the bundle of --from ' + source}"
            )
        changes.append(
            "sudo ling-admin node prepare"
            if not FleetProbe.prepared(state)
            else "sudo ling-admin node prepare if `host check` fails there"
        )
        if self.options.get("web"):
            changes.append("node enable (ling web on the LAN)")
        elif state.get("advertised") != PROBE_YES:
            changes.append("node enable --no-web")
        return changes

    def _dry_run_models(self, state: dict[str, str]) -> list[str]:
        """The weights, images, SearXNG and assignment a dry run names for one probed machine."""
        from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS

        from_hub = self.options.get("model_source") == HUB_SOURCE
        verb = "download from Hugging Face" if from_hub else "copy"
        changes = [
            f"{verb} {folder}" for folder in FleetProbe.missing_models(state, self.plan["folders"])
        ]
        changes += [
            f"image {image}"
            for image in FleetProbe.missing_images(
                state, self.plan["copy_images"] + self.plan["pull_images"]
            )
        ]
        if state.get("searxng") != CONTAINER_RUNNING:
            changes.append("searxng start")
        if (state.get("model") or DEFAULT_MODEL_ALIAS) != self.model:
            changes.append(f"main-model set {self.model}")
        return changes

    @classmethod
    def configured_model(cls) -> str:
        """Returns:
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
