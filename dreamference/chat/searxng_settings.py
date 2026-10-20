"""The `settings.yml` SearXNG runs with, written by `ling-admin searxng start`.

Until 2026-10-20 the file was made by hand once and never touched again, and SearXNG asked its
default general engines: Brave, DuckDuckGo, Google (through `google cse`), Startpage, Wikipedia and
Wikidata. On 2026-10-20 every one of the four web engines refused this machine (Brave and Google
"too many requests", DuckDuckGo and Startpage a CAPTCHA) and web search returned nothing. The logs
showed Brave, DuckDuckGo and Startpage refusing the first query of every day since the container
was created on 2026-10-14, after hours without a query, and Brave answering a single request from
the host itself with 429: the egress address was refused whatever the volume, and `google cse`,
the one web engine still answering, joined them that morning. Restarting the container clears
SearXNG's own suspensions and nothing upstream.

Version 3 (the same day) turns on every engine of SearXNG's list that answered one query from
this machine, needs no key and is not run by a Russian or Chinese company; those are removed from
SearXNG altogether (`REMOVED_ENGINES`), so not even a bang reaches them. Each stays in its own
category, so a general search is not drowned in package names and papers: `ling-search
--category news|science|it|...` asks the others. Left off: engines that refused or failed that
day (DuckDuckGo's HTML endpoint, whose CAPTCHA carries a zero-second suspension so it was asked
on every search; Qwant, Mojeek, Yahoo, TMDB, Vimeo, Geizhals and others), Bing's web search (it
answered a song question and a kernel question with speed-test sites; its news, images and videos
answer well and are on), engines whose owner could not be established, and the torrent indexes.
`specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md` lists every engine on and who runs it.

The file carries a version line. `ling-admin searxng start` rewrites a file that has it (or the
hand-made file's first line) when this module's version is newer, keeping the `secret_key`, and
leaves any other file alone: deleting the first line keeps a file of one's own.
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Final

# Bumped whenever the rendered file changes, so an installed file is rewritten on the next start.
SETTINGS_VERSION: Final[int] = 3

# The first line of a file this module wrote.
MANAGED_MARKER: Final[str] = "# ling-admin searxng settings, version "

# The first line of the hand-made file every installation before version 2 has.
LEGACY_MARKER: Final[str] = "# Minimal SearXNG settings for Dreamference"

# What `SearxngSettings.ensure` did.
WRITTEN: Final[str] = "written"
CURRENT: Final[str] = "current"
OWN: Final[str] = "own"
UNWRITTEN: Final[str] = "unwritten"

# The general engines, in the order `searxng status` lists them.
GENERAL_ENGINES: Final[tuple[str, ...]] = (
    "brave",
    "google cse",
    "startpage",
    "duckduckgo web",
    "mwmbl",
    "yep",
    "seznam",
    "gmx",
    "searchch",
    "wikipedia",
    "wikidata",
)

# Run by Russian or Chinese companies (Yandex; Baidu, Qihoo 360, Alibaba's Quark, Tencent's Sogou,
# Bilibili, AcFun, iQIYI; Habr). Removed from SearXNG, not only switched off.
REMOVED_ENGINES: Final[tuple[str, ...]] = (
    "yandex",
    "yandex images",
    "yandex music",
    "baidu",
    "baidu images",
    "baidu kaifa",
    "360search",
    "360search videos",
    "quark",
    "quark images",
    "sogou",
    "sogou images",
    "sogou videos",
    "sogou wechat",
    "bilibili",
    "acfun",
    "iqiyi",
    "habrahabr",
)

# Each engine this file changes, with what it sets. `disabled: False` turns on an engine SearXNG
# ships off; `categories` keeps an engine out of `general` that SearXNG puts there; `weight` below
# 1 lets a local or small engine add results without outranking the large ones.
ENGINE_OVERRIDES: Final[dict[str, dict[str, object]]] = {
    # General web search.
    "duckduckgo web": {"disabled": False, "categories": ["general", "web"]},
    # Mwmbl does not know languages: its results for a Portuguese or Ukrainian query were English.
    "mwmbl": {"disabled": False, "weight": 0.6},
    "yep": {"disabled": False},
    "seznam": {"disabled": False},
    "gmx": {"disabled": False, "categories": ["general", "web"], "weight": 0.5},
    "searchch": {"disabled": False, "categories": ["general", "web"], "weight": 0.5},
    # Forum posts: in general they outranked the answer to a French question.
    "boardreader": {"disabled": False, "categories": ["social media"]},
    "ddg definitions": {"disabled": False},
    "duckduckgo": {"disabled": True},
    # Wikimedia's other projects, kept out of general: `--category wikimedia`, or their bangs.
    "wikivoyage": {"disabled": False, "categories": ["wikimedia"]},
    "wikibooks": {"disabled": False, "categories": ["wikimedia"]},
    "wikiquote": {"disabled": False, "categories": ["wikimedia"]},
    "wikisource": {"disabled": False, "categories": ["wikimedia"]},
    "wikiversity": {"disabled": False, "categories": ["wikimedia"]},
    "wikispecies": {"disabled": False, "categories": ["science", "wikimedia"]},
    # Dictionaries.
    "duden": {"disabled": False},
    "jisho": {"disabled": False},
    # News, with the Italian and German ones.
    "ansa": {"disabled": False},
    "il post": {"disabled": False},
    "tagesschau": {"disabled": False, "categories": ["news"]},
    # Science.
    "crossref": {"disabled": False},
    "openalex": {"disabled": False},
    # Code, packages and documentation.
    # Docker Hub images outranked crates and repositories for a library question.
    "docker hub": {"weight": 0.3},
    "gitlab": {"disabled": False},
    "sourcehut": {"disabled": False},
    "codeberg": {"disabled": False, "timeout": 6.0},
    "crates.io": {"disabled": False},
    "lib.rs": {"disabled": False},
    "npm": {"disabled": False},
    "hex": {"disabled": False},
    "packagist": {"disabled": False},
    "pub.dev": {"disabled": False},
    "huggingface": {"disabled": False},
    "huggingface datasets": {"disabled": False},
    "huggingface spaces": {"disabled": False},
    "microsoft learn": {"disabled": False},
    "discuss.python": {"disabled": False},
    "caddy.community": {"disabled": False},
    "pi-hole.community": {"disabled": False},
    "hackernews": {"disabled": False},
    "national vulnerability database": {"disabled": False},
    "nixos wiki": {"disabled": False},
    "alpine linux packages": {"disabled": False},
    "cachy os packages": {"disabled": False},
    "voidlinux": {"disabled": False},
    # Places.
    "apple maps": {"disabled": False},
    # Apps.
    "apk mirror": {"disabled": False},
    "apple app store": {"disabled": False},
    "google play apps": {"disabled": False},
    "fdroid": {"disabled": False},
    "openrepos": {"disabled": False},
    # The torrent indexes SearXNG ships on.
    "kickass": {"disabled": True},
    "piratebay": {"disabled": True},
    "solidtorrents": {"disabled": True},
    "bt4g": {"disabled": True},
    # Videos, with the French and German archives.
    "peertube": {"disabled": False},
    "media.ccc.de": {"disabled": False},
    "odysee": {"disabled": False},
    "pixabay videos": {"disabled": False},
    "ina": {"disabled": False},
    "mediathekviewweb": {"disabled": False},
    # Music, films and books.
    "deezer": {"disabled": False},
    "podchaser": {"disabled": False, "categories": ["music", "other"]},
    "imdb": {"disabled": False},
    "rottentomatoes": {"disabled": False},
    "moviepilot": {"disabled": False},
    "senscritique": {"disabled": False},
    "openlibrary": {"disabled": False, "categories": ["books"]},
    "goodreads": {"disabled": False, "categories": ["books", "other"]},
    # Images (image search uses these).
    "imgur": {"disabled": False},
    "ipernity": {"disabled": False},
    "magnific": {"disabled": False},
    "pixabay images": {"disabled": False},
    "stocksnap": {"disabled": False},
    "shopify stock": {"disabled": False},
    "picjumbo": {"disabled": False},
    "1x": {"disabled": False},
    # Everything else.
    "steam": {"disabled": False},
    "emojipedia": {"disabled": False},
    "duckduckgo weather": {"disabled": False},
    "openmeteo": {"disabled": False},
}

SETTINGS_HEADER: Final[str] = """\
# ling-admin searxng settings, version {version}
#
# Written by `ling-admin searxng start` (dreamference/chat/searxng_settings.py), which rewrites it
# when a newer version ships and keeps the secret_key. To keep a file of your own instead, delete
# the first line: a file without it is left alone.
#
# Departures from SearXNG's defaults (the module's docstring says why):
#   * search.formats includes json: SearXNG serves HTML only by default, and ling-search needs JSON.
#   * server.limiter is off: the limiter is bot protection, and a local tool calling its own
#     instance looks exactly like a bot to it.
#   * Engines run by Russian or Chinese companies are removed; every other engine that answered
#     from this machine on 2026-10-20 is on, each in its own category.
#   * Suspensions: an engine answering "too many requests" or "access denied" rests 15 minutes, not
#     3, so a refused address is asked four times an hour rather than twenty. Cloudflare and
#     reCAPTCHA challenges rest a day, not two weeks or one.
use_default_settings:
  engines:
    remove:
{removed}

