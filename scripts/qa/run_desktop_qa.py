#!/usr/bin/env python3
"""Run the daily x86-64 Linux QA on a client desktop over SSH, from the node, and report it.

docs/dev/testing.md, "Daily QA on the idle machines". A user timer on the node runs this; it names
the desktop only by the SSH alias it is given, and the node under test by what the node is at run
time (its mDNS name, and its overlay name from the overlay client), so the repository holds no
host name or address.

    run_desktop_qa.py --host <ssh alias> [--report-dir DIR] [--memory 6G] [--cpu 400%]

The desktop runs scripts/qa/desktop_linux_qa.sh, fed on standard input, inside a transient user
scope capped in memory and CPU, at the lowest CPU and I/O priority: it is someone's desktop. The
report is `<date>.json` and `<date>.md` (and the full log, `<date>.log`) in the report directory;
the owner is alerted (owner-notify) only when a check fails or the desktop cannot be reached.

`--platform macos` runs scripts/qa/macos_qa.sh instead (the light run on the old 8 GB Mac mini): no
systemd scope (macOS has none), `nice` and `taskpolicy -b` for the lowest priority, reports in
~/.local/state/dreamference/qa/macos, and a failure goes to the coordinator's inbox (coord-inbox
--action) instead of the owner.
"""

import argparse
import contextlib
import datetime
import json
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

MACOS = "macos"
SCRIPTS = {
    "linux": Path(__file__).with_name("desktop_linux_qa.sh"),
    MACOS: Path(__file__).with_name("macos_qa.sh"),
}
TITLES = {"linux": "Desktop QA", MACOS: "macOS QA"}
REPORTS = Path.home() / ".local/state/dreamference/qa"
INBOX = Path.home() / ".local/bin/coord-inbox"
NOTIFY = Path.home() / ".local/bin/owner-notify"
MARK = "QA-RESULT "
PASS = "pass"
FAIL = "fail"
UNREACHABLE = "unreachable"
SSH_FAILED = 255


def overlay_name() -> str:
    """This node's name in the remote-access overlay, as its overlay client says now ("" if none)."""
    netbird = shutil.which("netbird")
    if not netbird:
        return ""
    try:
        out = subprocess.run(
            [netbird, "status", "--json"], capture_output=True, text=True, timeout=20, check=False
        ).stdout
        status = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    if not status.get("management", {}).get("connected"):
        return ""
    return str(status.get("fqdn") or "")


def run_remote(
    host: str, platform: str, memory: str, cpu: str, env: dict[str, str]
) -> tuple[int | None, str]:
    """Run the QA script on the desktop; returns ssh's exit code (None: it timed out) and the output."""
    assignments = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
    # The remote login shell may be fish: one plain command line, nothing shell-specific.
    if platform == MACOS:
        remote = f"env {assignments} nice -n 19 taskpolicy -b bash -s"
    else:
        remote = (
            f"env {assignments} systemd-run --user --scope --quiet --collect"
            f" -p MemoryMax={memory} -p MemoryHigh={memory} -p MemorySwapMax=1G -p CPUQuota={cpu}"
            " -p IOWeight=10 nice -n 19 ionice -c 3 bash -s"
        )
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=60",
    ]
    with SCRIPTS[platform].open("rb") as script:
        try:
            done = subprocess.run(
                [*command, host, remote],
                stdin=script,
                capture_output=True,
                timeout=(1 if platform == MACOS else 3) * 3600,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            output = expired.stdout or b""
            return None, output.decode(errors="replace") if isinstance(output, bytes) else str(
                output
            )
    return done.returncode, (done.stdout + done.stderr).decode(errors="replace")


def markdown(report: dict[str, Any]) -> str:
    """The report as a short Markdown table."""
    lines = [
        f"# {TITLES[report['platform']]}, {report['date']}",
        "",
        f"Result: **{report['result']}** (ssh exit {report['ssh_exit']}, {report['minutes']} min)",
        "",
        "| Check | Result | Seconds | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for check in report["checks"]:
        detail = str(check["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check['name']} | {check['status']} | {check['seconds']} | {detail} |")
    return "\n".join(lines) + "\n"


def notify(text: str, platform: str) -> None:
    """A short alert (the coordinator's inbox for macOS, the owner otherwise); never fails the run."""
    if platform == MACOS:
        if INBOX.exists():
            subprocess.run(
                [str(INBOX), "add", f"macos-qa: {text}", "--action"],
                capture_output=True,
                timeout=60,
                check=False,
            )
    elif NOTIFY.exists():
        subprocess.run([str(NOTIFY), text], capture_output=True, timeout=60, check=False)


def main() -> int:
    """Run, write the report, alert on failure; exit 1 when the run failed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", required=True, help="the desktop's SSH alias")
    parser.add_argument("--platform", choices=sorted(SCRIPTS), default="linux")
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument("--memory", default="6G", help="the desktop scope's memory cap")
    parser.add_argument("--cpu", default="400%", help="the desktop scope's CPU quota")
    parser.add_argument("--no-notify", action="store_true", help="never alert the owner")
    args = parser.parse_args()
    if args.report_dir is None:
        args.report_dir = REPORTS / (MACOS if args.platform == MACOS else "desktop")

    started = datetime.datetime.now(datetime.UTC)
    env = {
        "QA_LAN_HOST": socket.gethostname().split(".")[0] + ".local",
        "QA_OVERLAY_HOST": "" if args.platform == MACOS else overlay_name(),
        "QA_MODEL_PORT": "8000",
        "QA_WEB_PORT": "3100",
    }
    code, output = run_remote(args.host, args.platform, args.memory, args.cpu, env)
    checks = []
    for line in output.splitlines():
        if line.startswith(MARK):
            with contextlib.suppress(ValueError):
                checks.append(json.loads(line[len(MARK) :]))
    failed = [check["name"] for check in checks if check.get("status") == FAIL]
    if not checks:
        result = UNREACHABLE if code == SSH_FAILED else "broken"
    elif failed or code != 0:
        result = FAIL
    else:
        result = PASS

    stamp = started.strftime("%Y-%m-%dT%H%MZ")
    report = {
        "date": started.isoformat(timespec="seconds"),
        "result": result,
        "platform": args.platform,
        "ssh_exit": code,
        "minutes": round((datetime.datetime.now(datetime.UTC) - started).total_seconds() / 60, 1),
        "checks": checks,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / f"{stamp}.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.report_dir / f"{stamp}.md").write_text(markdown(report))
    (args.report_dir / f"{stamp}.log").write_text(output)
    for name in ("latest.json", "latest.md"):
        (args.report_dir / name).write_text(
            (args.report_dir / f"{stamp}{Path(name).suffix}").read_text()
        )
    print(markdown(report))  # noqa: T201 - the timer's journal

    if result != PASS and not args.no_notify:
        if result == FAIL and failed:
            what = "failed: " + "; ".join(failed)
        elif result == FAIL:
            what = f"ended with exit {code}"
        else:
            what = "could not reach the machine" if result == UNREACHABLE else "printed no results"
        notify(
            f"{TITLES[args.platform]} {what}. Report: {args.report_dir / (stamp + '.md')}",
            args.platform,
        )
    return 0 if result == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
