"""What one ``ling-admin night archive`` moved (``NightShiftArchive``, DESKTOP §16 B41)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ArchiveOutcome:
    """What one archive moved, for its summary line: task ids, counts of logs and reports."""

    tasks: list[str] = field(default_factory=list)
    logs: int = 0
    reports: int = 0
    #: Files left in place because the archive already holds one of the same name.
    skipped: list[str] = field(default_factory=list)
