"""The `ling-admin` argument parser, which remembers each command group's own parser."""

import argparse


class CommandGroupParser(argparse.ArgumentParser):
    """An `ArgumentParser` that keeps, per command, the group's parser and its subcommand's name.

    `run_cli` prints a group's help from it when the group is named without a subcommand.
    """

    command_groups: dict[str, tuple[argparse.ArgumentParser, str]]

    def __init__(self, *args, **kwargs) -> None:
        """Builds the parser like `ArgumentParser`, with no command groups yet.

        Args:
            *args: Passed to `ArgumentParser`.
            **kwargs: Passed to `ArgumentParser`.
        """
        super().__init__(*args, **kwargs)
        self.command_groups = {}
