"""
Wi-Fi power saving, which loses the multicast a node is found by (`ling-admin host check|setup`,
`ling-admin node enable`).

A client finds its node by an mDNS browse for `_mightling-node._tcp` (docs/dev/node.md), and mDNS
is multicast. A Wi-Fi radio in power-saving mode sleeps between beacons and its access point
buffers multicast for it until the next DTIM beacon, and on the MediaTek MT7925 both GB10 machines
here use that delivery loses most of it: measured 2026-10-11/12, up to ~80% of the queries sent to
a node went unanswered, so `node-b.local` did not resolve and a client could not find its
node. NetworkManager turns power saving on for every Wi-Fi connection unless the connection says
otherwise (Ubuntu ships `wifi.powersave = 3` in
`/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf`, and a connection's own setting is
`default`), so a GB10 on Wi-Fi has it on out of the box.

The fix is per connection and has two halves: `nmcli connection modify <connection>
802-11-wireless.powersave 2` keeps it off across reconnects and reboots, and `iw dev <interface>
set power_save off` turns it off now, with no reconnect (a reconnect would drop an SSH session
that runs the setup). The global file is left alone: the connection's setting overrides it.

Only connected Wi-Fi devices count. A machine on Ethernet only, or without NetworkManager, has
nothing to report, and is told nothing.
"""

import os
import shutil
import subprocess
from typing import Any, Dict, Final, List, Optional

# The name of the step in `HostSafetySetup.steps()`, which `node prepare` also runs: the one change
# to NetworkManager it makes (`is_fix_command`; decided 2026-10-12).
STEP_NAME: Final[str] = "turn off Wi-Fi power saving"
# NetworkManager's `802-11-wireless.powersave` values: 0 default, 1 ignore, 2 disable, 3 enable.
# `nmcli -g` prints the keyword; older versions print the number.
DISABLED: Final[tuple] = ("disable", "2")
DISABLE_VALUE: Final[str] = "2"
# How long one nmcli or iw reading may take.
READ_SECONDS: Final[int] = 10