server:
  secret_key: "{secret_key}"
  limiter: false
  image_proxy: false

search:
  safe_search: 0
  formats:
    - html
    - json
  suspended_times:
    SearxEngineAccessDenied: 900
    SearxEngineCaptcha: 3600
    SearxEngineTooManyRequests: 900
    cf_SearxEngineCaptcha: 86400
    cf_SearxEngineAccessDenied: 86400
    recaptcha_SearxEngineCaptcha: 86400

engines:
{engines}"""

_SECRET_KEY = re.compile(r'^\s*secret_key:\s*["\']?([^"\'\s#]+)["\']?', re.MULTILINE)


class SearxngSettings:
    """Writes and upgrades `settings.yml` in SearXNG's configuration folder."""

    @classmethod
    def render(cls, secret_key: str) -> str:
        """Returns the settings file of this version.

        Args:
            secret_key (str): The instance's `server.secret_key`.

        Returns:
            str: The file's text.
        """
        removed = "\n".join(f'      - "{name}"' for name in REMOVED_ENGINES)
        engines = "".join(
            cls.engine_yaml(name, fields) for name, fields in ENGINE_OVERRIDES.items()
        )
        return SETTINGS_HEADER.format(
            version=SETTINGS_VERSION, secret_key=secret_key, removed=removed, engines=engines
        )

    @classmethod
    def engine_yaml(cls, name: str, fields: dict[str, object]) -> str:
        """Renders one entry of `engines:`, which SearXNG merges into its own by name.

        Args:
            name (str): The engine's name in SearXNG.
            fields (dict[str, object]): Booleans, numbers and lists of strings to set.

        Returns:
            str: The YAML lines, ending in a newline.
        """

        def value(item: object) -> str:
            if isinstance(item, bool):
                return "true" if item else "false"
            if isinstance(item, list):
                return "[" + ", ".join(str(part) for part in item) + "]"
            return str(item)

        lines = [f'  - name: "{name}"'] + [
            f"    {key}: {value(item)}" for key, item in fields.items()
        ]
        return "\n".join(lines) + "\n"

    @classmethod
    def secret_key_of(cls, text: str) -> str | None:
        """Reads the `secret_key` out of a settings file.

        Args:
            text (str): A settings file.

        Returns:
            str | None: Its `secret_key`, or None when it has none.
        """
        match = _SECRET_KEY.search(text)
        return match.group(1) if match else None

    @classmethod
    def version_of(cls, text: str) -> int | None:
        """Says which version of this module wrote a settings file.

        Args:
            text (str): A settings file.

        Returns:
            int | None: The version it was written at, 1 for the hand-made file every
            installation before version 2 has, None for a file of the user's own.
        """
        first = text.splitlines()[0] if text else ""
        if first.startswith(MANAGED_MARKER):
            number = first[len(MANAGED_MARKER) :].strip()
            return int(number) if number.isdigit() else None
        if first.startswith(LEGACY_MARKER):
            return 1
        return None

    @classmethod
    def ensure(
        cls, config_dir: str, write_as_owner: Callable[[str, str], bool] | None = None
    ) -> str:
        """Makes `settings.yml` in `config_dir` this version's, unless it is the user's own.

        Args:
            config_dir (str): SearXNG's configuration folder (mounted as `/etc/searxng`).
            write_as_owner (Callable[[str, str], bool] | None): Writes (folder, text) when this
                user cannot: SearXNG's image gives the folder to its own user at every start,
                so after the first start only that user (or root) can write in it.

        Returns:
            str: "written" when the file was created or rewritten (a running SearXNG needs a
            restart to read it), "current" when it already was this version's or a newer one's,
            "own" when it is the user's file and was left alone, "unwritten" when it needed
            writing and could not be written.
        """
        path = Path(config_dir) / "settings.yml"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if path.exists():
            version = cls.version_of(existing)
            if version is None:
                return OWN
            if version > SETTINGS_VERSION:
                return CURRENT
        wanted = cls.render(cls.secret_key_of(existing) or secrets.token_urlsafe(32))
        if existing == wanted:
            return CURRENT
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name("settings.yml.new")
            temporary.write_text(wanted, encoding="utf-8")
            temporary.chmod(0o644)
            temporary.replace(path)
        except PermissionError:
            if write_as_owner is None or not write_as_owner(config_dir, wanted):
                return UNWRITTEN
        return WRITTEN
