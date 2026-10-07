"""
Issue text with the fix's names taken out: `swe-bench run --strip-names`.

Most SWE-bench issues point at the code they are about: of the 24 instances the index arms ran,
13 name the file the fix is in and 9 a function or class it touches, so an agent finds the place
in a step or two with or without the code index. This arm measures the index where the issue
does not say where to look. Every name is derived from the gold patch alone, never from the
repository: the file paths in its diff headers, the module paths and file names they imply, and
the functions and classes in its hunk headers and in the `def`/`class` lines it adds or removes.
Each is replaced by a numbered neutral phrase (`[file 1]`, `[function 2]`), the same phrase for
the same name throughout, so an issue that says one function calls another still says so. Error
messages and descriptions of behaviour stay as written; only the names inside them change.

A plain lowercase name that is also an English word (`update`, `manager`, `vector`) is replaced
only where it reads as code: inside backquotes, after a `.`, before a `(`, as a traceback frame
(`, in update`), or as a file name with its extension. In prose it is left alone, because the word
means what it means in English as well, and replacing it everywhere would make the issue
unreadable. A plain name that is not an English word (`runserver`, `sqrtdenest`) is code wherever
it appears, except as a quoted value (`format="html"`), which describes behaviour. English is the
system word list (`/usr/share/dict/words`); without one, every plain name counts as English,
which strips less, never more. A run records the text its agent saw, so its result does not
depend on the list of the machine that reads the report.
"""

import os
import re
from pathlib import PurePosixPath
from typing import Dict, Final, FrozenSet, List, Optional, Tuple

# The phrase a name is replaced with, by kind: `[file 1]`, `[module 1]`, `[class 1]`, `[function 1]`.
# A file and its module share a number: `[file 2]` and `[module 2]` are the same file.
PHRASES: Final[dict] = {"file": "file", "module": "module", "class": "class", "function": "function"}

# Names too general to replace even in code position: they name no place in the repository.
GENERIC: Final[frozenset] = frozenset({"__init__", "__call__", "__str__", "__repr__", "self", "cls", "main"})

# The system word list that tells an English word from a name that can only be code.
WORD_LIST: Final[str] = "/usr/share/dict/words"

DIFF_FILE: Final = re.compile(r"^diff --git a/(\S+) b/", re.M)
HUNK_CONTEXT: Final = re.compile(r"^@@[^@]*@@\s*(.*)$", re.M)
DEFINITION: Final = re.compile(r"^\s*(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)")
CHANGED_DEFINITION: Final = re.compile(r"^[+-](?![+-])\s*(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)", re.M)


