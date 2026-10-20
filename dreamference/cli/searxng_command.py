"""`ling-admin searxng {start,status}`: the search container behind `ling-search`."""

from collections.abc import Callable
from typing import Final

from dreamference.chat.searxng_sidecar import (
    SEARXNG_CONTAINER_NAME,
    SEARXNG_HOST_PORT,
    SearxngSidecar,
)
from dreamference.chat.searxng_status import SearxngStatus

SEARXNG_URL: Final[str] = f"http://127.0.0.1:{SEARXNG_HOST_PORT}"
USAGE: Final[str] = "usage: ling-admin searxng {start,status}"
USAGE_EXIT: Final[int] = 2


class SearxngCommand:
    """Runs the `searxng` group."""

    @classmethod
    def run(cls, subcommand: str | None) -> int:
        """Runs one `searxng` subcommand.

        Args:
            subcommand (str | None): `start` or `status`; anything else prints the usage.

        Returns:
            int: The exit code.
        """
        handlers: dict[str, Callable[[], int]] = {"start": cls.start, "status": cls.status}
        handler = handlers.get(subcommand or "")
        if handler is None:
            print(USAGE)
            return USAGE_EXIT
        return handler()

    @classmethod
    def start(cls) -> int:
        """Starts SearXNG with this version's settings, and tells an advertised node's clients.

        Returns:
            int: 0 when it runs, 1 when it did not start.
        """
        if not SearxngSidecar.start(say=print):
            print("❌ SearXNG did not start.")
            return 1
        print(f"✅ SearXNG is running on {SEARXNG_URL}")
        # An advertised node offers it to clients from now on.
        from dreamference.node import NodeAdvertiser

        NodeAdvertiser.on_searxng_started()
        return 0

    @classmethod
    def status(cls) -> int:
        """Prints each general search engine's state.

        Returns:
            int: 0 when a web engine is ok, 1 when none is, 2 when SearXNG is not running.
        """
        lines, code = SearxngStatus.report_lines(SEARXNG_CONTAINER_NAME, SEARXNG_URL)
        print("\n".join(lines))
        return code
