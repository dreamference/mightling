"""`git` that cannot be talked into running the agent's programs: the Python twin of `ling-rs/safegit`.

A checkout's `.git/config`, `.gitattributes` and `.git/info/attributes` are writable by the agent,
while Night Shift, the node jobs and the other host-side callers run `git` there outside the
sandbox. Several config keys make git execute a program during a plain read (`core.fsmonitor`,
`core.pager`, `diff.external`, `diff.<driver>.textconv`, `filter.<driver>.clean|smudge|process`,
`merge.<driver>.driver`, `core.sshCommand`, `core.gitProxy`, `remote.<name>.uploadpack` and
`.receivepack`, `credential.helper`, `gpg.program`, the `ext::` transport, `submodule.<n>.update`).

The command-line `-c` scope outranks every file, so the fixed keys are pinned there; the three
driver families whose names the agent chooses are read from the checkout's own config (scopes
local, worktree and command only, so the user's global git-lfs filter keeps working) and blanked
name by name. `--no-ext-diff --no-textconv` follow `diff`, `show` and `log`, and the stock
`--upload-pack=` and `--receive-pack=` follow `fetch`, `pull`, `ls-remote` and `push`
(`remote.<n>.uploadpack` is first-value-wins, so a `-c` cannot replace it but a flag outranks it).
The environment drops `GIT_EXTERNAL_DIFF` and friends and sets `GIT_CONFIG_NOSYSTEM`.

Repository hooks RUN (`core.hooksPath` is not overridden and nothing passes `--no-verify`): the
owner's choice of 2026-10-28, "allow all for now". The known risk is that a hook the agent wrote
into `.git/hooks` runs on the host; see docs/dev/host-safety.md. One more window stays: a driver
name added between the config read and the command.
"""

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from typing import Final

DEFAULT_TIMEOUT_S: Final[int] = 120
NETWORK_TIMEOUT_S: Final[int] = 600
TIMED_OUT_EXIT: Final[int] = 124

FIXED_CONFIG: Final[tuple[str, ...]] = (
    "core.fsmonitor=false",
    "core.pager=cat",
    "core.sshCommand=ssh",
    "core.gitProxy=",
    "core.askPass=",
    "core.editor=true",
    "sequence.editor=true",
    "diff.external=",
    "diff.submodule=short",
    "credential.helper=",
    "gpg.program=gpg",
    "gpg.ssh.program=ssh-keygen",
    "gpg.x509.program=gpgsm",
    "gpg.openpgp.program=gpg",
    "gpg.ssh.defaultKeyCommand=",
    "log.showSignature=false",
    "protocol.ext.allow=never",
    "uploadpack.packObjectsHook=",
    "submodule.recurse=false",
    "status.submoduleSummary=false",
    "gc.auto=0",
    "maintenance.auto=false",
    "core.quotepath=off",
)
REMOVED_ENV: Final[tuple[str, ...]] = (
    "GIT_EXTERNAL_DIFF",
    "GIT_CONFIG_PARAMETERS",
)
FIXED_ENV: Final[dict[str, str]] = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    # Empty, not removed: core.gitProxy is first-value-wins, so a -c pair cannot replace the
    # checkout's own value, but git reads no core.gitProxy once this is set, and empty means none.
    "GIT_PROXY_COMMAND": "",
}
_UNWRITABLE: Final[re.Pattern] = re.compile("[=\n]")
_NETWORK_SUBCOMMANDS: Final[frozenset[str]] = frozenset(
    {"fetch", "pull", "push", "ls-remote", "clone"}
)


