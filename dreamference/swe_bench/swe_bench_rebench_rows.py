"""
SWE-rebench's rows in the form the pinned harness grades (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §28).

The pinned `swebench` grades any row that carries its own `image`, `eval_script`, `log_parser`
and `eval_type`. SWE-rebench's rows carry none of them: each has an `install_config` (Python
version, packages, install command, test command, log parser) from which the SWE-rebench fork of
the harness (`SWE-rebench/SWE-bench-fork`, `make_eval_script_list_py`) writes the eval script at
grading time. This module writes the same script once, when the snapshot is taken, so grading
is the pinned harness's as for Verified, and the eval script is in the snapshot for anyone to
read. The reset of the test files keeps the one-command shape `per_file_reset` rewrites.
"""

import json
import re
from typing import Any, Dict, Final, List

# The fork's markers and heredoc delimiter, byte for byte.
START_TEST_OUTPUT: Final[str] = ">>>>> Start Test Output"
END_TEST_OUTPUT: Final[str] = ">>>>> End Test Output"
TEST_PATCH_DELIMITER: Final[str] = "EOF_114329324912"

# The fork's `NON_TEST_EXTS`: files of a test patch that are not test directives (`csv` without
# its dot, as the fork has it).
NON_TEST_EXTS: Final[tuple] = (".json", ".png", "csv", ".txt", ".md", ".jpg", ".jpeg", ".pkl",
                               ".yml", ".yaml", ".toml")

ENV_NAME: Final[str] = "testbed"
REPO_DIRECTORY: Final[str] = "/testbed"

FILE_HEADER: Final["re.Pattern[str]"] = re.compile(r"^diff --git a/(.+?) b/(.+)$")


