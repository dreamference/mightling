"""The morning report (specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md §5.6).

One `## <repository>` section per repository, so `/night report` can print just the section of the
repository it is asked from, and the general notes after them.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Final

LAST_MESSAGE_STATUSES: Final[tuple] = ("stalled", "failed", "interrupted")


class NightShiftReport:
    """Formats and writes `reports/<date>.md`."""

    @classmethod
    def render(
        cls,
        started: datetime,
        tasks: list[dict[str, Any]],
        notes: list[str],
        compactions: list[str] | None = None,
    ) -> str:
        """Renders a report.

        Args:
            started: When the run started; it names the report.
            tasks: The records of every task the run looked at, as they ended.
            notes: Admission and scheduling notes (checks that stopped or delayed the run).
            compactions: The compaction audit's lines (compaction spec §10.3), if any.

        Returns:
            str: Markdown.
        """
        lines = [
            f"# Night Shift — {started:%Y-%m-%d}",
            "",
            f"Run started {started:%H:%M}, finished {datetime.now():%H:%M}.",
            "",
        ]
        by_repo: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            by_repo.setdefault(task.get("repo", "?"), []).append(task)
        for repo, repo_tasks in by_repo.items():
            lines += [f"## {repo}", ""]
            for task in repo_tasks:
                lines += [*cls._task_block(task), ""]
        if tasks:
            lines += cls._review(tasks)
        if compactions:
            lines += ["", "## Compactions", ""] + [f"- {line}" for line in compactions]
        if notes:
            lines += ["", "## Notes", ""] + [f"- {note}" for note in notes]
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def _review(cls, tasks: list[dict[str, Any]]) -> list[str]:
        lines = ["## Review", ""]
        branches = [task for task in tasks if (task.get("result") or {}).get("branch")]
        for task in branches:
            lines.append(
                f"- `git -C {task['repo']} diff {task['base'][:12]}..{task['result']['branch']}`"
            )
        if branches:
            lines.append(
                "- Nothing was merged into your checkout. `ling night apply <id>` (or `/night apply <id>`), "
                "run in the repository, brings a task's changes there as uncommitted changes; "
                "it refuses, changing nothing, if they conflict with yours."
            )
        else:
            lines.append("- No branch to review.")
        lines.append("- `git worktree list` shows the worktrees kept for interrupted tasks.")
        return lines

    @classmethod
    def _task_block(cls, task: dict[str, Any]) -> list[str]:
        result = task.get("result") or {}
        first = (task.get("task", "").strip().splitlines() or [""])[0]
        lines = [f"### {task.get('id')} — {task.get('status')}", "", f"- Task: {first}"]
        if task.get("on"):
            target = (task.get("sent_to") or {}).get("name") or task["on"]
            lines.append(
                f"- Ran on: {target}, by that node's own runner and model server"
                if result.get("node")
                else f"- Sent to: {target}"
            )
        elif result.get("model_node"):
            lines.append(f"- Model server: {result['model_node']}'s (the task ran on this machine)")
        if task.get("status") == "sent":
            lines.append("- Not finished there yet: its branch comes back with a later run.")
        if result.get("branch"):
            lines.append(
                f"- Branch: `{result['branch']}`, not merged; "
                f"`ling night apply {task.get('id')}` brings it into your checkout"
            )
        if result.get("diff_stat"):
            summary = result["diff_stat"].splitlines()[-1].strip()
            lines.append(f"- Changes: {summary}")
        if result.get("test_command"):
            sandbox = f", sandbox: {result['test_sandbox']}" if result.get("test_sandbox") else ""
            lines.append(
                f"- Tests: `{result['test_command']}` ({result.get('test_source', '?')}): "
                f"{result.get('test_result', '?')}{sandbox}"
            )
        lines.append(
            f"- Attempts: {result.get('attempts', task.get('attempts', 0))}, "
            f"nudges: {result.get('nudges', 0)}, wall time: {cls._minutes(result.get('wall_s'))}"
        )
        best = result.get("best_of")
        if isinstance(best, dict):
            lines += cls._best_of_lines(best)
        refine = result.get("refine")
        if isinstance(refine, dict):
            edited = (
                "; it changed the worktree, which was put back"
                if refine.get("study_edited")
                else ""
            )
            version = (
                f" (refine-{refine['version']})" if refine.get("version", "v1") != "v1" else ""
            )
            lines.append(
                f"- Refine: studied first for {cls._minutes(refine.get('study_s'))}, "
                f"a {refine.get('refined_chars', 0)}-character description{edited}{version}"
            )
        if task.get("status") in LAST_MESSAGE_STATUSES and result.get("last_message"):
            message = result["last_message"].strip().replace("\n", " ")
            lines.append(f"- Last message: {message[:400]}")
        if task.get("status") == "running":
            lines.append("- Still running when the report was written.")
        return lines

    @classmethod
    def _best_of_lines(cls, best: dict[str, Any]) -> list[str]:
        """Best-of-N (`NightShiftBestOfN`): what ran and why, the chosen attempt and why, every attempt."""
        chosen = best.get("chosen")
        points = best.get("points") or {}
        ran = f"{best.get('ran', 0)} of {best.get('n', '?')} attempts ran"
        if best.get("why"):
            ran += f" ({best['why']})"
        if chosen is None:
            lines = [f"- Best of {best.get('n', '?')}: {ran}; none chosen"]
        else:
            scored = (
                "; points "
                + ", ".join(f"{key} {value:.2f}" for key, value in sorted(points.items()))
                if points
                else ""
            )
            lines = [
                f"- Best of {best.get('n', '?')}: {ran}; chose attempt {chosen} by {best.get('stage', '?')}{scored}"
            ]
        if best.get("reason"):
            lines.append(f"  - Why: {best['reason']}")
        if best.get("judge_calls"):
            errors = (
                f", {best['judge_errors']} unreadable or failed" if best.get("judge_errors") else ""
            )
            log = f"; answers in `{best['judge_log']}`" if best.get("judge_log") else ""
            lines.append(f"  - Judge: {best['judge_calls']} call(s){errors}{log}")
        lines += [cls._attempt_line(entry, chosen) for entry in best.get("attempts") or []]
        return lines

    @classmethod
    def _attempt_line(cls, entry: dict[str, Any], chosen: int | None) -> str:
        """One attempt of a best-of-N task: its end, change, tests, time, and where its diff and logs are."""
        parts = [str(entry.get("status", "?"))]
        if entry.get("changes"):
            parts.append(entry["changes"])
        if entry.get("test_result"):
            parts.append(f"tests {entry['test_result']}")
        parts.append(cls._minutes(entry.get("wall_s")))
        if entry.get("attempt") == chosen:
            parts.append("chosen: the branch")
        elif entry.get("diff"):
            parts.append(f"diff `{entry['diff']}`")
        for key, label in (("log", "log"), ("test_log", "test log")):
            if entry.get(key):
                parts.append(f"{label} `{entry[key]}`")
        return f"  - Attempt {entry.get('attempt')}: " + ", ".join(parts)

    @classmethod
    def _minutes(cls, seconds: int | None) -> str:
        if seconds is None:
            return "—"
        return f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"

    @classmethod
    def write(cls, night_dir: Path, started: datetime, text: str) -> Path:
        """Writes the report, numbering it if a report for that date exists.

        Args:
            night_dir: The queue directory.
            started: When the run started.
            text: The rendered report.

        Returns:
            Path: Where it was written.
        """
        reports = night_dir / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        path = reports / f"{started:%Y-%m-%d}.md"
        counter = 2
        while path.exists():
            path = reports / f"{started:%Y-%m-%d}-{counter}.md"
            counter += 1
        path.write_text(text)
        return path
