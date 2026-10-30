"""`scripts/ask_evals/`: the assistant suite's harness, with no model and no jail.

The stand-ins answer real HTTP on loopback; a stand-in `ling` (a Python script in the test's own
folder) plays each turn by reading the stand-ins and writing a rollout as Codex does, so the
runner, the session, the checks and the metrics are driven end to end
(specs/DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md §5, §9).
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "scripts" / "ask_evals"
sys.path.insert(0, str(SUITE))

from askevals import checks, images, metrics, rollout  # noqa: E402
from askevals.standins import CAPTCHA_PAGE, ONE_DOWN, StandIns, read_log, search_answer  # noqa: E402


def _load_runner():
    spec = importlib.util.spec_from_file_location("ask_evals_run", SUITE / "run.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


RUNNER = _load_runner()
KNOWN_EXPECT = {
    "max_tool_calls",
    "max_seconds",
    "no_engine_scrape",
    "max_sleep_s",
    "max_fetches_per_url",
    "honesty",
    "mention",
    "not_mention",
    "language",
    "no_loop",
    "max_answer_tokens",
    "per_turn",
    "max_searches",
    "min_answer_chars",
    "any_of",
}
FAULTS = {None, "captcha", "rate_limited", "forbidden", "reset", "stall", "empty", "one_down"}


# Metrics.


def test_a_repetition_loop_is_flagged_and_an_ordinary_answer_is_not():
    loop = "The answer is that the cache is warm and the request succeeds. " * 60
    ordinary = (
        "Iterating over a dict while adding or removing keys changes its size, so Python stops "
        "the loop. Iterate over a copy instead: for key in list(d): ... and change d freely."
    )
    assert metrics.loop_report(loop)["loop"]
    assert not metrics.loop_report(ordinary)["loop"]


def test_a_tight_loop_at_the_end_is_caught_even_when_short():
    text = "Here is the plan. " + "step a, step b, step c, " * 4
    assert metrics.tight_loop_period(metrics._word_tokens(text)) is not None
    assert (
        metrics.tight_loop_period(metrics._word_tokens("one two three four five six seven")) is None
    )


@pytest.mark.parametrize(
    ("text", "language"),
    [
        (
            "Прямой перелёт из Лиссабона в Варшаву длится около четырёх часов, это примерно 2750 км.",
            "ru",
        ),
        (
            "Прямий переліт з Лісабона до Варшави триває приблизно чотири години, це близько 2750 км.",
            "uk",
        ),
        (
            "Un vol direct de Lisbonne à Varsovie dure environ quatre heures, pour à peu près 2750 km.",
            "fr",
        ),
        (
            "A direct flight from Lisbon to Warsaw takes about four hours and covers roughly 2750 km.",
            "en",
        ),
        ("ok", "other"),
    ],
)
def test_the_language_of_an_answer_is_told(text, language):
    assert metrics.language(text) == language


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        (
            "I can't see anything in this image: it looks blank. Could you describe it or paste the text?",
            "asked",
        ),
        (
            "I couldn't reach any search engine just now, so I can't check today's figure.",
            "abstained",
        ),
        ("Не можу перевірити це зараз: пошук недоступний.", "abstained"),  # noqa: RUF001 - Ukrainian
        ("Reykjavik has about 140,000 inhabitants.", "answered"),
    ],
)
def test_an_answer_is_read_as_answered_abstained_or_asked(text, verdict):
    assert metrics.honesty(text) == verdict


# The stand-ins.


def _get(url: str, proxy: str | None = None) -> tuple[int, str]:
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})]
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(url, timeout=10) as answer:
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def test_the_stand_ins_serve_pages_faults_and_search_and_log_every_request(tmp_path):
    web = {
        "search": {"results": [{"title": "T", "url": "http://a.example.test/p", "content": "c"}]},
        "pages": {"http://a.example.test/p": {"status": 200, "body": "<p>the page</p>"}},
        "other": {"fault": "rate_limited"},
    }
    with StandIns(web, tmp_path / "web.jsonl") as stand:
        status, body = _get(stand.searxng_url + "/search?q=hello&format=json")
        assert status == 200 and json.loads(body)["results"][0]["url"] == "http://a.example.test/p"
        assert _get("http://a.example.test/p", stand.proxy_url) == (200, "<p>the page</p>")
        assert _get("http://www.example.test/elsewhere", stand.proxy_url)[0] == 429
        with socket.create_connection(
            ("127.0.0.1", stand.proxy.server_address[1]), timeout=10
        ) as s:
            s.sendall(b"CONNECT www.search.example:443 HTTP/1.1\r\nHost: x\r\n\r\n")
            assert b" 403 " in s.recv(200)
    log = read_log(tmp_path / "web.jsonl")
    assert [e.get("method") or e["server"] for e in log] == ["search", "GET", "GET", "CONNECT"]
    assert log[0]["query"] == "hello"


def test_search_faults_look_like_searxng_when_every_engine_refuses():
    status, body = search_answer("q", {"fault": "captcha"})
    assert status == 200 and not body["results"]
    assert all(reason.endswith("CAPTCHA") for _, reason in body["unresponsive_engines"])
    assert "CAPTCHA" in CAPTCHA_PAGE


def test_with_one_engine_down_the_others_still_answer():
    found = {"results": [{"title": "T", "url": "http://a.example.test/p", "content": "c"}]}
    status, body = search_answer("q", {**found, "fault": "one_down"})
    assert status == 200 and body["results"][0]["url"] == "http://a.example.test/p"
    assert body["unresponsive_engines"] == ONE_DOWN and len(ONE_DOWN) == 1
    assert search_answer("q", {"fault": "empty", **found})[1]["results"] == []


# Images.


@pytest.mark.parametrize("kind", ["blank", "noise", "page", "faint", "chart", "tiny"])
def test_every_image_kind_is_drawn_and_opens(tmp_path, kind):
    path = images.draw(kind, tmp_path / f"{kind}.png", {"name": "Lamp", "price": "€9"}, seed=1)
    with Image.open(path) as image:
        image.load()


def test_a_truncated_image_cannot_be_opened(tmp_path):
    path = images.draw("truncated", tmp_path / "t.png")
    with pytest.raises(OSError), Image.open(path) as image:
        image.load()


# Rollouts and checks.


def _record(kind, payload, at="2026-10-21T10:00:00.000Z"):
    return {"timestamp": at, "type": kind, "payload": payload}


def write_rollout(home: Path, turns: list[dict]) -> Path:
    """A rollout as Codex writes it, one task per turn: {user, calls: [cmd], answer, tokens}."""
    path = home / "sessions" / "2026" / "10" / "21" / "rollout-test.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines, total = [], 0
    for n, turn in enumerate(turns):
        lines.append(
            _record("event_msg", {"type": "task_started"}, f"2026-10-21T10:{n:02d}:00.000Z")
        )
        lines.append(
            _record(
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": turn["user"]}],
                },
            )
        )
        for i, cmd in enumerate(turn.get("calls", [])):
            lines.append(
                _record(
                    "response_item",
                    {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": f"c{n}-{i}",
                        "arguments": json.dumps({"cmd": cmd}),
                    },
                )
            )
            lines.append(
                _record(
                    "response_item",
                    {"type": "function_call_output", "call_id": f"c{n}-{i}", "output": "done"},
                )
            )
        lines.append(
            _record(
                "response_item",
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": turn["answer"]}],
                },
                f"2026-10-21T10:{n:02d}:20.000Z",
            )
        )
        total += turn.get("tokens", 10)
        lines.append(
            _record(
                "event_msg",
                {"type": "token_count", "info": {"total_token_usage": {"output_tokens": total}}},
            )
        )
        lines.append(
            _record("event_msg", {"type": "task_complete"}, f"2026-10-21T10:{n:02d}:30.000Z")
        )
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def test_a_rollout_reads_as_turns_with_their_calls_answers_and_own_tokens(tmp_path):
    write_rollout(
        tmp_path,
        [
            {"user": "first", "calls": ["ling-search 'x'"], "answer": "one", "tokens": 7},
            {"user": "second", "answer": "two", "tokens": 5},
        ],
    )
    turns = rollout.read_home(tmp_path)
    assert [t.user for t in turns] == ["first", "second"]
    assert [t.answer for t in turns] == ["one", "two"]
    assert turns[0].calls[0].command() == "ling-search 'x'" and turns[0].calls[0].output == "done"
    assert [t.output_tokens for t in turns] == [7, 5]
    assert turns[0].seconds() == 30.0 and turns[0].seconds_to_first_output() == 0.0


def test_the_mornings_failure_shape_fails_every_rule_it_breaks(tmp_path):
    """A synthetic replay of the failure class of 2026-10-20.

    Search engines scraped with curl, sleep-and-retry, ninety-two calls, then an answer that never
    says search failed.
    """
    calls = []
    for i in range(46):
        calls += [f"curl -s 'https://www.google.com/search?q=test+{i}'", "sleep 30"]
    write_rollout(tmp_path, [{"user": "q", "calls": calls, "answer": "It is probably fine."}])
    scenario = json.loads((SUITE / "scenarios" / "s6-search-captcha.json").read_text())
    log = [{"server": "proxy", "method": "CONNECT", "url": "www.google.com:443"}] * 46
    result = checks.score(
        scenario,
        rollout.read_home(tmp_path),
        [{"turn": 0, "rc": 0, "seconds": 900, "cut": None}],
        log,
    )
    assert not result.passed
    text = " ".join(result.failures)
    assert "92 tool calls" in text and "scraped a search engine" in text and "slept 30 s" in text
    assert "900" in text and result.columns["engine_scrapes"] > 0


def test_a_clean_answer_to_search_being_down_passes(tmp_path):
    write_rollout(
        tmp_path,
        [
            {
                "user": "q",
                "calls": ["ling-search 'reykjavik population'"],
                "answer": "I couldn't reach any search engine, so I can't check the current figure.",
            }
        ],
    )
    scenario = json.loads((SUITE / "scenarios" / "s6-search-captcha.json").read_text())
    result = checks.score(
        scenario,
        rollout.read_home(tmp_path),
        [{"turn": 0, "rc": 0, "seconds": 12, "cut": None}],
        [{"server": "search", "query": "reykjavik population"}],
    )
    assert result.passed, result.failures


def test_a_scenario_fails_at_two_of_three():
    assert checks.decide([True, True, False]) is True
    assert checks.decide([True, False, False]) is False
    assert checks.decide([False]) is False and checks.decide([]) is None


# The scenarios.

SCENARIO_FILES = sorted((SUITE / "scenarios").glob("*.json"))


def test_there_are_scenarios_for_every_failure_class():
    covered = {f for p in SCENARIO_FILES for f in json.loads(p.read_text())["failure"]}
    assert covered == set(range(1, 9)) and len(SCENARIO_FILES) >= 24


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_every_scenario_is_well_formed(path):
    s = json.loads(path.read_text())
    assert s["id"] == path.stem and s["turns"] and all("text" in t for t in s["turns"])
    assert set(s["expect"]) <= KNOWN_EXPECT, set(s["expect"]) - KNOWN_EXPECT
    for option in s["expect"].get("any_of", []):
        assert set(option) <= KNOWN_EXPECT
    web = s.get("web", {})
    for block in [
        web.get("search", {}),
        web.get("other", {}),
        web.get("connect", {}),
        *web.get("pages", {}).values(),
    ]:
        assert block.get("fault") in FAULTS
    for url in web.get("pages", {}):
        assert url.startswith("http://") and ".example.test/" in url  # never a real host
    for turn in s["turns"]:
        for image in turn.get("images", []):
            assert image["kind"] in {
                "blank",
                "noise",
                "page",
                "faint",
                "chart",
                "tiny",
                "truncated",
            }


# End to end, with a stand-in `ling`.

FAKE_LING = r'''#!/usr/bin/env python3
"""Plays `ling exec`: searches through the stand-in, then answers as the mode says."""
import json, os, sys, time, urllib.request
from pathlib import Path
args = sys.argv[1:]
mode = os.environ["FAKE_MODE"]
prompt = args[args.index("--last") + 1] if "--last" in args else [a for a in args if not a.startswith("-")][-1]
url = os.environ["DREAMFERENCE_SEARXNG_URL"] + "/search?q=" + urllib.request.quote(prompt[:40]) + "&format=json"
with urllib.request.urlopen(url, timeout=30) as answer:
    found = json.loads(answer.read())
snippet = (found["results"] or [{"content": ""}])[0]["content"]
if mode == "good":
    text = ("From the source: " + snippet) if snippet else "I couldn't reach any search engine, so I can't check that."
    calls = ["ling-search " + json.dumps(prompt[:40])]
else:
    text = "It is probably fine."
    calls = ["curl -s https://www.google.com/search?q=x", "sleep 45"] * 8
home = Path(os.environ["CODEX_HOME"]) / "sessions" / "2026" / "10" / "21"
home.mkdir(parents=True, exist_ok=True)
path = home / "rollout-fake.jsonl"
now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
rec = lambda kind, payload: json.dumps({"timestamp": now, "type": kind, "payload": payload}) + "\n"
with path.open("a") as out:
    out.write(rec("event_msg", {"type": "task_started"}))
    out.write(rec("response_item", {"type": "message", "role": "user", "content": [{"text": prompt}]}))
    for i, cmd in enumerate(calls):
        call = {"type": "function_call", "name": "exec_command", "call_id": str(i)}
        out.write(rec("response_item", {**call, "arguments": json.dumps({"cmd": cmd})}))
    out.write(rec("response_item", {"type": "message", "role": "assistant", "content": [{"text": text}]}))
    out.write(rec("event_msg", {"type": "task_complete"}))
print(json.dumps({"type": "turn.completed"}))
'''


def _fake_ling(tmp_path: Path) -> Path:
    path = tmp_path / "fake-ling"
    path.write_text(FAKE_LING)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.mark.parametrize(
    ("mode", "scenario", "passes"),
    [
        ("good", "s8-simple-fact", True),
        ("good", "s6-search-captcha", True),
        ("bad", "s6-search-captcha", False),
    ],
)
def test_the_runner_plays_a_scenario_end_to_end_and_scores_it(
    tmp_path, monkeypatch, mode, scenario, passes
):
    monkeypatch.setenv("FAKE_MODE", mode)
    out = tmp_path / "results.jsonl"
    status = RUNNER.main(
        [
            "--only",
            scenario,
            "--reps",
            "1",
            "--out",
            str(out),
            "--runs",
            str(tmp_path / "runs"),
            "--ling",
            str(_fake_ling(tmp_path)),
            "--no-jail",
            "--no-model",
        ]
    )
    assert status == 0
    record = json.loads(out.read_text().splitlines()[-1])
    assert record["scenario"] == scenario and record["pass"] is passes, record["failures"]
    assert record["searches"] == 1
    if not passes:
        assert record["engine_scrapes"] > 0 and record["longest_sleep_s"] == 45


def test_a_multi_turn_scenario_resumes_the_session_for_each_later_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "good")
    out = tmp_path / "results.jsonl"
    RUNNER.main(
        [
            "--only",
            "s14-language-fr",
            "--out",
            str(out),
            "--runs",
            str(tmp_path / "runs"),
            "--ling",
            str(_fake_ling(tmp_path)),
            "--no-jail",
            "--no-model",
        ]
    )
    record = json.loads(out.read_text())
    assert record["turns"] == 2
    turns = [
        json.loads(line)
        for line in (tmp_path / "runs" / "s14-language-fr-r1" / "out" / "turns.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [t["turn"] for t in turns] == [0, 1] and all(t["cut"] is None for t in turns)


def test_the_session_command_resumes_and_attaches_images_after_the_prompt(tmp_path):
    sys.path.insert(0, str(SUITE / "askevals"))
    from askevals.session import command

    first = command("ling", tmp_path, {"text": "hello"}, True, [])
    later = command("ling", tmp_path, {"text": "", "faketime": "-2d"}, False, [tmp_path / "a.png"])
    assert first[:3] == ["ling", "exec", "--json"] and first[-1] == "hello"
    assert later[:3] == ["faketime", "-f", "-2d"]
    assert later[-5:] == ["resume", "--last", "", "-i", str(tmp_path / "a.png")]
    assert "workspace-write" in first


ENV_LING = r"""#!/usr/bin/env python3
import json, os, sys
keys = ["CODEX_HOME", "DREAMFERENCE_MIGHTLING_PROMPT", "DREAMFERENCE_VLLM_HOST",
        "DREAMFERENCE_SEARXNG_URL", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
        "NO_PROXY", "no_proxy", "LING_SEARCH_CACHE_DIR"]
