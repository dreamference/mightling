"""The forge's operations on a paired node's forced command (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §2.1, §6).

`ling-admin node serve-job` hands every `forge-*` request here. No password crosses: the mirror plan
carries the record's read-only mirror token, and a backup set's secrets go only to the owner's
paired nodes.
"""

import sys
from typing import Final

from dreamference.forge.forge_backup import ForgeBackup
from dreamference.forge.forge_mirrors import ForgeMirrors
from dreamference.forge.forge_move import ForgeMove

REFUSED: Final[int] = 2
REFUSAL: Final[str] = (
    "ling-admin node serve-job: forge operations are forge-mirror-plan, forge-mirror-apply, "
    "forge-backup-receive <set>, forge-restore <set> [<node id>], forge-promote, forge-hand-over, "
    "forge-unfreeze and forge-become-mirror <node id> <name>."
)
PLAN: Final[str] = "forge-mirror-plan"
APPLY: Final[str] = "forge-mirror-apply"
RECEIVE: Final[str] = "forge-backup-receive"
RESTORE: Final[str] = "forge-restore"
PROMOTE: Final[str] = "forge-promote"
HAND_OVER: Final[str] = "forge-hand-over"
UNFREEZE: Final[str] = "forge-unfreeze"
BECOME_MIRROR: Final[str] = "forge-become-mirror"
BECOME_MIRROR_ARGUMENTS: Final[int] = 2
RESTORE_ARGUMENTS: Final[tuple[int, ...]] = (1, 2)


class ForgeServe:
    """Carries out one forge operation for a paired key."""

    @classmethod
    def serve(cls, operation: str, arguments: list[str]) -> int:
        """Run one operation, refusing anything else.

        Args:
            operation: The operation's name.
            arguments: Its arguments.

        Returns:
            int: The exit code; 2 when refused.
        """
        if operation == PLAN and not arguments:
            return ForgeMirrors.serve_plan()
        if operation == APPLY and not arguments:
            return ForgeMirrors.serve_apply(sys.stdin.read())
        if operation == RECEIVE and len(arguments) == 1:
            return ForgeBackup.receive(arguments[0], sys.stdin.buffer)
        if operation == RESTORE and len(arguments) in RESTORE_ARGUMENTS:
            return ForgeMove.serve_restore(
                arguments[0], arguments[1] if len(arguments) > 1 else None
            )
        if operation == PROMOTE and not arguments:
            return ForgeMove.serve_promote()
        return cls.serve_move(operation, arguments)

    @classmethod
    def serve_move(cls, operation: str, arguments: list[str]) -> int:
        """Run one of the three operations a mirror pulling the role over asks for.

        Args:
            operation: The operation's name.
            arguments: Its arguments.

        Returns:
            int: The exit code; 2 when refused.
        """
        if operation == HAND_OVER and not arguments:
            return ForgeMove.serve_hand_over()
        if operation == UNFREEZE and not arguments:
            ForgeMove.freeze(False)
            return 0
        if operation == BECOME_MIRROR and len(arguments) == BECOME_MIRROR_ARGUMENTS:
            return ForgeMove.serve_become_mirror(arguments[0], arguments[1])
        sys.stderr.write(REFUSAL + "\n")
        return REFUSED
