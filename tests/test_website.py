"""The one-page website: every language file is complete and the committed pages are current."""

from __future__ import annotations

import importlib.util
import json
import re
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest

WEBSITE = Path(__file__).resolve().parent.parent / "website"
PLACEHOLDER = re.compile(r"\{\{([a-z0-9_.]+)\}\}")


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "website_builder", WEBSITE / "src" / "website_builder.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder().WebsiteBuilder
LANGUAGES = BUILDER.languages()
CODES = [lang["code"] for lang in LANGUAGES]
ENGLISH = BUILDER.strings("en")


def _markup(value: str) -> tuple[Counter[str], Counter[str], list[str]]:
    tags = Counter(re.findall(r"<\s*/?\s*([a-zA-Z0-9]+)", value))
    hrefs = Counter(re.findall(r'href="([^"]*)"', value))
    return tags, hrefs, re.findall(r"<code>(.*?)</code>", value)


def test_the_site_covers_the_twenty_most_spoken_languages() -> None:
    assert len(CODES) == 20
    assert len(set(CODES)) == 20
    assert CODES[0] == "en"
    assert {lang["code"] for lang in LANGUAGES if lang["dir"] == "rtl"} == {"ar", "ur"}


def test_every_language_has_a_file_and_every_file_a_language() -> None:
    files = {p.stem for p in (WEBSITE / "i18n").glob("*.json")}
    assert files == set(CODES)


def test_the_template_uses_exactly_the_english_keys() -> None:
    template = (WEBSITE / "src" / "index.template.html").read_text(encoding="utf-8")
    used = set(PLACEHOLDER.findall(template))
    ui_only = {k for k in ENGLISH if k.startswith("ui.") and k not in used}
    assert used - set(ENGLISH) == set()
    assert (
        set(ENGLISH) - used == ui_only
    )  # the header controls read the ui.* strings, not the template


@pytest.mark.parametrize("code", CODES)
def test_language_file_has_every_key_and_the_same_markup(code: str) -> None:
    strings = BUILDER.strings(code)
    assert list(strings) == list(ENGLISH), f"{code}: keys differ from en.json"
    for key, english in ENGLISH.items():
        value = strings[key]
        assert value.strip(), f"{code}: {key} is empty"
        assert _markup(value) == _markup(english), f"{code}: {key} changes tags, links or code"
        if key.startswith(("meta.", "ui.")):
            assert '"' not in value, (
                f"{code}: {key} lands in an attribute and must not hold a double quote"
            )


@pytest.mark.parametrize("code", CODES)
def test_committed_page_is_current(code: str) -> None:
    language = next(lang for lang in LANGUAGES if lang["code"] == code)
    page = WEBSITE / code / "index.html"
    assert page.read_text(encoding="utf-8") == BUILDER.render(language, LANGUAGES), (
        "run website/src/website_builder.py"
    )
    if code == "en":
        root = (WEBSITE / "index.html").read_text(encoding="utf-8")
        assert root == BUILDER.render(language, LANGUAGES, is_root=True)


@pytest.mark.parametrize("code", CODES)
def test_page_head_and_direction(code: str) -> None:
    language = next(lang for lang in LANGUAGES if lang["code"] == code)
    text = (WEBSITE / code / "index.html").read_text(encoding="utf-8")
    tag = BUILDER.html_lang(language)
    assert f'<html lang="{tag}" dir="{language["dir"]}">' in text
    assert text.count('rel="alternate" hreflang=') == 21  # twenty languages and x-default
    assert 'hreflang="x-default"' in text
    assert "{{" not in text and "[[" not in text
    assert re.search(r"<title>[^<]+</title>", text)


def test_the_root_page_alone_may_redirect() -> None:
    assert 'data-root="1"' in (WEBSITE / "index.html").read_text(encoding="utf-8")
    for code in CODES:
        assert 'data-root="1"' not in (WEBSITE / code / "index.html").read_text(encoding="utf-8")


def test_pages_load_nothing_from_other_hosts() -> None:
    for code in CODES:
        text = (WEBSITE / code / "index.html").read_text(encoding="utf-8")
        loads = re.findall(r"<(?:script|img|iframe)\b[^>]*\bsrc=\"([^\"]*)\"", text)
        loads += re.findall(
            r"<link\b[^>]*rel=\"(?:stylesheet|icon|apple-touch-icon)\"[^>]*href=\"([^\"]*)\"", text
        )
        assert loads
        assert all(url.startswith("/") for url in loads), loads
        assert "fonts.googleapis" not in text
    for name in ("init.js", "copy.js", "style.css"):
        source = (WEBSITE / name).read_text(encoding="utf-8")
        assert "http://" not in source and "https://" not in source.replace("// https", "")


def test_language_names_are_unique_and_native() -> None:
    names = [lang["name"] for lang in LANGUAGES]
    assert len(set(names)) == len(names)
    assert json.loads((WEBSITE / "src" / "languages.json").read_text(encoding="utf-8"))
