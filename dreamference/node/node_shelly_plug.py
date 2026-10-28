"""A Shelly smart plug, driven over its local HTTP API (docs/dev/remote-access.md, "Power-cycling").

Shelly plugs answer on the local network with no cloud account: the second generation (Plus and Pro
plugs, the Gen3 and Gen4 plug models) speaks JSON-RPC over HTTP, `GET /rpc/Switch.Set?id=0&on=false`
and `GET /rpc/Switch.GetStatus?id=0`; the first generation (the original Plug S) has
`GET /relay/0?turn=off` and `GET /relay/0`. Nothing here talks to Shelly's cloud.

The plug's local API has no password unless the owner sets one in the plug's web page, and this
module speaks the unauthenticated form only: the plug must sit on a network nobody untrusted is on
(the house LAN behind the router), and the node that drives it is reached over the overlay.
Network access goes through `_get`, which the tests replace.
"""

import json
import re
import urllib.error
import urllib.request
from typing import Any, Final

# A host name, `.local` name or address: no scheme, port, path or userinfo, so a configured value
# can only ever name a machine, never reshape the URL.
HOST: Final[re.Pattern] = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
TIMEOUT_S: Final[float] = 8.0
GEN1: Final[int] = 1
GEN2: Final[int] = 2
GENERATIONS: Final[tuple[int, ...]] = (GEN1, GEN2)


class NodeShellyPlug:
    """One Shelly plug's switch, by host name."""

    @classmethod
    def valid_host(cls, host: str) -> bool:
        """Whether a configured host is a plain name or address.

        Args:
            host: A configured host.

        Returns:
            bool: True when it is.
        """
        return bool(HOST.fullmatch(host or ""))

    @classmethod
    def _get(cls, url: str) -> dict[str, Any]:
        """One GET, parsed as JSON. Raises OSError (also for a bad reply) so callers catch one type."""
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
                body = json.load(response)
        except (urllib.error.URLError, ValueError, TimeoutError) as error:
            raise OSError(str(error)) from error
        if not isinstance(body, dict):
            raise OSError("the plug answered with something other than an object")
        return body

    @classmethod
    def _url(cls, host: str, generation: int, channel: int, on: bool | None) -> str:
        if generation == GEN2:
            if on is None:
                return f"http://{host}/rpc/Switch.GetStatus?id={channel}"
            return f"http://{host}/rpc/Switch.Set?id={channel}&on={'true' if on else 'false'}"
        if on is None:
            return f"http://{host}/relay/{channel}"
        return f"http://{host}/relay/{channel}?turn={'on' if on else 'off'}"

    @classmethod
    def state(cls, host: str, channel: int = 0, generation: int = 2) -> dict[str, Any]:
        """Reads the switch.

        Args:
            host: The plug's host name (a `.local` name, or the router's name for it).
            channel: The switch's id (0 on a plug).
            generation: 1 or 2 (the plug's generation).

        Returns:
            Dict[str, Any]: `on` (bool) and `power_w` (the load in watts when the plug reports it, else None).

        Raises:
            OSError: The plug did not answer, or answered in a form this module does not know.
            ValueError: The host or generation is not acceptable.
        """
        cls._check(host, generation)
        body = cls._get(cls._url(host, generation, channel, None))
        on = body.get("output") if generation == GEN2 else body.get("ison")
        if not isinstance(on, bool):
            raise OSError("the plug's reply names no switch state")
        power = body.get("apower") if generation == GEN2 else body.get("power")
        return {"on": on, "power_w": float(power) if isinstance(power, int | float) else None}

    @classmethod
    def switch(cls, host: str, on: bool, channel: int = 0, generation: int = 2) -> dict[str, Any]:
        """Turns the switch on or off, then reads it back.

        Args:
            host: The plug's host name.
            on: The state to set.
            channel: The switch's id.
            generation: 1 or 2.

        Returns:
            Dict[str, Any]: The state read back after the command (`state`).

        Raises:
            OSError: The plug did not answer, or did not end up in the asked state.
            ValueError: The host or generation is not acceptable.
        """
        cls._check(host, generation)
        cls._get(cls._url(host, generation, channel, on))
        after = cls.state(host, channel, generation)
        if after["on"] != on:
            raise OSError(
                f"the plug accepted the command but reports itself {'on' if after['on'] else 'off'}"
            )
        return after

    @classmethod
    def _check(cls, host: str, generation: int) -> None:
        if not cls.valid_host(host):
            raise ValueError(f"{host!r} is not a plain host name")
        if generation not in GENERATIONS:
            raise ValueError(f"generation {generation} is not 1 or 2")
