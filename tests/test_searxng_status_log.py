"""Reading SearXNG's log: an engine that never loaded stays not loaded, and lines without a time are skipped."""

from dreamference.chat.searxng_status import SearxngStatus

INIT_FAILED = (
    "2026-10-20T10:54:37.462Z 2026-10-20 10:54:37,462 ERROR:searx.searx.search.processor: (PID 850) "
    "wikidata: engine INIT failed, exception: HTTP error 403 (suspended_time=180)"
)


def test_an_engine_that_failed_to_load_keeps_that_error_after_later_lines():
    later = "2026-10-20T11:00:00.100Z 2026-10-20 11:00:00,100 ERROR:searx.engines.wikidata: engine timeout"
    event = SearxngStatus.parse_log([INIT_FAILED, later])["wikidata"]
    assert event.init_failed and event.reason == "HTTP error 403"
    assert SearxngStatus.state(event, event.at)[0] == "not loaded"


def test_a_line_whose_time_does_not_parse_is_skipped():
    lines = [
        "garbage 2026-10-20 ERROR:searx.engines.brave: engine timeout",
        "2026-13-40T10:00:00Z 2026-10-20 ERROR:searx.engines.google: engine timeout",
    ]
    assert SearxngStatus.parse_log(lines) == {}
    assert SearxngStatus.parse_time("2026-13-40T10:00:00Z") is None
    assert SearxngStatus.parse_time("yesterday") is None
