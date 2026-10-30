"""Strategy hints for diverse best-of-N attempts (the owner's idea of 2026-10-30).

Best-of-N runs N attempts of the same prompt, and identical attempts tend to fail on the same
tasks: on SWE-bench night 13 the mean single run resolved 48.7% and the oracle over three runs
72%. An attempt that is told to work in another way should fail less often where the others do,
which raises the oracle, the most any chooser can reach.

Attempt 1 gets no hint, so it stays the single-run baseline; attempt k (k >= 2) gets hint k-1 of
the set, wrapping round. The hint is appended after the task text, never before it, so every
attempt shares the prompt's prefix with the others and the server's prefix cache still serves it.

Night Shift turns it on with `[night] best_of_hints` (or a task's own `best_of_hints`); the
SWE-bench harness, where an attempt is a whole run, with `swe-bench run --strategy-hint N`.
"""

from typing import Final

# One or two sentences each, about how to work, never about what the answer is.
DEFAULT_HINTS: Final[tuple[str, ...]] = (
    "Before changing any code, write a small test or script that reproduces the problem and "
    "fails; then make the change and check that it passes.",
    "Before you edit a function, find every caller and every other place that uses it, and make "
    "sure your change is right for all of them.",
    "Find where the wrong behaviour starts, not just where it shows, and fix it at its cause.",
    "When your fix works, look for the same mistake elsewhere in the file and in its sibling "
    "functions, and fix it there too where it is the same bug.",
    "Make the smallest change that fixes the problem; leave unrelated code, names and formatting "
    "as they are.",
    "Read the existing tests of the code you will change before you change it, and follow what "
    "they show about how the code is meant to behave.",
)

# What comes between the task and the hint.
HINT_INTRO: Final[str] = "\n\nStrategy for this attempt: "

# The setting's spellings for the built-in set; any other word is off.
DEFAULT_VALUES: Final[tuple[str, ...]] = ("on", "true", "yes", "default")


class StrategyHints:
    """Chooses an attempt's strategy hint and appends it to the attempt's prompt."""

    @classmethod
    def resolve(cls, value: object) -> tuple[str, ...]:
        """Reads a `best_of_hints` setting.

        Args:
            value: False, None or `off` for none; True or `default` for `DEFAULT_HINTS`; a list
                of strings for those hints.

        Returns:
            tuple[str, ...]: The hints in force, empty when they are off.
        """
        if value is None or value is False:
            return ()
        if value is True:
            return DEFAULT_HINTS
        if isinstance(value, str):
            return DEFAULT_HINTS if value.strip().lower() in DEFAULT_VALUES else ()
        if isinstance(value, list | tuple):
            return tuple(str(hint).strip() for hint in value if str(hint).strip())
        return ()

    @classmethod
    def hint_for(cls, attempt: int, hints: tuple[str, ...]) -> str | None:
        """The hint attempt `attempt` gets.

        Args:
            attempt: The attempt's number, from 1.
            hints: The hints in force.

        Returns:
            str | None: None for attempt 1 or with no hints; else hint `attempt - 1` of the set,
            wrapping round when there are more attempts than hints.
        """
        if attempt <= 1 or not hints:
            return None
        return hints[(attempt - 2) % len(hints)]

    @classmethod
    def append(cls, prompt: str, hint: str | None) -> str:
        """Appends a hint after the prompt.

        Args:
            prompt: The prompt as it would be without a hint, the task text last.
            hint: The hint, or None.

        Returns:
            str: The prompt unchanged without a hint; else the prompt, `HINT_INTRO` and the hint.
        """
        return f"{prompt}{HINT_INTRO}{hint}" if hint else prompt
