"""The live messenger suite: linking the harness's devices, and the run (see run.sh).

    run.py link-telegram --qr PNG     link TDLib to the owner's Telegram (QR, once)
    run.py link-telegram --qr - --password-at-terminal
                                      the same at the owner's terminal, for an account with a
                                      cloud password (asked there, never kept)
    run.py link-signal --qr PNG       link signal-cli to the owner's Signal (QR, once)
    run.py run [--only telegram|signal] [--take-over] [--signal-night-queue] [--notify]
    run.py install-timer              the nightly user timer (18:00 UTC) and the after-merge one
    run.py after-merge                run the suite when origin/main changed the bridges and the
                                      installed `ling` has the change (the quarter-hourly timer)

`run` writes `<stamp>.json` and `<stamp>.md` (and `latest.*`) under
`~/.cache/dreamference/messenger-live/reports/` and exits 0 when every bridge check passed, 1 when
one failed, 3 when a messenger needs the owner (a lost session: re-link). With `--notify`, a
non-zero exit sends one line through `~/.local/bin/owner-notify`. A bridge failure also writes the
release marker `~/.local/share/dreamference/messenger-live/blocking`; the next green run removes it.
"""

from __future__ import annotations

import argparse
import getpass
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mlive import (
    bridge,
    gate,
    paths,
    report,
    signal_device,
    signal_suite,
    telegram_account,
    telegram_suite,
)

NOTIFY = Path.home() / ".local" / "bin" / "owner-notify"
UNIT = "mightling-messenger-live"
AFTER_MERGE = f"{UNIT}-after-merge"


def unit_files(run_sh: Path, when: str = "18:00") -> dict[str, str]:
    """The user units: the suite's oneshot service and its nightly timer (`when` is UTC), and the
    after-merge check's service and its quarter-hourly timer, which starts the suite's service."""
    service = f"""[Unit]
Description=Mightling's live messenger test (Telegram and Signal; scripts/messenger_live)
After=network-online.target

[Service]
Type=oneshot
ExecStart={run_sh} --notify
TimeoutStartSec=90min
MemoryMax=6G
Nice=10
"""
    timer = f"""[Unit]
Description=Mightling's live messenger test, nightly at {when} UTC (before Night Shift)

[Timer]
OnCalendar=*-*-* {when}:00 UTC
Persistent=false
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
"""
    check = f"""[Unit]
Description=Mightling's live messenger test after a merge that touches the bridges

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I {run_sh.parent / "run.py"} after-merge
TimeoutStartSec=100min
Nice=10
"""
    check_timer = """[Unit]
Description=Mightling's live messenger test after a merge, checked every quarter hour

[Timer]
OnCalendar=*:0/15
Persistent=false

[Install]
WantedBy=timers.target
"""
    return {
        f"{UNIT}.service": service,
        f"{UNIT}.timer": timer,
        f"{AFTER_MERGE}.service": check,
        f"{AFTER_MERGE}.timer": check_timer,
    }


def install_timer() -> int:
    """Writes and enables the user timers, pointing at this checkout's run.sh and run.py."""
    folder = Path.home() / ".config" / "systemd" / "user"
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in unit_files(Path(__file__).resolve().parent / "run.sh").items():
        (folder / name).write_text(text)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    for unit in (UNIT, AFTER_MERGE):
        subprocess.run(["systemctl", "--user", "enable", "--now", f"{unit}.timer"], check=True)
    print(f"✅ {UNIT}.timer: nightly at 18:00 UTC; {AFTER_MERGE}.timer: every quarter hour")
    return 0


def start_suite_unit() -> int:
    """Starts the suite's service and waits for it (systemd never runs it twice at once)."""
    done = subprocess.run(["systemctl", "--user", "start", f"{UNIT}.service"], check=False)
    return done.returncode


def after_merge() -> int:
    """The after-merge check, against the checkout this script lives in."""
    repo = Path(__file__).resolve().parents[2]
    return gate.AfterMerge(repo).check(start_suite_unit)


def installed_build() -> dict[str, str]:
    """What the run tests: the installed `ling`'s version and build key."""
    build = {"build-key": gate.installed_stamp() or "none"}
    try:
        version = subprocess.run(
            [str(bridge.LING), "--version"], capture_output=True, text=True, timeout=30
        )
        build["ling"] = version.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        build["ling"] = "not installed"
    return build


def link_telegram(qr: Path, minutes: float, password_at_terminal: bool) -> int:
    """Links the harness's TDLib device to the owner's account through a QR code PNG (or `-`)."""
    ask = None
    if password_at_terminal:
        if not sys.stdin.isatty():
            print("❌ --password-at-terminal asks at a terminal; run it in one")
            return 2

        def ask(hint: str) -> str:
            shown = f" (hint: {hint})" if hint else ""
            return getpass.getpass(f"Telegram cloud password{shown}; not shown, not kept: ")

    try:
        client, _me = telegram_account.open_session(qr, minutes * 60, ask)
    except telegram_account.NeedsOwner as err:
        print(f"❌ {err}")
        return 2
    client.close()
    print("✅ Telegram: the harness's device is linked")
    return 0


def link_signal(qr: Path, minutes: float) -> int:
    """Links the harness's signal-cli device to the owner's account through a QR code PNG."""
    data = paths.private_dir(paths.signal_dir())
    if signal_device.own_number(data):
        print("✅ Signal: the harness's device is already linked")
        return 0
    try:
        signal_device.link(data, qr, minutes)
    except telegram_account.NeedsOwner as err:
        print(f"❌ {err}")
        return 2
    print("✅ Signal: the harness's device is linked")
    return 0


