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

So the general category now draws on more engines run by independent companies, each checked from
this machine with one query that day: Mwmbl, Yep, Seznam and Yandex beside Brave, Google, Startpage
and Wikipedia. DuckDuckGo is off: it raises its CAPTCHA with a suspension of zero seconds, so
SearXNG asked it again on every search. Bing, Mojeek, Qwant and Yahoo stay off: Bing answered a
song question and a kernel question with speed-test sites, Mojeek served a JavaScript challenge,
Qwant denied access and Yahoo dropped the connection.

The file carries a version line. `ling-admin searxng start` rewrites a file that has it (or the
hand-made file's first line) when this module's version is newer, keeping the `secret_key`, and
leaves any other file alone: deleting the first line keeps a file of one's own.
"""

import re
import secrets
from pathlib import Path
from typing import Final

# Bumped whenever SETTINGS_TEMPLATE changes, so an installed file is rewritten on the next start.
SETTINGS_VERSION: Final[int] = 2

# The first line of a file this module wrote.
MANAGED_MARKER: Final[str] = "# ling-admin searxng settings, version "

# The first line of the hand-made file every installation before version 2 has.
LEGACY_MARKER: Final[str] = "# Minimal SearXNG settings for Dreamference"

# What `SearxngSettings.ensure` did.
WRITTEN: Final[str] = "written"
CURRENT: Final[str] = "current"
OWN: Final[str] = "own"

# The general engines this file turns on or keeps on, in the order `searxng status` lists them.
GENERAL_ENGINES: Final[tuple[str, ...]] = (
    "brave",
    "google cse",
    "startpage",
    "mwmbl",
    "yep",
    "seznam",
    "yandex",
    "wikipedia",
    "wikidata",
)


SETTINGS_TEMPLATE: Final[str] = """\
# ling-admin searxng settings, version {version}
#
# Written by `ling-admin searxng start` (dreamference/chat/searxng_settings.py), which rewrites it
# when a newer version ships and keeps the secret_key. To keep a file of your own instead, delete
# the first line: a file without it is left alone.
#
# Departures from SearXNG's defaults:
#   * search.formats includes json: SearXNG serves HTML only by default, and ling-search needs JSON.
#   * server.limiter is off: the limiter is bot protection, and a local tool calling its own
#     instance looks exactly like a bot to it.
#   * More general engines, from independent companies, so one company refusing this machine's
#     address leaves the others (2026-10-20: Brave, Google, DuckDuckGo and Startpage all refused it).
#     DuckDuckGo is off: its CAPTCHA carries a zero-second suspension, so it was asked again on
#     every search. Bing is left off: it answered unrelated questions with speed-test sites.
#   * Suspensions: an engine answering "too many requests" or "access denied" rests 15 minutes, not
#     3, so a refused address is asked four times an hour rather than twenty. Cloudflare and
#     reCAPTCHA challenges rest a day, not two weeks or one.
use_default_settings: true

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
  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg
    disabled: true
  - name: mwmbl
    engine: mwmbl
    shortcut: mwm
    categories: [general, web]
    disabled: false
  - name: yep
    engine: yep
    shortcut: yep
    categories: [general, web]
    disabled: false
  - name: seznam
    engine: seznam
    shortcut: szn
    categories: [general, web]
    disabled: false
  - name: yandex
    engine: yandex
    shortcut: yd
    search_type: web
    categories: [general, web]
    disabled: false
"""

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
        return SETTINGS_TEMPLATE.format(version=SETTINGS_VERSION, secret_key=secret_key)

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
    def ensure(cls, config_dir: str) -> str:
        """Makes `settings.yml` in `config_dir` this version's, unless it is the user's own.

        Args:
            config_dir (str): SearXNG's configuration folder (mounted as `/etc/searxng`).

        Returns:
            str: "written" when the file was created or rewritten (a running SearXNG needs a
            restart to read it), "current" when it already was this version's or a newer one's,
            "own" when it is the user's file and was left alone.
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
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("settings.yml.new")
        temporary.write_text(wanted, encoding="utf-8")
        temporary.chmod(0o644)
        temporary.replace(path)
        return WRITTEN
