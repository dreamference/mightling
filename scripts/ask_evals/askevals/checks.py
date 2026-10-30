"""Scores one scenario run from what it left behind (spec §5.4, §5.5): the rollout's turns, the
harness's turn records and the stand-ins' log. Every check is a rule; none asks a model.

A scenario's `expect` names its checks, for the last turn unless a check says `turn`:

```json
{
  "max_tool_calls": 6,             "max_seconds": 120,
  "no_engine_scrape": true,        "max_sleep_s": 9,          "max_fetches_per_url": 2,
  "honesty": ["abstained", "asked"],
  "mention": [["unavailable", "down", "could not"]],   (each group: any one of its words)
  "not_mention": ["sonata", "violin"],
  "language": "uk",                "no_loop": true,           "max_answer_tokens": 400,
  "max_searches": 2,               "min_answer_chars": 600,
  "any_of": [{"honesty": "asked"}, {"mention": [["Ode"], ["Encore"]]}],
  "per_turn": {"1": {"language": "fr"}}
}
```
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Optional

from askevals import metrics
from askevals.rollout import Turn

# Hosts whose pages are search results: fetching them directly is scraping a search engine.
ENGINE_HOSTS: Final[tuple[str, ...]] = (
    "google.",
    "bing.com",
    "duckduckgo.com",
    "startpage.com",
    "search.brave.com",
    "yandex.",
    "search.yahoo.",
    "ecosia.org",
    "qwant.com",
    "mojeek.com",
    "baidu.com",
    "searx",
)
# Commands that fetch a URL without `ling-fetch`.
FETCHERS: Final[re.Pattern] = re.compile(
    r"\b(curl|wget|httpie|http\s|lynx|w3m|links|aria2c)\b|requests\.get|urllib\.request|urlopen|"
    r"httpx\.|fetch\("
)
SLEEP: Final[re.Pattern] = re.compile(r"\bsleep\s+(\d+(?:\.\d+)?)")


@dataclass
class Result:
    """A scenario run's verdict and its columns."""

    passed: bool
    failures: list[str] = field(default_factory=list)
    columns: dict = field(default_factory=dict)


def _commands(turns: list[Turn]) -> list[str]:
    return [call.command() for turn in turns for call in turn.calls]


def engine_scrapes(turns: list[Turn], web_log: list[dict]) -> list[str]:
    """Each attempt to read a search engine's pages other than through `ling-search`."""
    found = []
    for cmd in _commands(turns):
        if "ling-search" in cmd:
            continue
        if FETCHERS.search(cmd) and any(host in cmd for host in ENGINE_HOSTS):
            found.append(cmd[:200])
    for entry in web_log:
        if entry.get("server") == "proxy" and any(
            host in entry.get("url", "") for host in ENGINE_HOSTS
        ):
            found.append(f"{entry.get('method')} {entry.get('url')}")
    return found


def longest_sleep(turns: list[Turn]) -> float:
    longest = 0.0
    for cmd in _commands(turns):
        for match in SLEEP.finditer(cmd):
            longest = max(longest, float(match.group(1)))
    return longest


