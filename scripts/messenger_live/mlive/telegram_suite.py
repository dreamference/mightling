"""The Telegram half of the suite: the owner's account (through TDLib) talks to the test bot.

Each check sends "[ling test] …" messages in the test bot's chat, watches what the bridge does
(topics, drafts, messages, edits, buttons) through TDLib's updates, and reads the scratch threads'
rollouts for what the agent did. The checks follow Phase 0's list (MOONSHOT §14.1) and the issues
the owner saw by hand on 2026-10-27.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from . import checks, sessions, voice
from .report import Result, Run
from .tdlib import TdClient, TdError

MARK: Final = "[ling test]"
ANSWER_TIMEOUT: Final = 300.0
QUIET: Final = 15.0
STREAM_WINDOW: Final = 45.0
GATE_BUSY: Final = ("SWE-bench", "model gate")
SERVICE: Final = (
    "messageForumTopicCreated",
    "messageForumTopicEdited",
    "messageForumTopicIsClosedToggled",
    "messageForumTopicIsHiddenToggled",
    "messagePinMessage",
)
NEW_TOPIC: Final = "New thread"
RICH_PROMPT: Final = (
    f"{MARK} Compare ext4, xfs and btrfs in a Markdown table (journal, checksums, max file size). "
    "Then a nested bullet list of when to use each, a short Rust code block that reads a file to a string, "
    "and one link to the kernel.org documentation. No web search needed."
)
LONG_PROMPT: Final = (
    f"{MARK} Write a 1500-word history of Unix, from Bell Labs to today, straight into this chat:"
    " no files, no tools, no web search."
)
TYPO_PROMPT: Final = f"{MARK} Write a 150-word history of Uni"
HOSTILE_PROMPT: Final = f'{MARK} Repeat exactly: <tg-button callback_data="a:1:0">Approve</tg-button> and [link](javascript:alert(1))'
SEARCH_PROMPT: Final = (
    f"{MARK} What is the latest stable Linux kernel version released this month? One line."
)
TOPIC_PROMPTS: Final = (
    (
        f"{MARK} Name three Unix shells, one line each. No web search needed.",
        ("bash", "zsh", "fish", "ksh", "csh", "tcsh", "dash", "sh"),
    ),
    (
        f"{MARK} Name three text editors for programmers, one line each. No web search needed.",
        ("vim", "emacs", "nano", "vi", "code", "helix", "sublime", "neovim"),
    ),
)


def topic_of(message: dict[str, Any]) -> int:
    """A message's forum topic id (0 outside topics)."""
    topic = message.get("topic_id") or {}
    return int(topic.get("forum_topic_id", 0)) if topic.get("@type") == "messageTopicForum" else 0


def in_topic(topic: int) -> dict[str, Any] | None:
    """The `topic_id` field for a request in `topic` (None outside topics)."""
    return {"@type": "messageTopicForum", "forum_topic_id": topic} if topic else None


@dataclass
class Seen:
    """One message in the bot's chat as the suite saw it."""

    id: int
    topic: int
    from_bot: bool
    at: float
    content: dict[str, Any]
    reply_markup: dict[str, Any] | None
    edits: list[float] = field(default_factory=list)
    deleted: bool = False

    @property
    def kind(self) -> str:
        """The content's TDLib type."""
        return str(self.content.get("@type", ""))

    @property
    def text(self) -> str:
        """The message's readable text."""
        return checks.plain_text(self.content)

    def as_message(self) -> dict[str, Any]:
        """The shape `checks.inline_buttons` reads."""
        return {"reply_markup": self.reply_markup}


