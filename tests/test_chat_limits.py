"""The phone bridges' answer bounds are one set of rules in two crates.

`ling-chat` and `ling-signal` are built apart (the Signal daemon is a small binary of its own), so
each carries `limits.rs`; from its `Verdict` type on, the two files must say the same thing
(specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §19.3, specs/DREAMFERENCE_MIGHTLING_CHAT.md §16.3).
"""

from pathlib import Path
from typing import Final

REPO: Final[Path] = Path(__file__).resolve().parent.parent
MARK: Final[str] = "/// What the bridge does about a turn."


def _from_verdict(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert MARK in text, path
    return text[text.index(MARK) :]


def test_both_bridges_bound_an_answer_with_the_same_rules_and_texts():
    chat = _from_verdict(REPO / "ling-rs" / "chat" / "src" / "limits.rs")
    signal = _from_verdict(REPO / "ling-rs" / "signal" / "src" / "limits.rs")
    assert chat == signal
