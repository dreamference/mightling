"""Decision branches of the name stripper: the word list and which names a gold patch reveals."""

from dreamference.swe_bench import swe_bench_name_stripper as module
from dreamference.swe_bench.swe_bench_name_stripper import SweBenchNameStripper


def test_word_list_is_read_once_and_lowercased(monkeypatch, tmp_path):
    words = tmp_path / "words"
    words.write_text("Update\n\n  Query  \n", encoding="utf-8")
    monkeypatch.setattr(module, "WORD_LIST", str(words))
    monkeypatch.setattr(SweBenchNameStripper, "_english", None)
    assert SweBenchNameStripper.english() == frozenset({"update", "query"})
    words.write_text("other\n", encoding="utf-8")
    assert SweBenchNameStripper.english() == frozenset({"update", "query"})
    assert SweBenchNameStripper.is_english("QUERY") is True
    assert SweBenchNameStripper.is_english("zzzz") is False


def test_missing_word_list_gives_an_empty_set_so_every_word_is_english(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "WORD_LIST", str(tmp_path / "absent"))
    monkeypatch.setattr(SweBenchNameStripper, "_english", None)
    assert SweBenchNameStripper.english() == frozenset()
    assert SweBenchNameStripper.is_english("anything") is True


def test_init_module_reveals_the_package_but_not_its_own_stem():
    patch = "diff --git a/pkg/sub/__init__.py b/pkg/sub/__init__.py\n--- a/pkg/sub/__init__.py\n"
    found = {name: kind for name, kind, _ in SweBenchNameStripper.names(patch)}
    assert found["pkg.sub"] == "module"
    assert "__init__" not in found
    assert "__init__.py" in found  # the file name stays a file, the generic stem does not


def test_top_level_module_and_non_python_file_reveal_only_what_they_are():
    patch = "diff --git a/setup.py b/setup.py\ndiff --git a/docs/changes.rst b/docs/changes.rst\n"
    found = {name: kind for name, kind, _ in SweBenchNameStripper.names(patch)}
    assert found["setup.py"] == "file"
    assert found["setup"] == "module"
    assert "docs.changes" not in found
    assert found["changes.rst"] == "file"
    assert "changes" not in found


def test_hunk_context_that_is_not_a_definition_reveals_nothing():
    patch = (
        "diff --git a/lib/mod.py b/lib/mod.py\n"
        "@@ -1,3 +1,3 @@ if x:\n"
        "@@ -9,3 +9,3 @@ class Widget(Base):\n"
        "@@ -20,3 +20,3 @@ def render_all(self):\n"
    )
    found = {name: kind for name, kind, _ in SweBenchNameStripper.names(patch)}
    assert found["Widget"] == "class"
    assert found["render_all"] == "function"
    assert "if" not in found and "x" not in found