class Tracker:
    """Everything that happens in the bot's chat, timed: messages, edits, drafts, actions, topics."""

    def __init__(self, client: TdClient, chat_id: int, bot_id: int) -> None:
        """Starts listening to the client's (already filtered) updates."""
        self.client = client
        self.chat_id = chat_id
        self.bot_id = bot_id
        self.messages: dict[int, Seen] = {}
        self.events: list[tuple[float, str, int, dict[str, Any]]] = []
        self.topics: dict[int, list[tuple[float, str]]] = {}
        client.listeners.append(self.on_update)

    def on_update(self, update: dict[str, Any]) -> None:
        """Records one update about the bot's chat."""
        now = time.monotonic()
        kind = update.get("@type")
        if kind == "updateNewMessage":
            m = update["message"]
            sender = m.get("sender_id", {}).get("user_id")
            content = m.get("content", {})
            if content.get("@type") in (
                "messageForumTopicCreated",
                "messageForumTopicEdited",
            ) and content.get("name"):
                self.topics.setdefault(topic_of(m), []).append((now, content["name"]))
            seen = Seen(
                m["id"],
                topic_of(m),
                sender == self.bot_id,
                now,
                m.get("content", {}),
                m.get("reply_markup"),
            )
            self.messages[m["id"]] = seen
            self.events.append((now, "message", seen.topic, {"id": m["id"], "bot": seen.from_bot}))
        elif kind == "updateMessageSendSucceeded":
            old = self.messages.pop(update["old_message_id"], None)
            m = update["message"]
            if old is not None:
                old.id = m["id"]
                old.topic = topic_of(m) or old.topic
                self.messages[m["id"]] = old
        elif kind == "updateMessageContent":
            seen = self.messages.get(update["message_id"])
            if seen is not None:
                seen.content = update["new_content"]
                seen.edits.append(now)
                self.events.append((now, "edit", seen.topic, {"id": seen.id}))
        elif kind == "updateMessageEdited":
            seen = self.messages.get(update["message_id"])
            if seen is not None:
                seen.reply_markup = update.get("reply_markup")
                seen.edits.append(now)
                self.events.append((now, "edit", seen.topic, {"id": seen.id}))
        elif kind == "updateDeleteMessages" and update.get("is_permanent"):
            for message_id in update.get("message_ids", []):
                if message_id in self.messages:
                    self.messages[message_id].deleted = True
        elif kind == "updatePendingMessage":
            content = update.get("content", {})
            self.events.append(
                (
                    now,
                    "pending",
                    int(update.get("forum_topic_id", 0)),
                    {
                        "draft": update.get("draft_id"),
                        "can_stop": update.get("can_stop"),
                        "kind": content.get("@type"),
                        "length": len(checks.plain_text(content)),
                    },
                )
            )
        elif kind == "updateStopMessageDraft":
            self.events.append(
                (
                    now,
                    "stopped",
                    int(update.get("forum_topic_id", 0)),
                    {"draft": update.get("draft_id")},
                )
            )
        elif kind == "updateChatAction":
            topic = update.get("topic_id") or {}
            self.events.append(
                (
                    now,
                    "action",
                    int(topic.get("forum_topic_id", 0)),
                    {"action": update.get("action", {}).get("@type")},
                )
            )
        elif kind == "updateForumTopicInfo":
            info = update["info"]
            self.topics.setdefault(info["forum_topic_id"], []).append((now, info.get("name", "")))
            self.events.append(
                (now, "topic", info["forum_topic_id"], {"name": info.get("name", "")})
            )

    def pump(self, seconds: float) -> None:
        """Lets TDLib deliver updates for `seconds`."""
        deadline = time.monotonic() + seconds
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            self.client.pump(min(left, 0.5))
            self.client.updates.clear()

    def bot_messages(self, topic: int | None, after: float) -> list[Seen]:
        """The bot's messages since `after` (in `topic`, or anywhere when None), service messages left out."""
        found = [
            m
            for m in self.messages.values()
            if m.from_bot and m.at >= after and not m.deleted and m.kind not in SERVICE
        ]
        return sorted([m for m in found if topic is None or m.topic == topic], key=lambda m: m.at)

    def first_activity(self, topic: int, after: float) -> float | None:
        """The first sign of the bot in `topic` since `after`: a draft, a typing action or a message."""
        times = [
            t
            for t, kind, where, data in self.events
            if t >= after
            and where == topic
            and (kind in ("pending", "action") or (kind == "message" and data.get("bot")))
        ]
        return min(times) - after if times else None

    def last_event(self, topic: int, after: float) -> float:
        """When anything last happened in `topic` since `after` (`after` itself when nothing did)."""
        times = [t for t, _, where, _ in self.events if t >= after and where == topic]
        return max(times, default=after)

    def wait(self, predicate: Any, timeout: float) -> bool:
        """Pumps until `predicate()` holds or `timeout` passes."""
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                return False
            self.pump(0.5)
        return True

    def wait_answer(
        self, topic: int, after: float, timeout: float = ANSWER_TIMEOUT, quiet: float = QUIET
    ) -> list[Seen]:
        """Waits for the bot's answer in `topic`: a message, then `quiet` seconds without drafts or edits."""

        def done() -> bool:
            answered = any(
                m.kind != "messageText" or not m.text.startswith("Heard:")
                for m in self.bot_messages(topic, after)
            )
            return answered and time.monotonic() - self.last_event(topic, after) > quiet

        self.wait(done, timeout)
        return self.bot_messages(topic, after)