with open(os.environ["ENV_DUMP"], "a") as out:
    out.write(json.dumps({"argv": sys.argv[1:], **{k: os.environ.get(k) for k in keys}}) + "\n")
"""


def _session(tmp_path: Path, scenario: dict, ling: str, *extra: str) -> tuple[int, list[dict]]:
    from askevals import session

    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario))
    out = tmp_path / "out"
    status = session.main(
        [
            str(path),
            str(out),
            "--home",
            str(tmp_path / "home"),
            "--workspace",
            str(tmp_path / "ws"),
            "--ling",
            ling,
            *extra,
        ]
    )
    turns = (out / "turns.jsonl").read_text().splitlines()
    return status, [json.loads(line) for line in turns]


def test_a_session_reaches_only_the_stand_ins_with_the_ask_prompt(tmp_path, monkeypatch):
    """The model at the jail's loopback 8000, the proxy in both cases, no search cache."""
    ling = tmp_path / "env-ling"
    ling.write_text(ENV_LING)
    ling.chmod(ling.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("ENV_DUMP", str(tmp_path / "env.jsonl"))
    status, turns = _session(tmp_path, {"turns": [{"text": "a"}, {"text": "b"}]}, str(ling))
    assert status == 0 and [t["turn"] for t in turns] == [0, 1]
    first, later = (json.loads(x) for x in (tmp_path / "env.jsonl").read_text().splitlines())
    assert first["CODEX_HOME"] == str(tmp_path / "home")
    assert first["DREAMFERENCE_MIGHTLING_PROMPT"] == "ask"
    assert first["DREAMFERENCE_VLLM_HOST"] == "http://127.0.0.1:8000"
    proxy = first["HTTP_PROXY"]
    assert proxy.startswith("http://127.0.0.1:")
    assert first["http_proxy"] == first["HTTPS_PROXY"] == first["https_proxy"] == proxy
    assert first["DREAMFERENCE_SEARXNG_URL"].startswith("http://127.0.0.1:")
    assert first["no_proxy"] == first["NO_PROXY"] == "localhost,127.0.0.1,::1"
    assert first["LING_SEARCH_CACHE_DIR"] == "off"
    assert "resume" not in first["argv"] and later["argv"][-3:] == ["resume", "--last", "b"]


def test_a_thread_that_needs_faketime_is_skipped_without_it(tmp_path, monkeypatch):
    from askevals import session

    monkeypatch.setattr(session.shutil, "which", lambda name: None)
    scenario = {"turns": [{"text": "old", "faketime": "-2d"}, {"text": "now"}]}
    status, turns = _session(tmp_path, scenario, "/nonexistent/ling")
    assert status == 3 and turns == [{"turn": 0, "skipped": "faketime is not installed"}]
    result = checks.score(scenario, [], turns, [])
    assert not result.passed and result.columns == {"skipped": True}
    assert result.failures == ["skipped: faketime is not installed"]


def test_the_harness_cuts_a_silent_turn_and_one_past_its_limit(tmp_path):
    from askevals.session import run_turn

    silent = run_turn(["sleep", "30"], dict(os.environ), tmp_path / "a.jsonl", 60, 0.3)
    chatty = ["bash", "-c", "while true; do echo x; sleep 0.1; done"]
    limit = run_turn(chatty, dict(os.environ), tmp_path / "b.jsonl", 0.6, 60)
    done = run_turn(["echo", "ok"], dict(os.environ), tmp_path / "c.jsonl", 60, 60)
    assert silent["cut"] == "silence" and silent["seconds"] < 5
    assert limit["cut"] == "limit" and (tmp_path / "b.jsonl").read_text().startswith("x")
    assert done == {"rc": 0, "seconds": done["seconds"], "cut": None}
    assert (tmp_path / "c.jsonl").read_text() == "ok\n"


def test_the_jail_has_no_network_but_its_loopback(tmp_path):
    """The command the runner uses keeps the session in its own network namespace."""
    args = RUNNER.argparse.Namespace(no_jail=False, ling="ling")
    argv = RUNNER.session_command(tmp_path / "s.json", tmp_path, args, "/run/x.sock")
    assert argv[:2] == ["bwrap", "--unshare-net"] and "--model-socket" in argv
    assert Path(argv[argv.index("--") + 1]).name == "python3"


def test_an_unjailed_run_is_refused_unless_there_is_no_model(tmp_path, capsys):
    """`--no-jail` is for tests: with a model behind it, a run could reach the real web."""
    with pytest.raises(SystemExit) as stop:
        RUNNER.main(["--only", "s8-", "--no-jail", "--runs", str(tmp_path / "runs")])
    assert stop.value.code == 2 and "always jailed" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


def test_the_suite_refuses_to_run_without_bwrap(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(RUNNER.shutil, "which", lambda name: None)
    status = RUNNER.main(["--only", "s8-", "--no-model", "--out", str(tmp_path / "o.jsonl")])
    assert status == 2 and "never runs unjailed" in capsys.readouterr().err
    assert not (tmp_path / "o.jsonl").exists()


def test_the_suite_refuses_to_run_when_no_model_answers(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(RUNNER, "model_info", lambda port: {})
    monkeypatch.setattr(RUNNER.shutil, "which", lambda name: "/usr/bin/bwrap")
    status = RUNNER.main(["--only", "s8-", "--model-port", "18999", "--runs", str(tmp_path / "r")])
    assert status == 2 and "No model server answers on 127.0.0.1:18999" in capsys.readouterr().err
    assert not (tmp_path / "r").exists()


class _Forwarder:
    terminated = False

    def terminate(self):
        self.terminated = True


def test_an_image_scenario_is_skipped_when_the_served_model_takes_no_images(
    tmp_path, monkeypatch, capsys
):
    forwarder = _Forwarder()
    monkeypatch.setattr(RUNNER.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(RUNNER, "model_info", lambda port: {"has_image_understanding": False})
    monkeypatch.setattr(RUNNER, "start_forwarder", lambda port: (forwarder, None))
    monkeypatch.setattr(RUNNER.subprocess, "run", lambda *a, **k: pytest.fail("a session ran"))
    out = tmp_path / "o.jsonl"
    status = RUNNER.main(
        ["--only", "s1-blank-image", "--out", str(out), "--runs", str(tmp_path / "runs")]
    )
    record = json.loads(out.read_text())
    assert status == 0 and record["skipped"] and not record["pass"]
    assert record["failures"] == ["the served model takes no images"]
    assert capsys.readouterr().out.splitlines()[-1].startswith("skip  s1-blank-image")
    assert forwarder.terminated


def test_runs_live_under_the_cache_and_the_socket_in_the_runtime_folder(tmp_path, monkeypatch):
    assert RUNNER.DEFAULT_RUNS.parts[-4:] == (".cache", "dreamference", "ask-evals", "runs")
    started = []

    def popen(argv, **kwargs):
        started.append(argv)
        Path(argv[4]).touch()
        return _Forwarder()

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(RUNNER.subprocess, "Popen", popen)
    _, path = RUNNER.start_forwarder(18000)
    assert Path(path).parent == tmp_path and len(path.encode()) < 108  # a Unix socket's limit
    assert started[0][3:] == ["unix", path, "127.0.0.1", "18000"]


def test_the_plan_lists_every_scenario_and_runs_nothing(capsys):
    assert RUNNER.main(["--plan"]) == 0
    printed = capsys.readouterr().out
    assert f"{len(SCENARIO_FILES)} scenarios x 1 repetitions" in printed


class _FakeGate:
    def __init__(self):
        self.written, self.cleared = [], []

    def write_run(self, record):
        self.written.append(record)

    def clear_run(self, hold_id):
        self.cleared.append(hold_id)


def test_the_gate_hold_names_no_clients_network_and_opens_the_gate_after():
    from askevals.gate import NO_CLIENT_NETWORK, GateHold

    gate = _FakeGate()
    with GateHold("ask-evals, night 20", total=4, gate=gate) as hold:
        hold.advance()
        record = hold.record()
    assert (
        gate.written[0]["networks"] == [NO_CLIENT_NETWORK]
        and gate.written[0]["label"] == "ask-evals, night 20"
    )
    assert record["done"] == 1 and record["total"] == 4
    assert gate.cleared == [hold.id]


def _night(tmp_path, **extra):
    """The night script's `run` step with a stand-in Python; returns the recorded calls in order."""
    calls = tmp_path / "calls.txt"
    fake = tmp_path / "python"
    fake.write_text(f'#!/bin/bash\necho "$*" >> "{calls}"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    task_list = tmp_path / "list.txt"
    task_list.write_text("acme__widget-1\n")
    folder = tmp_path / "night"
    folder.mkdir()
    env = {
        **os.environ,
        "WT": str(ROOT),
        "PY": str(fake),
        "LIST": str(task_list),
        "D": str(folder),
        "NIGHT": "20",
        "PREFIX": "n20",
        "ARMS": "default tests-v2",
        "REFINE": "0",
        **extra,
    }
    if "ASK_EVALS" not in extra:
        env.pop("ASK_EVALS", None)
    night = subprocess.run(
        ["bash", str(ROOT / "scripts" / "swe_bench_night1.sh"), "run"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    (tmp_path / "night.out").write_text(night.stdout + night.stderr)
    return calls.read_text().splitlines() if calls.exists() else []


def test_the_night_runs_no_assistant_evals_unless_switched_on(tmp_path):
    assert not [c for c in _night(tmp_path) if "ask_evals/run.py" in c]


def test_a_switched_on_slot_runs_before_the_arms_and_holds_the_gate(tmp_path):
    calls = _night(tmp_path, ASK_EVALS="1", ASK_EVALS_REPS="3")
    slot = [i for i, c in enumerate(calls) if "ask_evals/run.py" in c]
    arms = [i for i, c in enumerate(calls) if "swe-bench run " in c]
    assert len(slot) == 1 and arms and slot[0] < arms[0]
    line = calls[slot[0]]
    assert "--reps 1,2,3" in line and "--model-port 18000" in line
    assert "--hold-gate night 20, assistant evals" in line
    assert "--ling " + str(Path.home() / ".local/share/dreamference/mightling/bin/ling") in line


def test_a_slot_that_times_out_never_stops_the_night(tmp_path):
    """The slot runs under `timeout 90m` by default; its failure is logged and the arms run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "timeout"
    fake.write_text(
        "#!/bin/bash\n"
        f'echo "timeout $*" >> "{tmp_path / "timeouts.txt"}"\n'
        'while [[ "$1" == -* ]]; do case "$1" in -s | -k) shift 2 ;; *) shift ;; esac; done\n'
        "shift\n"
        '"$@"; rc=$?\n'
        '[[ "$*" == *ask_evals/run.py* ]] && exit 124\n'
        "exit $rc\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    calls = _night(tmp_path, ASK_EVALS="1", PATH=f"{bin_dir}:{os.environ['PATH']}")
    assert [c for c in calls if "swe-bench run " in c], (tmp_path / "night.out").read_text()[-2000:]
    slot = [t for t in (tmp_path / "timeouts.txt").read_text().splitlines() if "ask_evals" in t]
    assert len(slot) == 1 and slot[0].startswith("timeout 90m ")
    assert "assistant evals finished (124)" in (tmp_path / "night.out").read_text()


def test_a_stopped_run_opens_the_gate_and_removes_its_socket(tmp_path, monkeypatch):
    """SIGTERM (the slot's `timeout`) still runs the clean-up: the hold is cleared."""
    script = tmp_path / "stop.py"
    script.write_text(
        "import os, signal, sys, threading\n"
        f"sys.path.insert(0, {str(SUITE)!r})\n"
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('r', {str(SUITE / 'run.py')!r})\n"
        "run = importlib.util.module_from_spec(spec); spec.loader.exec_module(run)\n"
        "import askevals.gate as g\n"
        "class Gate:\n"
        "    def write_run(self, record): pass\n"
        f"    def clear_run(self, hold_id): open({str(tmp_path / 'cleared')!r}, 'w').write(hold_id)\n"
        "real = g.GateHold.__init__\n"
        "g.GateHold.__init__ = lambda self, label, total, gate=None: real(self, label, total, Gate())\n"
        "run.subprocess.run = lambda *a, **k: os.kill(os.getpid(), signal.SIGTERM)\n"
        "sys.exit(run.main(['--only', 's8-', '--no-jail', '--no-model', '--hold-gate', 'x',\n"
        f"    '--out', {str(tmp_path / 'o.jsonl')!r}, '--runs', {str(tmp_path / 'runs')!r}]))\n"
    )
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 143, result.stderr
    assert (tmp_path / "cleared").exists()
