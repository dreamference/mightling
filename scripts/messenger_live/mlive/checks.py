"""The suite's pure judgements: what a received answer shows, with no client and no network.

Telegram answers arrive as TDLib objects (`messageText` with entities, or `messageRichMessage`
with page blocks); Signal answers as text with style ranges. Everything here reads those shapes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, Final

CLARIFYING: Final = (
    "did you mean",
    "do you mean",
    "could you clarify",
    "can you clarify",
    "could you confirm",
    "please clarify",
    "which one do you",
    "what do you mean",
    "are you referring",
    "could you specify",
)
ATTACHMENT: Final = (
    "attachment",
    "attached",
    "can't see an image",
    "cannot see an image",
    "can't see the image",
    "no image",
)
STOPPED_MARK: Final = "(stopped)"


def walk(value: Any) -> Iterator[dict[str, Any]]:
    """Every TDLib object inside `value`, depth first."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def plain_text(content: dict[str, Any]) -> str:
    """The readable text of a message's content: `messageText`'s text, or a rich message's words."""
    kind = content.get("@type")
    if kind == "messageText":
        return content.get("text", {}).get("text", "")
    if kind in ("messageVoiceNote", "messageDocument", "messagePhoto"):
        return content.get("caption", {}).get("text", "")
    words = [
        obj["text"]
        for obj in walk(content)
        if obj.get("@type") == "richTextPlain" and isinstance(obj.get("text"), str)
    ]
    return " ".join(words)


def links(content: dict[str, Any]) -> list[str]:
    """Every link target a message carries: text entities' URLs and rich-text URLs."""
    found = []
    for obj in walk(content):
        if obj.get("@type") in (
            "textEntityTypeTextUrl",
            "richTextUrl",
            "richTextAnchorLink",
        ) and isinstance(obj.get("url"), str):
            found.append(obj["url"])
    return found


def dangerous_links(content: dict[str, Any]) -> list[str]:
    """Link targets with a scheme other than http, https, mailto or tg."""
    bad = []
    for url in links(content):
        scheme = url.split(":", 1)[0].strip().lower() if ":" in url else ""
        if scheme not in ("http", "https", "mailto", "tg"):
            bad.append(url)
    return bad


def inline_buttons(message: dict[str, Any]) -> list[dict[str, Any]]:
    """The inline keyboard's buttons of a message (none when it has no inline keyboard)."""
    markup = message.get("reply_markup") or {}
    if markup.get("@type") != "replyMarkupInlineKeyboard":
        return []
    return [button for row in markup.get("rows", []) for button in row]


def rich_features(content: dict[str, Any]) -> dict[str, Any]:
    """What a rich message is built from: table, nested list, code languages, links, block count."""
    blocks = (
        content.get("message", {}).get("blocks", [])
        if content.get("@type") == "messageRichMessage"
        else []
    )
    features: dict[str, Any] = {
        "rich": content.get("@type") == "messageRichMessage",
        "blocks": len(blocks),
        "table": False,
        "list": False,
        "nested_list": False,
        "code": [],
        "links": len(links(content)),
    }
    for obj in walk(blocks):
        kind = obj.get("@type")
        if kind == "pageBlockTable":
            features["table"] = True
        elif kind == "pageBlockList":
            features["list"] = True
        elif kind == "pageBlockListItem" and any(
            inner.get("@type") == "pageBlockList" for inner in walk(obj.get("blocks", []))
        ):
            features["nested_list"] = True
        elif kind == "pageBlockPreformatted":
            features["code"].append(obj.get("language", ""))
    if content.get("@type") == "messageText":
        for entity in content.get("text", {}).get("entities", []):
            entity_type = entity.get("type", {})
            if entity_type.get("@type") == "textEntityTypePreCode":
                features["code"].append(entity_type.get("language", ""))
    return features


def clarifying_question(text: str) -> bool:
    """Says whether an answer asks the user what they meant instead of answering."""
    lower = text.lower()
    if any(phrase in lower for phrase in CLARIFYING):
        return True
    stripped = lower.rstrip()
    return len(stripped) < 400 and stripped.endswith("?")


def mentions_attachment(text: str) -> bool:
    """Says whether an answer talks about an attachment or image the user never sent."""
    lower = text.lower()
    return any(phrase in lower for phrase in ATTACHMENT)


def word_count(text: str) -> int:
    """Words in a text."""
    return len(re.findall(r"\w+", text))


def stopped(text: str) -> bool:
    """Says whether a message is a stopped answer the bridge kept (it ends "(stopped)")."""
    return text.rstrip().endswith(STOPPED_MARK)


def signal_styles(styles: list[Any]) -> set[str]:
    """The style names of a Signal message's text styles (`BOLD`, `MONOSPACE`, …)."""
    names = set()
    for style in styles:
        if isinstance(style, dict) and style.get("style"):
            names.add(str(style["style"]).upper())
        elif isinstance(style, str):
            # signal-cli prints "start:length:STYLE".
            names.add(style.rsplit(":", 1)[-1].upper())
    return names


def leftover_markdown(text: str) -> list[str]:
    """Markdown syntax that should have become styles: `**bold**`, fences, table rules."""
    found = []
    if re.search(r"\*\*[^*\n]+\*\*", text):
        found.append("**")
    if "```" in text:
        found.append("```")
    if re.search(r"^\s*\|?\s*:?-{3,}:?\s*\|", text, re.MULTILINE):
        found.append("table rule")
    return found


def uses_curl(commands: list[str]) -> bool:
    """Says whether any of the agent's commands ran `curl` or `wget`."""
    return any(re.search(r"(^|[\s;|&(])(curl|wget)\s", " " + command) for command in commands)


def uses_ling_search(commands: list[str]) -> bool:
    """Says whether any of the agent's commands ran `ling-search` or `ling-fetch`."""
    return any("ling-search" in command or "ling-fetch" in command for command in commands)


def median(values: list[float]) -> float | None:
    """The median, or None for no values."""
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
