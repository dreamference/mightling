"""SearXNG's settings file and `ling-admin searxng status`.

On 2026-10-20 every web engine SearXNG asked refused this machine and search returned nothing;
the settings now draw on more engines and are written by `searxng start`, and `searxng status`
reads each engine's state from the logs. Every docker command here goes to a fake; nothing
touches the real daemon or the real configuration folder.
"""

import pathlib
import stat
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from dreamference.chat import searxng_sidecar
from dreamference.chat.searxng_engine_event import SearxngEngineEvent as EngineEvent
from dreamference.chat.searxng_settings import SETTINGS_VERSION, SearxngSettings
from dreamference.chat.searxng_sidecar import SEARXNG_CONTAINER_NAME, SearxngSidecar
from dreamference.chat.searxng_status import SearxngStatus
from dreamference.chat.sidecar_network import SIDECAR_NETWORK

# The hand-made file every installation before version 2 has.
LEGACY = """# Minimal SearXNG settings for Dreamference's web_search MCP tool.
use_default_settings: true

server:
  secret_key: "zlxoPRWU58tMJ3372nAGMOYMWa0iq9YfQbLmY1Qr"
  limiter: false
  image_proxy: false

search:
  safe_search: 0
  formats:
    - html
    - json
"""


def read(path):
    with pathlib.Path(path).open(encoding="utf-8") as handle:
        return handle.read()


def test_a_missing_file_is_written_with_a_new_secret_and_the_json_format(tmp_path):
    assert SearxngSettings.ensure(str(tmp_path / "searxng")) == "written"
    text = read(tmp_path / "searxng" / "settings.yml")
    assert text.startswith(f"# ling-admin searxng settings, version {SETTINGS_VERSION}\n")
    key = SearxngSettings.secret_key_of(text)
    assert key and len(key) >= 32
    assert "    - json\n" in text and "limiter: false" in text
    assert stat.S_IMODE((tmp_path / "searxng" / "settings.yml").stat().st_mode) == 0o644
    # Written once; the next start finds it current and restarts nothing.
    assert SearxngSettings.ensure(str(tmp_path / "searxng")) == "current"


def test_the_file_is_valid_yaml_with_the_engines_it_promises(tmp_path):
    yaml = pytest.importorskip("yaml")
    SearxngSettings.ensure(str(tmp_path))
    settings = yaml.safe_load(read(tmp_path / "settings.yml"))
    engines = {engine["name"]: engine for engine in settings["engines"]}
    # DuckDuckGo's HTML endpoint raises its CAPTCHA with a zero-second suspension, so it was asked
    # on every search; its API endpoint (`duckduckgo web`) answered and is on.
    assert engines["duckduckgo"]["disabled"] is True
    assert engines["duckduckgo web"]["disabled"] is False
    assert "general" in engines["duckduckgo web"]["categories"]
    for name in ("mwmbl", "yep", "seznam"):  # SearXNG ships these off
        assert engines[name]["disabled"] is False, name
    # Bing's web search is left at SearXNG's default, off: it answered with speed-test sites.
    assert "bing" not in engines
    # Wikimedia's other projects and the books engines stay out of general, so it is not drowned.
    for name in ("wikivoyage", "wikiquote", "openlibrary", "tagesschau"):
        assert "general" not in engines[name]["categories"], name
    # The torrent indexes SearXNG ships on are off.
    assert all(engines[name]["disabled"] is True for name in ("piratebay", "kickass", "bt4g"))
    # Engines run by Russian or Chinese companies are removed outright, so no bang reaches them.
    removed = settings["use_default_settings"]["engines"]["remove"]
    assert {"yandex", "baidu", "sogou", "360search", "quark", "bilibili"} <= set(removed)
    assert not set(removed) & set(engines)
    assert settings["search"]["suspended_times"]["SearxEngineTooManyRequests"] == 900
    assert settings["search"]["formats"] == ["html", "json"]
    assert settings["server"]["limiter"] is False


def test_the_hand_made_file_is_upgraded_and_keeps_its_secret(tmp_path):
    (tmp_path / "settings.yml").write_text(LEGACY)
    assert SearxngSettings.ensure(str(tmp_path)) == "written"
    text = read(tmp_path / "settings.yml")
    assert SearxngSettings.version_of(text) == SETTINGS_VERSION
    assert SearxngSettings.secret_key_of(text) == "zlxoPRWU58tMJ3372nAGMOYMWa0iq9YfQbLmY1Qr"


