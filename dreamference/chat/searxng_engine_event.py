"""The last error SearXNG logged for one engine, as `ling-admin searxng status` reads it."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class SearxngEngineEvent:
    """The last error SearXNG logged for one engine.

    Attributes:
        at (datetime): When it was logged, in UTC.
        reason (str): SearXNG's message, without the suspension length.
        suspended_seconds (int): How long SearXNG suspended the engine for it (0: not at all).
        init_failed (bool): The engine failed at start and was never loaded.
    """

    at: datetime
    reason: str
    suspended_seconds: int
    init_failed: bool = False
