"""
What an issue names, and the example it shows, as the `issue-v1` hooks' conditions
(specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §20).

Extraction is by rule from the issue's text, with no model call: repository paths, dotted module
names, and the functions and classes it names (`name()`, or `name` in backticks). The names are
then resolved against the repository by a light index, `git ls-files` and a `git grep` for their
`def`/`class` lines, run in the instance's container when the task starts. What does not resolve,
or resolves to more places than one could ask the agent to read, is dropped and recorded: a
condition built from issue text can name something that does not exist, and the gate must not
ask for it (agent survey §3.1).
"""

import ast
import re
import textwrap
import warnings
from typing import Any, Dict, Final, List, Optional, Tuple

# At most this many things to read before the first edit: past it the hold stops being a check
# and becomes a reading list. Kept in priority order (below), the rest recorded as dropped.
MAX_TARGETS: Final[int] = 6
# A name defined in more non-test files than this is too common to ask for (`save`, `get`).
MAX_DEFINITIONS: Final[int] = 3
# At most this many names go to the container's `git grep`.
MAX_NAMES: Final[int] = 40

CODE_SUFFIXES: Final[str] = r"py|pyx|pxd|pyi|c|h|cc|cpp|js|ts"
PATH: Final[re.Pattern] = re.compile(r"(?<![\w.-])((?:[A-Za-z]:)?[\w./\\-]*[\w-]\.(?:" + CODE_SUFFIXES + r"))(?![\w/])")
TRACEBACK_FILE: Final[re.Pattern] = re.compile(r'File "([^"]+)", line \d+')
FENCE: Final[re.Pattern] = re.compile(r"^[ \t]*```[ \t]*([\w+-]*)[^\n]*\n(.*?)^[ \t]*```", re.S | re.M)
INLINE: Final[re.Pattern] = re.compile(r"`([^`\n]+)`")
DOTTED: Final[re.Pattern] = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)(?!\w|\.\w)")
CALLED: Final[re.Pattern] = re.compile(r"(?<![\w.])((?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*)\(")
IDENTIFIER: Final[re.Pattern] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CALL: Final[re.Pattern] = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
DEFINITION: Final[re.Pattern] = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")

PYTHON_LANGS: Final[frozenset] = frozenset({"", "python", "py", "python3", "py3", "pycon", "ipython", "pytb"})
SHELL_LANGS: Final[frozenset] = frozenset({"bash", "sh", "shell", "console", "shell-session", "zsh", "shellsession"})
# Shell lines that set things up rather than show the problem.
SETUP_PROGRAMS: Final[frozenset] = frozenset({
    "pip", "pip3", "conda", "git", "cd", "export", "source", "mkdir", "cp", "mv", "rm", "ls", "echo", "cat",
    "apt", "apt-get", "virtualenv", "touch", "wget", "curl", "docker", "brew", "set", "unset", "which", "tree",
})
# Names that say nothing about the repository.
COMMON_NAMES: Final[frozenset] = frozenset("""
print len str int float bool list dict set tuple range repr type isinstance issubclass open format getattr setattr
hasattr delattr sorted reversed min max sum abs any all enumerate zip map filter super round id vars dir help input
iter next exit quit object Exception ValueError TypeError KeyError IndexError AttributeError RuntimeError
NotImplementedError AssertionError ImportError OSError StopIteration Warning DeprecationWarning UserWarning
bytes bytearray frozenset complex divmod pow hash callable chr ord hex oct bin eval exec compile globals locals
staticmethod classmethod property self cls None True False and not for while with assert lambda return yield
import from def class pass raise try except finally else elif del global nonlocal await async
""".split())
# Dotted words in prose that are not code: domains and file types.
NOT_CODE: Final[frozenset] = frozenset({"com", "org", "net", "html", "txt", "rst", "md", "pdf", "png", "yml",
                                        "yaml", "json", "toml", "cfg", "ini", "zip", "gz"})
TEST_PATH: Final[re.Pattern] = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_tests?\.py$|(^|/)conftest\.py$")