def test_a_file_of_ones_own_is_left_alone(tmp_path):
    own = 'use_default_settings: true\nserver:\n  secret_key: "mine"\n'
    (tmp_path / "settings.yml").write_text(own)
    assert SearxngSettings.ensure(str(tmp_path)) == "own"
    assert read(tmp_path / "settings.yml") == own
    # A file a newer ling-admin wrote is not downgraded.
    newer = f"# ling-admin searxng settings, version {SETTINGS_VERSION + 1}\n"
    (tmp_path / "settings.yml").write_text(newer)
    assert SearxngSettings.ensure(str(tmp_path)) == "current"
    assert read(tmp_path / "settings.yml") == newer


def fake_docker(monkeypatch, mode):
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ["docker", "inspect"]:
            if mode is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="no such object")
            if "NetworkMode" in argv[-1]:
                return SimpleNamespace(returncode=0, stdout=mode + "\n", stderr="")
            if "PortBindings" in argv[-1]:
                return SimpleNamespace(returncode=0, stdout="127.0.0.1 \n", stderr="")
        return ok

    from dreamference.chat import sidecar_network

    for module in (sidecar_network, searxng_sidecar):
        monkeypatch.setattr(module.subprocess, "run", run)
    return calls


def test_start_restarts_a_running_searxng_when_its_settings_changed(monkeypatch):
    calls = fake_docker(monkeypatch, SIDECAR_NETWORK)
    settings = pathlib.Path(SearxngSidecar.config_dir()) / "settings.yml"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(LEGACY, encoding="utf-8")
    assert SearxngSidecar.start()
    assert ["docker", "restart", SEARXNG_CONTAINER_NAME] in calls
    # The next start finds the settings current and only starts it.
    calls.clear()
    assert SearxngSidecar.start()
    assert ["docker", "start", SEARXNG_CONTAINER_NAME] in calls
    assert not [call for call in calls if call[1] == "restart"]


def test_a_new_searxng_gets_the_settings_before_its_container(monkeypatch):
    calls = fake_docker(monkeypatch, None)
    assert SearxngSidecar.start()
    assert (pathlib.Path(SearxngSidecar.config_dir()) / "settings.yml").exists()
    assert [call for call in calls if call[1] == "run"]


# Lines as `docker logs --timestamps` printed them on 2026-10-20 (abridged where marked).
LOG = [
    "2026-10-20T10:54:37.462Z 2026-10-20 10:54:37,462 ERROR:searx.searx.search.processor: (PID 850) wikidata: "
    "engine INIT failed, exception: HTTP error 403 (suspended_time=180)",
    "2026-10-20T10:58:32.955Z 2026-10-20 10:58:32,955 WARNING:searx.engines.duckduckgo: ErrorContext("
    "'searx/engines/duckduckgo.py', 477, 'raise SearxEngineCaptchaException(...)', "
    "'searx.exceptions.SearxEngineCaptchaException', None, ('CAPTCHA (us-en) (suspended_time=0)',)) False",
    "2026-10-20T10:58:32.978Z 2026-10-20 10:58:32,978 WARNING:searx.engines.google cse: ErrorContext("
    "'searx/engines/google_cse.py', 147, 'raise ...', 'searx.exceptions.SearxEngineTooManyRequestsException', None, "
    "('google cse: Our systems have detected unusual traffic from your network. (suspended_time=900)',)) False",
    "2026-10-20T10:58:32.980Z 2026-10-20 10:58:32,980 WARNING:searx.network.brave: HTTP Request failed: GET "
    "https://search.brave.com/search?q=weather&source=web",
    "2026-10-20T10:58:32.981Z 2026-10-20 10:58:32,981 WARNING:searx.engines.brave: ErrorContext("
    "'searx/search/processors/online.py', 207, 'response = req(...)', "
    "'searx.exceptions.SearxEngineTooManyRequestsException', None, ('Too many request (suspended_time=900)',)) False",
    "2026-10-20T11:00:00.100Z 2026-10-20 11:00:00,100 ERROR:searx.engines.wikipedia: engine timeout",
    "2026-10-20T11:00:00.300Z 2026-10-20 11:00:00,300 WARNING:searx.engines.wikipedia: ErrorContext("
    "'searx/search/processors/online.py', 207, 'response = req(...)', 'httpx.TimeoutException', None",
]


def test_the_log_gives_each_engines_last_error_and_its_suspension():
    events = SearxngStatus.parse_log(LOG)
    assert events["wikidata"].init_failed and events["wikidata"].reason == "HTTP error 403"
    assert (
        events["duckduckgo"].reason == "CAPTCHA (us-en)"
        and events["duckduckgo"].suspended_seconds == 0
    )
    assert events["brave"].suspended_seconds == 900 and events["brave"].reason == "Too many request"
    assert events["google cse"].reason.startswith(
        "google cse: Our systems have detected unusual traffic"
    )
    assert events["wikipedia"].reason == "TimeoutException"
    assert "network.brave" not in " ".join(events)