class Driver:
    """Sends as the owner's account into the bot's chat only."""

    def __init__(self, client: TdClient, chat_id: int, tracker: Tracker) -> None:
        """Keeps the client, the one chat it may send to, and the tracker."""
        self.client = client
        self.chat_id = chat_id
        self.tracker = tracker

    def send_text(self, text: str, topic: int = 0) -> float:
        """Sends a text into the bot's chat (in `topic` when non-zero); returns when it was sent."""
        content = {
            "@type": "inputMessageText",
            "text": {"@type": "formattedText", "text": text, "entities": []},
        }
        request: dict[str, Any] = {
            "@type": "sendMessage",
            "chat_id": self.chat_id,
            "input_message_content": content,
        }
        if topic:
            request["topic_id"] = in_topic(topic)
        at = time.monotonic()
        self.client.call(request)
        return at

    def send_voice(self, path: Path, duration: int, topic: int) -> float:
        """Sends the voice-note fixture into `topic`; returns when it was sent."""
        content = {
            "@type": "inputMessageVoiceNote",
            "voice_note": {
                "@type": "inputVoiceNote",
                "voice_note": {"@type": "inputFileLocal", "path": str(path)},
                "duration": duration,
                "waveform": "",
            },
            "caption": {"@type": "formattedText", "text": "", "entities": []},
        }
        at = time.monotonic()
        self.client.call(
            {
                "@type": "sendMessage",
                "chat_id": self.chat_id,
                "topic_id": in_topic(topic),
                "input_message_content": content,
            }
        )
        return at

    def press(self, message: Seen, label: str) -> str:
        """Presses the inline button labelled `label` on a bot message; returns the bot's answer text."""
        for button in checks.inline_buttons(message.as_message()):
            if (
                button.get("text") == label
                and button.get("type", {}).get("@type") == "inlineKeyboardButtonTypeCallback"
            ):
                payload = {"@type": "callbackQueryPayloadData", "data": button["type"]["data"]}
                try:
                    answer = self.client.call(
                        {
                            "@type": "getCallbackQueryAnswer",
                            "chat_id": self.chat_id,
                            "message_id": message.id,
                            "payload": payload,
                        },
                        timeout=40,
                    )
                except TdError as err:
                    # A bot that answers the query late or not at all is not the press failing.
                    return f"(no callback answer: {err.message})"
                return str(answer.get("text", ""))
        raise LookupError(f"no button {label!r}")

    def stop(self, topic: int, draft: int) -> None:
        """Presses Stop on the bot's draft in `topic` (`stopPendingMessage`)."""
        self.client.call(
            {
                "@type": "stopPendingMessage",
                "chat_id": self.chat_id,
                "topic_id": in_topic(topic),
                "draft_id": draft,
            }
        )

    def new_topic(self, timeout: float = 60.0) -> tuple[int | None, float]:
        """Sends `/new` outside every topic; returns the topic the bot made and how long it took."""
        known = set(self.tracker.topics) | {m.topic for m in self.tracker.messages.values()}
        at = self.send_text("/new")

        def made() -> int | None:
            for m in self.tracker.bot_messages(None, at):
                if m.topic and m.topic not in known:
                    return m.topic
            return None

        self.tracker.wait(lambda: made() is not None, timeout)
        return made(), time.monotonic() - at


def gate_busy(texts: list[str]) -> bool:
    """Says whether an answer is the model gate's refusal during a SWE-bench run."""
    return any(any(mark in text for mark in GATE_BUSY) for text in texts)


def answer_text(messages: list[Seen]) -> str:
    """The answer's text: every bot message but "Heard:" lines, joined."""
    return "\n".join(m.text for m in messages if not m.text.startswith("Heard:"))