class SafeGit:
    """Builds and runs `git` commands with the config-driven program execution switched off."""

    @classmethod
    def environment(cls, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """The base environment made safe for git.

        Args:
            base: The environment to start from; the process's own when omitted. A caller's
                `GIT_SSH_COMMAND` is kept (it outranks `core.sshCommand` and is the caller's).

        Returns:
            dict[str, str]: `base` without the program-naming variables and with the fixed ones.
        """
        env = dict(os.environ if base is None else base)
        for name in REMOVED_ENV:
            env.pop(name, None)
        env.update(FIXED_ENV)
        return env

    @classmethod
    def _location(
        cls, directory: str | os.PathLike | None, git_dir: str | os.PathLike | None
    ) -> list[str]:
        if git_dir is not None:
            return ["--git-dir", str(git_dir)]
        if directory is not None:
            return ["-C", str(directory)]
        return []

    @classmethod
    def driver_overrides(
        cls, directory: str | os.PathLike | None = None, git_dir: str | os.PathLike | None = None
    ) -> list[str]:
        """The `key=` pairs that blank the repository's own driver programs.

        Args:
            directory: The checkout (`-C`).
            git_dir: A bare repository or a git directory (`--git-dir`), instead of `directory`.

        Returns:
            list[str]: `key=` pairs blanking every driver program the repository's own config
            names (filter, textconv, diff command, merge driver, submodule update). Empty when git
            cannot say.
        """
        command = [
            "git",
            "-c",
            "safe.directory=*",
            *cls._location(directory, git_dir),
            "config",
            "--list",
            "--null",
            "--show-scope",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                env=cls.environment(),
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        fields = result.stdout.decode("utf-8", "replace").split("\0")
        found: set[str] = set()
        for scope, entry in zip(fields[0::2], fields[1::2], strict=False):
            if scope not in ("local", "worktree", "command"):
                continue
            key = entry.split("\n", 1)[0]
            family, _, rest = key.partition(".")
            name, _, variable = rest.rpartition(".")
            if not name or _UNWRITABLE.search(name):
                continue
            blank = {
                ("filter", "clean"): "",
                ("filter", "smudge"): "",
                ("filter", "process"): "",
                ("filter", "required"): "false",
                ("diff", "textconv"): "",
                ("diff", "command"): "",
                ("merge", "driver"): "false",
                ("submodule", "update"): "none",
            }.get((family, variable.lower()))
            if blank is not None:
                found.add(f"{key}={blank}")
        return sorted(found)

    @classmethod
    def _subcommand_index(cls, args: Sequence[str]) -> int | None:
        i = 0
        while i < len(args):
            if args[i] in (
                "-c",
                "-C",
                "--git-dir",
                "--work-tree",
                "--namespace",
                "--super-prefix",
                "--config-env",
            ):
                i += 2
            elif args[i].startswith("-"):
                i += 1
            else:
                return i
        return None

    @classmethod
    def safe_flags(cls, args: Sequence[str]) -> list[str]:
        """Places the flags config cannot override after the subcommand.

        Args:
            args: The arguments after `git` as the caller means them.

        Returns:
            list[str]: `args` with `--no-ext-diff --no-textconv` after `diff`/`show`/`log`,
            `--upload-pack=` after `fetch`/`pull`/`ls-remote` and `--receive-pack=` after `push`.
        """
        out = list(args)
        i = cls._subcommand_index(args)
        if i is not None:
            extra = {
                "diff": ["--no-ext-diff", "--no-textconv"],
                "show": ["--no-ext-diff", "--no-textconv"],
                "log": ["--no-ext-diff", "--no-textconv"],
                "fetch": ["--upload-pack=git-upload-pack"],
                "pull": ["--upload-pack=git-upload-pack"],
                "ls-remote": ["--upload-pack=git-upload-pack"],
                "push": ["--receive-pack=git-receive-pack"],
            }.get(args[i], [])
            out[i + 1 : i + 1] = extra
        return out

    @classmethod
    def command(
        cls,
        args: Sequence[str],
        directory: str | os.PathLike | None = None,
        git_dir: str | os.PathLike | None = None,
    ) -> list[str]:
        """The complete hardened command line.

        Args:
            args: Everything after `git` (and after `-C <directory>`), as the caller means it.
            directory: Run in this checkout (`-C`); its driver names are blanked.
            git_dir: Run against this git directory (`--git-dir`) instead.

        Returns:
            list[str]: The complete hardened command line, starting with `git`.
        """
        pairs = list(FIXED_CONFIG)
        if directory is not None or git_dir is not None:
            pairs += cls.driver_overrides(directory, git_dir)
        config = [item for pair in pairs for item in ("-c", pair)]
        return [
            "git",
            "--no-pager",
            *config,
            *cls._location(directory, git_dir),
            *cls.safe_flags(args),
        ]

    @classmethod
    def run(
        cls,
        args: Sequence[str],
        directory: str | os.PathLike | None = None,
        git_dir: str | os.PathLike | None = None,
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        text: bool = True,
    ) -> "subprocess.CompletedProcess":
        """Runs a hardened command with captured output, no stdin of its own and a timeout.

        Args:
            args: Everything after `git`.
            directory: The checkout (`-C`).
            git_dir: A git directory (`--git-dir`), instead of `directory`.
            timeout: Seconds before the command is killed; `DEFAULT_TIMEOUT_S`, or
                `NETWORK_TIMEOUT_S` for fetch, pull, push, ls-remote and clone.
            env: The environment to start from (see `environment`).
            text: Decode output as text.

        Returns:
            subprocess.CompletedProcess: The result. On a timeout the exit code is 124 and stderr
            says so, so no caller has to catch an exception it never had.
        """
        if timeout is None:
            index = cls._subcommand_index(args)
            network = index is not None and args[index] in _NETWORK_SUBCOMMANDS
            timeout = NETWORK_TIMEOUT_S if network else DEFAULT_TIMEOUT_S
        command = cls.command(args, directory, git_dir)
        kwargs: dict = {
            "capture_output": True,
            "text": text,
            "env": cls.environment(env),
            "timeout": timeout,
            "check": False,
            "stdin": subprocess.DEVNULL,
        }
        try:
            return subprocess.run(command, **kwargs)
        except subprocess.TimeoutExpired:
            message = f"git timed out after {timeout:g}s"
            return subprocess.CompletedProcess(
                command, TIMED_OUT_EXIT, "" if text else b"", message if text else message.encode()
            )
        except FileNotFoundError as error:
            return subprocess.CompletedProcess(
                command, 127, "" if text else b"", str(error) if text else str(error).encode()
            )
