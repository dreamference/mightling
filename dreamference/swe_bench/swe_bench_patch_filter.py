"""
Test files taken out of a collected patch, for the regrade-only arm
(specs/DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md §6.2).

`swe-bench eval --drop-test-hunks` grades a copy of a run's predictions with every file section
whose path is a test file removed. The rule looks at paths alone and never at the dataset's
`test_patch`: it has to be one a real submission could apply without knowing the benchmark's
tests.
"""

import re
from pathlib import PurePosixPath
from typing import Final, List, Optional, Tuple

# A directory of tests, as a whole path component (`django/test/` is framework source).
TEST_DIRECTORIES: Final[frozenset] = frozenset({"tests", "testing"})

# A test module by its file name.
TEST_FILE_NAMES: Final[re.Pattern] = re.compile(r"^(test_.*\.py|.*_test\.py|tests\.py|conftest\.py)$")

DIFF_HEADER: Final[str] = "diff --git "


class SweBenchPatchFilter:
    """Removes the test files from a unified diff."""

    @classmethod
    def is_test_path(cls, path: str) -> bool:
        """
        Args:
            path: A repository-relative path.

        Returns:
            bool: True for a file under a `tests/` or `testing/` directory, or named `test_*.py`,
            `*_test.py`, `tests.py` or `conftest.py`.
        """
        parts = PurePosixPath(path).parts
        if not parts:
            return False
        return any(part in TEST_DIRECTORIES for part in parts[:-1]) or bool(TEST_FILE_NAMES.match(parts[-1]))

    @classmethod
    def drop_test_hunks(cls, patch: str) -> Tuple[str, List[str]]:
        """
        Removes every file section of a `git diff` whose old or new path is a test file.

        Args:
            patch: The collected patch.

        Returns:
            Tuple[str, List[str]]: The patch without those sections (empty when nothing is
            left), and the paths dropped, in the patch's order.
        """
        kept: List[str] = []
        dropped: List[str] = []
        for section in cls.sections(patch):
            paths = cls.paths(section)
            if paths and any(cls.is_test_path(path) for path in paths):
                dropped.append(paths[-1])
            else:
                kept.append(section)
        text = "".join(kept)
        return (text if text.strip() else ""), dropped

    @classmethod
    def sections(cls, patch: str) -> List[str]:
        """
        Args:
            patch: A unified diff in `git diff`'s format.

        Returns:
            List[str]: Its pieces, each starting at a `diff --git` line (anything before the
            first such line is a piece of its own), whose concatenation is the patch.
        """
        pieces: List[str] = []
        current: List[str] = []
        for line in patch.splitlines(keepends=True):
            if line.startswith(DIFF_HEADER) and current:
                pieces.append("".join(current))
                current = []
            current.append(line)
        if current:
            pieces.append("".join(current))
        return pieces

    @classmethod
    def paths(cls, section: str) -> List[str]:
        """
        Args:
            section: One file section of a `git diff`.

        Returns:
            List[str]: The paths it names (old and new, from the `---`/`+++`, rename and
            header lines), without their `a/`/`b/` prefixes; empty for a piece that is not a
            file section.
        """
        lines = section.splitlines()
        if not lines or not lines[0].startswith(DIFF_HEADER):
            return []
        found: List[str] = []
        for line in lines[1:]:
            if line.startswith("@@"):
                break
            path = None
            if line.startswith("--- ") or line.startswith("+++ "):
                path = cls._strip(line[4:], prefixed=True)
            elif line.startswith("rename from ") or line.startswith("rename to "):
                path = cls._strip(line.split(" ", 2)[2], prefixed=False)
            elif line.startswith("copy from ") or line.startswith("copy to "):
                path = cls._strip(line.split(" ", 2)[2], prefixed=False)
            if path:
                found.append(path)
        if not found:
            # A mode change or an empty new file has no `---`/`+++` lines: read the header,
            # `diff --git a/<path> b/<path>`, where both paths are the same.
            header = lines[0][len(DIFF_HEADER):]
            half = (len(header) - 1) // 2
            if header.startswith("a/") and header[half + 1:].startswith("b/"):
                found.append(header[2:half])
        return found

    @classmethod
    def _strip(cls, text: str, prefixed: bool) -> Optional[str]:
        text = text.rstrip("\n").split("\t")[0]
        if text.startswith('"') and text.endswith('"'):
            # Git quotes a path with special characters; only the quote and backslash matter to
            # whether it is a test path.
            text = text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if text == "/dev/null":
            return None
        if prefixed and (text.startswith("a/") or text.startswith("b/")):
            text = text[2:]
        return text or None
