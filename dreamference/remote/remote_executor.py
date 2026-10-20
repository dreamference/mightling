"""Where remote access runs commands: the one seam the tests replace (REMOTE_ACCESS §9, item 5).

Docker, systemd, openssl, ssh-keygen, sudo and NetBird's client are all reached through `run`, so
a test replaces that one method and sees every command the code would have run.
"""

import subprocess

from dreamference.remote.remote_step import RemoteStep

DEFAULT_TIMEOUT_S = 120
# What a missing program returns, as a shell would.
NOT_FOUND = 127


class RemoteExecutor:
    """Runs commands for remote access."""

    @classmethod
    def run(
        cls, argv: list[str], stdin: str | None = None, timeout: int = DEFAULT_TIMEOUT_S
    ) -> subprocess.CompletedProcess[str]:
        """Runs a command, capturing its output. A missing program is a failed step, not an error.

        Args:
            argv: The command.
            stdin: Text for its standard input, if any.
            timeout: Seconds before it is stopped.

        Returns:
            subprocess.CompletedProcess[str]: What it returned.
        """
        try:
            return subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as error:
            return subprocess.CompletedProcess(argv, NOT_FOUND, "", str(error))
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 1, "", f"timed out after {timeout} s")

    @classmethod
    def ok(
        cls, argv: list[str], stdin: str | None = None, timeout: int = DEFAULT_TIMEOUT_S
    ) -> bool:
        """Runs a command and says whether it succeeded.

        Args:
            argv: The command.
            stdin: Its standard input, if any.
            timeout: Seconds before it is stopped.

        Returns:
            bool: Whether it exited 0.
        """
        return cls.run(argv, stdin, timeout).returncode == 0

    @classmethod
    def run_step(
        cls, step: RemoteStep, timeout: int = DEFAULT_TIMEOUT_S
    ) -> subprocess.CompletedProcess[str]:
        """Runs one step of a plan.

        Args:
            step: The step.
            timeout: Seconds before it is stopped.

        Returns:
            subprocess.CompletedProcess[str]: What it returned.
        """
        return cls.run(step.command(), step.stdin, timeout)
