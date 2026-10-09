"""
The `issue-v1` hooks, run inside an instance's container by Codex
(specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §20).

`issue-v1` as a task rule only asks; this enforces two of its lines, once each per task:

- **Before the first edit** (`PreToolUse`): the files and definitions the issue names, as the
  runner resolved them when the task started (`SweBenchIssueTargets`), must have been read. If
  any is unread, the first edit is denied once, naming what is still unread.
- **Before the first stop** (`Stop`): if the issue shows a runnable example and the agent has
  edited, some command after the last edit must have run that example; if not, the stop is
  blocked once, with that instruction.

A `PostToolUse` hook only records what each tool call read. Neither hook blocks a second time,
whatever the agent does, so a wrong condition costs one turn and can never trap the run. A hook
that fails for any reason of its own allows the call and records the error.

This file is mounted read-only into the container and started by Codex once per hook event:
`issue_gate.py <pre-tool-use|post-tool-use|stop> <directory>`, the event's JSON on stdin. It runs
under the image's own Python (3.6 or later) with the standard library alone, so it imports nothing
from this package; the tests import it as a module and call the same methods. Its state, and the
conditions the runner wrote, live in `<directory>` (the instance's scratch, on the host).
"""

import fcntl
import json
import os
import re
import shlex
import sys
import time
from typing import Any, Dict, List, Optional

CONDITIONS_FILE = "conditions.json"
STATE_FILE = "state.json"
LOCK_FILE = "lock"

# The reasons the model is shown. Codex appends ". Command: <the command>" to a denied tool call's
# reason (core/src/hook_runtime.rs), so the first ends without a full stop.
EDIT_MESSAGE = ("Not yet: read what the issue names before your first edit. Still unread: {unread}. "
                "Open each one (cat, sed -n, or grep -n for its definition), then make the edit again")
STOP_MESSAGE = ("Before you stop: the issue shows an example, and no command since your last edit has "
                "run it{first}. Run the issue's example now and check that its output is what the issue "
                "expects; if it is not, fix the code. Then stop.")

# How much of a script the agent runs is read to compare with the example.
SCRIPT_LIMIT = 200000
# How many entries of each list the state keeps.
KEEP = 40

PYTHON = re.compile(r"(^|/)(i?python[0-9.]*)$")
PYTEST = re.compile(r"(^|/)(py\.?test)$")
CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
REDIRECT = re.compile(r"(?:^|[^0-9&<>])>>?\s*([^\s;|&<>()]+)")
HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?[^\n]*\n(.*?)\n\s*\1\s*(?:\n|$)", re.S)
PATCH_FILE = re.compile(r"^\*\*\* (Update|Delete|Add) File: (.+?)\s*$", re.M)


