"""
The texts of refine mode (specs/DREAMFERENCE_PUFFIN_REFINE.md): a first session studies a task and
writes a refined description without changing anything, then a fresh session does the task with
that description.

One text serves the product and the benchmark. The pieces below are the ones in
`puffin-rs/prompts/refine.md`, which the launcher composes from for `puffin exec` and the
interactive session; a test keeps the two equal. Night Shift composes from them here, and
SWE-bench's `--refine` arm (`swe_bench_instance_run.py`) builds its two prompts from the same
pieces, byte for byte the prompts it was measured with (a test pins them). `{subject}` is "task"
in the product and "issue" in the benchmark.
"""

import re
from typing import Dict, Final

STUDY_INTRO: Final[str] = """This is the first of two steps. Do not fix anything yet: study the {subject} below and write a precise
description of the problem for the second step, which will make the fix."""

STUDY_SECTIONS: Final[str] = """1. Intent: what the reporter is trying to achieve, from the title and their use case, not only
   from the example they give.
2. Requirements: the behaviour the fix must produce, as observable results ("for input X the
   result is Y"). Never "it no longer raises": say what it returns or prints.
3. Code paths: every place in the repository that produces the behaviour in question, found by
   following callers and references, not only the one the example reaches. Name each by file and
   function.
4. Edge cases: inputs the example does not cover that the same fix must handle (other types,
   subclasses, ancestors, empty input, a sibling function with the same flaw).
5. Must not change: behaviour that other code or the existing tests rely on.
6. Acceptance checks: commands or short scripts that will show the fix is complete, each with
   its expected output.

Keep it factual: say what you checked in the code and what you are inferring."""

# The study step's code-index sentence: it is there to find every path, not one.
STUDY_CODE_INDEX: Final[str] = """- Find the code with the `code_*` tools: `code_search` with the {subject}'s words, then
  `code_callers` and `code_refs` to find every other path that produces the same behaviour."""

# The product's study prompt. The description comes back as the session's final message, so the
# step needs to write no file (in `puffin exec` and the interactive session it runs read-only).
STUDY_TEMPLATE: Final[str] = """{intro}

- You may read the code, run it and run the repository's tests. Do not change any file in the
  repository: {writes}
- Nobody will answer questions: where something is unclear, make the reasonable choice and say
  which you made.
{code_index}
Write the description as your final message, in six sections:
{sections} The final message is all the second step receives, so it must stand on its own.

Task:
{task}"""

FIX_RULES: Final[str] = """- A first step studied the {subject} and wrote the refined description that follows it. Use it to
  see the whole problem, but the {subject} is authoritative: where the two disagree, follow the {subject}.
- Fix the cause, not the symptom: no guard (a length check, a try/except, an early return) that
  only hides the reported failure.
- Before you stop, run every acceptance check in the refined description and confirm that each
  gives its expected result."""

REFINED_HEADING: Final[str] = "Refined description (written by the first step; it may be incomplete or wrong):"

# What the second step is told when the first wrote nothing.
NO_REFINED: Final[str] = "(The first step wrote no description: work from the {subject} alone.)"

# The product's second-step block, after the task: the rules, then the description.
FIX_TEMPLATE: Final[str] = """{rules}

{heading}
{refined}"""

# The pieces by their marker names in puffin-rs/prompts/refine.md.
PIECES: Final[Dict[str, str]] = {
    "study-intro": STUDY_INTRO,
    "study-sections": STUDY_SECTIONS,
    "study-code-index": STUDY_CODE_INDEX,
    "study": STUDY_TEMPLATE,
    "fix-rules": FIX_RULES,
    "refined-heading": REFINED_HEADING,
    "no-refined": NO_REFINED,
    "fix": FIX_TEMPLATE,
}

# Night Shift's study step works in the task's own worktree, which the runner puts back afterwards.
NIGHT_WRITES: Final[str] = "every change there is discarded when this step ends. Put scratch files in /tmp."

MARKER: Final[re.Pattern] = re.compile(r"<!-- ([a-z-]+) -->")


class RefinePrompt:
    """Composes refine mode's prompts from the shared pieces."""

    @classmethod
    def subject(cls, piece: str, subject: str) -> str:
        """
        Fills a piece's `{subject}`.

        Args:
            piece: One of the pieces above.
            subject: "task" (the product) or "issue" (SWE-bench).

        Returns:
            str: The piece.
        """
        return piece.replace("{subject}", subject)

    @classmethod
    def compose_study(cls, task: str, writes: str, code_index: bool = False, subject: str = "task") -> str:
        """
        Builds the product's study prompt.

        Args:
            task: The task, verbatim.
            writes: What the study step is told about writing files (`NIGHT_WRITES` for Night Shift).
            code_index: Whether the session has the code index's tools.
            subject: What the task is called.

        Returns:
            str: The prompt.
        """
        hint = cls.subject(STUDY_CODE_INDEX, subject) + "\n" if code_index else ""
        # `{task}` last: a task's own text is never searched for placeholders.
        return (STUDY_TEMPLATE.replace("{intro}", cls.subject(STUDY_INTRO, subject))
                .replace("{writes}", writes)
                .replace("{code_index}", hint)
                .replace("{sections}", STUDY_SECTIONS)
                .replace("{task}", task))

    @classmethod
    def fix_block(cls, refined: str, subject: str = "task") -> str:
        """
        Builds what the second step is given after the task: the rules, then the description.

        Args:
            refined: The first step's description; empty when it wrote none.
            subject: What the task is called.

        Returns:
            str: The block.
        """
        refined = refined.strip() or cls.subject(NO_REFINED, subject)
        return (FIX_TEMPLATE.replace("{rules}", cls.subject(FIX_RULES, subject))
                .replace("{heading}", REFINED_HEADING)
                .replace("{refined}", refined))

    @classmethod
    def compose_fix(cls, task_prompt: str, refined: str, subject: str = "task") -> str:
        """
        Builds the product's second prompt: the task as it would have been given, then the block.

        Args:
            task_prompt: The prompt the task gets without refine mode.
            refined: The first step's description.
            subject: What the task is called.

        Returns:
            str: The prompt.
        """
        return f"{task_prompt}\n\n{cls.fix_block(refined, subject)}"

    @classmethod
    def parse_pieces(cls, text: str) -> Dict[str, str]:
        """
        Reads the pieces of `puffin-rs/prompts/refine.md`, as the launcher does.

        Args:
            text: The file's contents.

        Returns:
            Dict[str, str]: Each marker's piece: the lines between it and the next marker.
        """
        pieces: Dict[str, str] = {}
        name = None
        lines: list = []
        for line in text.split("\n"):
            match = MARKER.fullmatch(line)
            if match:
                if name is not None:
                    pieces[name] = "\n".join(lines)
                name, lines = match.group(1), []
            elif name is not None:
                lines.append(line)
        pieces.pop("end", None)
        return pieces
