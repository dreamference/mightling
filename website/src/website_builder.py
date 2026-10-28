"""Build the one-page website once per language from a template and one JSON file per language.

``website/src/index.template.html`` holds the markup; ``website/i18n/<code>.json`` holds every visible
string of one language (HTML allowed in the values); ``website/src/languages.json`` lists the languages
in menu order. The English page is written to ``website/index.html`` and, as ``/en/``, to
``website/en/index.html``; every other language goes to ``website/<code>/index.html``.

Run ``python website/src/website_builder.py`` (a test checks that the committed pages are current).
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Final

WEBSITE_DIR: Final[Path] = Path(__file__).resolve().parent.parent
SITE_URL: Final[str] = "https://mightling.dreamference.ai"
DEFAULT_LANGUAGE: Final[str] = "en"
STRING_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"\{\{([a-z0-9_.]+)\}\}")
RIGHT_TO_LEFT: Final[str] = "rtl"
ATTRIBUTE_KEY_PREFIXES: Final[tuple[str, ...]] = ("meta.", "ui.")

GLOBE_ICON: Final[str] = (
    '<svg class="ico" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>'
)
THEME_ICONS: Final[str] = (
    '<svg class="ico ico-system" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>'
    '<svg class="ico ico-light" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
    'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
    '<svg class="ico ico-dark" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21 13.5A9 9 0 1 1 10.5 3a7 7 0 0 0 10.5 10.5z"/></svg>'
)


class WebsiteBuilder:
    """Render ``index.template.html`` for each language and write the pages under ``website/``."""

    @classmethod
    def languages(cls) -> list[dict[str, Any]]:
        """Return the language list, in menu order.

        Returns:
            One dict per language with ``code``, ``name``, ``dir`` and ``locale`` (and ``html`` when the
            ``lang`` attribute differs from the code).
        """
        return json.loads((WEBSITE_DIR / "src" / "languages.json").read_text(encoding="utf-8"))

    @classmethod
    def strings(cls, code: str) -> dict[str, str]:
        """Load one language's strings.

        Args:
            code: The language code, such as ``"es"``.

        Returns:
            The key to string mapping from ``website/i18n/<code>.json``.
        """
        return json.loads((WEBSITE_DIR / "i18n" / f"{code}.json").read_text(encoding="utf-8"))

    @classmethod
    def page_path(cls, code: str) -> str:
        """Return the URL path of a language's page.

        Args:
            code: The language code.

        Returns:
            ``/`` for the default language, ``/<code>/`` otherwise.
        """
        return "/" if code == DEFAULT_LANGUAGE else f"/{code}/"

    @classmethod
    def html_lang(cls, language: dict[str, Any]) -> str:
        """Return the BCP 47 tag for the ``lang`` and ``hreflang`` attributes."""
        return str(language.get("html", language["code"]))

    @classmethod
    def alternates(cls, languages: list[dict[str, Any]]) -> str:
        """Build the ``hreflang`` alternate links shared by every page.

        Args:
            languages: The language list.

        Returns:
            One ``<link rel="alternate">`` per language plus ``x-default``.
        """
        lines = [
            f'<link rel="alternate" hreflang="{cls.html_lang(lang)}" href="{SITE_URL}{cls.page_path(lang["code"])}">'
            for lang in languages
        ]
        lines.append(f'<link rel="alternate" hreflang="x-default" href="{SITE_URL}/">')
        return "\n".join(lines)

    @classmethod
    def controls(
        cls, current: dict[str, Any], languages: list[dict[str, Any]], ui: dict[str, str]
    ) -> str:
        """Build the header's language menu and theme button.

        Args:
            current: The language being rendered.
            languages: The language list.
            ui: The current language's strings (``ui.*`` keys are used).

        Returns:
            The HTML of the ``div.controls`` element.
        """
        esc = html.escape
        items = []
        for lang in languages:
            code = lang["code"]
            direction = ' dir="rtl"' if lang["dir"] == RIGHT_TO_LEFT else ""
            here = ' aria-current="true"' if code == current["code"] else ""
            items.append(
                f'<li><a href="{cls.page_path(code) if code != DEFAULT_LANGUAGE else "/en/"}" '
                f'hreflang="{cls.html_lang(lang)}" lang="{cls.html_lang(lang)}"{direction} data-lang="{code}"{here}>'
                f"{esc(lang['name'])}</a></li>"
            )
        label = esc(ui["ui.language"], quote=True)
        theme = esc(ui["ui.theme"], quote=True)
        return (
            '<div class="controls">'
            f'<details class="lang"><summary aria-label="{label}" title="{label}">{GLOBE_ICON}'
            f'<span class="cur">{esc(current["name"])}</span><span class="cc">{esc(current["code"])}</span></summary>'
            f"<ul>{''.join(items)}</ul></details>"
            f'<button class="theme" type="button" hidden data-mode="system" aria-label="{theme}" title="{theme}" '
            f'data-theme-label="{theme}" data-label-system="{esc(ui["ui.theme_system"], quote=True)}" '
            f'data-label-light="{esc(ui["ui.theme_light"], quote=True)}" '
            f'data-label-dark="{esc(ui["ui.theme_dark"], quote=True)}">{THEME_ICONS}'
            f'<span class="tl">{esc(ui["ui.theme_system"])}</span></button>'
            "</div>"
        )

    @classmethod
    def render(
        cls, language: dict[str, Any], languages: list[dict[str, Any]], is_root: bool = False
    ) -> str:
        """Render one page.

        Args:
            language: The language to render.
            languages: The language list.
            is_root: True for the site root, which alone may redirect to a remembered language.

        Returns:
            The page HTML.

        Raises:
            KeyError: When the template names a string the language file does not have.
        """
        code = language["code"]
        strings = cls.strings(code)
        template = (WEBSITE_DIR / "src" / "index.template.html").read_text(encoding="utf-8")

        def substitute(match: re.Match[str]) -> str:
            key = match.group(1)
            value = strings[key]
            return (
                html.escape(value, quote=True) if key.startswith(ATTRIBUTE_KEY_PREFIXES) else value
            )

        text = STRING_PLACEHOLDER.sub(substitute, template)
        canonical = f"{SITE_URL}{cls.page_path(code)}"
        root_attributes = (
            ' data-root="1" data-langs="' + ",".join(lang["code"] for lang in languages) + '"'
        )
        values = {
            "[[lang]]": cls.html_lang(language),
            "[[dir]]": language["dir"],
            "[[canonical]]": canonical,
            "[[og_locale]]": language["locale"],
            "[[alternates]]": cls.alternates(languages),
            "[[controls]]": cls.controls(language, languages, strings),
            "[[root]]": root_attributes if is_root else "",
            "[[home]]": "#",
        }
        for token, value in values.items():
            text = text.replace(token, value)
        return text

    @classmethod
    def build(cls, out_dir: Path = WEBSITE_DIR) -> list[Path]:
        """Write every page.

        Args:
            out_dir: The directory to write into; defaults to ``website/``.

        Returns:
            The paths written.
        """
        languages = cls.languages()
        written = []
        for language in languages:
            code = language["code"]
            targets = [(out_dir / code / "index.html", False)]
            if code == DEFAULT_LANGUAGE:
                targets.append((out_dir / "index.html", True))
            for path, is_root in targets:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(cls.render(language, languages, is_root), encoding="utf-8")
                written.append(path)
        return written

    @classmethod
    def stale_language_dirs(cls) -> list[Path]:
        """Return generated language folders that no longer match ``languages.json``."""
        known = {lang["code"] for lang in cls.languages()}
        return [
            p
            for p in WEBSITE_DIR.iterdir()
            if p.is_dir()
            and (p / "index.html").exists()
            and p.name not in known
            and p.name not in {"src", "i18n"}
        ]


def main() -> int:
    """Rebuild the pages; remove folders of languages that were dropped."""
    for stale in WebsiteBuilder.stale_language_dirs():
        shutil.rmtree(stale)
    for path in WebsiteBuilder.build():
        sys.stdout.write(f"{path.relative_to(WEBSITE_DIR.parent)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
