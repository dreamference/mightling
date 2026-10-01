"""
The outcome of an egress audit (specs/DREAMFERENCE_PUFFIN_EGRESS.md §3.2).

This module provides the EgressVerdict dataclass: pass, fail or "trace failed", with the reasons.
"""

from dataclasses import dataclass, field
from typing import Final, List

PASS: Final[str] = "pass"
FAIL: Final[str] = "fail"
TRACE_FAILED: Final[str] = "trace failed"


@dataclass
class EgressVerdict:
    """What the audit concluded."""

    # `pass`, `fail` or `trace failed`. A trace that failed is never a pass: nothing was shown.
    status: str
    # What makes it not a pass, most important first; empty on a pass.
    problems: List[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """
        Returns:
            int: 0 on a pass, 1 on an unexpected destination, 2 when the trace itself failed.
        """
        return {PASS: 0, FAIL: 1}.get(self.status, 2)
