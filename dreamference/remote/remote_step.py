"""One step of a remote-access plan: a command, shown before it runs (REMOTE_ACCESS §3).

`remote setup` prints every step that changes the machine outside the user's own folders before
it asks, as `ling signal setup` does, so the plan a user reads is the plan that runs. A step can
carry a secret (a setup key, a token) that is passed on standard input and never displayed.
"""

import shlex
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RemoteStep:
    """A command of a plan.

    Attributes:
        what: The line that says what it does, in the plan.
        argv: The command.
        root: Whether it runs under sudo.
        stdin: Text for the command's standard input (a unit file through `tee`), or None.
        secret: Whether `stdin` must never be shown.
    """

    what: str
    argv: tuple[str, ...]
    root: bool = False
    stdin: str | None = field(default=None, repr=False)
    secret: bool = False

    @classmethod
    def user(cls, what: str, *argv: str, stdin: str | None = None) -> "RemoteStep":
        """A step run as the user.

        Args:
            what: What it does.
            *argv: The command.
            stdin: Its standard input, if any.

        Returns:
            RemoteStep: The step.
        """
        return cls(what, tuple(argv), stdin=stdin)

    @classmethod
    def sudo(
        cls, what: str, *argv: str, stdin: str | None = None, secret: bool = False
    ) -> "RemoteStep":
        """A step run under sudo.

        Args:
            what: What it does.
            *argv: The command.
            stdin: Its standard input, if any.
            secret: Whether the input must never be shown.

        Returns:
            RemoteStep: The step.
        """
        return cls(what, tuple(argv), root=True, stdin=stdin, secret=secret)

    def command(self) -> list[str]:
        """The argv as it runs: under `sudo` for a root step.

        Returns:
            list[str]: The command.
        """
        return ["sudo", *self.argv] if self.root else list(self.argv)

    def display(self) -> str:
        """The command as the plan shows it.

        Returns:
            str: A shell-quoted line, with `(input withheld)` for a secret input.
        """
        line = shlex.join(self.command())
        return f"{line}   (input withheld)" if self.secret else line