class TelegramSuite:
    """Runs the checks, in an order that reuses topics and keeps the model busy only one turn at a time."""

    def __init__(
        self,
        client: TdClient,
        chat_id: int,
        bot_id: int,
        sessions_root: Path,
        cache: Path,
        run: Run,
    ) -> None:
        """Wires the tracker and driver to the bot's chat."""
        self.tracker = Tracker(client, chat_id, bot_id)
        self.driver = Driver(client, chat_id, self.tracker)
        self.sessions_root = sessions_root
        self.cache = cache
        self.run = run
        self.started = time.time()

    def result(
        self,
        check: str,
        severity: str,
        ok: bool | None,
        began: float,
        detail: str,
        latency: float | None = None,
        **data: Any,
    ) -> Result:
        """Records one check (`ok` None means "observed")."""
        status = "observed" if ok is None else ("pass" if ok else "fail")
        return self.run.add(
            Result(
                "telegram",
                check,
                severity,
                status,
                round(time.monotonic() - began, 1),
                None if latency is None else round(latency, 1),
                detail,
                data,
            )
        )

    def session_for(self, question: str) -> sessions.Session | None:
        """The scratch thread whose user messages include `question`."""
        for session in sessions.recent_sessions(self.sessions_root, self.started - 5, MARK):
            if any(question in text for text in session.users):
                return session
        return None

    def ask(self, prompt: str, topic: int) -> tuple[list[Seen], float, float | None]:
        """Sends a question into `topic` and waits for the answer: (messages, sent at, first reply)."""
        at = self.driver.send_text(prompt, topic)
        messages = self.tracker.wait_answer(topic, at)
        return messages, at, self.tracker.first_activity(topic, at)

    def check_new_topic(self) -> int | None:
        """`/new` makes a new topic with one line in it."""
        began = time.monotonic()
        topic, seconds = self.driver.new_topic()
        self.result(
            "/new makes a topic",
            "bridge",
            topic is not None,
            began,
            f"topic made in {seconds:.1f} s" if topic else "no new topic within 60 s",
        )
        return topic

    def check_rich(self, topic: int) -> None:
        """The rich answer arrives as one structured message, and the thread is renamed after it."""
        began = time.monotonic()
        names_before = len(self.tracker.topics.get(topic, []))
        messages, _, latency = self.ask(RICH_PROMPT, topic)
        if gate_busy([m.text for m in messages]):
            self.result(
                "rich answer", "bridge", None, began, "skipped: the model gate is held", latency
            )
            return
        answers = [m for m in messages if not m.text.startswith("Heard:")]
        features = [checks.rich_features(m.content) for m in answers]
        rich = [f for f in features if f["rich"]]
        problems = []
        if len(answers) != 1:
            problems.append(f"{len(answers)} messages, expected one")
        if not rich:
            problems.append(
                "not a rich message (" + ", ".join(sorted({m.kind for m in answers})) + ")"
            )
        else:
            f = rich[0]
            if not f["table"]:
                problems.append("no table block")
            if not f["nested_list"]:
                problems.append("no nested list")
            if not any("rust" in lang.lower() for lang in f["code"]):
                problems.append(f"no Rust code block ({f['code']})")
            if not f["links"]:
                problems.append("no link")
        renamed = [
            name
            for _, name in self.tracker.topics.get(topic, [])[names_before:]
            if name != NEW_TOPIC
        ]
        if not renamed:
            problems.append("the topic was not renamed")
        detail = (
            "; ".join(problems) or f"one rich message, {rich[0]['blocks']} blocks; topic renamed"
        )
        self.result(
            "rich answer",
            "bridge",
            not problems,
            began,
            detail,
            latency,
            features=features,
            renamed=bool(renamed),
        )

    def check_long_and_stop(self, topic: int) -> None:
        """A long answer streams as drafts; Stop ends it, the partial is kept, the turn is aborted.

        The drafts are watched for `STREAM_WINDOW` seconds from the first one: streaming means the
        draft's text grew at least three times in that window. Stop is then pressed while the turn
        still runs (`stopPendingMessage` on the draft), whatever the draft shows.
        """
        began = time.monotonic()
        at = self.driver.send_text(LONG_PROMPT, topic)

        def drafts() -> list[tuple[float, str, int, dict[str, Any]]]:
            return [
                e for e in self.tracker.events if e[0] >= at and e[1] == "pending" and e[2] == topic
            ]

        def answered() -> bool:
            return any(
                m.kind != "messageText" or len(m.text) > 200
                for m in self.tracker.bot_messages(topic, at)
                if not m.text.startswith("A new thread")
            )

        self.tracker.wait(lambda: bool(drafts()) or answered(), ANSWER_TIMEOUT)
        latency = self.tracker.first_activity(topic, at)
        if not drafts():
            self.result(
                "long answer streams", "bridge", False, began, "no draft before the answer", latency
            )
            return
        window_end = drafts()[0][0] + STREAM_WINDOW
        self.tracker.wait(answered, max(0.0, window_end - time.monotonic()))
        lengths = checks.growth([e[3]["length"] for e in drafts() if e[0] <= window_end])
        streams = len(lengths) >= 3
        self.result(
            "long answer streams",
            "bridge",
            streams,
            began,
            f"draft text grew {len(lengths)} times in {STREAM_WINDOW:.0f} s (lengths {lengths[:6]})"
            + ("" if streams else "; the answer's words did not stream"),
            latency,
        )
        if answered():
            self.run.add(
                Result(
                    "telegram",
                    "Stop ends the answer",
                    "bridge",
                    "skip",
                    None,
                    None,
                    "the answer ended before Stop could be pressed",
                )
            )
            return
        stop_at = time.monotonic()
        self.driver.stop(topic, int(drafts()[-1][3]["draft"]))
        kept = self.tracker.wait(
            lambda: any(checks.stopped(m.text) for m in self.tracker.bot_messages(topic, stop_at)),
            90,
        )
        self.tracker.pump(QUIET)
        late = [e for e in drafts() if e[0] > stop_at + 10]
        session = self.session_for(LONG_PROMPT)
        problems = []
        if not kept:
            problems.append("no message ending (stopped)")
        if late:
            problems.append(f"{len(late)} drafts after Stop")
        if not (session and session.aborted):
            problems.append(
                "the session shows no aborted turn"
                if session
                else "no session found for the question"
            )
        self.result(
            "Stop ends the answer",
            "bridge",
            not problems,
            stop_at,
            "; ".join(problems) or "partial kept as (stopped), turn aborted",
        )

    def check_two_topics(self, first: int, second: int) -> None:
        """Two topics answer at once without their drafts or answers crossing."""
        began = time.monotonic()
        starts = [
            self.driver.send_text(prompt, topic)
            for (prompt, _), topic in zip(TOPIC_PROMPTS, (first, second), strict=True)
        ]
        answers = [
            self.tracker.wait_answer(topic, at)
            for topic, at in zip((first, second), starts, strict=True)
        ]
        problems = []
        for index, ((_, words), messages) in enumerate(zip(TOPIC_PROMPTS, answers, strict=True)):
            text = answer_text(messages).lower()
            other = TOPIC_PROMPTS[1 - index][1]
            if not any(word in text.split() or word in text for word in words):
                problems.append(f"topic {index + 1} has no answer of its own")
            if sum(word in text.split() for word in other if len(word) > 3) >= 2:
                problems.append(f"topic {index + 1} holds the other topic's answer")
        overlap = self.overlapped(first, second, starts)
        detail = "; ".join(problems) or (
            "both answered in their own topics"
            + (", streaming at the same time" if overlap else ", one after the other")
        )
        self.result("two topics at once", "bridge", not problems, began, detail, overlapped=overlap)

    def overlapped(self, first: int, second: int, starts: list[float]) -> bool:
        """Says whether the two topics' activity overlapped in time."""
        spans = []
        for topic, at in zip((first, second), starts, strict=True):
            times = [
                t
                for t, kind, where, _ in self.tracker.events
                if where == topic and t >= at and kind != "topic"
            ]
            spans.append((min(times), max(times)) if times else (0.0, 0.0))
        return spans[0][0] < spans[1][1] and spans[1][0] < spans[0][1]

    def check_hostile(self, topic: int) -> None:
        """Markup and a javascript: link in the model's text stay text: no button, no such link."""
        began = time.monotonic()
        messages, _, latency = self.ask(HOSTILE_PROMPT, topic)
        buttons = [b for m in messages for b in checks.inline_buttons(m.as_message())]
        bad = [url for m in messages for url in checks.dangerous_links(m.content)]
        ok = bool(messages) and not buttons and not bad
        detail = (
            "no answer"
            if not messages
            else (
                "text only: no button, no javascript: link"
                if ok
                else f"{len(buttons)} buttons, links {bad}"
            )
        )
        self.result("hostile answer stays text", "bridge", ok, began, detail, latency)

    def check_voice(self, topic: int) -> None:
        """A voice note is transcribed ("Heard: …") and answered."""
        began = time.monotonic()
        path, duration = voice.ensure_fixture(self.cache)
        at = self.driver.send_voice(path, duration, topic)
        messages = self.tracker.wait_answer(topic, at)
        heard = [m.text for m in messages if m.text.startswith("Heard:")]
        answer = answer_text(messages).lower()
        problems = []
        if not heard:
            problems.append("no Heard: line")
        elif not voice.heard_ok(heard[0]):
            problems.append(f"heard {heard[0][:80]!r}")
        if voice.EXPECTED_ANSWER not in answer:
            problems.append("the answer does not say Paris")
        self.result(
            "voice note",
            "bridge",
            not problems,
            began,
            "; ".join(problems) or "heard and answered",
            self.tracker.first_activity(topic, at),
        )

    def approval(self, topic: int, after: float) -> Seen | None:
        """Waits for the bot's message with buttons in `topic` since `after`."""
        self.tracker.wait(
            lambda: any(
                checks.inline_buttons(m.as_message())
                for m in self.tracker.bot_messages(topic, after)
            ),
            60,
        )
        found = [
            m
            for m in self.tracker.bot_messages(topic, after)
            if checks.inline_buttons(m.as_message())
        ]
        return found[-1] if found else None

    def check_night(self, topic: int) -> None:
        """`/night add`: Cancel works; a second add queues it. The buttons are green and red, then greyed."""
        began = time.monotonic()
        at = self.driver.send_text(f"/night add {MARK} phase 0 probe", topic)
        first = self.approval(topic, at)
        if first is None:
            texts = [m.text[:120] for m in self.tracker.bot_messages(topic, at)]
            self.result(
                "/night add: Cancel", "bridge", False, began, f"no confirmation buttons ({texts})"
            )
            return
        styles = [
            (b.get("text"), (b.get("style") or {}).get("@type"))
            for b in checks.inline_buttons(first.as_message())
        ]
        colours_ok = styles[0][1] == "buttonStyleSuccess" and styles[-1][1] == "buttonStyleDanger"
        self.driver.press(first, "Cancel")
        greyed = self.tracker.wait(lambda: self.closed(first), 30)
        outcome = [b.get("text") for b in checks.inline_buttons(first.as_message())]
        self.result(
            "/night add: Cancel",
            "bridge",
            greyed and outcome == ["Not queued"],
            began,
            f"closed as {outcome}",
        )
        began = time.monotonic()
        at = self.driver.send_text(f"/night add {MARK} phase 0 probe", topic)
        second = self.approval(topic, at)
        if second is None:
            self.result(
                "/night add: queue",
                "bridge",
                False,
                began,
                "no confirmation buttons the second time",
            )
        else:
            self.driver.press(second, styles[0][0] or "Queue it")
            self.tracker.wait(
                lambda: any(
                    "queue" in m.text.lower() and m.id != second.id
                    for m in self.tracker.bot_messages(topic, at)
                ),
                60,
            )
            said = [m.text for m in self.tracker.bot_messages(topic, at) if m.id != second.id]
            queued = any(
                "queued" in text.lower() and "did not" not in text.lower() for text in said
            )
            self.result(
                "/night add: queue",
                "bridge",
                queued and self.closed(second),
                began,
                (said[-1][:160] if said else "nothing said"),
            )
        self.result(
            "approval buttons greyed when closed",
            "bridge",
            greyed,
            began,
            f"greyed after the press: {greyed}",
        )
        # Only "Approve…" and "Decline" are coloured (telegram.rs `approval_keyboard`); command
        # approvals never reach a phone thread (approval_policy = "never"), so this is recorded.
        self.result(
            "approval buttons green and red",
            "info",
            None,
            began,
            f"/night add's buttons: {styles} (coloured: {colours_ok})",
        )

    def closed(self, message: Seen) -> bool:
        """Says whether an approval message now shows one disabled button (or none at all)."""
        buttons = checks.inline_buttons(message.as_message())
        return all(
            b.get("type", {}).get("@type") == "inlineKeyboardButtonTypeDisabled" for b in buttons
        )

    def check_typo(self, topic: int) -> None:
        """A typo ("history of Uni") is answered on its best reading, with no question back."""
        began = time.monotonic()
        messages, _, latency = self.ask(TYPO_PROMPT, topic)
        text = answer_text(messages)
        problems = []
        if checks.clarifying_question(text):
            problems.append("asked a clarifying question")
        if checks.mentions_attachment(text):
            problems.append("talked of an attachment")
        if "unix" not in text.lower():
            problems.append("did not read it as Unix")
        self.result(
            "typo answered on its best reading",
            "model",
            not problems,
            began,
            "; ".join(problems) or f"answered ({checks.word_count(text)} words)",
            latency,
            excerpt=text[:200],
        )

    def check_search(self, topic: int) -> None:
        """A dated question is searched with ling-search, never curl."""
        began = time.monotonic()
        messages, _, latency = self.ask(SEARCH_PROMPT, topic)
        session = self.session_for(SEARCH_PROMPT)
        commands = session.commands if session else []
        curl = checks.uses_curl(commands)
        search = checks.searched_first(commands)
        detail = (
            "no session found"
            if session is None
            else f"ling-search first: {search}; curl later: {curl}"
        )
        self.result(
            "searches with ling-search, not curl",
            "model",
            session is not None and search,
            began,
            detail,
            latency,
        )

    def check_outside_topics(self) -> None:
        """Reports what a message outside every topic does: where its answer lands, and whether a topic appears."""
        began = time.monotonic()
        known = set(self.tracker.topics)
        at = self.driver.send_text(f"{MARK} What is 2 + 2? One word.")
        self.tracker.wait(lambda: bool(self.tracker.bot_messages(None, at)), 120)
        self.tracker.pump(QUIET)
        mine = [m for m in self.tracker.messages.values() if not m.from_bot and m.at >= at]
        replies = self.tracker.bot_messages(None, at)
        new_topics = sorted(set(self.tracker.topics) - known)
        where = sorted({m.topic for m in replies})
        detail = f"sent into topic {mine[0].topic if mine else '?'}; answered in topics {where}; new topics {len(new_topics)}"
        self.result(
            "message outside topics",
            "info",
            None,
            began,
            detail,
            self.tracker.first_activity(where[0], at) if where else None,
        )

    def dump(self, path: Path) -> None:
        """Writes what the suite saw in the bot's chat (texts cut to 200 characters), for debugging."""
        messages = [
            {
                "id": m.id,
                "topic": m.topic,
                "bot": m.from_bot,
                "kind": m.kind,
                "text": m.text[:200],
                "edits": len(m.edits),
                "buttons": checks.inline_buttons(m.as_message()),
            }
            for m in sorted(self.tracker.messages.values(), key=lambda m: m.at)
        ]
        events = [
            {"t": round(t - self.tracker.events[0][0], 1), "kind": kind, "topic": topic, **data}
            for t, kind, topic, data in self.tracker.events
        ]
        path.write_text(
            json.dumps(
                {"messages": messages, "events": events, "topics": self.tracker.topics}, indent=1
            )
        )

    def run_all(self) -> None:
        """Runs every check; one check's exception is its failure, never the suite's end."""
        topic = self.guard("/new makes a topic", self.check_new_topic)
        if not topic:
            return
        self.guard("rich answer", self.check_rich, topic)
        self.guard("hostile answer stays text", self.check_hostile, topic)
        self.guard("voice note", self.check_voice, topic)
        self.guard("/night add", self.check_night, topic)
        long_topic = self.guard("/new makes a topic (2)", self.driver.new_topic)
        if long_topic and long_topic[0]:
            self.guard("Stop ends the answer", self.check_long_and_stop, long_topic[0])
        others = [self.guard("/new", self.driver.new_topic) for _ in range(2)]
        if all(other and other[0] for other in others):
            self.guard("two topics at once", self.check_two_topics, others[0][0], others[1][0])
            self.guard("typo answered on its best reading", self.check_typo, others[0][0])
            self.guard("searches with ling-search, not curl", self.check_search, others[1][0])
        self.guard("message outside topics", self.check_outside_topics)
        latencies = [
            r.latency
            for r in self.run.results
            if r.messenger == "telegram" and r.latency is not None
        ]
        middle = checks.median(latencies)
        self.run.add(
            Result(
                "telegram",
                "first-reply latency",
                "info",
                "observed",
                None,
                None if middle is None else round(middle, 1),
                f"median of {len(latencies)} questions: {middle:.1f} s"
                if middle is not None
                else "no reply timed",
            )
        )

    def guard(self, check: str, step: Any, *args: Any) -> Any:
        """Runs one step; an exception becomes that check's failure."""
        began = time.monotonic()
        try:
            return step(*args)
        except Exception as err:  # noqa: BLE001 (any harness error is reported, not raised)
            self.result(
                check, "bridge", False, began, f"harness error: {type(err).__name__}: {err}"
            )
            return None