class WifiPowerSave:
    """Reads whether a connected Wi-Fi interface has power saving on, and builds the fix."""

    @classmethod
    def problems(cls) -> List[Dict[str, Any]]:
        """
        Lists the connected Wi-Fi interfaces whose power saving is not off for good.

        Returns:
            List[Dict[str, Any]]: One entry per interface: `device`, `connection`, `setting` (the
            connection's `802-11-wireless.powersave` as nmcli prints it), `radio` (True when `iw`
            says power saving is on now, False when off, None when it cannot say) and `commands`
            (argv lists to run as root, without `sudo`). Empty on Ethernet only, with power saving
            already off, or without NetworkManager.
        """
        found: List[Dict[str, Any]] = []
        for interface in cls.interfaces():
            setting_off = interface["setting"].split(" ", 1)[0].lower() in DISABLED
            radio = interface["radio"]
            if setting_off and radio is not True:
                continue
            commands: List[List[str]] = []
            if not setting_off:
                commands.append(["nmcli", "connection", "modify", interface["connection"],
                                 "802-11-wireless.powersave", DISABLE_VALUE])
            if radio is True:
                commands.append(["iw", "dev", interface["device"], "set", "power_save", "off"])
            found.append({**interface, "commands": commands})
        return found

    @classmethod
    def interfaces(cls) -> List[Dict[str, Any]]:
        """
        Reads every connected Wi-Fi interface NetworkManager manages.

        Returns:
            List[Dict[str, Any]]: `device`, `connection`, `setting` and `radio` for each (see
            `problems`); empty when nmcli is missing, NetworkManager is not running, or no Wi-Fi
            interface is connected.
        """
        listing = cls._read(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"])
        if listing is None:
            return []
        found: List[Dict[str, Any]] = []
        for line in listing.splitlines():
            # Device names, types and states hold no colon, so the terse fields split cleanly.
            fields = line.split(":")
            if len(fields) < 3 or fields[1] != "wifi" or not fields[2].startswith("connected"):
                continue
            device = fields[0]
            connection = cls._read(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", device])
            if not connection or not connection.strip():
                continue
            connection = connection.strip()
            setting = cls._read(["nmcli", "-g", "802-11-wireless.powersave", "connection", "show", connection])
            found.append({"device": device, "connection": connection,
                          "setting": (setting or "").strip() or "unknown",
                          "radio": cls.radio_power_save(device)})
        return found

    @classmethod
    def radio_power_save(cls, device: str) -> Optional[bool]:
        """
        Args:
            device: The Wi-Fi interface.

        Returns:
            Optional[bool]: Whether the radio is in power-saving mode now (`iw dev <device> get
            power_save`), or None when iw is missing or does not say.
        """
        output = cls._read(["iw", "dev", device, "get", "power_save"])
        if output is None:
            return None
        text = output.strip().lower()
        if text.endswith(": on"):
            return True
        if text.endswith(": off"):
            return False
        return None

    @classmethod
    def step(cls) -> Optional[Dict[str, Any]]:
        """
        The fix as a `HostSafetySetup` step.

        Returns:
            Optional[Dict[str, Any]]: `name`, `why` and `commands`, or None when nothing is to fix.
        """
        found = cls.problems()
        if not found:
            return None
        why = (f"power saving is on for {cls._named(found)}, and it loses the multicast mDNS that "
               f"clients find this node by")
        if any(problem["radio"] is None for problem in found):
            why += " (iw cannot turn it off now here, so the change takes effect at the next reconnect)"
        return {"name": STEP_NAME, "why": why,
                "commands": [command for problem in found for command in problem["commands"]]}

    @classmethod
    def warning(cls) -> Optional[str]:
        """
        The warning `ling-admin node enable` prints, with the commands that fix it.

        Returns:
            Optional[str]: The text, or None when power saving is off or there is no Wi-Fi.
        """
        found = cls.problems()
        if not found:
            return None
        import shlex
        lines = "\n".join("     sudo " + " ".join(shlex.quote(part) for part in command)
                          for problem in found for command in problem["commands"])
        named = cls._named(found)
        return (f"⚠️  Wi-Fi power saving is on for {named}. It loses the multicast mDNS that clients find\n"
                f"   this node by (up to ~80% of queries went unanswered on a GB10), so a client may not find it.\n"
                f"   `ling-admin host setup` turns it off, or run:\n"
                f"{lines}")

    @classmethod
    def is_fix_command(cls, argv: List[str]) -> bool:
        """
        Whether a command is exactly one of the two this class builds, and nothing else: `node
        prepare` refuses every other nmcli command (and every other use of iw).

        Args:
            argv: The command, without `sudo`.

        Returns:
            bool: True for `nmcli connection modify <connection> 802-11-wireless.powersave 2` and
            `iw dev <interface> set power_save off`, with a name that cannot pass for an option.
        """
        parts = [str(part) for part in argv]
        if len(parts) != 6:
            return False
        if parts[0] == "nmcli":
            name, shape = parts[3], parts[:3] + parts[4:]
            expected = ["nmcli", "connection", "modify", "802-11-wireless.powersave", DISABLE_VALUE]
        elif parts[0] == "iw":
            name, shape = parts[2], parts[:2] + parts[3:]
            expected = ["iw", "dev", "set", "power_save", "off"]
        else:
            return False
        return shape == expected and bool(name) and not name.startswith("-")

    @classmethod
    def _named(cls, found: List[Dict[str, Any]]) -> str:
        return ", ".join(f"{problem['device']} ({problem['connection']})" for problem in found)

    @classmethod
    def _read(cls, argv: List[str]) -> Optional[str]:
        """
        Runs one reading command; the one place this class runs anything.

        Args:
            argv: The command, which changes nothing.

        Returns:
            Optional[str]: Its output, or None when the program is missing, fails or times out.
        """
        # iw is in /usr/sbin, which not every user's PATH has.
        program = shutil.which(argv[0]) or shutil.which(argv[0], path="/usr/sbin:/sbin")
        if program is None:
            return None
        try:
            result = subprocess.run([program, *argv[1:]], capture_output=True, text=True, timeout=READ_SECONDS,
                                    stdin=subprocess.DEVNULL, check=False,
                                    env={**os.environ, "LC_ALL": "C"})
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout
