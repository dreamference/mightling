"""`ling-admin node power-cycle <node>`: switch a hung node's smart plug off and on again.

The recovery for a node at another site that no longer answers: the plug is the only thing left
to reach. The plug's own local API does the switching (`NodeShellyPlug`); how this machine reaches
the plug depends on where it is:

- **Direct:** the plug is on a network this machine is on, and the call is made from here.
- **Through a neighbour:** the plug is on the node's house LAN, which this machine cannot reach.
  Another paired node there (`via`) is on that LAN and on the overlay, and acts for it: the request
  `power-plug <plug name> <on|off|status>` goes over the pairing's forced command, and that node
  looks the name up in **its own** `node-power.json`. A sender can name only a plug the neighbour
  already knows, never a host.

Before it cuts power it checks that the node really does not answer (a power cut can lose a running
job or corrupt a file system); `--force` skips that. After switching on it waits for the node to
answer over the pairing again. The node's BIOS must be set to start on power restore, or the plug
turns the machine's mains on and nothing boots: tested once by hand before it is relied on.
"""

import json
import subprocess
import sys
import time
from typing import Any, Final

from dreamference.node.node_pairing import NodePairing
from dreamference.node.node_power_settings import NodePowerSettings
from dreamference.node.node_shelly_plug import NodeShellyPlug

ON: Final[str] = "on"
OFF: Final[str] = "off"
STATUS: Final[str] = "status"
ACTIONS: Final[tuple[str, ...]] = (ON, OFF, STATUS)
RESTORE_TRIES: Final[int] = 5
SERVE_ARGUMENTS: Final[int] = 2
OFF_SECONDS: Final[int] = 20
MIN_OFF_SECONDS: Final[int] = 5
WAIT_MINUTES: Final[int] = 10
POLL_SECONDS: Final[float] = 10.0