class SweBenchNameStripper:
    """Derives a gold patch's names and takes them out of an issue's text."""

    # The words of `WORD_LIST`, read once; the tests replace it.
    _english: Optional[FrozenSet[str]] = None

    @classmethod
    def english(cls) -> FrozenSet[str]:
        """
        Returns:
            FrozenSet[str]: The lowercase words of the system word list; empty when there is none,
            which `is_english` reads as "every word is English".
        """
        if cls._english is None:
            words: set = set()
            if os.path.isfile(WORD_LIST):
                with open(WORD_LIST, encoding="utf-8", errors="replace") as handle:
                    words = {line.strip().lower() for line in handle if line.strip()}
            cls._english = frozenset(words)
        return cls._english

    @classmethod
    def is_english(cls, word: str) -> bool:
        """
        Args:
            word: A plain name, letters only.

        Returns:
            bool: True when the word is in the word list, or when there is no list.
        """
        words = cls.english()
        return not words or word.lower() in words

    @classmethod
    def names(cls, patch: str) -> List[Tuple[str, str, str]]:
        """
        The names a gold patch reveals, longest first so a path is replaced before its parts.

        Args:
            patch: The instance's gold `patch` (a unified diff of source files only).

        Returns:
            List[Tuple[str, str, str]]: `(name, kind, group)` triples, kind one of `file`,
            `module`, `class`, `function`; each name once. Every form of one file (its path, a
            shorter tail of it, its file name, its module) shares the file's path as its group,
            so they get one number; a function or class is its own group.
        """
        found: Dict[str, Tuple[str, str]] = {}
        for path in DIFF_FILE.findall(patch or ""):
            pure = PurePosixPath(path)
            parts = pure.parts
            found.setdefault(path, ("file", path))
            # The same file as an issue would write it: a shorter tail of the path
            # (`sql/compiler.py`) and the file name with its extension.
            for start in range(1, len(parts) - 1):
                found.setdefault("/".join(parts[start:]), ("file", path))
            found.setdefault(pure.name, ("file", path))
            if pure.suffix == ".py":
                module = list(parts[:-1]) + ([] if pure.stem == "__init__" else [pure.stem])
                if len(module) >= 2:
                    found.setdefault(".".join(module), ("module", path))
                if pure.stem != "__init__":
                    found.setdefault(pure.stem, ("module", path))
        for context in HUNK_CONTEXT.findall(patch or ""):
            match = DEFINITION.match(context)
            if match:
                name = match.group(2)
                found.setdefault(name, ("class" if match.group(1) == "class" else "function", name))
        for kind, name in CHANGED_DEFINITION.findall(patch or ""):
            found.setdefault(name, ("class" if kind == "class" else "function", name))
        triples = [(name, kind, group) for name, (kind, group) in found.items()
                   if name not in GENERIC and len(name) > 2]
        return sorted(triples, key=lambda triple: (-len(triple[0]), triple[0]))

    @classmethod
    def distinctive(cls, name: str) -> bool:
        """
        Whether a name can only be code: it has an underscore, a dot, a slash, a digit, or a capital
        after its first letter (`get_meta`, `QuerySet`, `sql/compiler.py`).

        Args:
            name: The name.

        Returns:
            bool: True when replacing it in prose cannot hit an English word.
        """
        return bool(re.search(r"[_./\d]", name) or re.search(r"[A-Z]", name[1:]))

    @classmethod
    def pattern(cls, name: str) -> "re.Pattern[str]":
        """
        The pattern a name is found by in issue text.

        Args:
            name: The name.

        Returns:
            re.Pattern[str]: Whole-name matches. A path also matches with backslashes, as a
            Windows traceback writes it. An English word matches only in code position: after a
            `.` or a backquote or `, in ` (a traceback frame), or before a `(`, a backquote or a
            `.py`. Any other plain name matches anywhere except as a quoted value.
        """
        escaped = re.escape(name)
        if "/" in name:
            escaped = re.escape(name).replace("/", r"[/\\]")
        whole = rf"(?<![\w]){escaped}(?![\w])"
        if cls.distinctive(name):
            return re.compile(whole)
        if not cls.is_english(name):
            return re.compile(rf"(?<![\w'\"]){escaped}(?![\w'\"])")
        return re.compile(rf"(?:(?<=[.`]){escaped}(?![\w])|(?<=, in ){escaped}(?![\w])"
                          rf"|(?<![\w]){escaped}(?=\(|`|\.py\b))")

    @classmethod
    def strip(cls, problem_statement: str, patch: str) -> Tuple[str, Dict[str, str]]:
        """
        Takes the gold patch's names out of an issue's text.

        Args:
            problem_statement: The issue as the dataset gives it.
            patch: The instance's gold patch.

        Returns:
            Tuple[str, Dict[str, str]]: The rewritten text, and each name that was found in it
            mapped to the phrase that replaced it (empty when the issue named none of them).
        """
        text = problem_statement or ""
        numbers: Dict[str, int] = {}
        counters: Dict[str, int] = {}
        replaced: Dict[str, str] = {}
        for name, kind, group in cls.names(patch):
            pattern = cls.pattern(name)
            if not pattern.search(text):
                continue
            family = "file" if kind in ("file", "module") else kind
            if group not in numbers:
                counters[family] = counters.get(family, 0) + 1
                numbers[group] = counters[family]
            phrase = f"[{PHRASES[kind]} {numbers[group]}]"
            text = pattern.sub(phrase, text)
            replaced[name] = phrase
        return text, replaced
