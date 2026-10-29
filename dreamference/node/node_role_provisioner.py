"""The steps `ling-admin node provision --role night|engine` adds to a provisioning run.

`NodeProvisioner` installs, prepares, advertises, copies the model and pairs (FLEET §7.1). With a
role it also leaves the machine as the last one was provisioned by hand (docs/dev/node.md): the
main node's key authorised, docker's daemon.json merged, the root helper and the sudoers drop-in
that allows it alone, the
model downloaded when this machine has no copy, the forge mirror, SSH key-only, the role's own
setup and a readiness report. That is the persistent access FLEET's decisions 3 and 6 rule out for a plain
`node provision`, so it is only ever done when `--role` asks for it.

Every step reads the machine first and changes nothing when it is already as wanted; with
`--dry-run` it only says what it would change. Root commands go through the run's one
`sudo -S`: the password is read once with getpass, held in memory and written to sudo's standard
input only; root files are passed base64-encoded on the command line (they hold no secret) and
installed from a `mktemp` file, so nothing is staged under a predictable name.
"""

import base64
import json
import re
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from dreamference.node.fleet_model_plan import FleetModelPlan
from dreamference.node.fleet_probe import FleetProbe
from dreamference.node.fleet_session import (
    AUTHORIZED_KEYS_ENDS_IN_NEWLINE,
    REMOTE_ADMIN,
    FleetSession,
)
from dreamference.node.node_pairing import NodePairing
from dreamference.vllm_server.host_root_helper import HELPER_PATH, HostRootHelper

if TYPE_CHECKING:
    from dreamference.node.node_provisioner import NodeProvisioner

ENGINE: Final[str] = "engine"
DERIVE_FLAG: Final[str] = "-y"

DOCKER_DAEMON_JSON: Final[str] = "/etc/docker/daemon.json"
DOCKER_SETTINGS: Final[dict[str, Any]] = {
    "max-download-attempts": 100,
    "runtimes": {"nvidia": {"args": [], "path": "nvidia-container-runtime"}},
}
# Docker is never restarted under a running model server (`server start` names it so).
MODEL_CONTAINER_PREFIX: Final[str] = "dreamference-vllm-"

SUDOERS_PATH: Final[str] = "/etc/sudoers.d/mightling-agents"
NOPASSWD_ALL: Final[str] = r"^[^#]*NOPASSWD:[[:space:]]*ALL[[:space:]]*$"

SSHD_DROPIN_PATH: Final[str] = "/etc/ssh/sshd_config.d/00-mightling-key-only.conf"
# `00-`: sshd keeps the first value it reads, and Ubuntu's own drop-ins (50-cloud-init.conf may
# say `PasswordAuthentication yes`) come later.
SSHD_DROPIN: Final[str] = (
    "# Key-only SSH; the local console login is unchanged.\n"
    "PasswordAuthentication no\n"
    "KbdInteractiveAuthentication no\n"
)
SSHD_KEY_ONLY: Final[str] = "sshd -T | grep -qx 'passwordauthentication no'"

NIGHT_DIRS: Final[tuple[str, ...]] = (
    "$HOME/.local/share/dreamference/swe-bench/chain",
    "$HOME/.local/share/dreamference/swe-bench/pins",
)
GPU_LOCK_FILES: Final[tuple[str, ...]] = ("$HOME/.ling-gpu.lock", "$HOME/.ling-gpu-holds.log")
# `swe-bench smoke`'s record of a pass (SweBenchRunner.smoke_path), which needs `setup` before it.
SMOKE_PASSED: Final[str] = (
    "grep -qs '\"passed\": true' $HOME/.cache/dreamference/swe-bench/smoke-*.json"
)
NVCC: Final[str] = "/usr/local/cuda/bin/nvcc"

LONG_TIMEOUT_S: Final[int] = 4 * 60 * 60
MIN_FREE_GIB: Final[int] = 50
PASS: Final[str] = "pass"
# What `NodeProvisioner._verify` says of a model server that answered.
VERIFIED: Final[str] = "ok"