class SweBenchIssueGate:
    """The two holds and the record of reads; every method works on plain dicts."""

    # -- reads ---------------------------------------------------------------------------------

    @classmethod
    def text_of(cls, value: Any) -> str:
        """
        Args:
            value: A hook payload's `tool_input` or `tool_response`, of any shape.

        Returns:
            str: Every string in it, joined by newlines.
        """
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return "\n".join(cls.text_of(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return "\n".join(cls.text_of(item) for item in value)
        return "" if value is None else str(value)

    @classmethod
    def file_touched(cls, path: str, command: str, output: str) -> bool:
        """
        Whether a tool call touched a repository file: its command or output names the path
        (`cat a/b.py`, `grep -n x a/b.py`, a `grep -rn` hit `./a/b.py:12:`), or its command names
        the file's directory and then its base name (`cd a && cat b.py`).

        Args:
            path: The file, relative to the repository.
            command: The call's command or arguments.
            output: What the call printed.

        Returns:
            bool: True when it counts as a read.
        """
        if path in command or path in output:
            return True
        directory, name = os.path.split(path)
        return bool(directory) and directory in command and re.search(r"(^|[\s/'\"])" + re.escape(name) + r"\b", command) is not None

    @classmethod
    def reads(cls, targets: List[Dict[str, Any]], command: str, output: str) -> List[str]:
        """
        Which targets a tool call read. A file is read when the call touched it
        (`file_touched`); a definition when the output shows `def <name>` or `class <name>`,
        or the call touched a file that defines it.

        Args:
            targets: The conditions' targets.
            command: The call's command or arguments.
            output: What the call printed (empty before it ran).

        Returns:
            List[str]: The ids of the targets read.
        """
        found = []
        for target in targets:
            if target["kind"] == "file":
                hit = cls.file_touched(target["path"], command, output)
            else:
                pattern = r"\b(def|class)\s+" + re.escape(target["name"]) + r"\b"
                hit = re.search(pattern, output) is not None or \
                    any(cls.file_touched(path, command, output) for path in target.get("files", []))
            if hit:
                found.append(target["id"])
        return found

    # -- edits ---------------------------------------------------------------------------------

    @classmethod
    def repository_file(cls, path: str, cwd: str, root: str) -> Optional[str]:
        """
        Args:
            path: A path as a command wrote it.
            cwd: The directory the command ran in.
            root: The repository.

        Returns:
            Optional[str]: The path relative to the repository when it names a file that exists
            there (outside `.git`); None for anything else, `/tmp` and new files included.
        """
        path = path.strip().strip("'\"")
        if not path or path.startswith("-") or path.startswith("/dev/"):
            return None
        full = os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd or root, path))
        root = os.path.normpath(root)
        if not full.startswith(root + os.sep):
            return None
        relative = full[len(root) + 1:]
        if relative.split(os.sep)[0] == ".git" or not os.path.isfile(full):
            return None
        return relative

    @classmethod
    def segments(cls, command: str) -> List[str]:
        """
        Args:
            command: A shell command line.

        Returns:
            List[str]: Its simple commands: split at `&&`, `||`, `;`, `|` and newlines outside
            quotes.
        """
        pieces, current, quote, index = [], [], None, 0
        while index < len(command):
            char = command[index]
            if quote:
                if char == quote:
                    quote = None
                elif char == "\\" and quote == '"' and index + 1 < len(command):
                    current.append(char)
                    index += 1
                    char = command[index]
                current.append(char)
            elif char in "'\"":
                quote = char
                current.append(char)
            elif char in ";|\n" or command.startswith("&&", index):
                pieces.append("".join(current))
                current = []
                if command.startswith(("&&", "||"), index):
                    index += 1
            else:
                current.append(char)
            index += 1
        pieces.append("".join(current))
        return [piece for piece in pieces if piece.strip()]

    @classmethod
    def words(cls, segment: str) -> List[str]:
        try:
            return shlex.split(segment, comments=False, posix=True)
        except ValueError:
            return segment.split()

    @classmethod
    def edit_of(cls, tool_name: str, command: str, cwd: str, root: str) -> Optional[Dict[str, Any]]:
        """
        Whether a tool call changes a file that exists in the repository: what the first-edit
        hold and the "after the last edit" rule mean by an edit. Detected: `apply_patch` (the
        tool, or the command in a shell) updating or deleting a file; in a shell command,
        `sed -i`/`perl -i` on a file, `>`, `>>` or `tee` onto one, `git apply`, `git am` and
        `patch`. A new file is not an edit (a script in /tmp or in the repository), and neither
        is `git stash`. Not detected: a program that writes the file itself (`python -c
        "open(...,'w')"`), `cp`, `mv`.

        Args:
            tool_name: The hook payload's `tool_name` (`apply_patch`, `Bash`, an MCP tool's).
            command: Its `tool_input.command` (or all of its input as text).
            cwd: The call's working directory.
            root: The repository.

        Returns:
            Optional[Dict[str, Any]]: `{"kind", "paths"}`, or None when it is not an edit.
        """
        if tool_name == "apply_patch" or "*** Begin Patch" in command:
            paths = [cls.repository_file(match.group(2), cwd, root)
                     for match in PATCH_FILE.finditer(command) if match.group(1) != "Add"]
            paths = [path for path in paths if path]
            return {"kind": "apply_patch", "paths": paths} if paths else None
        body = HEREDOC.sub("\n", command)
        for segment in cls.segments(body):
            words = cls.words(segment)
            if not words:
                continue
            program = os.path.basename(words[0])
            if program in ("sed", "perl") and any(word == "-i" or word.startswith("-i") or word.startswith("--in-place")
                                                  or (program == "perl" and word.startswith("-") and "i" in word[1:])
                                                  for word in words[1:]):
                paths = [cls.repository_file(word, cwd, root) for word in words[1:]]
                paths = [path for path in paths if path]
                if paths:
                    return {"kind": program + " -i", "paths": paths}
            if program == "git" and len(words) > 1 and words[1] in ("apply", "am"):
                return {"kind": "git " + words[1], "paths": []}
            if program == "patch":
                return {"kind": "patch", "paths": []}
            if program == "tee":
                paths = [cls.repository_file(word, cwd, root) for word in words[1:]]
                paths = [path for path in paths if path]
                if paths:
                    return {"kind": "tee", "paths": paths}
        for match in REDIRECT.finditer(body):
            path = cls.repository_file(match.group(1), cwd, root)
            if path:
                return {"kind": "redirect", "paths": [path]}
        return None

    # -- the example ---------------------------------------------------------------------------

    @classmethod
    def squeeze(cls, text: str) -> str:
        return re.sub(r"\s+", "", text)

    @classmethod
    def code_matches(cls, code: str, example: Dict[str, Any]) -> bool:
        """
        Whether code the agent ran is the issue's Python example: one of the example's key
        lines appears in it (whitespace ignored), or it calls at least half of the example's
        distinctive functions.

        Args:
            code: The code run.
            example: A Python example from the conditions.

        Returns:
            bool: True when it counts as running the example.
        """
        squeezed = cls.squeeze(code)
        if any(cls.squeeze(line) in squeezed for line in example.get("key_lines", []) if cls.squeeze(line)):
            return True
        calls = set(example.get("calls", []))
        if not calls:
            return False
        made = set(CALL.findall(code))
        return len(calls & made) >= max(1, (len(calls) + 1) // 2)

    @classmethod
    def ran_code(cls, command: str, cwd: str) -> List[Optional[str]]:
        """
        The Python code a shell command runs: `-c` arguments, here-documents fed to Python, and
        the scripts it runs with Python or pytest, read from disk (`None` for a script that
        cannot be read: what it holds is unknown).

        Args:
            command: The shell command.
            cwd: Its working directory.

        Returns:
            List[Optional[str]]: One entry per piece of code; empty when it runs no Python.
        """
        pieces: List[Optional[str]] = []
        for match in HEREDOC.finditer(command):
            before = command[:match.start()].split("\n")[-1]
            if any(PYTHON.search(word) for word in cls.words(before.replace("<", " "))):
                pieces.append(match.group(2))
        for segment in cls.segments(HEREDOC.sub("\n", command)):
            words = cls.words(segment)
            for index, word in enumerate(words):
                python = PYTHON.search(word) is not None
                pytest = PYTEST.search(word) is not None or \
                    (python and words[index + 1:index + 3] == ["-m", "pytest"])
                if not python and not pytest:
                    continue
                rest = words[index + 1:]
                if python and "-c" in rest and rest.index("-c") + 1 < len(rest):
                    pieces.append(rest[rest.index("-c") + 1])
                    break
                for argument in rest:
                    script = argument.split("::")[0]
                    if script.startswith("-") or not script.endswith(".py"):
                        continue
                    path = script if os.path.isabs(script) else os.path.join(cwd or "/", script)
                    try:
                        with open(path, errors="replace") as handle:
                            pieces.append(handle.read(SCRIPT_LIMIT))
                    except OSError:
                        pieces.append(None)
                    if python:
                        break
                break
        return pieces

    @classmethod
    def runs_example(cls, command: str, cwd: str, examples: List[Dict[str, Any]]) -> bool:
        """
        Whether a shell command runs the issue's example. A Python example: some code the
        command runs matches it (`code_matches`), or the command runs a script that cannot be
        read (unsure, so it counts). A shell example: the command runs the same program on the
        same file.

        Args:
            command: The shell command.
            cwd: Its working directory.
            examples: The conditions' examples.

        Returns:
            bool: True when it counts.
        """
        pieces = None
        for example in examples:
            if example["kind"] == "python":
                if pieces is None:
                    pieces = cls.ran_code(command, cwd)
                if any(piece is None or cls.code_matches(piece, example) for piece in pieces):
                    return True
            else:
                normal = re.sub(r"\bpython[0-9.]*\b", "python", command)
                for program, argument in example.get("commands", []):
                    if re.search(r"(^|[\s/])" + re.escape(program) + r"\b", normal) and (not argument or argument in normal):
                        return True
        return False

    # -- the events ----------------------------------------------------------------------------

    @classmethod
    def pre_tool_use(cls, state: Dict[str, Any], conditions: Dict[str, Any],
                     payload: Dict[str, Any]) -> Optional[str]:
        """
        Before a tool call: records it, and decides whether it is the first edit to hold.

        Args:
            state: The task's state, changed in place.
            conditions: What the runner extracted from the issue.
            payload: The hook's input.

        Returns:
            Optional[str]: The reason to deny the call, or None to allow it.
        """
        state["seq"] = state.get("seq", 0) + 1
        seq = state["seq"]
        targets = conditions.get("targets", [])
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str) \
            else cls.text_of(tool_input)
        cwd = payload.get("cwd") or conditions.get("root", "/testbed")
        edit = cls.edit_of(payload.get("tool_name", ""), command, cwd, conditions.get("root", "/testbed"))
        reads = state.setdefault("reads", {})
        if edit is None:
            for target in cls.reads(targets, command, ""):
                reads.setdefault(target, seq)
            if conditions.get("examples") and state.get("edits") and \
                    cls.runs_example(command, cwd, conditions["examples"]):
                state["last_example_run"] = seq
                state["example_runs"] = (state.get("example_runs", []) + [seq])[-KEEP:]
            return None
        unread = [target for target in targets if target["id"] not in reads]
        if "first_edit" not in state:
            state["first_edit"] = {"seq": seq, "kind": edit["kind"], "paths": edit["paths"][:10],
                                   "read": [t["id"] for t in targets if t["id"] in reads],
                                   "unread": [t["id"] for t in unread]}
        hold = state.get("edit_hold")
        if hold and hold.get("complied") is None:
            # The first edit after the hold: did the agent read what it was told was unread?
            hold["read_after"] = [target for target in hold["unread"] if target in reads]
            hold["complied"] = len(hold["read_after"]) == len(hold["unread"])
            hold["next_edit_seq"] = seq
        if unread and not hold and not state.get("edits"):
            state["edit_hold"] = {"seq": seq, "kind": edit["kind"], "paths": edit["paths"][:10],
                                  "unread": [t["id"] for t in unread], "complied": None}
            return EDIT_MESSAGE.format(unread="; ".join(target["label"] for target in unread))
        state["edits"] = state.get("edits", 0) + 1
        state["last_edit"] = seq
        state["edit_log"] = (state.get("edit_log", []) + [{"seq": seq, "kind": edit["kind"],
                                                            "paths": edit["paths"][:10]}])[-KEEP:]
        return None

    @classmethod
    def post_tool_use(cls, state: Dict[str, Any], conditions: Dict[str, Any], payload: Dict[str, Any]) -> None:
        """
        After a tool call: records the targets its command or output read.

        Args:
            state: The task's state, changed in place.
            conditions: What the runner extracted from the issue.
            payload: The hook's input.
        """
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str) \
            else cls.text_of(tool_input)
        seq = state.get("seq", 0)
        reads = state.setdefault("reads", {})
        for target in cls.reads(conditions.get("targets", []), command, cls.text_of(payload.get("tool_response"))):
            reads.setdefault(target, seq)

    @classmethod
    def stop(cls, state: Dict[str, Any], conditions: Dict[str, Any], payload: Dict[str, Any]) -> Optional[str]:
        """
        When the agent stops: decides whether this is the stop to hold.

        Args:
            state: The task's state, changed in place.
            conditions: What the runner extracted from the issue.
            payload: The hook's input.

        Returns:
            Optional[str]: The continuation prompt that blocks the stop, or None to let it stop.
        """
        seq = state.get("seq", 0)
        examples = conditions.get("examples", [])
        last_edit = state.get("last_edit")
        ran = state.get("last_example_run")
        after = None if not examples or last_edit is None else (ran is not None and ran > last_edit)
        stops = state.setdefault("stops", [])
        hold = state.get("stop_hold")
        if hold and hold.get("complied") is None:
            # The first stop after the hold: did a command since then run the example?
            hold["complied"] = ran is not None and ran > hold["seq"]
        block = bool(examples) and last_edit is not None and not after and not hold
        stops.append({"seq": seq, "blocked": block, "example_run_after_last_edit": after})
        del stops[:-KEEP]
        if not block:
            return None
        state["stop_hold"] = {"seq": seq, "edits": state.get("edits", 0), "complied": None}
        first = examples[0].get("first") or ""
        return STOP_MESSAGE.format(first=f" (it starts `{first}`)" if first else "")

    # -- the process ---------------------------------------------------------------------------

    @classmethod
    def handle(cls, event: str, directory: str, payload: Dict[str, Any]) -> Optional[str]:
        """
        One hook event, under the state's lock: loads the conditions and the state, decides,
        and writes the state back.

        Args:
            event: `pre-tool-use`, `post-tool-use` or `stop`.
            directory: Where the conditions and the state are.
            payload: The hook's input.

        Returns:
            Optional[str]: The reason to block, or None.
        """
        with open(os.path.join(directory, LOCK_FILE), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state_path = os.path.join(directory, STATE_FILE)
            try:
                with open(state_path) as handle:
                    state = json.load(handle)
            except (OSError, ValueError):
                state = {}
            counts = state.setdefault("invocations", {})
            try:
                with open(os.path.join(directory, CONDITIONS_FILE)) as handle:
                    conditions = json.load(handle)
            except (OSError, ValueError):
                # No conditions yet (the refine arm's study step): counted, never held.
                counts["before_conditions"] = counts.get("before_conditions", 0) + 1
                conditions = None
            reason = None
            if conditions is not None:
                counts[event] = counts.get(event, 0) + 1
                try:
                    if event == "pre-tool-use":
                        reason = cls.pre_tool_use(state, conditions, payload)
                    elif event == "post-tool-use":
                        cls.post_tool_use(state, conditions, payload)
                    elif event == "stop":
                        reason = cls.stop(state, conditions, payload)
                except Exception as error:  # Never hold a call on a fault of the gate's own.
                    state["errors"] = (state.get("errors", []) + [f"{event}: {error!r}"])[-KEEP:]
                    reason = None
            state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            temporary = state_path + ".tmp"
            with open(temporary, "w") as handle:
                json.dump(state, handle)
            os.replace(temporary, state_path)
            return reason

    @classmethod
    def main(cls, argv: List[str]) -> int:
        """
        Args:
            argv: `[event, directory]`.

        Returns:
            int: 2 with the reason on stderr to block (Codex's convention for both events), 0
            otherwise.
        """
        try:
            event, directory = argv[0], argv[1]
            payload = json.loads(sys.stdin.read() or "{}")
            reason = cls.handle(event, directory, payload)
        except Exception:
            return 0
        if reason:
            sys.stderr.write(reason)
            return 2
        return 0


if __name__ == "__main__":
    sys.exit(SweBenchIssueGate.main(sys.argv[1:]))
