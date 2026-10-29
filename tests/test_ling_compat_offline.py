"""The Claude Code compatibility mode reads Claude Code's files and never reaches Anthropic.

`ling --compat claude` translates `.claude/` and `CLAUDE.md` into `ling`'s own configuration and
talks only to the local model server. Its code therefore has no reason to name an Anthropic host,
an Anthropic key variable or a Claude model: one appearing is a call home or a borrowed credential
on its way in (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md §8.1 and §13).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPAT_SOURCES = sorted((ROOT / "ling-rs" / "src").glob("compat*.rs"))

# A host, the key and token variables Claude Code reads, and model ids in their API and cloud forms.
FORBIDDEN = re.compile(
    r"anthropic\.com|claude\.ai|claude\.com"
    r"|ANTHROPIC_[A-Z_]+|CLAUDE_CODE_OAUTH_TOKEN"
    r"|\bclaude-(?:opus|sonnet|haiku|fable|instant|[0-9])[\w.-]*|\b(?:us|eu|apac)\.anthropic\.",
    re.IGNORECASE,
)


def test_the_mode_has_its_source_files():
    names = {path.name for path in COMPAT_SOURCES}
    assert {
        "compat.rs",
        "compat_print.rs",
        "compat_hooks.rs",
        "compat_permissions.rs",
        "compat_mcp.rs",
    } <= names


def test_the_mode_names_no_anthropic_host_key_or_model():
    found = [
        f"{path.relative_to(ROOT)}:{number}: {match.group(0)}"
        for path in COMPAT_SOURCES
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for match in FORBIDDEN.finditer(line)
    ]
    assert not found, found


def test_the_pattern_catches_what_it_is_for():
    for text in (
        "https://api.anthropic.com/v1/messages",
        "claude.ai/login",
        'std::env::var("ANTHROPIC_API_KEY")',
        "CLAUDE_CODE_OAUTH_TOKEN",
        "claude-sonnet-4-5",
        "us.anthropic.claude-3-haiku",
    ):
        assert FORBIDDEN.search(text), text
    for text in (
        "Claude Code's settings",
        "ling --compat claude",
        ".claude/settings.json",
        "CLAUDE.md",
    ):
        assert not FORBIDDEN.search(text), text
