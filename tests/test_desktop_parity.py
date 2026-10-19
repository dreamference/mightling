"""The desktop app's parity with the Codex app (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §5).

Two of §5's checks, read from files only: the protocol inventory (the committed generated types,
the grouped list beside them and, in a checkout with the submodule, the pinned Codex's own schema
agree, so a Codex bump that moves a method fails here until the spec and the policy are reviewed),
and the policy coverage (every method a §2 row ships through is in ling-rs/web/policy.json or on
the list of methods Phase 2 still has to add, with the item that adds it).
"""

import json
import re
from pathlib import Path

import pytest

from dreamference.chat.desktop_protocol_types import PROTOCOL_DIR, DesktopProtocolTypes
from dreamference.chat.desktop_runner import UI_DIR

REPO = Path(UI_DIR).parent.parent
SPEC = REPO / "specs" / "DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md"
POLICY = REPO / "ling-rs" / "web" / "policy.json"
INVENTORY = Path(UI_DIR) / "src" / "protocol-inventory.json"
KINDS = ("ClientRequest", "ServerRequest", "ServerNotification", "ClientNotification")

# How many methods of each kind the pinned Codex (rust-v0.158.0) has: §1.4 states these numbers.
COUNTS = {"ClientRequest": 170, "ServerRequest": 11, "ServerNotification": 85, "ClientNotification": 1}

# Methods §2's Phase 2 rows ship through that ling-rs/web/policy.json does not allow yet, with the
# §4.2 item that adds them. Adding one to the policy needs its Rust conformance run (cargo), so it
# lands with its item when that can run; this list shrinks as they do.
POLICY_TO_ADD = {
    "project/delete": 3, "project/import": 3, "project/move": 3,
    "threadSection/create": 3, "threadSection/list": 3, "threadSection/update": 3, "threadSection/delete": 3,
    "thread/section/move": 3, "thread/searchOccurrences": 3,
    "skills/extraRoots/set": 11, "plugin/skill/read": 11,
    "plugin/read": 11, "plugin/search": 11,
    "plugin/installed": 11, "app/read": 11, "app/installed": 11,
}


# Methods a §2 row names that Mightling deliberately does not allow, with the reason.
NOT_ADDED = {
    "thread/shellCommand": "runs unsandboxed with full access, so a command from the window would pass /airgapped on",
}


def methods_in(path: Path | str) -> set[str]:
    """The method names in a generated file, given as its path or as its text."""
    text = path.read_text(encoding="utf-8") if isinstance(path, Path) else path
    return set(re.findall(r'"method": "([^"]+)"', text))


def test_the_inventory_lists_every_method_of_the_committed_types_once():
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))["kinds"]
    for kind in KINDS:
        listed = [name for group in inventory[kind].values() for name in group]
        assert len(listed) == len(set(listed)), kind
        assert set(listed) == methods_in(PROTOCOL_DIR / f"{kind}.ts"), kind
        assert len(listed) == COUNTS[kind], kind


def test_the_spec_states_the_inventory_counts():
    text = SPEC.read_text(encoding="utf-8")
    assert (f"{COUNTS['ClientRequest']} client requests, {COUNTS['ServerRequest']} server requests, "
            f"{COUNTS['ServerNotification']} server notifications, {COUNTS['ClientNotification']} client notification") in text


# The pinned Codex's experimental export, which `generate-ts --experimental` writes and the committed
# types come from. The submodule's `schema/typescript/` is the stable surface (107 client requests,
# no experimental methods), so it is never the inventory's reference.
@pytest.mark.skipif(not DesktopProtocolTypes.has_source(), reason="needs the Codex submodule and zstd")
def test_the_inventory_is_the_pinned_codexs():
    exports = DesktopProtocolTypes.exports()
    for kind in KINDS:
        assert methods_in(exports[f"{kind}.ts"]) == methods_in(PROTOCOL_DIR / f"{kind}.ts"), kind


def expand(token: str) -> list[str]:
    """`project/list|read|create` → project/list, project/read, project/create."""
    head, _, last = token.rpartition("/")
    return [f"{head}/{part}" if head else part for part in last.split("|")]


# A row that names a method by its family: the methods that come with it.
IMPLIED = {
    "command/exec": ["command/exec/write", "command/exec/resize", "command/exec/terminate"],
    "threadSection/*": ["threadSection/create", "threadSection/list", "threadSection/update", "threadSection/delete", "thread/section/move"],
}


def cells_of(line: str) -> list[str]:
    """A Markdown table row's cells; a `|` inside backticks (`project/list|read`) does not split."""
    cells, current, in_code = [], "", False
    # The terminal's shortcut, Ctrl+`, is a lone backtick, not the start of code.
    for char in line.strip().strip("|").replace("+`", "+BACKTICK"):
        if char == "`":
            in_code = not in_code
        if char == "|" and not in_code:
            cells.append(current.strip())
            current = ""
        else:
            current += char
    cells.append(current.strip())
    return cells


def phase_2_methods() -> dict[str, list[str]]:
    """Client requests named in the mechanism column of §2's rows that Phase 2 ships, by row."""
    text = SPEC.read_text(encoding="utf-8")
    section = text[text.index("## 2. Every feature"):text.index("## 3. The look")]
    known = methods_in(PROTOCOL_DIR / "ClientRequest.ts")
    found: dict[str, list[str]] = {}
    for line in section.splitlines():
        cells = cells_of(line) if line.startswith("|") else []
        if len(cells) < 4 or set(cells[0]) <= {"-", " "}:
            continue
        status, phase = cells[-2], cells[-1]
        if phase != "2" or not re.search(r"\*\*(Built|Same|Allowed|Partial|Mightling's)", status):
            continue
        for token in re.findall(r"`([^`]+)`", cells[1]):
            for name in expand(token):
                for method in [name, *IMPLIED.get(name, [])]:
                    if method in known:
                        found.setdefault(method, []).append(cells[0][:60])
    return found


def test_every_phase_2_method_is_allowed_or_listed():
    allowed = set(json.loads(POLICY.read_text(encoding="utf-8"))["allowedRequests"])
    needed = phase_2_methods()
    assert needed, "no methods found in §2: the parse is broken"
    missing = sorted(set(needed) - allowed - set(NOT_ADDED))
    print("\nPhase 2 methods still to add to policy.json:")
    for name in missing:
        print(f"  {name} (item {POLICY_TO_ADD.get(name, '?')}): {needed[name][0]}")
    assert missing == sorted(POLICY_TO_ADD), "update POLICY_TO_ADD: a method was added to the policy or a row changed"
    assert not set(POLICY_TO_ADD) & allowed
    assert not set(NOT_ADDED) & allowed, "a method Mightling refuses is in the policy"
