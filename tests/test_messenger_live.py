"""`scripts/messenger_live/`: the live messenger harness's own logic, with no messenger and no network.

The harness drives real Telegram (TDLib) and Signal (signal-cli) accounts; here only its pure parts
run: the privacy filters (one Telegram chat, Note to Self from the bridge's device), the checks'
judgements on TDLib and signal-cli shapes, the rollout reader, the reports and the units.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "scripts" / "messenger_live"
sys.path.insert(0, str(SUITE))

from mlive import (  # noqa: E402
    bridge,
    checks,
    report,
    sessions,
    signal_device,
    tdlib,
    telegram_account,
    telegram_suite,
    voice,
)

NUMBER = "+15550000000"


def _envelope(sent: dict, number: str = NUMBER, device: int = 3) -> dict:
    return {
        "source": number,
        "sourceNumber": number,
        "sourceDevice": device,
        "timestamp": 1,
        "syncMessage": {"sentMessage": sent},
    }


# Privacy filters --------------------------------------------------------------------------------


def test_the_tdlib_client_keeps_only_the_allowed_chat_and_its_own_session():
    client = tdlib.TdClient.__new__(tdlib.TdClient)
    client.allowed_chat = None
    auth = {
        "@type": "updateAuthorizationState",
        "authorization_state": {"@type": "authorizationStateReady"},
    }
    other = {"@type": "updateNewMessage", "message": {"chat_id": 99, "content": {}}}
    user = {"@type": "updateUser", "user": {"id": 5, "first_name": "someone"}}
    assert client.keeps(auth)
    assert not client.keeps(other)
    assert not client.keeps(user)
    client.allowed_chat = 42
    assert client.keeps({"@type": "updateNewMessage", "message": {"chat_id": 42}})
    assert client.keeps(
        {"@type": "updateForumTopicInfo", "info": {"chat_id": 42, "forum_topic_id": 7}}
    )
    assert client.keeps({"@type": "updatePendingMessage", "chat_id": 42, "forum_topic_id": 7})
    assert not client.keeps(other)
    assert not client.keeps(user)
    assert not client.keeps({"@type": "updateChatLastMessage", "chat_id": 99})


def test_note_to_self_keeps_only_the_accounts_own_notes():
    kept = signal_device.note_to_self(
        _envelope(
            {
                "destinationNumber": NUMBER,
                "timestamp": 7,
                "message": "hi",
                "textStyles": [{"start": 0, "length": 2, "style": "BOLD"}],
            }
        ),
        NUMBER,
    )
    assert kept == {
        "timestamp": 7,
        "text": "hi",
        "edit_of": None,
        "styles": [{"start": 0, "length": 2, "style": "BOLD"}],
        "quote": None,
        "device": 3,
        "attachments": 0,
    }
    # A message the owner sent to someone else, one received from someone else, a plain data message.
    assert (
        signal_device.note_to_self(
            _envelope({"destinationNumber": "+15551111111", "message": "private"}), NUMBER
        )
        is None
    )
    assert (
        signal_device.note_to_self(
            _envelope({"destinationNumber": NUMBER, "message": "x"}, number="+15552222222"), NUMBER
        )
        is None
    )
    assert (
        signal_device.note_to_self(
            {"sourceNumber": NUMBER, "dataMessage": {"message": "x"}}, NUMBER
        )
        is None
    )
    assert (
        signal_device.note_to_self({"sourceNumber": NUMBER, "receiptMessage": {}}, NUMBER) is None
    )


def test_note_to_self_reads_an_edit_as_its_targets_new_text():
    sent = {
        "destinationNumber": NUMBER,
        "timestamp": 9,
        "editMessage": {"targetSentTimestamp": 7, "dataMessage": {"message": "longer"}},
    }
    kept = signal_device.note_to_self(_envelope(sent), NUMBER)
    assert kept is not None and kept["edit_of"] == 7 and kept["text"] == "longer"


def test_the_signal_reader_drops_notes_from_other_devices(tmp_path):
    from mlive import signal_suite

    rpc = signal_suite.SignalRpc.__new__(signal_suite.SignalRpc)
    rpc.number, rpc.debug, rpc.bridge_device = NUMBER, None, 4
    rpc.lock = __import__("threading").Lock()
    rpc.notes, rpc.order = {}, []
    lines = [
        {
            "method": "receive",
            "params": {
                "envelope": _envelope(
                    {"destinationNumber": NUMBER, "timestamp": 1, "message": "owner's own note"},
                    device=1,
                )
            },
        },
        {
            "method": "receive",
            "params": {
                "envelope": _envelope(
                    {"destinationNumber": NUMBER, "timestamp": 2, "message": "🐦 Working on it…"},
                    device=4,
                )
            },
        },
        {
            "method": "receive",
            "params": {
                "envelope": _envelope(
                    {
                        "destinationNumber": NUMBER,
                        "timestamp": 3,
                        "editMessage": {
                            "targetSentTimestamp": 2,
                            "dataMessage": {"message": "Paris"},
                        },
                    },
                    device=4,
                )
            },
        },
    ]

    class Out:
        stdout = iter(json.dumps(line) + "\n" for line in lines)

    rpc.process = Out()
    rpc.answers = {}
    rpc._read()
    assert [note.text for note in rpc.notes.values()] == ["Paris"]
    assert len(rpc.notes[2].edits) == 1
    assert rpc.notes[2].answered_at is not None


# Checks -------------------------------------------------------------------------------------------


def _rich(blocks: list) -> dict:
    return {
        "@type": "messageRichMessage",
        "message": {"@type": "richMessage", "blocks": blocks, "is_rtl": False, "is_full": True},
    }


def _plain(text: str) -> dict:
    return {"@type": "richTextPlain", "text": text}


def test_rich_features_find_a_table_a_nested_list_code_and_a_link():
    nested = {
        "@type": "pageBlockList",
        "items": [
            {
                "@type": "pageBlockListItem",
                "label": "•",
                "blocks": [{"@type": "pageBlockParagraph", "text": _plain("inner")}],
            }
        ],
    }
    content = _rich(
        [
            {
                "@type": "pageBlockTable",
                "cells": [[{"@type": "pageBlockTableCell", "text": _plain("ext4")}]],
            },
            {
                "@type": "pageBlockList",
                "items": [
                    {
                        "@type": "pageBlockListItem",
                        "label": "•",
                        "blocks": [
                            {"@type": "pageBlockParagraph", "text": _plain("outer")},
                            nested,
                        ],
                    }
                ],
            },
            {"@type": "pageBlockPreformatted", "text": _plain("fn main() {}"), "language": "rust"},
            {
                "@type": "pageBlockParagraph",
                "text": {
                    "@type": "richTextUrl",
                    "text": _plain("docs"),
                    "url": "https://kernel.org/doc/",
                },
            },
        ]
    )
    features = checks.rich_features(content)
    assert features["rich"] and features["table"] and features["nested_list"]
    assert features["code"] == ["rust"] and features["links"] == 1
    assert "outer" in checks.plain_text(content)
    flat = checks.rich_features(
        _rich(
            [
                {
                    "@type": "pageBlockList",
                    "items": [
                        {
                            "@type": "pageBlockListItem",
                            "blocks": [{"@type": "pageBlockParagraph", "text": _plain("x")}],
                        }
                    ],
                }
            ]
        )
    )
    assert flat["list"] and not flat["nested_list"]


def test_a_javascript_link_or_an_inline_button_is_caught():
    text = {
        "@type": "messageText",
        "text": {
            "text": "link",
            "entities": [
                {
                    "offset": 0,
                    "length": 4,
                    "type": {"@type": "textEntityTypeTextUrl", "url": "javascript:alert(1)"},
                }
            ],
        },
    }
    assert checks.dangerous_links(text) == ["javascript:alert(1)"]
    safe = {"@type": "messageText", "text": {"text": "[link](javascript:alert(1))", "entities": []}}
    assert checks.dangerous_links(safe) == []
    assert (
        checks.dangerous_links(
            _rich(
                [
                    {
                        "@type": "pageBlockParagraph",
                        "text": {
                            "@type": "richTextUrl",
                            "text": _plain("x"),
                            "url": "https://example.org",
                        },
                    }
                ]
            )
        )
        == []
    )
    keyboard = {
        "reply_markup": {
            "@type": "replyMarkupInlineKeyboard",
            "rows": [
                [
                    {
                        "text": "Approve",
                        "type": {"@type": "inlineKeyboardButtonTypeCallback", "data": "YToxOjA="},
                    }
                ]
            ],
        }
    }
    assert len(checks.inline_buttons(keyboard)) == 1
    assert checks.inline_buttons({"reply_markup": None}) == []


@pytest.mark.parametrize(
    ("answer", "asks"),
    [
        ("Did you mean Unix or the University of Nottingham?", True),
        ("Which one?", True),
        ("Unix began at Bell Labs in 1969, when Ken Thompson and Dennis Ritchie…", False),
        ("Unix began in 1969. " * 40 + "Want more?", False),
    ],
)
def test_a_clarifying_question_is_told_from_an_answer(answer, asks):
    assert checks.clarifying_question(answer) is asks


def test_attachments_styles_markdown_and_commands():
    assert checks.mentions_attachment("I can't see an image or attachment in your message")
    assert not checks.mentions_attachment("Unix history")
    assert checks.mentions_attachment("The attached file is empty")
    assert not checks.mentions_attachment("Each brand attached the word to its products")
    assert checks.signal_styles([{"start": 0, "length": 3, "style": "BOLD"}, "4:2:MONOSPACE"]) == {
        "BOLD",
        "MONOSPACE",
    }
    assert checks.leftover_markdown("**bold** and\n|---|---|\n```rust") == [
        "**",
        "```",
        "table rule",
    ]
    assert checks.leftover_markdown("plain text") == []
    assert checks.uses_curl(["curl -s https://kernel.org"]) and checks.uses_curl(
        ["cd /tmp && wget x"]
    )
    assert not checks.uses_curl(["ling-search 'kernel curling'"])
    assert checks.uses_ling_search(["ling-search --read 'linux kernel'"])
    assert checks.stopped("partial\n\n(stopped)") and not checks.stopped("done")
    assert (
        checks.strip_mark("🐦 Working on it…") == "Working on it…"
        and checks.strip_mark("Paris") == "Paris"
    )
    assert checks.median([3.0, 1.0, 2.0]) == 2.0 and checks.median([]) is None


def test_the_voice_fixture_is_judged_by_its_words():
    assert voice.heard_ok("Heard: What is the capital of France?")
    assert not voice.heard_ok("Heard: what is the capital")


# Telegram tracker -----------------------------------------------------------------------------------


class _Client:
    def __init__(self):
        self.listeners = []
        self.updates = []


def test_the_tracker_follows_topics_drafts_edits_and_the_bots_messages():
    tracker = telegram_suite.Tracker(_Client(), 42, bot_id=7)
    topic = {"@type": "messageTopicForum", "forum_topic_id": 11}
    tracker.on_update(
        {
            "@type": "updateNewMessage",
            "message": {
                "id": 1,
                "chat_id": 42,
                "sender_id": {"user_id": 5},
                "topic_id": topic,
                "content": {"@type": "messageText", "text": {"text": "q", "entities": []}},
            },
        }
    )
    tracker.on_update(
        {
            "@type": "updatePendingMessage",
            "chat_id": 42,
            "forum_topic_id": 11,
            "draft_id": 3,
            "can_stop": True,
            "content": {"@type": "messageText", "text": {"text": "par", "entities": []}},
        }
    )
    tracker.on_update(
        {
            "@type": "updateNewMessage",
            "message": {
                "id": 2,
                "chat_id": 42,
                "sender_id": {"user_id": 7},
                "topic_id": topic,
                "content": {"@type": "messageText", "text": {"text": "Paris", "entities": []}},
            },
        }
    )
    tracker.on_update(
        {"@type": "updateMessageEdited", "chat_id": 42, "message_id": 2, "reply_markup": None}
    )
    tracker.on_update(
        {
            "@type": "updateForumTopicInfo",
            "info": {"chat_id": 42, "forum_topic_id": 11, "name": "Capital of France"},
        }
    )
    tracker.on_update(
        {
            "@type": "updateMessageSendSucceeded",
            "old_message_id": 1,
            "message": {"id": 100, "chat_id": 42, "topic_id": topic},
        }
    )
    assert [m.text for m in tracker.bot_messages(11, 0)] == ["Paris"]
    assert tracker.messages[100].text == "q" and 1 not in tracker.messages
    assert tracker.topics[11][-1][1] == "Capital of France"
    assert tracker.first_activity(11, 0) is not None
    assert tracker.messages[2].edits


def test_topics_and_the_gate():
    assert (
        telegram_suite.topic_of({"topic_id": {"@type": "messageTopicForum", "forum_topic_id": 5}})
        == 5
    )
    assert telegram_suite.topic_of({"topic_id": None}) == 0
    assert telegram_suite.in_topic(0) is None
    assert telegram_suite.gate_busy(["The model is busy with a SWE-bench run for 2 h"])
    assert all(
        prompt.startswith(telegram_suite.MARK)
        for prompt in (
            telegram_suite.RICH_PROMPT,
            telegram_suite.LONG_PROMPT,
            telegram_suite.TYPO_PROMPT,
            telegram_suite.HOSTILE_PROMPT,
        )
    )


# Sessions -------------------------------------------------------------------------------------------


def _rollout(path: Path, first: str, calls: list[str], aborted: bool) -> None:
    records = [
        {"type": "session_meta", "payload": {}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "<environment_context>x</environment_context>"}
                ],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": first}],
            },
        },
    ]
    for command in calls:
        records.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": command}),
                },
            }
        )
    if aborted:
        records.append({"type": "event_msg", "payload": {"type": "turn_aborted"}})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_only_the_tests_own_threads_are_read(tmp_path):
    _rollout(
        tmp_path / "2026/10/27/rollout-a.jsonl",
        "[ling test] What is new in Linux?",
        ["ling-search --read 'linux'"],
        aborted=True,
    )
    _rollout(
        tmp_path / "2026/10/27/rollout-b.jsonl",
        "the owner's own question",
        ["curl https://example.org"],
        aborted=False,
    )
    found = sessions.recent_sessions(tmp_path, 0, "[ling test]")
    assert len(found) == 1
    assert found[0].commands == ["ling-search --read 'linux'"] and found[0].aborted == 1
    assert (
        sessions.read_session(
            (tmp_path / "2026/10/27/rollout-b.jsonl").read_text().splitlines(),
            "[ling test]",
            Path(),
        )
        is None
    )


# Reports, units, setup ------------------------------------------------------------------------------


def test_a_model_finding_never_alerts_but_a_bridge_failure_does(tmp_path):
    run = report.Run(started=0)
    run.add(report.Result("telegram", "typo", "model", "fail", 3.0, 1.0, "asked back"))
    assert run.exit_code() == 0 and not run.alerts()
    run.blocked["signal"] = "owner re-link needed"
    assert run.exit_code() == 3
    run.add(
        report.Result("telegram", "voice note", "bridge", "fail", 5.0, None, "no Heard: | line")
    )
    assert run.exit_code() == 1
    json_path, md_path = report.write(run, tmp_path)
    markdown = md_path.read_text()
    assert (
        "FAIL" in markdown
        and "owner re-link needed" in markdown
        and "no Heard: \\| line" in markdown
    )
    assert json.loads(json_path.read_text())["exit_code"] == 1
    assert (tmp_path / "latest.md").resolve() == md_path.resolve()
    with pytest.raises(ValueError):
        report.Result("telegram", "x", "loud", "pass")


def test_the_nightly_units_run_the_checkout_at_six_utc():
    sys.path.insert(0, str(SUITE))
    import run as runner

    units = runner.unit_files(Path("/srv/checkout/scripts/messenger_live/run.sh"))
    assert (
        "ExecStart=/srv/checkout/scripts/messenger_live/run.sh --notify"
        in units["mightling-messenger-live.service"]
    )
    assert "OnCalendar=*-*-* 18:00:00 UTC" in units["mightling-messenger-live.timer"]
    assert (
        "ExecStart=/usr/bin/python3 -I /srv/checkout/scripts/messenger_live/run.py after-merge"
        in (units["mightling-messenger-live-after-merge.service"])
    )
    assert "OnCalendar=*:0/15" in units["mightling-messenger-live-after-merge.timer"]


def test_a_known_failure_is_reported_but_never_alerts(tmp_path, monkeypatch):
    monkeypatch.setitem(report.KNOWN_FAILING, "long answer streams", "known failing, model server")
    run = report.Run(started=0)
    known = run.add(
        report.Result("telegram", "long answer streams", "bridge", "fail", 9.0, 2.0, "grew 0 times")
    )
    assert known.severity == "known" and "model server" in known.detail
    assert run.exit_code() == 0 and not run.alerts() and run.known() == [known]
    markdown = report.to_markdown(run)
    assert "PASS" in markdown and "1 known failures" in markdown
    assert "Known failing (telegram): **long answer streams**" in markdown
    run.add(report.Result("signal", "long answer streams", "bridge", "pass", 9.0, 2.0))
    assert "remove it from the list" in report.to_markdown(run)


def test_the_release_marker_is_set_by_a_bridge_failure_and_cleared_by_a_green_run(
    tmp_path, monkeypatch
):
    from mlive import gate

    monkeypatch.setattr(gate, "state_dir", lambda: tmp_path / "state")
    md = tmp_path / "r.md"
    assert gate.update_marker(3, md, [], {}) == "kept" and not gate.marker_path().exists()
    assert gate.update_marker(1, md, ["signal: /new"], {"build-key": "k"}) == "set"
    text = gate.marker_path().read_text()
    assert f"report: {md}" in text and "signal: /new" in text and "build-key: k" in text
    assert gate.update_marker(3, md, [], {}) == "kept" and gate.marker_path().exists()
    assert gate.update_marker(0, md, [], {}) == "cleared" and not gate.marker_path().exists()


def test_the_after_merge_check_waits_for_the_installed_build():
    from mlive import gate

    history = {("c2", "h2"), ("c1", "h2"), ("c1", "h1")}

    def ancestor(a: str, b: str) -> bool:
        return (a, b) in history

    assert gate.decide({}, [], "k", ancestor)[0] == "init"
    assert gate.decide({"last_tested": "t"}, [], "k", ancestor)[0] == "idle"
    state = {"last_tested": "t", "installed": {"key": "k", "commit": "h1"}}
    assert gate.decide(state, ["c2", "c1"], "k", ancestor)[0] == "wait"
    assert gate.decide(state, ["c2", "c1"], "other", ancestor)[0] == "wait"
    state["installed"] = {"key": "k", "commit": "h2"}
    assert gate.decide(state, ["c2", "c1"], "k", ancestor)[0] == "run"
    assert gate.decide(state, ["c2", "c1"], None, ancestor)[0] == "wait"


def test_the_scratch_bridge_is_paired_and_kept_apart(tmp_path):
    env = bridge.scratch_env(tmp_path / "home")
    assert env["HOME"] == str(tmp_path / "home") and env["CODEX_HOME"] == str(
        tmp_path / "home" / ".mightling"
    )
    assert env["XDG_CONFIG_HOME"].startswith(str(tmp_path))
    config = json.loads(bridge.telegram_config("123:abc", 55, 1000.0))
    assert config == {"token": "123:abc", "consented": 1000, "users": [55]}


def test_tdlib_is_pinned_and_signed_in_without_databases():
    assert (
        tdlib.library_member(["tdjson/__init__.py", "tdjson.libs/libtdjson-3e2110f5.so.1.8.69"])
        == "tdjson.libs/libtdjson-3e2110f5.so.1.8.69"
    )
    assert tdlib.library_member(["tdjson/tdjson_ext.so"]) is None
    assert all(len(sha) == 64 for _, sha in tdlib.WHEELS.values())
    params = telegram_account.tdlib_parameters(Path("/x"), {"api_id": "1", "api_hash": "h"})
    assert (
        params["use_message_database"] is False
        and params["use_file_database"] is False
        and params["use_test_dc"] is False
    )
    assert params["device_model"] == "ling test"
    assert all(
        request["settings"]["is_auto_download_enabled"] is False
        for request in telegram_account.no_auto_download()
    )
    assert signal_device.associated_number("Associated with: +15550000000") == NUMBER
    assert signal_device.associated_number("sgnl://linkdevice?uuid=x") is None


def test_streaming_and_search_order_are_judged():
    assert checks.growth([9, 9, 9, 8657]) == [8657]
    assert checks.growth([9, 120, 480, 900, 900]) == [120, 480, 900]
    assert checks.searched_first(
        ["ling-search 'kernel'", "curl -s https://kernel.org/releases.json"]
    )
    assert not checks.searched_first(["curl -s https://kernel.org", "ling-search x"])
    assert not checks.searched_first([])


def test_stop_is_judged_by_what_the_answer_had_written():
    assert checks.stop_kept(["partial\n\n(stopped)"], True)[0]
    assert not checks.stop_kept(["\N{BIRD} Stopped."], True)[0]
    assert checks.stop_kept(["\N{BIRD} Stopped."], False) == (
        True,
        "nothing written before Stop: the Stopped. notice",
    )
    assert checks.stop_kept(["Stopped."], False)[0]
    assert not checks.stop_kept(["Working on it…"], False)[0]
