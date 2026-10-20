r"""The tree carries no personal or machine-specific identifiers.

Two kinds of check run over every tracked text file (submodules are separate repositories and are
left out):

- **The denylist**, kept outside the repository in `~/.config/dreamference/anonymity-denylist.txt`
  so that the identifiers it guards never enter the tree themselves. One entry per line; blank
  lines and `#` comments are ignored. Each entry is a case-insensitive Python regular expression
  matched as a whole word (no letter or digit directly before or after); `\w*term\w*` matches
  inside words, and a line that does not compile is taken literally. Without the file the check
  is skipped with a message saying where it goes.
- **Generic checks** that need no list: an address in 192.168.0.0/16 outside the example subnets,
  an overlay (CGNAT, 100.64.0.0/10) address outside the examples, and an absolute home path
  (`/home/<name>`, `/Users/<name>`, `C:\Users\<name>`) naming anyone but a placeholder user.

Fixtures and examples use placeholders instead: `gx10-1a2b`, `node-b`, `spark-1`; `/home/user`,
`~/mightling`; `192.168.50.x` (or `192.168.1.x`) for a LAN, 192.0.2.x where any address does.
"""

import ipaddress
import os
import pwd
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from re import Pattern
from typing import Final

import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
THIS_FILE: Final[str] = "tests/test_anonymity.py"

# The real home, not the test's: conftest gives every test a HOME of its own.
DENYLIST_PATH: Final[Path] = (
    Path(pwd.getpwuid(os.getuid()).pw_dir) / ".config/dreamference/anonymity-denylist.txt"
)

# Subnets of 192.168.0.0/16 the tree uses as examples.
EXAMPLE_LAN_SUBNETS: Final[tuple[ipaddress.IPv4Network, ...]] = tuple(
    ipaddress.IPv4Network(n) for n in ("192.168.1.0/24", "192.168.50.0/24", "192.168.100.0/24")
)
# Overlay addresses the tree uses as examples (Tailscale's MagicDNS address among them).
EXAMPLE_OVERLAY_ADDRESSES: Final[frozenset] = frozenset(
    {"100.64.0.0", "100.64.0.1", "100.100.100.100", "100.101.102.103"}
)
LAN: Final[ipaddress.IPv4Network] = ipaddress.IPv4Network("192.168.0.0/16")
OVERLAY: Final[ipaddress.IPv4Network] = ipaddress.IPv4Network("100.64.0.0/10")

# Placeholder users in home paths (`/home/sessions` is a folder named home in a results file).
PLACEHOLDER_USERS: Final[frozenset] = frozenset(
    {
        "user",
        "u",
        "x",
        "j",
        "o",
        "a",
        "me",
        "you",
        "jane",
        "ubuntu",
        "runner",
        "owner",
        "alice",
        "bob",
        "sessions",
        "shell_snapshots",
        "...",
    }
)

IPV4: Final[Pattern] = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.]*\d)")
HOME_PATH: Final[Pattern] = re.compile(
    r"(?:(?:(?<![\w.~/-])|(?<=file://))/(?:home|Users)/|[A-Za-z]:\\{1,2}Users\\{1,2})([A-Za-z0-9_.-]+)"
)

# Files no identifier could be read from.
BINARY_SUFFIXES: Final[frozenset] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".icns",
        ".webp",
        ".pdf",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".gz",
        ".zip",
        ".wasm",
        ".onnx",
        ".bin",
    }
)


def tracked_text_files() -> Iterator[tuple[str, str]]:
    """Yields each tracked text file's path (relative to the root) and its text.

    Returns:
        Iterator[Tuple[str, str]]: (path, text) pairs; submodules, symlinks and binaries are skipped.
    """
    listing = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8", "replace")
    for name in filter(None, listing.split("\0")):
        path = REPO_ROOT / name
        if (
            name == THIS_FILE
            or path.suffix.lower() in BINARY_SUFFIXES
            or path.is_symlink()
            or not path.is_file()
        ):
            continue
        raw = path.read_bytes()
        if b"\0" in raw[:8192]:
            continue
        yield name, raw.decode("utf-8", "replace")


