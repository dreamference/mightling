"""The run's results and its two reports: a JSON file and a short Markdown summary.

Every check has a severity. `bridge` checks test the bridge and the messenger (a failure alerts the
owner); `model` checks test what the model says (a failure is a finding, never an alert); `info`
checks only record what happened. A check is skipped when something outside the bridge stopped
it (the model gate held by a SWE-bench run, a messenger that needs the owner to link it again).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

SEVERITIES: Final = ("bridge", "model", "info")
STATUSES: Final = ("pass", "fail", "skip", "observed")


@dataclass
class Result:
    """One check's outcome."""

    messenger: str
    check: str
    severity: str
    status: str
    seconds: float | None = None
    latency: float | None = None
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuses a severity or status outside the known ones."""
        if self.severity not in SEVERITIES or self.status not in STATUSES:
            raise ValueError(f"bad result {self.severity}/{self.status}")


@dataclass
class Run:
    """A whole run: the results of each messenger, and why a messenger did not run."""

    started: float = field(default_factory=time.time)
    results: list[Result] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)

    def add(self, result: Result) -> Result:
        """Records a result and returns it."""
        self.results.append(result)
        return result

    def alerts(self) -> list[Result]:
        """The failures worth waking the owner for: bridge checks that failed."""
        return [r for r in self.results if r.status == "fail" and r.severity == "bridge"]

    def exit_code(self) -> int:
        """0 green, 1 a bridge failure, 3 nothing failed but a messenger needs the owner."""
        if self.alerts():
            return 1
        if self.blocked:
            return 3
        return 0


def to_json(run: Run) -> str:
    """The JSON report."""
    return json.dumps(
        {
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(run.started)),
            "exit_code": run.exit_code(),
            "blocked": run.blocked,
            "results": [asdict(r) for r in run.results],
        },
        indent=1,
    )


def _seconds(value: float | None) -> str:
    return "" if value is None else f"{value:.1f}"


def to_markdown(run: Run) -> str:
    """The short Markdown report: a verdict line, the blocked messengers, a table per messenger."""
    alerts = run.alerts()
    findings = [r for r in run.results if r.status == "fail" and r.severity == "model"]
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(run.started))
    verdict = "FAIL" if alerts else ("NEEDS OWNER" if run.blocked else "PASS")
    lines = [f"# Live messenger test, {when}: {verdict}", ""]
    passed = sum(r.status == "pass" for r in run.results)
    lines.append(
        f"{passed} passed, {len(alerts)} bridge failures, {len(findings)} model findings, {sum(r.status == 'skip' for r in run.results)} skipped."
    )
    for messenger, why in sorted(run.blocked.items()):
        lines.append(f"- **{messenger}** did not run: {why}")
    for messenger in sorted({r.messenger for r in run.results}):
        lines += [
            "",
            f"## {messenger}",
            "",
            "| Check | Kind | Result | Seconds | First reply (s) | Detail |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in run.results:
            if r.messenger == messenger:
                detail = r.detail.replace("|", "\\|").replace("\n", " ")
                lines.append(
                    f"| {r.check} | {r.severity} | {r.status} | {_seconds(r.seconds)} | {_seconds(r.latency)} | {detail} |"
                )
    return "\n".join(lines) + "\n"


def write(run: Run, folder: Path) -> tuple[Path, Path]:
    """Writes both reports into `folder` (named by the run's start) and `latest.*` beside them."""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(run.started))
    json_path, md_path = folder / f"{stamp}.json", folder / f"{stamp}.md"
    json_path.write_text(to_json(run))
    md_path.write_text(to_markdown(run))
    for path in (json_path, md_path):
        latest = folder / f"latest{path.suffix}"
        latest.unlink(missing_ok=True)
        latest.symlink_to(path.name)
    return json_path, md_path