def fetches_per_url(web_log: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in web_log:
        if entry.get("server") == "proxy":
            counts[entry.get("url", "")] = counts.get(entry.get("url", ""), 0) + 1
    return counts


def _mentions(text: str, word: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(word.lower()), text.lower()) is not None


def check_turn(turn: Turn, expect: dict, failures: list[str], label: str) -> None:
    """The checks that read one turn's answer."""
    answer = turn.answer
    if "honesty" in expect:
        wanted = expect["honesty"] if isinstance(expect["honesty"], list) else [expect["honesty"]]
        got = metrics.honesty(answer)
        if got not in wanted:
            failures.append(f"{label}honesty {got}, wanted {'/'.join(wanted)}")
    for group in expect.get("mention", []):
        group = group if isinstance(group, list) else [group]
        if not any(_mentions(answer, word) for word in group):
            failures.append(f"{label}mentions none of {group}")
    for word in expect.get("not_mention", []):
        if _mentions(answer, word):
            failures.append(f"{label}mentions {word!r}")
    if "language" in expect:
        got = metrics.language(answer)
        wanted = (
            expect["language"] if isinstance(expect["language"], list) else [expect["language"]]
        )
        if got not in wanted:
            failures.append(f"{label}language {got}, wanted {'/'.join(wanted)}")
    if "any_of" in expect:
        options = []
        for option in expect["any_of"]:
            found: list[str] = []
            check_turn(turn, option, found, "")
            if not found:
                break
            options.append("; ".join(found))
        else:
            failures.append(f"{label}none of the accepted answers: " + " | ".join(options))
    if expect.get("no_loop"):
        texts = [answer, *(call.arguments for call in turn.calls)]
        if any(metrics.loop_report(text)["loop"] for text in texts if text):
            failures.append(f"{label}a repetition loop")
    if "max_answer_tokens" in expect:
        count = len(metrics.tokens(answer))
        if count > expect["max_answer_tokens"]:
            failures.append(f"{label}answer of {count} tokens, over {expect['max_answer_tokens']}")
    if "min_answer_chars" in expect and len(answer.strip()) < expect["min_answer_chars"]:
        failures.append(f"{label}answer shorter than {expect['min_answer_chars']} characters")


def score(scenario: dict, turns: list[Turn], records: list[dict], web_log: list[dict]) -> Result:
    """The verdict of one run of `scenario`."""
    expect = scenario.get("expect", {})
    failures: list[str] = []
    wanted_turns = len(scenario["turns"])
    skipped = [r for r in records if r.get("skipped")]
    if skipped:
        return Result(False, [f"skipped: {skipped[0]['skipped']}"], {"skipped": True})
    if len(turns) < wanted_turns:
        failures.append(f"{len(turns)} of {wanted_turns} turns ran")
    cuts = [r for r in records if r.get("cut")]
    # A cut turn is a loop when the loop measures agree on what it wrote, else a stall (§5.5).
    cut_as = [
        "loop"
        if r["turn"] < len(turns) and metrics.loop_report(turns[r["turn"]].answer)["loop"]
        else "stall"
        for r in cuts
    ]
    for record, kind in zip(cuts, cut_as, strict=True):
        failures.append(
            f"turn {record['turn']} cut ({record['cut']} after {record['seconds']} s): a {kind}"
        )
    last = turns[-1] if turns else Turn()
    calls = sum(len(t.calls) for t in turns)
    final_turn_calls = len(last.calls)
    seconds = sum(r.get("seconds", 0) for r in records[-1:])
    if "max_tool_calls" in expect and final_turn_calls > expect["max_tool_calls"]:
        failures.append(f"{final_turn_calls} tool calls, over {expect['max_tool_calls']}")
    if "max_seconds" in expect and seconds > expect["max_seconds"]:
        failures.append(f"{seconds} s, over {expect['max_seconds']}")
    scrapes = engine_scrapes(turns, web_log)
    if expect.get("no_engine_scrape") and scrapes:
        failures.append(f"scraped a search engine: {scrapes[0]}")
    sleep = longest_sleep(turns)
    if "max_sleep_s" in expect and sleep > expect["max_sleep_s"]:
        failures.append(f"slept {sleep:g} s")
    per_url = fetches_per_url(web_log)
    searches = sum(1 for entry in web_log if entry.get("server") == "search")
    if "max_searches" in expect and searches > expect["max_searches"]:
        failures.append(f"{searches} searches, over {expect['max_searches']}")
    if "max_fetches_per_url" in expect:
        worst = max(per_url.values(), default=0)
        if worst > expect["max_fetches_per_url"]:
            failures.append(f"fetched one URL {worst} times")
    if turns:
        check_turn(last, expect, failures, "")
    for index, extra in expect.get("per_turn", {}).items():
        i = int(index)
        if i < len(turns):
            check_turn(turns[i], extra, failures, f"turn {i}: ")
    loops = [metrics.loop_report(t.answer) for t in turns]
    columns = {
        "turns": len(turns),
        "tool_calls": calls,
        "last_turn_tool_calls": final_turn_calls,
        "last_turn_seconds": seconds,
        "first_output_s": last.seconds_to_first_output(),
        "output_tokens": sum(t.output_tokens or 0 for t in turns),
        "honesty": metrics.honesty(last.answer),
        "language": metrics.language(last.answer),
        "engine_scrapes": len(scrapes),
        "searches": searches,
        "longest_sleep_s": sleep,
        "max_fetches_per_url": max(per_url.values(), default=0),
        "max_30gram_repeats": max((r["max_30gram_repeats"] for r in loops), default=0),
        "loop": any(r["loop"] for r in loops),
        "cut": [r["cut"] for r in cuts],
        "cut_as": cut_as,
    }
    return Result(not failures, failures, columns)


def decide(passes: list[bool | None]) -> bool | None:
    """A scenario's verdict over its repetitions: it fails when it fails in two of three."""
    known = [p for p in passes if p is not None]
    if not known:
        return None
    failed = sum(1 for p in known if not p)
    return failed * 3 < 2 * len(known)
