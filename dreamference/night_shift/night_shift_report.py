"""
The morning report (specs/DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md §5.6).

One `## <repository>` section per repository, so `/night report` can print just the section of the
repository it is asked from, and the general notes after them.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

LAST_MESSAGE_STATUSES: Final[tuple] = ("stalled", "failed", "interrupted")


class NightShiftReport:
    """Formats and writes `reports/<date>.md`."""

    @classmethod
    def render(cls, started: datetime, tasks: List[Dict[str, Any]], notes: List[str]) -> str:
        """
        Renders a report.

        Args:
            started: When the run started; it names the report.
            tasks: The records of every task the run looked at, as they ended.
            notes: Admission and scheduling notes (checks that stopped or delayed the run).

        Returns:
            str: Markdown.
        """
        lines = [f"# Night Shift — {started:%Y-%m-%d}", "",
                 f"Run started {started:%H:%M}, finished {datetime.now():%H:%M}.", ""]
        by_repo: Dict[str, List[Dict[str, Any]]] = {}
        for task in tasks:
            by_repo.setdefault(task.get("repo", "?"), []).append(task)
        for repo, repo_tasks in by_repo.items():
            lines += [f"## {repo}", ""]
            for task in repo_tasks:
                lines += cls._task_block(task) + [""]
        lines += ["## Review", ""]
        branches = [task for task in tasks if (task.get("result") or {}).get("branch")]
        for task in branches:
            lines.append(f"- `git -C {task['repo']} diff {task['base'][:12]}..{task['result']['branch']}`")
        if not branches:
            lines.append("- No branch to review.")
        lines.append("- `git worktree list` shows the worktrees kept for interrupted tasks.")
        if notes:
            lines += ["", "## Notes", ""] + [f"- {note}" for note in notes]
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def _task_block(cls, task: Dict[str, Any]) -> List[str]:
        result = task.get("result") or {}
        first = (task.get("task", "").strip().splitlines() or [""])[0]
        lines = [f"### {task.get('id')} — {task.get('status')}", "", f"- Task: {first}"]
        if task.get("on"):
            target = (task.get("sent_to") or {}).get("name") or task["on"]
            lines.append(f"- Ran on: {target}, by that node's own runner and model server"
                         if result.get("node") else f"- Sent to: {target}")
        elif result.get("model_node"):
            lines.append(f"- Model server: {result['model_node']}'s (the task ran on this machine)")
        if task.get("status") == "sent":
            lines.append("- Not finished there yet: its branch comes back with a later run.")
        if result.get("branch"):
            lines.append(f"- Branch: `{result['branch']}`")
        if result.get("diff_stat"):
            summary = result["diff_stat"].splitlines()[-1].strip()
            lines.append(f"- Changes: {summary}")
        if result.get("test_command"):
            sandbox = f", sandbox: {result['test_sandbox']}" if result.get("test_sandbox") else ""
            lines.append(f"- Tests: `{result['test_command']}` ({result.get('test_source', '?')}): "
                         f"{result.get('test_result', '?')}{sandbox}")
        lines.append(f"- Attempts: {result.get('attempts', task.get('attempts', 0))}, "
                     f"nudges: {result.get('nudges', 0)}, wall time: {cls._minutes(result.get('wall_s'))}")
        if task.get("status") in LAST_MESSAGE_STATUSES and result.get("last_message"):
            message = result["last_message"].strip().replace("\n", " ")
            lines.append(f"- Last message: {message[:400]}")
        if task.get("status") == "running":
            lines.append("- Still running when the report was written.")
        return lines

    @classmethod
    def _minutes(cls, seconds: Optional[int]) -> str:
        if seconds is None:
            return "—"
        return f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"

    @classmethod
    def write(cls, night_dir: Path, started: datetime, text: str) -> Path:
        """
        Writes the report, numbering it if a report for that date exists.

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
