"""
Codex hooks as an arm (`swe-bench run --hooks`, specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §20).

A hook set registers commands for Codex's hook events in the instance's own `CODEX_HOME`
before its first session starts, each with the trust entry Codex requires before it runs a
hook: an untrusted hook is skipped in silence (MIGHTLING_COMPACTION §7). The launcher in the
container edits the same `config.toml` and keeps groups and trust entries that are not its own
(ling-rs/src/compaction.rs). The only set is `issue-v1` (`SweBenchIssueGate`).
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

from dreamference.swe_bench import swe_bench_issue_gate

# The hook sets `--hooks` takes, with what each enforces.
HOOK_SETS: Final[Dict[str, str]] = {
    "issue-v1": "the first edit is held once until the files and functions the issue names have been read, "
                "and the first stop is held once if the issue shows an example that no command since "
                "the last edit has run",
}

# Where the gate is mounted, and where its conditions and state are, inside the container.
GATE_MOUNT: Final[str] = "/opt/ling-issue-gate/issue_gate.py"
GATE_DIRECTORY: Final[str] = "issue-gate"

# The hook runs through the session's shell. Conda's base Python comes first: the image's
# testbed environment may be older than the gate supports, and a login shell may reset PATH. If
# none is there the hook does nothing (exit 0), and the state file then records no invocation.
HOOK_COMMAND: Final[str] = ('for p in /opt/miniconda3/bin/python3 /opt/miniconda3/envs/testbed/bin/python3 '
                            '/usr/bin/python3; do [ -x "$p" ] && exec "$p" ' + GATE_MOUNT +
                            ' {event} {directory}; done; exit 0')

# Codex's events (the table under `[hooks]`), their label in trust keys, and the gate's event.
EVENTS: Final[List[tuple]] = [("PreToolUse", "pre_tool_use", "pre-tool-use"),
                              ("PostToolUse", "post_tool_use", "post-tool-use"),
                              ("Stop", "stop", "stop")]
HOOK_TIMEOUT_S: Final[int] = 30


class SweBenchHooks:
    """Registers a hook set in an instance's `CODEX_HOME` and reads back what it did."""

    @classmethod
    def hook_hash(cls, event_label: str, command: str, timeout: int, matcher: Optional[str] = None) -> str:
        """
        The hash Codex compares with `hooks.state.<key>.trusted_hash` before it runs a hook
        (codex-rs/config/src/fingerprint.rs `version_for_toml` over the normalised hook,
        hooks/src/engine/discovery.rs `hook_hash`): SHA-256 over canonical JSON, keys sorted,
        no spaces. The same scheme as the launcher's (ling-rs/src/compaction.rs), pinned by a
        test to a hash a live session accepted.

        Args:
            event_label: `pre_tool_use`, `post_tool_use`, `stop`, `session_start`.
            command: The handler's command.
            timeout: Its timeout in seconds.
            matcher: The group's matcher, if any (Codex ignores it for `Stop`).

        Returns:
            str: `sha256:<hex>`.
        """
        identity: Dict[str, Any] = {"event_name": event_label,
                                    "hooks": [{"async": False, "command": command, "timeout": timeout, "type": "command"}]}
        if matcher:
            identity["matcher"] = matcher
        serialized = json.dumps(identity, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        return "sha256:" + hashlib.sha256(serialized.encode()).hexdigest()

    @classmethod
    def config_text(cls, config_path: str, directory: str) -> str:
        """
        The `[hooks]` tables of a hook set: one group per event with no matcher (every tool),
        one command handler each, and their trust entries keyed
        `<config path>:<event label>:<group>:<handler>`.

        Args:
            config_path: `config.toml` as Codex in the container sees it: the trust key's prefix.
            directory: The gate's directory in the container.

        Returns:
            str: TOML to put in a `config.toml` that has no `[hooks]` yet.
        """
        lines = ["# The SWE-bench runner's hooks (ling-admin swe-bench run --hooks)."]
        for table, _, event in EVENTS:
            command = HOOK_COMMAND.format(event=event, directory=directory)
            lines += [f"[[hooks.{table}]]", f"[[hooks.{table}.hooks]]", 'type = "command"',
                      f"command = {json.dumps(command)}", f"timeout = {HOOK_TIMEOUT_S}", ""]
        for _, label, event in EVENTS:
            command = HOOK_COMMAND.format(event=event, directory=directory)
            lines += [f"[hooks.state.{json.dumps(f'{config_path}:{label}:0:0')}]",
                      f'trusted_hash = "{cls.hook_hash(label, command, HOOK_TIMEOUT_S)}"', ""]
        return "\n".join(lines)

    @classmethod
    def register(cls, codex_home: Path, container_codex_home: str, container_directory: str) -> None:
        """
        Writes the hook set into an instance's `config.toml`, before any session.

        Args:
            codex_home: The instance's `CODEX_HOME` on the host.
            container_codex_home: The same in the container.
            container_directory: The gate's directory in the container.
        """
        path = codex_home / "config.toml"
        existing = path.read_text() if path.exists() else ""
        if "[hooks" in existing:
            raise ValueError(f"{path} already has hooks")
        text = cls.config_text(f"{container_codex_home}/config.toml", container_directory)
        path.write_text((existing.rstrip("\n") + "\n\n" if existing.strip() else "") + text)

    @classmethod
    def gate_source(cls) -> Path:
        """
        Returns:
            Path: The gate's file in the installed package, mounted read-only into containers.
        """
        return Path(swe_bench_issue_gate.__file__).resolve()

    @classmethod
    def gate_digest(cls) -> str:
        """
        Returns:
            str: The SHA-256 of the gate's file, recorded in the manifest.
        """
        return hashlib.sha256(cls.gate_source().read_bytes()).hexdigest()

    @classmethod
    def outcome(cls, directory: Path) -> Dict[str, Any]:
        """
        What the gate did in one task, from its conditions and state, for the instance's state.

        Args:
            directory: The gate's directory on the host.

        Returns:
            Dict[str, Any]: `ran` (any hook ran once the conditions were written),
            `invocations`, `targets` (labels), `dropped`, `examples`, `edit_hold`,
            `stop_hold`, `first_edit` (`read` of `targets`), `edits`,
            `example_run_after_last_edit` (at the last stop; None without an example or an
            edit), `errors`.
        """
        def load(name: str) -> Dict[str, Any]:
            try:
                return json.loads((directory / name).read_text())
            except (OSError, ValueError):
                return {}

        conditions, state = load(swe_bench_issue_gate.CONDITIONS_FILE), load(swe_bench_issue_gate.STATE_FILE)
        invocations = state.get("invocations", {})
        targets = conditions.get("targets", [])
        labels = {target["id"]: target["label"] for target in targets}
        first_edit = state.get("first_edit")
        stops = state.get("stops", [])
        edit_hold, stop_hold = state.get("edit_hold"), state.get("stop_hold")
        return {
            "ran": any(count for event, count in invocations.items() if event != "before_conditions"),
            "invocations": invocations,
            "targets": [target["label"] for target in targets],
            "dropped": len(conditions.get("dropped", [])),
            "examples": len(conditions.get("examples", [])),
            "edit_hold": None if not edit_hold else {
                "unread": [labels.get(i, i) for i in edit_hold.get("unread", [])],
                "kind": edit_hold.get("kind"), "complied": edit_hold.get("complied"),
                "read_after": [labels.get(i, i) for i in edit_hold.get("read_after", [])]},
            "stop_hold": None if not stop_hold else {"complied": stop_hold.get("complied")},
            "first_edit": None if not first_edit else {"read": len(first_edit.get("read", [])), "of": len(targets),
                                                       "kind": first_edit.get("kind")},
            "edits": int(state.get("edits") or 0),
            "example_run_after_last_edit": stops[-1].get("example_run_after_last_edit") if stops else None,
            "errors": state.get("errors", [])[-5:],
        }