class SweBenchRebenchRows:
    """Turns SWE-rebench rows into rows the pinned harness grades."""

    @classmethod
    def install_config(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Args:
            row: A SWE-rebench row.

        Returns:
            Dict[str, Any]: Its `install_config`, decoded when the dataset gives it as JSON text.
        """
        config = row.get("install_config") or {}
        if isinstance(config, str):
            config = json.loads(config)
        return config if isinstance(config, dict) else {}

    @classmethod
    def adapt(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Adds what the pinned harness needs to a SWE-rebench row, keeping every field it has.

        Args:
            row: A SWE-rebench row.

        Returns:
            Dict[str, Any]: The row with `eval_script`, `log_parser`, `eval_type` and `image`
            (the published x86-64 image; `SweBenchImages` names the one this machine runs), and
            the `interface` column at the end of `problem_statement` when the statement lacks it.
        """
        config = cls.install_config(row)
        adapted = dict(row)
        adapted["install_config"] = config
        adapted["eval_script"] = cls.eval_script(row)
        adapted["log_parser"] = config.get("log_parser") or "parse_log_pytest"
        adapted["eval_type"] = "pass_and_fail"
        adapted["image"] = row.get("docker_image") or row.get("image_name") or ""
        interface = str(row.get("interface") or "").strip()
        statement = str(row.get("problem_statement") or "")
        if interface and interface not in statement:
            # Six of the 49 `_interface` tasks name the interface the tests call only in this
            # column (five end their statement with the bare heading): the agent is given it.
            heading = "" if statement.rstrip().endswith("## Interface") else "\n\n## Interface"
            adapted["problem_statement"] = f"{statement.rstrip()}{heading}\n\n{interface}\n"
            adapted["interface_appended"] = True
        return adapted

    @classmethod
    def eval_script(cls, row: Dict[str, Any]) -> str:
        """
        Writes the eval script the fork writes for a row: activate the environment, record the
        tree, run the install command again, reset the test patch's files, apply the test patch,
        run the test command with the test patch's test files between the two markers, reset
        the files again.

        One difference, the one `per_file_reset` makes for Verified: the reset names every file
        the test patch touches, the files it adds too, not only those the base commit has (the
        fork's `get_modified_files`). `per_file_reset` then removes an added file before the test
        patch is applied, so an agent's own file of that name cannot stop the patch from applying,
        and the reset is never the fork's bare `git checkout <base>` (43 of the 860 test patches
        only add files), which moves `HEAD` instead of resetting a file.

        Args:
            row: A SWE-rebench row.

        Returns:
            str: The script, as the harness reads it from a row.
        """
        config = cls.install_config(row)
        base = row["base_commit"]
        test_patch = row.get("test_patch") or ""
        test_files = cls.touched_files(test_patch)
        reset = [f"git checkout {base} {' '.join(test_files)}"] if test_files else []
        test_cmd = config.get("test_cmd") or ""
        if isinstance(test_cmd, list):
            test_cmd = " ".join(test_cmd)
        test_command = " ".join([test_cmd, *cls.test_directives(row)])
        commands = [
            "source /opt/miniconda3/bin/activate",
            f"conda activate {ENV_NAME}",
            f"cd {REPO_DIRECTORY}",
            *list(config.get("eval_commands") or []),
            f"git config --global --add safe.directory {REPO_DIRECTORY}",
            f"cd {REPO_DIRECTORY}",
            "git status",
            "git show",
            f"git -c core.fileMode=false diff {base}",
            "source /opt/miniconda3/bin/activate",
            f"conda activate {ENV_NAME}",
        ]
        if "install" in config:
            commands.append(str(config.get("install") or ""))
        commands += [
            *reset,
            f"git apply -v - <<'{TEST_PATCH_DELIMITER}'\n{test_patch}\n{TEST_PATCH_DELIMITER}",
            f": '{START_TEST_OUTPUT}'",
            test_command,
            f": '{END_TEST_OUTPUT}'",
            *reset,
        ]
        return "#!/bin/bash\nset -uxo pipefail\n" + "\n".join(commands) + "\n"

    # -- the test patch's files ------------------------------------------------------------------

    @classmethod
    def files(cls, patch: str) -> List[Dict[str, str]]:
        """
        Reads a patch's files the way `unidiff` gives them to the fork.

        Args:
            patch: A git patch.

        Returns:
            List[Dict[str, str]]: Per file, `source` and `target` as the `---`/`+++` lines name
            them (`/dev/null` for a side that does not exist), in the patch's order.
        """
        found: List[Dict[str, str]] = []
        current: Dict[str, str] = {}
        for line in patch.split("\n"):
            header = FILE_HEADER.match(line)
            if header:
                current = {"source": f"a/{header.group(1)}", "target": f"b/{header.group(2)}"}
                found.append(current)
            elif not current:
                continue
            elif line.startswith("new file mode"):
                current["source"] = "/dev/null"
            elif line.startswith("deleted file mode"):
                current["target"] = "/dev/null"
            elif line.startswith("--- ") and "hunks" not in current:
                current["source"] = line[4:].split("\t")[0].strip()
            elif line.startswith("+++ ") and "hunks" not in current:
                current["target"] = line[4:].split("\t")[0].strip()
            elif line.startswith("@@"):
                current["hunks"] = "yes"
        return [{"source": entry["source"], "target": entry["target"]} for entry in found]

    @classmethod
    def modified_files(cls, patch: str) -> List[str]:
        """
        The fork's `get_modified_files`: the files a patch changes that exist before it.

        Args:
            patch: A git patch.

        Returns:
            List[str]: Their paths.
        """
        return [entry["source"][2:] for entry in cls.files(patch)
                if entry["source"] != "/dev/null" and entry["source"].startswith("a/")]

    @classmethod
    def touched_files(cls, patch: str) -> List[str]:
        """
        Args:
            patch: A git patch.

        Returns:
            List[str]: Every file it changes, adds or removes, each once, in the patch's order.
        """
        paths: List[str] = []
        for entry in cls.files(patch):
            side = entry["source"] if entry["source"] != "/dev/null" else entry["target"]
            path = side[2:] if side[:2] in ("a/", "b/") else side
            if path != "/dev/null" and path not in paths:
                paths.append(path)
        return paths

    @classmethod
    def test_directives(cls, row: Dict[str, Any]) -> List[str]:
        """
        The fork's `get_test_directives`: the test patch's files that are not removed and not
        data, named for Django as its test runner wants them.

        Args:
            row: A SWE-rebench row.

        Returns:
            List[str]: The arguments after the test command.
        """
        directives = []
        for entry in cls.files(row.get("test_patch") or ""):
            if entry["target"] == "/dev/null":
                continue
            path = entry["target"][2:] if entry["target"].startswith("b/") else entry["target"]
            if not any(path.endswith(extension) for extension in NON_TEST_EXTS):
                directives.append(path)
        if row.get("repo") == "django/django":
            transformed = []
            for directive in directives:
                directive = directive[:-len(".py")] if directive.endswith(".py") else directive
                directive = directive[len("tests/"):] if directive.startswith("tests/") else directive
                transformed.append(directive.replace("/", "."))
            directives = transformed
        return directives