def run_telegram(
    run: report.Run, cache: Path, take_over: bool, log: Path, debug: Path | None
) -> None:
    """The Telegram half: the owner's device, the scratch bridge, the checks."""
    try:
        client, me = telegram_account.open_session(None, 120)
    except telegram_account.NeedsOwner as err:
        run.blocked["telegram"] = str(err)
        return
    scratch = bridge.ScratchBridge(log)
    try:
        others = bridge.bridges_with_token(scratch.token, scratch.home)
        if others and take_over:
            for pid in others:
                os.kill(pid, signal.SIGTERM)
            time.sleep(3)
        scratch.prepare(int(me["id"]))
        username = scratch.start()
        chat = telegram_account.BotChat(client, username)
        suite = telegram_suite.TelegramSuite(
            client, chat.chat_id, chat.bot_user_id, scratch.sessions(), cache, run
        )
        try:
            suite.run_all()
        finally:
            if debug is not None:
                suite.dump(debug)
    except Exception as err:  # noqa: BLE001 (reported as the run's failure)
        run.add(
            report.Result(
                "telegram", "setup", "bridge", "fail", None, None, f"{type(err).__name__}: {err}"
            )
        )
    finally:
        scratch.stop()
        client.close()


def run_signal(run: report.Run, cache: Path, queue_night: bool, debug: Path | None) -> None:
    """The Signal half: the harness's device writes Note to Self to the owner's live bridge."""
    data = paths.signal_dir()
    number = signal_device.own_number(data) if data.exists() else None
    if not number:
        run.blocked["signal"] = "owner re-link needed: the harness's Signal device is not linked"
        return
    rpc = signal_suite.SignalRpc(data, number, debug)
    try:
        signal_suite.SignalSuite(
            rpc, Path.home() / ".mightling" / "sessions", cache, run, queue_night
        ).run_all()
    except Exception as err:  # noqa: BLE001 (reported as the run's failure)
        run.add(
            report.Result(
                "signal", "setup", "bridge", "fail", None, None, f"{type(err).__name__}: {err}"
            )
        )
    finally:
        rpc.close()


def notify(run: report.Run, md: Path) -> None:
    """Tells the owner about a failed or blocked run, in one line."""
    if not NOTIFY.exists():
        return
    alerts = run.alerts()
    if alerts:
        names = ", ".join(f"{r.messenger}: {r.check}" for r in alerts[:4])
        text = (
            f"Live messenger test: {len(alerts)} bridge check(s) failed ({names}); "
            f"1.7.x patch releases blocked ({gate.marker_path()}). Report: {md}"
        )
    else:
        text = "Live messenger test: " + "; ".join(
            f"{m}: {why}" for m, why in sorted(run.blocked.items())
        )
    subprocess.run([str(NOTIFY), text], check=False, timeout=60)


def run_suite(
    only: str | None, take_over: bool, queue_night: bool, send_notice: bool, debug: bool
) -> int:
    """Runs the chosen messengers' checks and writes the reports."""
    cache = paths.private_dir(paths.cache_dir())
    reports = paths.private_dir(cache / "reports")
    run = report.Run(build=installed_build())
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(run.started))
    if only in (None, "telegram"):
        run_telegram(
            run,
            cache,
            take_over,
            reports / f"{stamp}-bridge.log",
            (reports / f"{stamp}-telegram.json") if debug else None,
        )
    if only in (None, "signal"):
        run_signal(run, cache, queue_night, (reports / f"{stamp}-signal.jsonl") if debug else None)
    json_path, md_path = report.write(run, reports)
    print(md_path.read_text())
    print(f"Reports: {json_path} {md_path}")
    code = run.exit_code()
    failed = [f"{r.messenger}: {r.check}" for r in run.alerts()]
    marker = gate.update_marker(code, md_path, failed, run.build)
    if marker != "kept":
        print(f"Release marker {gate.marker_path()}: {marker}")
    if send_notice and code != 0:
        notify(run, md_path)
    return code


def main() -> int:
    """Parses the command line and runs the chosen step."""
    parser = argparse.ArgumentParser(description="The live messenger suite (Telegram and Signal).")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("link-telegram", "link-signal"):
        link = sub.add_parser(
            name, help=f"link the harness's device ({name.split('-')[1]}) by QR code"
        )
        link.add_argument("--qr", type=Path, required=True)
        link.add_argument("--minutes", type=float, default=30)
        if name == "link-telegram":
            link.add_argument(
                "--password-at-terminal",
                action="store_true",
                help="ask for the account's cloud password here (never kept); `--qr -` draws the code here",
            )
    go = sub.add_parser("run", help="run the suite and write the reports")
    go.add_argument("--only", choices=("telegram", "signal"))
    go.add_argument(
        "--take-over", action="store_true", help="stop another bridge answering as the test bot"
    )
    go.add_argument(
        "--signal-night-queue",
        action="store_true",
        help="let Signal's /night add really queue (the owner's queue)",
    )
    go.add_argument(
        "--notify",
        action="store_true",
        help="tell the owner through owner-notify when the run is not green",
    )
    go.add_argument(
        "--debug",
        action="store_true",
        help="keep the bridge's Note to Self messages the harness saw",
    )
    sub.add_parser(
        "install-timer", help="install the nightly user timer (18:00 UTC) and the after-merge one"
    )
    sub.add_parser(
        "after-merge", help="run the suite if origin/main changed the bridges and it is installed"
    )
    args = parser.parse_args()
    if args.command == "install-timer":
        return install_timer()
    if args.command == "after-merge":
        return after_merge()
    if args.command == "link-telegram":
        return link_telegram(args.qr, args.minutes, args.password_at_terminal)
    if args.command == "link-signal":
        return link_signal(args.qr, args.minutes)
    return run_suite(args.only, args.take_over, args.signal_night_queue, args.notify, args.debug)


if __name__ == "__main__":
    sys.exit(main())
