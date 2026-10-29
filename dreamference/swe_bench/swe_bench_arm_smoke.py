"""The verdict on a night arm's smoke check.

One real task run with the arm's exact settings before the night starts
(`scripts/swe_bench_night1.sh start`), read from the run's files.

Night 5's candidate arm failed at startup on all 44 of its tasks (the model catalog named an
`apply_patch_tool_type` that the pinned Codex rejects), and its round exited 0 all the same: every
instance was recorded as `error`, the log held the one line `ling` printed before it stopped, and
the night was lost. The smoke check asks only whether the arm runs. A wrong patch, an empty one or
the smoke's short time limit are all fine; an agent that errors, exits non-zero or never reaches
the model is not.
"""

import json
from typing import Any, Final

from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore

# Statuses an instance that ran can end with (the patch is not judged).
RAN: Final[tuple] = ("done", "empty", "stalled", "timeout")
# `ling exec` outcomes that mean the agent ran: a normal end, or the smoke's time limit.
EXEC_RAN: Final[tuple] = ("ok", "timeout")
# Item types the model's answers produce. An `error` item is a warning `ling` prints (a config
# warning, the long-thread notice) and proves nothing about the model.
MODEL_ITEMS: Final[tuple] = (
    "agent_message",
    "reasoning",
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "web_search",
    "todo_list",
    "collab_tool_call",
)
# How much of a log line or a note a refusal quotes.
QUOTE: Final[int] = 300


class SweBenchArmSmoke:
    """Judges one smoke instance of a night arm: did the agent run and reach the model?"""

    @classmethod
    def events(cls, log_text: str) -> list[dict[str, Any]]:
        """The events.

        Args:
            log_text: An instance's log (`ling exec --json`'s events, after a first line that is
                not JSON).

        Returns:
            List[Dict[str, Any]]: The events, in order; lines that are not a JSON object are left out.
        """
        found = []
        for line in log_text.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                found.append(event)
        return found

    @classmethod
    def first_words(cls, log_text: str) -> str:
        """The first line of the log that is not JSON and not the stdin notice: what `ling` said when it stopped."""
        for line in log_text.splitlines():
            line = line.strip()
            if not line or line.startswith(("{", "Reading additional input")):
                continue
            return line[:QUOTE]
        return ""

    @classmethod
    def model_replied(cls, events: list[dict[str, Any]]) -> bool:
        """True when an event shows an answer from the model: a model item, or a turn that completed with tokens."""
        for event in events:
            kind = event.get("type")
            raw_item = event.get("item")
            item = raw_item if isinstance(raw_item, dict) else {}
            if (
                kind in ("item.started", "item.completed", "item.updated")
                and item.get("type") in MODEL_ITEMS
            ):
                return True
            if kind == "turn.completed":
                raw_usage = event.get("usage")
                usage = raw_usage if isinstance(raw_usage, dict) else {}
                if (usage.get("input_tokens") or 0) > 0 or (usage.get("output_tokens") or 0) > 0:
                    return True
        return False

    @classmethod
    def verdict(cls, state: dict[str, Any] | None, log_text: str, run_status: int) -> str | None:
        """Decides whether the arm ran.

        Args:
            state: The instance's state (`runs/<run>/instances/<id>.json`), or None when there is none.
            log_text: The instance's log (`runs/<run>/logs/<id>.jsonl`), empty when there is none.
            run_status: The exit status of the smoke's `swe-bench run`.

        Returns:
            Optional[str]: Why the arm must not run tonight, or None when it ran.
        """
        said = cls.first_words(log_text)
        said = f"; ling said: {said}" if said else ""
        if state is None:
            return f"the smoke task never started (swe-bench run exited {run_status})"
        status, outcome = state.get("status"), state.get("exec")
        notes = "; ".join(str(note) for note in state.get("notes") or [])[:QUOTE]
        notes = f"; notes: {notes}" if notes else ""
        if status not in RAN:
            return f"the smoke task ended `{status}` (ling exec: {outcome}){said}{notes}"
        if outcome not in EXEC_RAN:
            return (
                f"ling exec ended `{outcome}` (the agent errored or exited non-zero){said}{notes}"
            )
        events = cls.events(log_text)
        for index, event in enumerate(events):
            if event.get("type") == "turn.failed":
                raw_error = event.get("error")
                error = raw_error if isinstance(raw_error, dict) else {}
                return f"a turn failed: {str(error.get('message') or event)[:QUOTE]}"
            # A stream error the agent retried is followed by a completed turn; one it was not is fatal.
            if event.get("type") == "error" and not any(
                later.get("type") == "turn.completed" for later in events[index + 1 :]
            ):
                return (
                    f"the session reported an error: {str(event.get('message') or event)[:QUOTE]}"
                )
        if not cls.model_replied(events):
            return f"no answer from the model reached the agent (status {status}, ling exec: {outcome}){said}"
        if run_status != 0:
            return f"swe-bench run exited {run_status}"
        return None

    @classmethod
    def check(cls, run: str, instance_id: str, run_status: int, arm: str) -> int:
        """Prints the verdict on a smoke run, the way the night script shows it.

        Args:
            run: The smoke run's name.
            instance_id: The smoke task.
            run_status: The exit status of the smoke's `swe-bench run`.
            arm: The arm, as the night's ARMS spells it.

        Returns:
            int: 0 when the arm ran, 1 when it must not run tonight.
        """
        store = SweBenchRunStore(run)
        try:
            log_text = store.log_path(instance_id).read_text(errors="replace")
        except OSError:
            log_text = ""
        state = store.state(instance_id)
        reason = cls.verdict(state, log_text, run_status)
        if reason:
            print(f"❌ Smoke check of arm {arm} ({run}, {instance_id}): {reason}.")
            return 1
        stats = store.log_stats(instance_id)
        assert state is not None  # `verdict` reports a missing state
        print(
            f"✅ Smoke check of arm {arm}: {instance_id} ran ({state.get('status')}, {state.get('wall_s')} s, "
            f"{stats['commands']} command(s), {stats['input_tokens']} input tokens); run {run}."
        )
        return 0