def test_the_states_follow_the_suspension_and_the_age_of_the_error():
    at = datetime(2026, 10, 20, 10, 58, tzinfo=UTC)
    resting = EngineEvent(at, "Too many request", 900)
    assert SearxngStatus.state(resting, at + timedelta(minutes=5))[0] == "suspended"
    assert SearxngStatus.state(resting, at + timedelta(minutes=16))[0] == "ok"
    captcha = EngineEvent(at, "CAPTCHA", 0)
    assert SearxngStatus.state(captcha, at + timedelta(minutes=1))[0] == "failing"
    assert SearxngStatus.state(captcha, at + timedelta(hours=1))[0] == "ok"
    assert (
        SearxngStatus.state(EngineEvent(at, "HTTP error 403", 0, init_failed=True), at)[0]
        == "not loaded"
    )
    assert SearxngStatus.state(None, at) == ("ok", "no error since SearXNG started")


def test_the_report_lists_every_general_engine_and_says_when_none_answers(monkeypatch):
    now = datetime.now(UTC)
    stamp = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
    lines = [
        f"{stamp} x WARNING:searx.engines.{name}: ErrorContext('f', 1, 'c', 'E', None, "
        f"('Too many request (suspended_time=900)',)) False"
        for name in ("brave", "mwmbl", "yep")
    ]
    monkeypatch.setattr(SearxngStatus, "container_log", classmethod(lambda cls, name: lines))
    monkeypatch.setattr(
        SearxngStatus,
        "enabled_engines",
        classmethod(
            lambda cls, url: {
                "brave": True,
                "mwmbl": True,
                "yep": True,
                "wikipedia": True,
                "duckduckgo": False,
            }
        ),
    )
    report, code = SearxngStatus.report_lines(SEARXNG_CONTAINER_NAME, "http://127.0.0.1:8888")
    assert code == 1
    out = "\n".join(report)
    assert "brave" in out and "suspended: until" in out
    assert "duckduckgo" in out and "off" in out
    assert "No web engine is answering" in out  # Wikipedia alone is not web search

    monkeypatch.setattr(SearxngStatus, "container_log", classmethod(lambda cls, name: lines[:1]))
    assert SearxngStatus.report_lines(SEARXNG_CONTAINER_NAME, "http://127.0.0.1:8888")[1] == 0

    monkeypatch.setattr(SearxngStatus, "container_log", classmethod(lambda cls, name: None))
    assert SearxngStatus.report_lines(SEARXNG_CONTAINER_NAME, "http://127.0.0.1:8888")[1] == 2


def test_a_folder_searxng_took_over_is_written_through_its_owner(tmp_path):
    # SearXNG's image gives /etc/searxng to its own user at every start: after the first start
    # this user can read the settings but not write them (seen on this machine, 2026-10-20).
    (tmp_path / "settings.yml").write_text(LEGACY)
    tmp_path.chmod(0o555)
    (tmp_path / "settings.yml").chmod(0o444)
    written = []
    try:
        assert SearxngSettings.ensure(str(tmp_path)) == "unwritten"
        assert (
            SearxngSettings.ensure(str(tmp_path), lambda folder, text: written.append(text) or True)
            == "written"
        )
        assert SearxngSettings.ensure(str(tmp_path), lambda folder, text: False) == "unwritten"
    finally:
        tmp_path.chmod(0o755)
    assert SearxngSettings.secret_key_of(written[0]) == "zlxoPRWU58tMJ3372nAGMOYMWa0iq9YfQbLmY1Qr"
    assert read(tmp_path / "settings.yml") == LEGACY


def test_the_owner_writes_through_a_throwaway_container_without_network(monkeypatch):
    seen = []
    monkeypatch.setattr(
        searxng_sidecar.subprocess,
        "run",
        lambda argv, **kwargs: (
            seen.append((argv, kwargs)) or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    assert SearxngSidecar.write_settings_as_owner("/home/u/.config/searxng", "settings text")
    ((argv, kwargs),) = seen
    assert (
        argv[:3] == ["docker", "run", "--rm"]
        and "--network" in argv
        and argv[argv.index("--network") + 1] == "none"
    )
    assert "/home/u/.config/searxng:/etc/searxng" in argv
    assert kwargs["input"] == "settings text"


def test_the_owner_writes_as_root_and_a_failed_write_is_reported(monkeypatch):
    # Only root in the throwaway container can write in a folder SearXNG's user took over.
    seen = []
    monkeypatch.setattr(
        searxng_sidecar.subprocess,
        "run",
        lambda argv, **kwargs: (
            seen.append(argv) or SimpleNamespace(returncode=1, stdout="", stderr="denied")
        ),
    )
    assert not SearxngSidecar.write_settings_as_owner("/home/u/.config/searxng", "settings text")
    (argv,) = seen
    assert argv[argv.index("--user") + 1] == "0"
