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


def run(
    hash_: str,
    answer: dict | list[dict],
    *,
    search: str = "",
    unpaired: bool = False,
    reloadable: bool = False,
    throw_on_storage: bool = False,
    receive_events: list[dict] | None = None,
    view_messages: list[dict] | None = None,
    receive_non_finite_safe_area: bool = False,
) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    options = {
        "search": search,
        "unpaired": unpaired,
        "reloadable": reloadable,
        "throwOnStorage": throw_on_storage,
        "receiveEvents": receive_events or [],
        "viewMessages": view_messages or [],
        "receiveNonFiniteSafeArea": receive_non_finite_safe_area,
    }
    result = subprocess.run(
        [
            "node",
            str(HARNESS),
            str(BRIDGE),
            hash_,
            json.dumps(answer),
            json.dumps(options),
        ],
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
INIT_DATA = "query_id=AA&user=%7B%22id%22%3A42%7D&auth_date=1&hash=ab"


def test_a_telegram_launch_is_checked_before_anything_connects() -> None:
    record = run(LAUNCH, {"ok": True})
    assert record["fetches"] == [
        {
            "url": "/api/telegram/check",
            "body": {"initData": INIT_DATA},
        }
    ]
    assert record["sockets"] == 1
    assert record["events"] == [
        "web_app_ready",
        "web_app_expand",
        "web_app_request_safe_area",
        "web_app_request_content_safe_area",
    ]
    assert record["replaced"] == "/"
    assert record["host"] == "telegram"
    assert record["dataTheme"] == "dark"
    assert record["theme"] == "light"
    assert record["openTarget"] is None


def test_a_refused_launch_says_why_and_never_connects() -> None:
    record = run(
        LAUNCH,
        {"ok": False, "reason": "Your Telegram account is not paired."},
        search="?open=repo:acme/widgets&approval=1",
    )
    assert record["sockets"] == 0
    assert "Your Telegram account is not paired." in (record["body"] or "")
    assert record["openTargetBeforeCheck"] is None
    assert record["openTarget"] is None


def test_an_ordinary_browser_tab_is_unchanged() -> None:
    record = run("", {"ok": False})
    assert record["fetches"] == []
    assert record["sockets"] == 1
    assert record["events"] == []
    assert record["host"] is None
    assert record["hasReceiver"] is False
    assert record["replaced"] is None
    assert record["openTarget"] is None


def test_unpaired_telegram_launch_pairs_shows_code_and_then_runs_launch_check() -> None:
    record = run(
        LAUNCH,
        [
            {"pending": "req-99", "code": "482 913"},
            {"status": "pending", "ok": False},
            {"status": "approved", "ok": True},
            {"ok": True},
        ],
        search="?open=thread:0123abcd&approval=1",
        unpaired=True,
    )
    assert record["openTargetBeforeCheck"] is None
    assert record["body"] == "Approve this device in the bot | 482 913"
    assert record["fetches"] == [
        {"url": "/api/telegram/launch-pair", "body": {"initData": INIT_DATA}},
        {
            "url": "/api/telegram/launch-pair?pending=req-99",
            "headers": {"X-Telegram-Init-Data": INIT_DATA},
        },
        {
            "url": "/api/telegram/launch-pair?pending=req-99",
            "headers": {"X-Telegram-Init-Data": INIT_DATA},
        },
        {"url": "/api/telegram/check", "body": {"initData": INIT_DATA}},
    ]
    assert record["sockets"] == 1
    assert record["openTarget"] == {
        "kind": "thread",
        "threadId": "0123abcd",
        "approval": True,
    }
    assert record["replaced"] == "/"


def test_deep_links_are_read_and_stripped_only_after_launch_check_and_validated() -> None:
    for search, expected in [
        ("?open=repos", {"kind": "repos", "approval": False}),
        (
            "?open=repo:acme/widgets&approval=1",
            {"kind": "repo", "repo": "acme/widgets", "approval": True},
        ),
        (
            "?open=thread:01a0f1ae-2da4-7942-99ae-c3151dc137cc",
            {
                "kind": "thread",
                "threadId": "01a0f1ae-2da4-7942-99ae-c3151dc137cc",
                "approval": False,
            },
        ),
        ("?open=repo:../etc/passwd", None),
        ("?open=thread:NOT_HEX!", None),
        ("?open=unknown:target", None),
    ]:
        record = run(LAUNCH, {"ok": True}, search=search)
        assert record["openTargetBeforeCheck"] is None
        assert record["openTarget"] == expected
        assert record["replaced"] == "/"


def test_locked_page_reload_preserves_deep_link_query_for_reloaded_app_page() -> None:
    record = run(
        LAUNCH,
        [
            {"pending": "req-99", "code": "482 913"},
            {"status": "approved", "ok": True},
            {"ok": True},
        ],
        search="?open=thread:0123abcd&approval=1",
        unpaired=True,
        reloadable=True,
    )
    assert record["reloads"] == 1
    assert record["openTarget"] is None
    assert record["finalSearch"] == "?open=thread:0123abcd&approval=1"
    assert record["replacedHistory"] == ["/?open=thread:0123abcd&approval=1"]


def test_tg_web_app_data_hash_is_stripped_even_when_session_storage_throws() -> None:
    record = run(
        LAUNCH,
        {"ok": True},
        search="?open=repos",
        throw_on_storage=True,
    )
    assert record["replacedHistory"] == ["/?open=repos", "/"]
    assert record["openTarget"] == {"kind": "repos", "approval": False}


def test_safe_area_events_set_css_custom_properties_and_ignore_bad_values() -> None:
    record = run(
        LAUNCH,
        {"ok": True},
        receive_events=[
            {
                "type": "safe_area_changed",
                "data": {"top": 44, "right": 0, "bottom": 34, "left": 12},
            },
            {
                "type": "content_safe_area_changed",
                "data": {"top": 56, "right": 0, "bottom": 8, "left": 0},
            },
            {
                "type": "safe_area_changed",
                "data": {
                    "top": -5,
                    "right": "16px",
                    "bottom": None,
                    "left": {"bad": 1},
                },
            },
            {
                "type": "content_safe_area_changed",
                "data": "not-an-object",
            },
        ],
        receive_non_finite_safe_area=True,
    )
    assert record["cssVars"] == {
        "--tg-safe-top": "44px",
        "--tg-safe-right": "0px",
        "--tg-safe-bottom": "34px",
        "--tg-safe-left": "12px",
        "--tg-content-safe-top": "56px",
        "--tg-content-safe-right": "0px",
        "--tg-content-safe-bottom": "8px",
        "--tg-content-safe-left": "0px",
    }


def test_back_button_pressed_reaches_page_and_page_message_posts_setup() -> None:
    record = run(
        LAUNCH,
        {"ok": True},
        receive_events=[
            {"type": "back_button_pressed", "data": {}},
            {"type": "back_button_pressed", "data": None},
        ],
        view_messages=[
            {"type": "telegram/back-button", "visible": True},
            {"type": "telegram/back-button", "visible": False},
        ],
    )
    assert record["telegramBack"] == 2
    assert record["eventPayloads"] == [
        {"type": "web_app_ready", "data": {}},
        {"type": "web_app_expand", "data": {}},
        {"type": "web_app_request_safe_area", "data": {}},
        {"type": "web_app_request_content_safe_area", "data": {}},
        {"type": "web_app_setup_back_button", "data": {"is_visible": True}},
        {"type": "web_app_setup_back_button", "data": {"is_visible": False}},
    ]


def test_ordinary_browser_tab_posts_no_safe_area_or_back_button_events() -> None:
    record = run(
        "",
        {"ok": False},
        view_messages=[
            {"type": "telegram/back-button", "visible": True},
            {"type": "telegram/back-button", "visible": False},
        ],
    )
    assert record["events"] == []
    assert record["eventPayloads"] == []
    assert record["cssVars"] == {}
    assert record["telegramBack"] == 0
    assert record["sockets"] == 1

