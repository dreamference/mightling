"""`ling web`'s bridge as a Telegram Mini App (TELEGRAM_MOONSHOT §15).

bridge.js is run in Node's `vm` with a stand-in window: Telegram's launch parameters in the
address's fragment, a fetch that answers the server's launch check, and a WebSocket that only
records that it was opened. Nothing here touches a network or the real machine.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parent.parent / "ling-rs" / "web" / "static" / "bridge.js"

HARNESS = Path(__file__).resolve().parent / "telegram_mini_app_harness.js"


def run(hash_: str, answer: dict) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        ["node", str(HARNESS), str(BRIDGE), hash_, json.dumps(answer)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


LAUNCH = (
    "#tgWebAppData=query_id%3DAA%26user%3D%257B%2522id%2522%253A42%257D%26auth_date%3D1%26hash%3Dab"
    "&tgWebAppThemeParams=%7B%22bg_color%22%3A%22%23101010%22%7D"
)


def test_a_telegram_launch_is_checked_before_anything_connects() -> None:
    record = run(LAUNCH, {"ok": True})
    assert record["fetches"] == [
        {
            "url": "/api/telegram/check",
            "body": {"initData": "query_id=AA&user=%7B%22id%22%3A42%7D&auth_date=1&hash=ab"},
        }
    ]
    assert record["sockets"] == 1
    assert record["events"] == ["web_app_ready", "web_app_expand"]
    assert record["replaced"] == "/"
    assert record["host"] == "telegram"
    assert record["dataTheme"] == "dark"
    assert record["theme"] == "light"


def test_a_refused_launch_says_why_and_never_connects() -> None:
    record = run(LAUNCH, {"ok": False, "reason": "Your Telegram account is not paired."})
    assert record["sockets"] == 0
    assert "Your Telegram account is not paired." in (record["body"] or "")


def test_an_ordinary_browser_tab_is_unchanged() -> None:
    record = run("", {"ok": False})
    assert record["fetches"] == []
    assert record["sockets"] == 1
    assert record["events"] == []
    assert record["host"] is None
    assert record["hasReceiver"] is False