class NodeRoleProvisioner:
    """The role's steps, run inside a `NodeProvisioner` run over its sessions."""

    def __init__(self, provisioner: "NodeProvisioner", role: str) -> None:
        """Set up the role's steps for one run.

        Args:
            provisioner: The run: its options, passwords, results and bookkeeping.
            role: `night` or `engine`.
        """
        self.provisioner = provisioner
        self.role = role
        self.dry_run = bool(provisioner.options.get("dry_run"))

    # -- where they go in the run ------------------------------------------------------------------

    def steps(
        self, base: list[Callable[[str, FleetSession], bool]]
    ) -> list[Callable[[str, FleetSession], bool]]:
        """Return the run's steps with the role's in their places.

        Args:
            base: `NodeProvisioner`'s steps: probe, OS update, install, prepare, advertise,
                models, assign, pair.

        Returns:
            list: The same steps with the key before the install, docker, the root helper and
            sudoers after the prepare, the download after the copy, and the node list, forge and key-only SSH after
            the pairing.
        """
        probe, os_update, install, prepare, advertise, models, assign, pair = base
        return [
            probe,
            os_update,
            self.authorize_key,
            install,
            prepare,
            self.docker_daemon,
            self.root_helper,
            self.sudoers,
            advertise,
            models,
            self.download_missing,
            assign,
            pair,
            self.listed,
            self.forge_mirror,
            self.key_only_ssh,
        ]

    def describe(self, host: str, session: FleetSession) -> None:
        """Print what a dry run's role steps would change; every one only reads.

        Args:
            host: The machine.
            session: Its open session.
        """
        for step in (
            self.authorize_key,
            self.docker_daemon,
            self.root_helper,
            self.sudoers,
            self.download_missing,
            self.forge_mirror,
            self.key_only_ssh,
            self.role_setup,
        ):
            step(host, session)
        print(f"📋 {host} would then run the readiness report.")

    def finish(self, host: str, session: FleetSession) -> None:
        """Start and verify the model server, run the role's setup and the readiness report.

        Args:
            host: The machine, once every step before succeeded.
            session: Its open session.
        """
        result = self.provisioner.results[host]
        if self.dry_run or result.get("failed") or not result.get("paired_record"):
            return
        self.provisioner._start_and_verify(host)
        if not result.get("failed"):  # the role's setup needs the model server (swe-bench smoke)
            self.role_setup(host, session)
        self.report(host, session)

    # -- the steps ----------------------------------------------------------------------------------

    def authorize_key(self, host: str, session: FleetSession) -> bool:
        """Authorise the main node's own key there (`ssh-copy-id`'s job), which key-only SSH needs.

        Args:
            host: The machine.
            session: Its open session, whose identity is the key.

        Returns:
            bool: True once the key is in `~/.ssh/authorized_keys`.
        """
        if session.identity is None:
            return self.provisioner._fail(host, "authorise the key", "no key here", "")
        public = Path(f"{session.identity}.pub")
        if not public.is_file():
            # A main node with no `~/.ssh/id_ed25519` (its keys are named per machine) makes the one
            # key the role needs, as `ssh-keygen -t ed25519` would, or restores the missing half.
            private = Path(session.identity)
            if private.is_file():
                command = ["ssh-keygen", DERIVE_FLAG, "-f", str(private)]
            else:
                command = ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private)]
            if not self._change(
                host,
                f"make {public.name} on this machine (the key the role authorises)",
                lambda: self._make_key(command, private, public),
            ):
                return False
            if self.dry_run:
                return True
        key = public.read_text().strip()
        found = session.run(
            f"grep -qF {shlex.quote(' '.join(key.split()[:2]))} ~/.ssh/authorized_keys"
        )
        if found.returncode == 0:
            return True
        return self._change(
            host,
            f"authorise {public.name} in ~/.ssh/authorized_keys",
            lambda: session.run(
                f"umask 077 && mkdir -p ~/.ssh && {AUTHORIZED_KEYS_ENDS_IN_NEWLINE} && "
                "cat >> ~/.ssh/authorized_keys",
                input_text=key + "\n",
            ),
        )

    @staticmethod
    def _make_key(command: list[str], private: Path, public: Path) -> subprocess.CompletedProcess:
        """Run `ssh-keygen` for the main node's key; a derived public half is written to `public`.

        Args:
            command: The `ssh-keygen` command line.
            private: The private key's path.
            public: The public key's path.

        Returns:
            subprocess.CompletedProcess: The result, a failure when `ssh-keygen` is missing.
        """
        try:
            private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            answer = subprocess.run(command, capture_output=True, text=True, check=False)
            if answer.returncode == 0 and command[1] == DERIVE_FLAG:
                public.write_text(answer.stdout)
            return answer
        except OSError as error:
            return subprocess.CompletedProcess(command, 1, "", str(error))

    def docker_daemon(self, host: str, session: FleetSession) -> bool:
        """Merge the nvidia runtime and `max-download-attempts` into daemon.json, keeping the rest.

        Docker is restarted only when the file changed, and never under a running model server.

        Args:
            host: The machine.
            session: Its open session.

        Returns:
            bool: True when the file holds both settings.
        """
        current = session.run(f"if [ -e {DOCKER_DAEMON_JSON} ]; then cat {DOCKER_DAEMON_JSON}; fi")
        try:
            if current.returncode != 0:
                raise ValueError(current.stderr.strip() or "unreadable")
            merged = self.merge_daemon_json(current.stdout)
        except ValueError as error:
            return self.provisioner._fail(
                host, "docker daemon.json", f"left alone: {error}", f"cat {DOCKER_DAEMON_JSON}"
            )
        if merged is None:
            return True
        restart = (
            "if docker ps --format '{{.Names}}' | grep -q '^"
            + MODEL_CONTAINER_PREFIX
            + "'; then echo 'a model server runs: docker not restarted'; "
            "else systemctl restart docker; fi"
        )
        return self._change(
            host,
            f"merge the nvidia runtime and max-download-attempts into {DOCKER_DAEMON_JSON}",
            lambda: self._root_file(session, DOCKER_DAEMON_JSON, merged, "0644", then=restart),
        )

    @classmethod
    def merge_daemon_json(cls, text: str) -> str | None:
        """Return daemon.json with Mightling's two settings merged in, or None when it has them.

        Args:
            text: The current file; empty when there is none.

        Returns:
            str | None: The merged file (every other key and runtime kept), or None.

        Raises:
            ValueError: The file is not a JSON object, or its `runtimes` is not one.
        """
        config = json.loads(text) if text.strip() else {}
        if not isinstance(config, dict) or not isinstance(config.get("runtimes", {}), dict):
            raise ValueError("not a JSON object with an object `runtimes`")
        runtimes = config.get("runtimes", {})
        # An nvidia runtime already there (`nvidia-ctk` may name its own path) is kept as it is.
        nvidia = runtimes.get("nvidia")
        has_nvidia = isinstance(nvidia, dict) and bool(nvidia.get("path"))
        attempts = DOCKER_SETTINGS["max-download-attempts"]
        if has_nvidia and config.get("max-download-attempts") == attempts:
            return None
        config["max-download-attempts"] = attempts
        if not has_nvidia:
            config["runtimes"] = {**runtimes, "nvidia": DOCKER_SETTINGS["runtimes"]["nvidia"]}
        return json.dumps(config, indent=4) + "\n"

    def root_helper(self, host: str, session: FleetSession) -> bool:
        """Install the root helper, the one command the sudoers drop-in allows (`HostRootHelper`).

        Args:
            host: The machine.
            session: Its open session.

        Returns:
            bool: True when the installed helper is the rendered one.
        """
        password = self.provisioner.passwords.get(session.host)
        wanted = HostRootHelper.render()
        if session.sudo(f"cat {HELPER_PATH}", password).stdout == wanted:
            return True
        return self._change(
            host,
            f"install {HELPER_PATH} (root:root 0755)",
            lambda: self._root_file(session, HELPER_PATH, wanted, "0755", check='sh -n "$f"'),
        )

    def sudoers(self, host: str, session: FleetSession) -> bool:
        """Install the sudoers drop-in that allows the root helper alone, checked by `visudo -c`.

        A node provisioned before 2026-10-29 has the old drop-in at the same path, which allowed
        `systemctl`, `apt-get`, `apt` and `docker` with any arguments: it is replaced. A leftover
        `NOPASSWD: ALL` elsewhere is reported, never removed.

        Args:
            host: The machine.
            session: Its open session.

        Returns:
            bool: True when the drop-in is installed as rendered.
        """
        password = self.provisioner.passwords.get(session.host)
        leftover = session.sudo(
            f"grep -lE {shlex.quote(NOPASSWD_ALL)} /etc/sudoers /etc/sudoers.d/ -r", password
        )
        for path in leftover.stdout.split():
            print(f"⚠️  {host}: {path} grants NOPASSWD: ALL. Left as it is; remove it by hand.")
        try:
            wanted = self.render_sudoers(session.user)
        except ValueError as error:
            return self.provisioner._fail(host, "sudoers", str(error), "")
        if session.sudo(f"cat {SUDOERS_PATH}", password).stdout == wanted:
            return True
        return self._change(
            host,
            f"install {SUDOERS_PATH} (checked by visudo -c)",
            lambda: self._root_file(
                session, SUDOERS_PATH, wanted, "0440", check='visudo -cf "$f" >/dev/null'
            ),
        )

    @classmethod
    def render_sudoers(cls, user: str) -> str:
        """Return the drop-in: the node's user may run the root helper, and nothing else, as root.

        Args:
            user: The account the agents run as.

        Returns:
            str: The file.

        Raises:
            ValueError: A user name sudoers would read as something else.
        """
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
            raise ValueError(f"unexpected user name {user!r}")
        return (
            f"# Passwordless sudo for {user}: Mightling's root helper and nothing else. It runs\n"
            "# host setup's fixes; every other root command asks for the password.\n"
            f"{user} ALL=(root) NOPASSWD: {HELPER_PATH}\n"
        )

    def download_missing(self, host: str, session: FleetSession) -> bool:
        """Download, at the pinned revision, the model folders this machine had no copy of.

        Args:
            host: The machine.
            session: Its open session.

        Returns:
            bool: True when nothing is missing or the download succeeded.
        """
        missing = [
            folder
            for folder in FleetProbe.missing_models(
                self.provisioner._state(host), self.provisioner.plan["folders"]
            )
            if not FleetModelPlan.local_folders([folder])
        ]
        if not missing:
            return True
        model = self.provisioner.model
        return self._change(
            host,
            f"ling-admin model download --model {model} ({', '.join(missing)}: no copy here)",
            lambda: session.run_admin(
                ["model", "download", "--model", model], timeout=LONG_TIMEOUT_S
            ),
        )

    def listed(self, host: str, session: FleetSession) -> bool:
        """Check the paired node is among those `ling-admin node list` shows.

        Args:
            host: The machine.
            session: Its open session (unused).

        Returns:
            bool: True when it is.
        """
        node = self.provisioner.results[host].get("node")
        if any(record["node"] == node for record in NodePairing.paired()):
            return True
        return self.provisioner._fail(
            host, "node list", "not listed after pairing", "ling-admin node list"
        )

    def forge_mirror(self, host: str, session: FleetSession) -> bool:
        """Start the node's forge (a mirror once a record exists) and hand it the mirror plan.

        Args:
            host: The machine.
            session: Its open session.

        Returns:
            bool: True when its forge runs (or `--no-forge`).
        """
        if self.provisioner.options.get("no_forge"):
            return True
        running = session.run(
            FleetSession.with_docker_group(
                "docker ps --format '{{.Names}}' | grep -qx mightling-forge"
            )
        )
        if running.returncode != 0 and not self._change(
            host, "ling-admin forge on", lambda: session.run_admin(["forge", "on"], docker=True)
        ):
            return False
        if not self.dry_run:
            from dreamference.forge.forge_mirrors import ForgeMirrors

            if ForgeMirrors.sync(quiet=True) != 0:
                print(f"⚠️  {host}: `ling-admin forge mirrors sync` did not complete; re-run it.")
        return True

    def key_only_ssh(self, host: str, session: FleetSession) -> bool:
        """Turn password SSH off, only once a separate login with the key alone has worked.

        Args:
            host: The machine.
            session: Its open session.

        Returns:
            bool: True when the drop-in is in place and sshd still lets the key in.
        """
        password = self.provisioner.passwords.get(session.host)
        # Done when sshd itself says so (another drop-in read first would win over ours).
        if session.sudo(f"sh -c {shlex.quote(SSHD_KEY_ONLY)}", password).returncode == 0:
            return True
        if self.dry_run:
            return self._change(
                host, f"prove a key login, then key-only SSH ({SSHD_DROPIN_PATH})", None
            )
        if not session.key_login_works():
            return self.provisioner._fail(
                host,
                "key-only SSH",
                "a separate login with the key alone failed; password SSH left on",
                f"ssh -o IdentitiesOnly=yes -i {session.identity} {session.user}@{session.host} true",
            )
        validate = f"sshd -t || {{ rm -f {SSHD_DROPIN_PATH}; exit 1; }}; systemctl reload ssh && {SSHD_KEY_ONLY}"
        if not self._change(
            host,
            f"key-only SSH ({SSHD_DROPIN_PATH}, sshd -t, reload)",
            lambda: self._root_file(session, SSHD_DROPIN_PATH, SSHD_DROPIN, "0644", then=validate),
        ):
            return False
        if session.key_login_works():
            return True
        # Never leave a machine nobody can log in to: put password SSH back.
        session.sudo(f"sh -c 'rm -f {SSHD_DROPIN_PATH} && systemctl reload ssh'", password)
        return self.provisioner._fail(
            host, "key-only SSH", "the key stopped working after the reload; undone", ""
        )

    def role_setup(self, host: str, session: FleetSession) -> bool:
        """The role's own setup: SWE-bench and the GPU lock for `night`, build tools for `engine`.

        Args:
            host: The machine.
            session: Its open session.

        Returns:
            bool: True when the role's setup succeeded.
        """
        if self.role == ENGINE:
            return self._engine_setup(host, session)
        return self._night_setup(host, session)

    def _engine_setup(self, host: str, session: FleetSession) -> bool:
        """Install cmake and ninja when missing and read the CUDA version; drivers are never touched."""
        tools = session.run("command -v cmake && command -v ninja")
        if tools.returncode != 0 and not self._change(
            host,
            "apt-get install cmake ninja-build",
            lambda: session.sudo(
                "env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 "
                "install -y cmake ninja-build",
                self.provisioner.passwords.get(session.host),
            ),
        ):
            return False
        nvcc = session.run(f"{NVCC} --version")
        version = re.search(r"release ([0-9.]+)", nvcc.stdout)
        self.provisioner.results[host]["cuda"] = version.group(1) if version else ""
        return True

    def _night_setup(self, host: str, session: FleetSession) -> bool:
        """Make the SWE-bench folders and GPU lock files, then `swe-bench setup` and `smoke`."""
        result = self.provisioner.results[host]
        ready = session.run(
            " && ".join(
                [
                    *(f"test -d {path}" for path in NIGHT_DIRS),
                    *(f"test -f {path}" for path in GPU_LOCK_FILES),
                ]
            )
        )
        if ready.returncode != 0 and not self._change(
            host,
            "swe-bench/{chain,pins} and the GPU lock files",
            lambda: session.run(
                f"mkdir -p {' '.join(NIGHT_DIRS)} && touch {' '.join(GPU_LOCK_FILES)}"
            ),
        ):
            return False
        if session.run(SMOKE_PASSED).returncode == 0:
            result["swe-bench setup"] = result["swe-bench smoke"] = True
            return True
        if self.dry_run:
            print(f"📋 {host} would run: ling-admin swe-bench setup, then swe-bench smoke.")
            return True
        for step in ("setup", "smoke"):
            answer = session.run_admin(["swe-bench", step], timeout=LONG_TIMEOUT_S, docker=True)
            result[f"swe-bench {step}"] = answer.returncode == 0
            if answer.returncode != 0:
                return self.provisioner._fail(
                    host,
                    f"swe-bench {step}",
                    (answer.stdout or answer.stderr).strip()[-200:],
                    f"ssh {session.user}@{host} {REMOTE_ADMIN} swe-bench {step}",
                )
        return True

    def report(self, host: str, session: FleetSession) -> None:
        """Print the readiness table; the machine is complete only if every check passed.

        Args:
            host: The machine.
            session: Its open session.
        """
        from rich.console import Console
        from rich.table import Table

        result = self.provisioner.results[host]
        free = session.run('df -Pk "$HOME" | awk \'NR==2 {printf "%d", $4/1048576}\'').stdout
        if self.role == ENGINE:
            role_ok, role_detail = (
                bool(result.get("cuda")),
                f"CUDA {result.get('cuda') or 'not found'}",
            )
        else:
            role_ok = bool(result.get("swe-bench setup") and result.get("swe-bench smoke"))
            role_detail = "swe-bench setup and smoke" + ("" if role_ok else " failed")
        audit = session.run_admin(["audit", "egress"], timeout=30 * 60)
        checks = [
            ("host check", session.run_admin(["host", "check"]).returncode == 0, ""),
            (
                "model server canary",
                result.get("verify") == VERIFIED,
                str(result.get("verify", "")),
            ),
            ("egress audit", audit.returncode == 0, audit.stdout.strip()[-80:]),
            ("disk free", free.isdigit() and int(free) >= MIN_FREE_GIB, f"{free or '?'} GiB"),
            (f"role {self.role}", role_ok, role_detail),
        ]
        table = Table(title=f"{host} readiness")
        for column in ("check", "result", "detail"):
            table.add_column(column)
        for name, passed, detail in checks:
            table.add_row(name, PASS if passed else "fail", detail)
        Console().print(table)
        failed = [name for name, passed, _ in checks if not passed]
        result["complete"] = not failed and not result.get("failed")
        if failed:
            self.provisioner._fail(
                host,
                "readiness",
                ", ".join(failed),
                f"ling-admin node provision {host} --role {self.role}",
            )

    # -- helpers ------------------------------------------------------------------------------------

    def _change(
        self, host: str, what: str, apply: Callable[[], subprocess.CompletedProcess] | None
    ) -> bool:
        """Apply one change, or only name it on a dry run.

        Args:
            host: The machine.
            what: The change, as the run's output names it.
            apply: Makes the change; None where a dry run is the only caller.

        Returns:
            bool: True when it was made (or a dry run named it), False with the failure recorded.
        """
        if self.dry_run or apply is None:
            print(f"📋 {host} would: {what}")
            return True
        answer = apply()
        if answer.returncode != 0:
            return self.provisioner._fail(
                host, what, (answer.stderr or answer.stdout).strip()[-200:], ""
            )
        self.provisioner._done(
            host, what + (f" ({answer.stdout.strip()})" if answer.stdout.strip() else "")
        )
        return True

    def _root_file(
        self,
        session: FleetSession,
        path: str,
        content: str,
        mode: str,
        check: str = "",
        then: str = "",
    ) -> subprocess.CompletedProcess:
        """Install a root-owned file through one `sudo -S`, from a `mktemp` file.

        Args:
            session: The open session.
            path: Where the file goes.
            content: Its text (no secret: it travels on the command line).
            mode: Its octal mode.
            check: A command that must accept `"$f"` before it is installed (visudo).
            then: A command run as root after the install.

        Returns:
            subprocess.CompletedProcess: sudo's result.
        """
        encoded = base64.b64encode(content.encode()).decode()
        script = (
            f'f=$(mktemp) || exit 1; echo {encoded} | base64 -d > "$f" && chmod {mode} "$f" && '
            f"{check + ' && ' if check else ''}"
            f'install -o root -g root -m {mode} "$f" {path}; r=$?; rm -f "$f"; '
            f"[ $r = 0 ] || exit $r; {then or 'true'}"
        )
        return session.sudo(
            f"sh -c {shlex.quote(script)}", self.provisioner.passwords.get(session.host)
        )