class SweBenchIssueTargets:
    """Extracts candidates from an issue and resolves them against the repository."""

    # -- the text ------------------------------------------------------------------------------

    @classmethod
    def split(cls, text: str) -> Tuple[str, List[Tuple[str, str]]]:
        """
        Args:
            text: The issue.

        Returns:
            Tuple[str, List[Tuple[str, str]]]: The prose with fenced blocks taken out, and each
            block as `(language, body)`.
        """
        blocks = [(match.group(1).lower(), match.group(2)) for match in FENCE.finditer(text)]
        return FENCE.sub("\n", text), blocks

    @classmethod
    def candidates(cls, text: str) -> Dict[str, List[Any]]:
        """
        What the issue names, before resolution, in priority order within each kind.

        - `paths`: `(path, source)` for each code file named in the prose or inline code
          (`prose`), in a code block (`code`), or in a traceback frame (`traceback`, deepest
          frame first: the one nearest the error).
        - `dotted`: dotted names in the prose and inline code (`django.db.models.query`,
          `QuerySet.bulk_create`); code blocks are left out, where every import would count.
        - `names`: functions and classes named in the prose as `name(` or in backticks.

        Args:
            text: The issue.

        Returns:
            Dict[str, List[Any]]: `paths`, `dotted` and `names`.
        """
        prose, blocks = cls.split(text)
        paths: List[Tuple[str, str]] = []
        seen = set()

        def add_path(path: str, source: str) -> None:
            path = path.replace("\\", "/")
            if path not in seen:
                seen.add(path)
                paths.append((path, source))

        frames = TRACEBACK_FILE.findall(text)
        for match in PATH.finditer(TRACEBACK_FILE.sub(" ", prose)):
            add_path(match.group(1), "prose")
        for _, body in blocks:
            for match in PATH.finditer(TRACEBACK_FILE.sub(" ", body)):
                add_path(match.group(1), "code")
        for frame in reversed(frames):
            add_path(frame, "traceback")
        inline = " ".join(INLINE.findall(prose))
        dotted: List[str] = []
        names: List[str] = []
        for source in (inline, prose):
            for match in DOTTED.finditer(source):
                name = match.group(1)
                last = name.split(".")[-1]
                if not re.search(r"\.(" + CODE_SUFFIXES + r")$", name) and name not in dotted \
                        and len(last) >= 3 and last not in NOT_CODE and name.split(".")[0] not in COMMON_NAMES:
                    dotted.append(name)
        for source in (inline, prose):
            for match in CALLED.finditer(source):
                name = match.group(1).split(".")[-1]
                if name not in names:
                    names.append(name)
        for code in INLINE.findall(prose):
            code = code.strip().rstrip("()")
            if IDENTIFIER.fullmatch(code) and code not in names:
                names.append(code)
        names = [name for name in names if len(name) >= 3 and name not in COMMON_NAMES and not name.startswith("__")]
        return {"paths": paths, "dotted": dotted, "names": names}

    @classmethod
    def examples(cls, text: str) -> List[Dict[str, Any]]:
        """
        The runnable examples the issue shows. A Python example is a fenced block that parses
        as Python, calls something and is more than one bare expression (which is more likely
        printed output), or a run of `>>>` lines (fenced or not); a shell example
        is a command in a shell block or after a `$ ` prompt that is not setup (`pip install`,
        `cd`, `git clone`). A traceback, program output or Python 2 code is not an example:
        when unsure there is none, so the stop is never held for it.

        Args:
            text: The issue.

        Returns:
            List[Dict[str, Any]]: `{"kind": "python", "key_lines", "calls", "first"}` or
            `{"kind": "shell", "commands": [[program, argument]], "first"}`.
        """
        prose, blocks = cls.split(text)
        found: List[Dict[str, Any]] = []
        for language, body in blocks:
            if language in SHELL_LANGS or re.search(r"^\s*\$ ", body, re.M):
                example = cls.shell_example(body, language in SHELL_LANGS)
            elif language in PYTHON_LANGS:
                example = cls.python_example(body)
            else:
                example = None
            if example:
                found.append(example)
        prompts = "\n".join(line for line in prose.splitlines() if re.match(r"\s*(>>>|\.\.\.)( |$)", line))
        example = cls.python_example(prompts) if prompts else None
        if example:
            found.append(example)
        return found

    @classmethod
    def python_example(cls, body: str) -> Optional[Dict[str, Any]]:
        """
        Args:
            body: A code block.

        Returns:
            Optional[Dict[str, Any]]: The example, or None when the block is not runnable
            Python (see `examples`).
        """
        lines = body.splitlines()
        prompted = [line for line in lines if re.match(r"\s*(>>>|\.\.\.|In \[\d+\]:)( |$)", line)]
        if prompted:
            code = [re.sub(r"^\s*(>>>|\.\.\.|In \[\d+\]:) ?", "", line) for line in prompted]
        else:
            if body.lstrip().startswith("Traceback"):
                return None
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")  # an issue's `"\d"` is not this program's fault
                    tree = ast.parse(textwrap.dedent(body))
            except (SyntaxError, ValueError):
                return None
            if not any(isinstance(node, ast.Call) for node in ast.walk(tree)):
                return None
            if len(tree.body) < 2 and not isinstance(tree.body[0], (ast.Import, ast.ImportFrom, ast.Assign)):
                # One bare expression: a printed value (`array([[True, False]])`), not a program.
                return None
            code = lines
        key_lines = []
        for line in code:
            stripped = line.strip()
            if len(stripped) < 4 or stripped.startswith("#") or "(" not in stripped \
                    or re.match(r"(import|from)\s", stripped):
                continue
            if stripped not in key_lines:
                key_lines.append(stripped)
        if not key_lines:
            return None
        calls = []
        for name in CALL.findall("\n".join(code)):
            if name not in COMMON_NAMES and name not in calls:
                calls.append(name)
        return {"kind": "python", "key_lines": key_lines[:8], "calls": calls[:10], "first": key_lines[0][:80]}

    @classmethod
    def shell_example(cls, body: str, shell_block: bool) -> Optional[Dict[str, Any]]:
        """
        Args:
            body: A code block.
            shell_block: Whether its language is a shell's, where unprompted lines are commands.

        Returns:
            Optional[Dict[str, Any]]: The example, or None when it only sets things up.
        """
        prompted = [line.split("$ ", 1)[1] for line in body.splitlines() if re.match(r"\s*\$ ", line)]
        lines = prompted if prompted else ([line for line in body.splitlines() if line.strip()
                                            and not line.strip().startswith("#")] if shell_block else [])
        commands, first = [], None
        for line in lines:
            words = line.split()
            if not words:
                continue
            program = re.sub(r"^python[0-9.]*$", "python", words[0].split("/")[-1])
            if program in SETUP_PROGRAMS or "=" in program:
                continue
            arguments = [word for word in words[1:] if not word.startswith("-")]
            argument = arguments[0].split("/")[-1].split("::")[0] if arguments else ""
            if program == "python" and words[1:2] == ["-m"] and len(words) > 2:
                program, argument = words[2], (arguments[1].split("/")[-1] if len(arguments) > 1 else "")
            if [program, argument] not in commands:
                commands.append([program, argument])
            first = first or line.strip()[:80]
        return {"kind": "shell", "commands": commands[:5], "first": first} if commands else None

    # -- the repository ------------------------------------------------------------------------

    @classmethod
    def resolve_script(cls, names: List[str]) -> str:
        """
        The light index, run in the container: every tracked file, then the `def`/`class` lines
        of the names in tracked Python files.

        Args:
            names: Identifiers (validated: letters, digits and `_` only).

        Returns:
            str: A bash script printing the repository's directory on the first line, then
            `git ls-files`, a marker line, and `git grep`'s hits.
        """
        names = [name for name in names if IDENTIFIER.fullmatch(name)][:MAX_NAMES]
        script = 'cd "${TESTBED:-/testbed}" || exit 3\npwd\ngit -c core.quotePath=false ls-files\necho "@@definitions@@"\n'
        if names:
            pattern = r"^[[:space:]]*(async[[:space:]]+)?(def|class)[[:space:]]+(" + "|".join(names) + r")([^A-Za-z0-9_]|$)"
            script += f"git -c core.quotePath=false grep -n -I -E '{pattern}' -- '*.py' || true\n"
        return script

    @classmethod
    def parse_listing(cls, output: str) -> Tuple[List[str], Dict[str, List[str]]]:
        """
        Args:
            output: What `resolve_script` printed.

        Returns:
            Tuple[List[str], Dict[str, List[str]]]: The tracked files, and each name's defining
            files (test files left out), in the order found.
        """
        files_part, _, definitions_part = output.partition("@@definitions@@\n")
        files = [line for line in files_part.splitlines() if line]
        definitions: Dict[str, List[str]] = {}
        for line in definitions_part.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            match = DEFINITION.match(parts[2])
            if match and not TEST_PATH.search(parts[0]):
                where = definitions.setdefault(match.group(1), [])
                if parts[0] not in where:
                    where.append(parts[0])
        return files, definitions

    @classmethod
    def resolve_path(cls, path: str, files: List[str]) -> Tuple[Optional[str], str]:
        """
        Finds the tracked file a path names: the path itself, or the longest tail of it that
        names exactly one file (`/usr/lib/python3/site-packages/django/db/models/query.py` is
        `django/db/models/query.py`). A bare base name must be unique.

        Returns:
            Tuple[Optional[str], str]: The file, or None and why not.
        """
        parts = [part for part in path.split("/") if part not in ("", ".")]
        for start in range(len(parts)):
            tail = "/".join(parts[start:])
            if tail in files:
                return tail, ""
            matches = [f for f in files if f.endswith("/" + tail)]
            if len(matches) == 1:
                return matches[0], ""
            if len(matches) > 1 and start == len(parts) - 1:
                return None, f"names {len(matches)} files"
        return None, "not in the repository"

    @classmethod
    def resolve_dotted(cls, name: str, files: List[str]) -> Tuple[Optional[str], List[str]]:
        """
        Maps a dotted name to a module file: `a.b.c` is `a/b/c.py` or `a/b/c/__init__.py`, or
        a shorter prefix with the rest naming definitions in it.

        Returns:
            Tuple[Optional[str], List[str]]: The module file (None when no prefix of two or more
            parts is a module), and the parts after it.
        """
        parts = name.split(".")
        tracked = set(files)
        for end in range(len(parts), 1, -1):
            stem = "/".join(parts[:end])
            for candidate in (stem + ".py", stem + "/__init__.py"):
                for prefix in ("", "src/", "lib/"):
                    if prefix + candidate in tracked:
                        return prefix + candidate, parts[end:]
        return None, parts

    @classmethod
    def conditions(cls, text: str, listing: str, root: str = "/testbed") -> Dict[str, Any]:
        """
        Builds the hooks' conditions: what must be read before the first edit, and the
        example to run before stopping.

        Priority, until `MAX_TARGETS`: paths named in the prose, dotted module names, functions
        and classes, paths in code blocks, then traceback frames (deepest first). A function or
        class is dropped when a file that defines it is a target too.

        Args:
            text: The issue as the agent sees it.
            listing: What `resolve_script(names_to_resolve(text))` printed after its first line.
            root: The repository in the container.

        Returns:
            Dict[str, Any]: `root`, `targets` (`id`, `kind`, `path` or `name` and `files`,
            `label`, `from`), `examples`, and `dropped` (`text`, `why`).
        """
        found = cls.candidates(text)
        files, definitions = cls.parse_listing(listing)
        targets: List[Dict[str, Any]] = []
        dropped: List[Dict[str, str]] = []
        target_files: set = set()

        def add_file(path: str, source: str, mentioned: str) -> None:
            if path in target_files:
                return
            target_files.add(path)
            targets.append({"id": f"file:{path}", "kind": "file", "path": path, "label": path, "from": source,
                            "text": mentioned})

        def add_definition(name: str, where: List[str], source: str, mentioned: str) -> None:
            if any(t.get("name") == name for t in targets):
                return
            targets.append({"id": f"def:{name}", "kind": "definition", "name": name, "files": where,
                            "label": f"`{name}` ({', '.join(where)})", "from": source, "text": mentioned})

        later = []
        for path, source in found["paths"]:
            resolved, why = cls.resolve_path(path, files)
            if resolved is None:
                dropped.append({"text": path, "why": why})
            elif source == "prose":
                add_file(resolved, source, path)
            else:
                later.append((resolved, source, path))
        for name in found["dotted"]:
            module, rest = cls.resolve_dotted(name, files)
            if module is not None and not rest:
                add_file(module, "dotted", name)
                continue
            last = rest[-1] if rest else name.split(".")[-1]
            where = definitions.get(last, [])
            if module is not None:
                where = [path for path in where if path == module] or []
                if not where:
                    add_file(module, "dotted", name)
                    continue
            elif len(where) > 1 and len(name.split(".")) > 1:
                # `QuerySet.bulk_create`: the files that also define the class.
                owners = definitions.get(name.split(".")[-2], [])
                where = [path for path in where if path in owners] or where
            if not where:
                dropped.append({"text": name, "why": "not defined in the repository"})
            elif len(where) > MAX_DEFINITIONS:
                dropped.append({"text": name, "why": f"defined in {len(where)} files"})
            else:
                add_definition(last, where, "dotted", name)
        for name in found["names"]:
            where = definitions.get(name, [])
            if any(t.get("name") == name for t in targets):
                continue
            if not where:
                dropped.append({"text": name, "why": "not defined in the repository"})
            elif len(where) > MAX_DEFINITIONS:
                dropped.append({"text": name, "why": f"defined in {len(where)} files"})
            else:
                add_definition(name, where, "name", name)
        for resolved, source, path in later:
            add_file(resolved, source, path)
        for target in [t for t in targets if t["kind"] == "definition"]:
            if any(path in target_files for path in target["files"]):
                # Reading the file reads the definition.
                targets.remove(target)
                dropped.append({"text": target["text"], "why": "its file is already to be read"})
        for target in targets[MAX_TARGETS:]:
            dropped.append({"text": target["text"], "why": f"past the first {MAX_TARGETS}"})
        return {"version": 1, "set": "issue-v1", "root": root, "targets": targets[:MAX_TARGETS],
                "examples": cls.examples(text), "dropped": dropped[:40]}

    @classmethod
    def names_to_resolve(cls, text: str) -> List[str]:
        """
        Args:
            text: The issue.

        Returns:
            List[str]: The identifiers the container's `git grep` looks up: the named functions
            and classes, and the last two parts of each dotted name.
        """
        found = cls.candidates(text)
        names = list(found["names"])
        for name in found["dotted"]:
            for part in name.split(".")[-2:]:
                if part not in names and part not in COMMON_NAMES and len(part) >= 3:
                    names.append(part)
        return names[:MAX_NAMES]
