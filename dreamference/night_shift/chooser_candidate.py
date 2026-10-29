"""One candidate patch for the best-of-N chooser (specs/DREAMFERENCE_BACKLOG.md items 1 and 6).

Shared by Night Shift's best-of-N (`NightShiftBestOfN`, where a candidate is one attempt's change)
and the chooser test on disk (`scripts/swe_bench_chooser_test.py`, where it is one distinct patch of
several SWE-bench runs). It carries no verdict on the issue: the chooser must never see one.
"""

from dataclasses import dataclass


@dataclass
class ChooserCandidate:
    """One distinct patch for a task, as the chooser sees it: no verdict on the issue."""

    id: str
    patch: str
    runs: list[str]
    # The regression filter's input: the patch applied, and how many of the repository's existing
    # tests it broke (None when no test results are known). Never FAIL_TO_PASS, never `resolved`.
    applied: bool = True
    p2p_failed: int | None = None

    @property
    def breaks(self) -> bool:
        """True when the regression filter drops it."""
        return not self.applied or bool(self.p2p_failed)
