"""The provisioning session to one machine (specs/DREAMFERENCE_MIGHTLING_FLEET.md §8.1).

An ordinary SSH login, authenticated by the account's password, multiplexed so the password is
typed (or supplied) once: every later command and copy rides the same connection, and the master
is closed at the end of the run. It is the only channel that installs anything or runs anything as
root. The pairing key of MIGHTLING_NODE §13.2 is never used for that.

A password is never written to disk, put on a command line or into an environment variable:
`ssh` asks for it through `FleetAskpass` (a socket in a 0700 folder) and `sudo -S` reads it from
the remote command's standard input over the encrypted channel. The run's log records every
command and its output, never what was written to a command's input.
"""

import logging
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Final

LOG: Final[logging.Logger] = logging.getLogger(__name__)

# Seconds to wait for the first connection, which may sit at a password prompt.
CONNECT_TIMEOUT: Final[int] = 15

# The remote `ling-admin`, by the path `install.sh` gives it (the link in ~/.local/bin is not
# on the PATH of a non-interactive SSH command).
REMOTE_ADMIN: Final[str] = "$HOME/.local/share/dreamference/venv/bin/ling-admin"


class FleetSession:
    """One multiplexed SSH login to a machine being provisioned."""

    def __init__(
        self,
        host: str,
        user: str,
        run_dir: Path,
        ssh_port: int = 22,
        log_path: Path | None = None,
        identity: Path | None = None,
    ) -> None:
        """Args:
        host: The machine's name or address.
        user: The account on it.
        run_dir: This run's private folder (0700): the control socket and the run's own
            known-hosts file live there.
        ssh_port: The machine's SSH port.
        log_path: Where each command and its output is appended; None for no log.
        identity: The one key the login offers (`-i`); None for the keys `~/.ssh/config`
            names for the host, or the default files.
        """
        self.host = host
        self.user = user
        self.ssh_port = ssh_port
        self.run_dir = run_dir
        self.log_path = log_path
        self.identity = identity
        # The standard error of the last `open`, for the "Permission denied" hint of pairing.
        self.last_error = ""
        safe = "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in host)
        self.control_path = run_dir / f"cm-{safe}"
        self.known_hosts = run_dir / "known_hosts"
        self.askpass_env: dict | None = None

    # -- the connection ---------------------------------------------------------------------------

    def options(self, first_contact: bool = False) -> list[str]:
        """Args:
            first_contact: True for the command that opens the master, the only one that may
                store a host key (`accept-new`).

        Returns:
            List[str]: The ssh options every command of the session uses: the run's known-hosts
            file, the control socket, no agent or port forwarding.
        """
        return [
            *(["-i", str(self.identity)] if self.identity else []),
            "-p",
            str(self.ssh_port),
            "-o",
            f"ControlPath={self.control_path}",
            # Only `open` starts the master; a command finding no master fails instead of
            # opening a second login that would ask for the password again.
            "-o",
            f"ControlMaster={'yes' if first_contact else 'no'}",
            "-o",
            "ControlPersist=yes",
            "-o",
            f"UserKnownHostsFile={self.known_hosts}",
            # The run's known-hosts file names the key after the host as given here, whatever
            # `~/.ssh/config` says: an alias there (node-b's `HostKeyAlias gx10-3c4d`) filed
            # it under that name, the fingerprint printed `?` and pairing found no key to pin
            # (first dry run against a real machine, 2026-10-20).
            "-o",
            f"HostKeyAlias={self._known_host_name()}",
            "-o",
            f"StrictHostKeyChecking={'accept-new' if first_contact else 'yes'}",
            "-o",
            f"ConnectTimeout={CONNECT_TIMEOUT}",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "PreferredAuthentications=publickey,keyboard-interactive,password",
            # Only the keys `~/.ssh/config` names for this host (or the default files), never
            # every key an agent holds: with seven in this machine's agent, sshd's MaxAuthTries
            # (6) ended the login before the password prompt was ever reached (2026-10-20).
            "-o",
            "IdentitiesOnly=yes",
        ]

    def target(self) -> str:
        """Returns:
        str: `user@host` for ssh.
        """
        return f"{self.user}@{self.host}"

    def open(self) -> bool:
        """Opens the master connection: the password is asked here, by ssh's own prompt, or answered
        by `FleetAskpass` when the run holds it.

        Returns:
            bool: True once the master is up.
        """
        self.run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        command = ["ssh", *self.options(first_contact=True), "-f", "-N", self.target()]
        environment = None if self.askpass_env is None else {**os.environ, **self.askpass_env}
        # `-f` leaves the master running in the background holding whatever it inherited, so its
        # output goes to a file, never to a pipe this process would wait on for ever.
        with tempfile.TemporaryFile("w+") as errors:
            try:
                code = subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=errors,
                    env=environment,
                    stdin=subprocess.DEVNULL if self.askpass_env else None,
                    timeout=CONNECT_TIMEOUT * 8,
                    check=False,
                ).returncode
            except (OSError, subprocess.TimeoutExpired) as error:
                code = 255
                errors.write(str(error))
            errors.seek(0)
            result = subprocess.CompletedProcess(command, code, "", errors.read())
        self._log(command, result)
        self.last_error = result.stderr
        return code == 0

    def key_login_works(self) -> bool:
        """Return whether a fresh login gets in with the session's key alone.

        A separate connection, never the master: no control socket, no password, no prompt. Key-only
        SSH is turned on only after this has worked.

        Returns:
            bool: True when `ssh … true` with the identity alone succeeded.
        """
        if self.identity is None:
            return False
        command = [
            "ssh",
            "-i",
            str(self.identity),
            "-p",
            str(self.ssh_port),
            "-o",
            "ControlPath=none",
            "-o",
            "BatchMode=yes",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            f"UserKnownHostsFile={self.known_hosts}",
            "-o",
            f"HostKeyAlias={self._known_host_name()}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"ConnectTimeout={CONNECT_TIMEOUT}",
            "-T",
            self.target(),
            "true",
        ]
        result = self._spawn(command, capture=True, timeout=CONNECT_TIMEOUT * 2)
        self._log(command, result)
        return result.returncode == 0

    def is_open(self) -> bool:
        """Returns:
        bool: True if the master answers `ssh -O check`.
        """
        command = ["ssh", "-o", f"ControlPath={self.control_path}", "-O", "check", self.target()]
        return self._spawn(command, capture=True).returncode == 0

    def close(self) -> None:
        """Closes the master. Safe to call twice, and from a signal handler."""
        command = ["ssh", "-o", f"ControlPath={self.control_path}", "-O", "exit", self.target()]
        self._spawn(command, capture=True)

    def host_key_fingerprint(self) -> str | None:
        """Returns:
        Optional[str]: The SHA256 fingerprint of the host key the session accepted, printed so
        it can be compared with what the machine shows locally; None if none was stored.
        """
        if not self.known_hosts.is_file():
            return None
        result = self._spawn(
            ["ssh-keygen", "-l", "-F", self._known_host_name(), "-f", str(self.known_hosts)],
            capture=True,
        )
        for line in result.stdout.splitlines():
            if "SHA256:" in line:
                return line.split("SHA256:", 1)[1].split()[0]
        return None

    def host_key_lines(self) -> list[str]:
        """Returns:
        List[str]: The host key the session accepted, as `known_hosts` lines without the host
        field (`<type> <key>`), for pinning the same key to the node id at pairing.
        """
        result = self._spawn(
            ["ssh-keygen", "-F", self._known_host_name(), "-f", str(self.known_hosts)], capture=True
        )
        keys = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if line.startswith("#") or len(fields) < 3:
                continue
            keys.append(" ".join(fields[1:3]))
        return keys

    # -- running things there ---------------------------------------------------------------------

    def run(
        self,
        script: str,
        input_text: str | None = None,
        terminal: bool = False,
        secret_input: bool = False,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess:
        """Runs a shell command on the machine through the open master.

        Args:
            script: The remote shell command line.
            input_text: Text for its standard input.
            terminal: Allocate a terminal (`-t`), so a remote prompt such as sudo's reaches the
                user; the output then streams here instead of being captured.
            secret_input: The input is a password: it is never logged.
            timeout: Seconds before giving up; None for none.

        Returns:
            subprocess.CompletedProcess: The result (stdout and stderr captured unless terminal).
        """
        command = ["ssh", *self.options(), *(["-t"] if terminal else ["-T"]), self.target(), script]
        result = self._spawn(command, capture=not terminal, input_text=input_text, timeout=timeout)
        self._log(command, result, input_note="<password>" if secret_input and input_text else None)
        return result

    def run_admin(
        self, arguments: list[str], timeout: int | None = None, docker: bool = False
    ) -> subprocess.CompletedProcess:
        """Runs the machine's own `ling-admin`.

        Args:
            arguments: The command line after `ling-admin`.
            timeout: Seconds before giving up.
            docker: Run it under `sg docker`, for a command that talks to Docker: the session's
                login predates the group `node prepare` added (§8.1).

        Returns:
            subprocess.CompletedProcess: The result.
        """
        line = " ".join([REMOTE_ADMIN, *(shlex.quote(part) for part in arguments)])
        return self.run(self.with_docker_group(line) if docker else line, timeout=timeout)

    def sudo(self, line: str, password: str | None) -> subprocess.CompletedProcess:
        """Runs one command as root there.

        Args:
            line: The remote command line, without `sudo`.
            password: The account's password when the run holds it: written to `sudo -S`'s
                standard input. None to let sudo prompt on a remote terminal instead.

        Returns:
            subprocess.CompletedProcess: The result.
        """
        if password is None:
            return self.run(f"sudo {line}", terminal=True)
        return self.run(f"sudo -S -p '' {line}", input_text=password + "\n", secret_input=True)

    @classmethod
    def with_docker_group(cls, line: str) -> str:
        """Args:
            line: A shell command line that talks to Docker.

        Returns:
            str: The same line run through `sg docker -c`, which needs only the group's entry in
            `/etc/group`, not a new login.
        """
        return f"sg docker -c {shlex.quote(line)}"

    # rsync's and ssh's exits that mean the connection went, not that the copy is wrong: the
    # protocol or stream ended early (10, 12, 30 timeout, 35), or ssh itself failed (255).
    RSYNC_DROPPED: tuple[int, ...] = (10, 12, 30, 35, 255)

    def rsync(
        self,
        source: Path,
        destination: str,
        attempts: int = 1,
        wait_s: float = 20.0,
        files: list[str] | None = None,
    ) -> subprocess.CompletedProcess:
        """Copies a folder there over the session: `rsync -aH --partial --append-verify`, so the
        hub cache's links survive and an interrupted copy resumes where it stopped.

        A copy over the internet (a node at another site, through the overlay) drops now and then:
        rsync gives up after `--timeout` of silence, the ssh keep-alive notices a dead path, and
        when the exit code says the connection went, the copy is started again (up to `attempts`
        times), carrying on from the partial file instead of from zero. A weight file is named by
        its SHA-256 in the hub cache, so `--append-verify` (append, then check the whole file) is
        safe and spares re-reading what already arrived. Any other exit is a real failure.

        Args:
            source: The local folder.
            destination: The remote folder (may start with `~/`).
            attempts: How many times to try while the connection keeps dropping.
            wait_s: Seconds to wait between attempts.
            files: Only these paths of the source folder (relative to it), with links kept as
                links, instead of the whole folder.

        Returns:
            subprocess.CompletedProcess: rsync's result (the last attempt's).
        """
        listing: Path | None = None
        if files is not None:
            self.run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", dir=self.run_dir, prefix="files-", delete=False
            ) as handle:
                handle.write("\n".join(files) + "\n")
                listing = Path(handle.name)
        try:
            return self._rsync(source, destination, attempts, wait_s, listing)
        finally:
            if listing is not None:
                listing.unlink(missing_ok=True)

    def _rsync(
        self, source: Path, destination: str, attempts: int, wait_s: float, listing: Path | None
    ) -> subprocess.CompletedProcess:
        transport = " ".join(
            [
                "ssh",
                *(shlex.quote(option) for option in self.options()),
                "-o",
                "ServerAliveInterval=15",
                "-o",
                "ServerAliveCountMax=4",
            ]
        )
        command = [
            "rsync",
            "-aH",
            "--partial",
            "--append-verify",
            "--timeout=120",
            "--mkpath",
            *([f"--files-from={listing}"] if listing is not None else []),
            "-e",
            transport,
            f"{source}/",
            # rsync splits `host:path` at the first colon, so an IPv6 host goes in brackets.
            f"{self.user}@[{self.host}]:{destination}/"
            if ":" in self.host
            else f"{self.target()}:{destination}/",
        ]
        result = self._spawn(command, capture=True)
        self._log(command, result)
        for attempt in range(2, attempts + 1):
            if result.returncode not in self.RSYNC_DROPPED:
                break
            LOG.warning(
                f"⏳ The connection to {self.host} dropped (rsync exit {result.returncode}); "
                f"resuming in {wait_s:.0f} s (attempt {attempt} of {attempts})."
            )
            time.sleep(wait_s)
            result = self._spawn(command, capture=True)
            self._log(command, result)
        return result

    def pipe_into(self, producer: list[list[str]], remote_line: str) -> int:
        """Streams the output of a local pipeline into a remote command (`docker save … | zstd`
        into `zstd -d | docker load`).

        Args:
            producer: The local commands, piped one into the next.
            remote_line: The remote command that reads the stream.

        Returns:
            int: The remote command's exit code, or the first failing local one's.
        """
        processes: list[subprocess.Popen] = []
        upstream = None
        for command in producer:
            process = subprocess.Popen(command, stdin=upstream, stdout=subprocess.PIPE)
            if upstream is not None:
                upstream.close()
            upstream = process.stdout
            processes.append(process)
        ssh = ["ssh", *self.options(), "-T", self.target(), remote_line]
        receiver = subprocess.Popen(ssh, stdin=upstream)
        if upstream is not None:
            upstream.close()
        code = receiver.wait()
        for process in processes:
            local = process.wait()
            if local != 0 and code == 0:
                code = local
        self._log(
            ssh,
            subprocess.CompletedProcess(ssh, code, "", ""),
            input_note=" | ".join(" ".join(c) for c in producer),
        )
        return code

    # -- internals --------------------------------------------------------------------------------

    def _known_host_name(self) -> str:
        """The name the session's host key is filed under: `HostKeyAlias`, so never with a port."""
        return self.host if self.ssh_port == 22 else f"{self.host}-port-{self.ssh_port}"

    @classmethod
    def _spawn(
        cls,
        command: list[str],
        capture: bool,
        input_text: str | None = None,
        env: dict | None = None,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess:
        environment = None if env is None else {**os.environ, **env}
        try:
            return subprocess.run(
                command,
                capture_output=capture,
                text=True,
                input=input_text,
                env=environment,
                timeout=timeout,
                check=False,
                stdin=None if input_text is not None else (subprocess.DEVNULL if capture else None),
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(command, 124, "", "timed out")
        except OSError as error:
            return subprocess.CompletedProcess(command, 255, "", str(error))

    def _log(
        self,
        command: list[str],
        result: subprocess.CompletedProcess,
        input_note: str | None = None,
    ) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"$ {shlex.join(command)}"]
        if input_note:
            lines.append(f"  (input: {input_note})")
        for stream in (result.stdout, result.stderr):
            if stream:
                lines.append(stream.rstrip())
        lines.append(f"  -> exit {result.returncode}")
        with Path(self.log_path).open("a") as handle:
            handle.write("\n".join(lines) + "\n")

    @classmethod
    def new_run_dir(cls) -> Path:
        """Returns:
        Path: A fresh 0700 folder for one run's sockets and known-hosts file, under
        `$XDG_RUNTIME_DIR` when there is one.
        """
        base = os.environ.get("XDG_RUNTIME_DIR") or None
        return Path(tempfile.mkdtemp(prefix="mightling-fleet-", dir=base))