def denylist_patterns(text: str) -> list[tuple[str, Pattern]]:
    """Compiles the denylist's entries, each as a whole-word, case-insensitive pattern.

    Args:
        text (str): The denylist file's contents.

    Returns:
        List[Tuple[str, Pattern]]: Each entry and its compiled pattern.
    """
    patterns = []
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        try:
            re.compile(entry)
            body = entry
        except re.error:
            body = re.escape(entry)
        patterns.append(
            (entry, re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])", re.IGNORECASE))
        )
    return patterns


def generic_findings(text: str) -> list[str]:
    """Finds the identifiers no list is needed for: real-looking LAN and overlay addresses, home paths.

    Args:
        text (str): One file's text.

    Returns:
        List[str]: What was found, one entry per kind and value.
    """
    found = []
    for match in IPV4.finditer(text):
        try:
            address = ipaddress.IPv4Address(match.group(1))
        except ValueError:
            continue
        if address in LAN and not any(address in subnet for subnet in EXAMPLE_LAN_SUBNETS):
            found.append(f"LAN address {address}")
        elif address in OVERLAY and str(address) not in EXAMPLE_OVERLAY_ADDRESSES:
            found.append(f"overlay address {address}")
    for match in HOME_PATH.finditer(text):
        if match.group(1).lower() not in PLACEHOLDER_USERS:
            found.append(f"home path {match.group(0)}")
    return sorted(set(found))


def denylist_text() -> str | None:
    """Reads the denylist, or None when this machine has none."""
    try:
        return DENYLIST_PATH.read_text(encoding="utf-8")
    except OSError:
        return None


def test_no_denylisted_identifier_is_in_the_tree():
    text = denylist_text()
    if text is None:
        pytest.skip(
            f"no denylist at {DENYLIST_PATH}: one identifier pattern per line, outside the repository"
        )
    patterns = denylist_patterns(text)
    hits = []
    for name, body in tracked_text_files():
        for number, line in enumerate(body.splitlines(), 1):
            # The entry's number, not the matched text, so the failure message repeats nothing.
            matched = [
                index for index, (_, pattern) in enumerate(patterns, 1) if pattern.search(line)
            ]
            hits.extend(f"{name}:{number}: matches denylist entry #{index}" for index in matched)
    assert not hits, "denylisted identifiers in the tree:\n" + "\n".join(hits[:200])


def test_no_real_looking_address_or_home_path_is_in_the_tree():
    hits = []
    for name, body in tracked_text_files():
        for number, line in enumerate(body.splitlines(), 1):
            hits.extend(f"{name}:{number}: {finding}" for finding in generic_findings(line))
    assert not hits, "replace these with placeholders (see this file's docstring):\n" + "\n".join(
        hits[:200]
    )


def test_the_generic_checks_catch_what_they_are_for():
    assert generic_findings("connect to 192.168.7.105:8000") == ["LAN address 192.168.7.105"]
    assert generic_findings("tailnet peer 100.90.1.2") == ["overlay address 100.90.1.2"]
    assert generic_findings("run /home/somebody/bin/x") == ["home path /home/somebody"]
    assert generic_findings("file:///home/somebody/x") == ["home path /home/somebody"]
    assert generic_findings(r"C:\Users\somebody\x") == [r"home path C:\Users\somebody"]
    assert (
        generic_findings("192.168.50.105 100.101.102.103 /home/user/mightling 10.0.0.2 172.17.0.1")
        == []
    )
    assert generic_findings("version 1.192.168.0.5 and 2001.192.168.0") == []
    assert (
        generic_findings("https://example.com/home/kitchen and results/run-1/home/sessions") == []
    )


def test_denylist_entries_match_whole_words_and_fall_back_to_literals():
    patterns = dict(denylist_patterns("# a comment\n\nnode-q\n\\w*zed\\w*\n[unclosed\n"))
    assert patterns["node-q"].search("ssh NODE-Q.local") and not patterns["node-q"].search(
        "node-q2"
    )
    assert patterns["\\w*zed\\w*"].search("realized")
    assert patterns["[unclosed"].search("a [unclosed bracket")
    assert "# a comment" not in patterns