class NodePowerCycle:
    """Power-cycles a node through its plug."""

    # -- the plug's side (the machine that can reach it) ------------------------------------------

    @classmethod
    def act(cls, plug_name: str, action: str) -> dict[str, Any]:
        """Switches or reads one plug of this machine's own table.

        Args:
            plug_name: A name in `node-power.json`'s `plugs`.
            action: `on`, `off` or `status`.

        Returns:
            Dict[str, Any]: The plug's state after the action (`on`, `power_w`).

        Raises:
            KeyError: No such plug here.
            OSError: The plug did not answer or did not reach the asked state.
            ValueError: The action or the configuration is not acceptable.
        """
        if action not in ACTIONS:
            raise ValueError(f"{action!r} is not one of {', '.join(ACTIONS)}")
        entry = NodePowerSettings.load()["plugs"].get(plug_name)
        if entry is None:
            raise KeyError(plug_name)
        host, channel, generation = (
            str(entry.get("host", "")),
            int(entry.get("channel", 0)),
            int(entry.get("generation", 2)),
        )
        if action == STATUS:
            return NodeShellyPlug.state(host, channel, generation)
        return NodeShellyPlug.switch(host, action == ON, channel, generation)

    @classmethod
    def serve(cls, arguments: list[str]) -> int:
        """The forced command's `power-plug <plug name> <action>`: prints the state as one JSON line.

        Args:
            arguments: The plug's name and the action.

        Returns:
            int: 0 on success, 1 when the plug failed, 2 for a request that is not understood.
        """
        if len(arguments) != SERVE_ARGUMENTS:
            print("power-plug <plug name> <on|off|status>", file=sys.stderr)
            return 2
        try:
            print(json.dumps(cls.act(arguments[0], arguments[1])))
        except KeyError:
            print(f"no plug named {arguments[0]} on this node", file=sys.stderr)
            return 2
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
        except OSError as error:
            print(f"the plug failed: {error}", file=sys.stderr)
            return 1
        return 0

    # -- the sender's side ------------------------------------------------------------------------

    @classmethod
    def _plug(cls, node: str, plug: str, via: str | None, action: str) -> dict[str, Any]:
        """One plug action, direct or through the neighbour. Raises OSError with the reason."""
        if via is None:
            try:
                return cls.act(plug, action)
            except (KeyError, ValueError) as error:
                raise OSError(str(error)) from error
        record = NodePairing.find(via, browse=False)
        if record is None:
            raise OSError(f"{via} is not a paired node (`ling-admin node add {via}`)")
        answer = NodePairing.run(record, f"power-plug {plug} {action}")
        if answer.returncode != 0:
            raise OSError(
                (answer.stderr or answer.stdout).strip()[-200:] or f"{via} did not answer"
            )
        try:
            state = json.loads(answer.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as error:
            raise OSError(f"{via} answered with something unreadable") from error
        return state if isinstance(state, dict) else {}

    @classmethod
    def _answers(cls, node: str) -> bool:
        record = NodePairing.find(node, browse=False)
        if record is None:
            return False
        try:
            return NodePairing.run(record, "info").returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @classmethod
    def _cut_and_restore(cls, node: str, plug: str, via: str | None, off_seconds: int) -> bool:
        """Reads the plug, turns it off, waits, and turns it on again; True once it is on.

        Once an `off` was sent, `on` is always tried (five times), even when the `off` reply was
        lost: the plug may be off, and a node left dark by this command is worse than a hung one.
        """
        try:
            before = cls._plug(node, plug, via, STATUS)
        except OSError as error:
            print(f"❌ The plug could not be reached: {error}")
            return False
        watts = before.get("power_w")
        print(
            f"🔌 {plug}: {'on' if before.get('on') else 'off'}"
            + (f", drawing {watts:.0f} W" if watts is not None else "")
        )
        print(f"⚡ Cutting the power for {off_seconds} s...", flush=True)
        try:
            still_on = bool(cls._plug(node, plug, via, OFF).get("on"))
        except OSError as error:
            # Also what a plug that took the command and stayed on raises: ask it again.
            try:
                still_on = bool(cls._plug(node, plug, via, STATUS).get("on"))
            except OSError:
                still_on = False
            if not still_on:
                print(
                    f"⚠️  The reply to 'off' was lost ({error}); making sure the plug is on again."
                )
        if still_on:
            print("❌ The plug still reports itself on; nothing more was done.")
            return False
        time.sleep(off_seconds)
        for _ in range(RESTORE_TRIES):
            try:
                if cls._plug(node, plug, via, ON).get("on"):
                    return True
            except OSError as error:
                print(f"⚠️  The plug did not take 'on' yet ({error}); trying again...", flush=True)
            time.sleep(MIN_OFF_SECONDS)
        print(
            f"❌ THE PLUG IS OFF and would not switch on: `ling-admin node power-cycle {node} --force` "
            "or the plug's own button."
        )
        return False

    @classmethod
    def cycle(
        cls,
        node: str,
        off_seconds: int = OFF_SECONDS,
        force: bool = False,
        wait_minutes: int = WAIT_MINUTES,
    ) -> int:
        """Cuts the node's power, restores it, and waits for it to answer.

        Args:
            node: A paired node's name.
            off_seconds: How long the power stays off (at least 5).
            force: Cut power even though the node answers.
            wait_minutes: How long to wait for the node to answer afterwards.

        Returns:
            int: 0 when the node answers again, 1 otherwise, 3 when nothing is configured for it.
        """
        entry = NodePowerSettings.load()["nodes"].get(node)
        if entry is None or not entry.get("plug"):
            print(
                f"❌ No plug is set for {node}: ling-admin node power-config node {node} --plug <name> [--via <node>]"
            )
            return 3
        plug, via = str(entry["plug"]), entry.get("via")
        off_seconds = max(MIN_OFF_SECONDS, off_seconds)
        if not force and cls._answers(node):
            print(
                f"❌ {node} answers over its pairing, so it is not hung. "
                "Cutting its power can lose a running job. Restart it from inside, or use --force."
            )
            return 1
        if not cls._cut_and_restore(node, plug, via, off_seconds):
            return 1
        print(
            f"✅ Power is back. Waiting up to {wait_minutes} min for {node} to answer...",
            flush=True,
        )
        deadline = time.monotonic() + wait_minutes * 60
        while time.monotonic() < deadline:
            if cls._answers(node):
                print(f"✅ {node} answers again.")
                return 0
            time.sleep(POLL_SECONDS)
        print(
            f"❌ {node} did not answer in {wait_minutes} min. The plug is on; if the machine stayed dark, "
            "its BIOS may not start on power restore (press its power button there)."
        )
        return 1
