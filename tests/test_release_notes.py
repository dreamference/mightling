"""`scripts/release_notes.py`: a release's description is its notes spec's text between the rules.

The release workflow publishes what this script writes, with no hand step after it, so a description
that is missing or unfinished must stop the release, and the text must survive GitHub's rendering of
every newline as a line break.
"""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def notes():
    spec = importlib.util.spec_from_file_location("release_notes", os.path.join(ROOT, "scripts", "release_notes.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _spec(tmp_path: Path, body: str, header: str = "# Mightling 9.9.9 — release notes\n\n**Status:** draft.\n") -> Path:
    path = tmp_path / "DREAMFERENCE_RELEASE_9.9.9.md"
    path.write_text(f"{header}\n---\n\n{body}\n---\n\n**Checklist:** not part of the release.\n", encoding="utf-8")
    return path


def test_only_the_text_between_the_first_two_rules_is_taken(notes, tmp_path):
    path = _spec(tmp_path, "The description.\n")
    assert notes.render(path) == "The description.\n"


def test_a_wrapped_paragraph_becomes_one_line(notes, tmp_path):
    path = _spec(tmp_path, "**New.** One sentence that\nwraps over\nthree lines.\n\nA second paragraph.\n")
    assert notes.render(path) == "**New.** One sentence that wraps over three lines.\n\nA second paragraph.\n"


def test_list_items_are_unwrapped_and_kept_apart_from_the_paragraph_above(notes, tmp_path):
    body = (
        "**Fixed:**\n"
        "- **First.** A fix whose text\n"
        "  goes on here;\n"
        "- second;\n"
        "1. a numbered item\n"
        "   continued\n"
        "   - nested, kept\n"
        "     indented\n"
    )
    assert notes.render(_spec(tmp_path, body)).splitlines() == [
        "**Fixed:**",
        "- **First.** A fix whose text goes on here;",
        "- second;",
        "1. a numbered item continued",
        "   - nested, kept indented",
    ]


def test_a_figure_at_the_start_of_a_wrapped_line_is_not_a_list_item(notes, tmp_path):
    body = (
        "It resolved 68 of\n100. That is our own measurement.\n\n"
        "**Updating:**\n1. Run `ling update`;\n2. then\n   restart.\n\n"
        "   A paragraph of item 2\n   on two lines.\n3. Done.\n"
    )
    assert notes.render(_spec(tmp_path, body)).splitlines() == [
        "It resolved 68 of 100. That is our own measurement.",
        "",
        "**Updating:**",
        "1. Run `ling update`;",
        "2. then restart.",
        "",
        "   A paragraph of item 2 on two lines.",
        "3. Done.",
    ]


def test_tables_headings_and_code_fences_are_kept_as_they_are(notes, tmp_path):
    body = (
        "## Upgrading\n"
        "| Before | Now |\n"
        "|---|---|\n"
        "| `a` | `b` |\n"
        "\n"
        "```bash\n"
        "ling update\n"
        "  --flag\n"
        "---\n"
        "```\n"
        "After the fence\n"
        "a paragraph.\n"
    )
    assert notes.render(_spec(tmp_path, body)).splitlines() == [
        "## Upgrading",
        "| Before | Now |",
        "|---|---|",
        "| `a` | `b` |",
        "",
        "```bash",
        "ling update",
        "  --flag",
        "---",
        "```",
        "After the fence a paragraph.",
    ]


def test_a_markdown_hard_break_is_kept(notes, tmp_path):
    path = _spec(tmp_path, "First line  \nsecond line\\\nthird line\n")
    assert notes.render(path).splitlines() == ["First line", "second line\\", "third line"]


def test_markers_in_the_header_are_allowed(notes, tmp_path):
    header = "# 9.9.9\n\nEvery `{{TBD: …}}` must be filled; exactly one VARIANT is kept.\n"
    assert notes.render(_spec(tmp_path, "Done.\n", header=header)) == "Done.\n"


@pytest.mark.parametrize("marker", ["{{TBD}}", "{{TBD: night 5's result}}", "<!-- REFINE, VARIANT A -->"])
def test_an_unfinished_description_is_refused(notes, tmp_path, marker, capsys):
    path = _spec(tmp_path, f"Ready.\n\n{marker}\n")
    with pytest.raises(notes.ReleaseNotesError, match="fill or remove it"):
        notes.render(path)
    assert notes.main([str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "line 3 after the first '---'" in captured.err


def test_a_missing_spec_is_refused(notes, tmp_path, capsys):
    assert notes.main([str(tmp_path / "DREAMFERENCE_RELEASE_9.9.9.md")]) == 1
    assert "does not exist" in capsys.readouterr().err


@pytest.mark.parametrize("text", ["# 9.9.9\n\nNo rules at all.\n", "# 9.9.9\n\n---\n\nOne rule only.\n", "---\n\n---\n"])
def test_a_spec_without_a_description_is_refused(notes, tmp_path, text):
    path = tmp_path / "DREAMFERENCE_RELEASE_9.9.9.md"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(notes.ReleaseNotesError):
        notes.render(path)


def test_version_names_the_spec_and_out_writes_the_file(notes, tmp_path, monkeypatch):
    (tmp_path / "specs").mkdir()
    _spec(tmp_path / "specs", "Body\ntext.\n")
    monkeypatch.setattr(notes, "ROOT", tmp_path)
    out = tmp_path / "body.md"
    assert notes.main(["--version", "9.9.9", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "Body text.\n"


def test_the_published_1_6_0_spec_renders(notes):
    """1.6.0 was published from this shape: its header names the markers, its body has none."""
    text = notes.render(Path(ROOT) / "specs" / "DREAMFERENCE_RELEASE_1.6.0.md")
    assert text.startswith("**Search your own documents.** Mightling now keeps a private index of your files and the agent can search it.")
    assert "| **Signal** | End-to-end encrypted;" in text
    assert "\n## Upgrading from 1.5.1\n" in text
    assert "Checklist before publishing" not in text
