"""One task Night Shift could queue by itself (night_shift_autofill.py)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AutofillCandidate:
    """A finding turned into a task: where it came from, what to do and how it is checked.

    Attributes:
        key: A stable id of the finding (``qa-desktop:...``, ``ci-test:...``, ``spec:...``); the
            ledger and the task's marker line use it, so one finding is queued once per cooldown.
        source: ``ci``, ``qa`` or ``spec``; the order in which candidates are taken.
        title: One line naming the finding, for the notes of the run.
        detail: What is known about it, already cleaned and shortened.
        test: The command the runner's test run uses (the acceptance test), or None.
        acceptance: The acceptance test in words, written into the task.
    """

    key: str
    source: str
    title: str
    detail: str
    test: str | None
    acceptance: str
