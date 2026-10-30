"""`scripts/swe_bench_chooser_fanout.py`: chooser v3's prompts and aggregation, with a fake server."""

import importlib.util
import json
import math
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent

PATCH_A = """diff --git a/pkg/core.py b/pkg/core.py
--- a/pkg/core.py
+++ b/pkg/core.py
@@ -10,3 +10,3 @@ def f():
-    return 1
+    return GOOD
"""
PATCH_B = """diff --git a/pkg/core.py b/pkg/core.py
--- a/pkg/core.py
+++ b/pkg/core.py
@@ -90,2 +90,2 @@ def g():
-    x = 1
+    x = 2
diff --git a/pkg/new.py b/pkg/new.py
new file mode 100644
--- /dev/null
+++ b/pkg/new.py
@@ -0,0 +1,2 @@
+def h():
+    pass
diff --git a/tests/test_core.py b/tests/test_core.py
--- a/tests/test_core.py
+++ b/tests/test_core.py
@@ -5,1 +5,2 @@
+    assert True
"""


@pytest.fixture
def fanout():
    """The script's module, loaded beside chooser v1's, both dropped afterwards."""
    names = ("swe_bench_chooser_fanout", "swe_bench_chooser_test")
    spec = importlib.util.spec_from_file_location(names[0], ROOT / "scripts" / f"{names[0]}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up while they are built
    spec.loader.exec_module(module)
    yield module
    for name in names:
        sys.modules.pop(name, None)


def candidate(cid, patch="", breaks=False):
    """A stand-in for `ChooserCandidate`: an id, a patch and the regression filter's verdict."""
    return SimpleNamespace(id=cid, patch=patch, breaks=breaks, runs=[cid])


def logprob_choice(alternatives, content=None):
    """A chat completion's choice whose first token has these (token, probability) alternatives."""
    top = [{"token": t, "logprob": math.log(p)} for t, p in alternatives]
    return {
        "message": {"content": content if content is not None else alternatives[0][0]},
        "logprobs": {"content": [{**top[0], "top_logprobs": top}]},
    }


def test_edited_ranges_skip_new_files_and_read_the_pre_image(fanout):
    ranges = fanout.edited_ranges(PATCH_B)
    assert ranges == {"pkg/core.py": [(90, 91)], "tests/test_core.py": [(5, 5)]}


def test_windows_widen_clamp_and_merge(fanout):
    assert fanout.windows([(10, 12), (60, 61)], total=100, margin=40) == [(1, 100)]
    assert fanout.windows([(10, 12), (95, 96)], total=100, margin=5) == [(5, 17), (90, 100)]
    assert fanout.windows([(500, 510)], total=100, margin=5) == []


def test_source_context_puts_library_files_first_and_respects_the_limit(fanout):
    files = {
        "pkg/core.py": "\n".join(f"line {n}" for n in range(1, 201)),
        "tests/test_core.py": "t\n" * 10,
    }
    text = fanout.source_context([PATCH_A, PATCH_B], files.get)
    assert text.index('path="pkg/core.py"') < text.index('path="tests/test_core.py"')
    assert "   10 line 10" in text and "  130 line 130" in text and "  140 line 140" not in text
    core_only = fanout.source_context([PATCH_A, PATCH_B], {"pkg/core.py": files["pkg/core.py"]}.get)
    short = fanout.source_context([PATCH_A, PATCH_B], files.get, limit=len(core_only) + 5)
    assert "tests/test_core.py" not in short and "left out for length" in short
    assert fanout.source_context([PATCH_A], lambda path: None) == ""


def test_every_probe_shares_the_prefix_byte_for_byte(fanout):
    context = fanout.Context(
        issue="It breaks.",
        source="<file/>",
        order=[candidate("a", PATCH_A), candidate("b", PATCH_B)],
    )
    probes = fanout.build_probes(context, ["f() returns GOOD"], random.Random(0))
    prompts = [json.dumps(fanout.messages(context, p.text)) for p in probes]
    prefix = json.dumps(fanout.messages(context, ""))[
        :-3
    ]  # up to the question, before the closing quote and braces
    assert all(p.startswith(prefix) for p in prompts)
    assert len({p.key for p in probes}) == len(probes)
    per_candidate = sum(len(f) for _, _, f in fanout.ATTRIBUTE_PROBES) + 1
    assert len(probes) == 2 * per_candidate + 2 * len(fanout.PAIRWISE_PROBES)
    pairs = {p.pair for p in probes if p.kind == "pairwise"}
    assert pairs == {("a", "b"), ("b", "a")}  # both orders, so the labels' position bias cancels


def test_rotation_renumbers_the_candidates(fanout):
    order = fanout.rotate([candidate("a"), candidate("b"), candidate("c")], 1)
    assert fanout.Context(issue="", source="", order=order).numbers == {
        "b": "1",
        "c": "2",
        "a": "3",
    }


def test_yes_no_answers_sum_case_and_spacing_and_fall_back_to_the_vote(fanout):
    probe = fanout.Probe(key="k", kind="attribute", text="?")
    choice = logprob_choice([("Yes", 0.5), ("yes", 0.1), (" No", 0.2), ("The", 0.2)])
    assert fanout.answer_of(probe, choice) == pytest.approx(0.75)
    assert fanout.answer_of(probe, {"message": {"content": "No."}}) == 0.0
    assert fanout.answer_of(probe, {"message": {"content": "Maybe"}}) is None


def test_pairwise_answers_read_the_two_labels(fanout):
    probe = fanout.Probe(key="k", kind="pairwise", text="?", pair=("a", "b"), labels=("2", "1"))
    assert fanout.answer_of(
        probe, logprob_choice([("1", 0.6), ("2", 0.2), ("3", 0.2)])
    ) == pytest.approx(0.25)


def test_scores_count_polarity_and_both_sides_of_a_pair(fanout):
    probes = [
        fanout.Probe(key="1", kind="attribute", text="", candidate="a", polarity=1, answer=0.9),
        fanout.Probe(key="2", kind="attribute", text="", candidate="a", polarity=-1, answer=0.3),
        fanout.Probe(key="3", kind="attribute", text="", candidate="b", polarity=1, answer=None),
        fanout.Probe(key="4", kind="check", text="", candidate="b", answer=0.5),
        fanout.Probe(key="5", kind="pairwise", text="", pair=("a", "b"), answer=0.8),
        fanout.Probe(key="6", kind="pairwise", text="", pair=("b", "a"), answer=0.4),
    ]
    scores = fanout.candidate_scores(["a", "b"], probes)
    assert scores["a"]["attribute"] == pytest.approx(0.8)
    assert scores["a"]["check"] is None
    assert scores["a"]["pairwise"] == pytest.approx(0.7)
    assert scores["a"]["total"] == pytest.approx(0.75)
    assert scores["b"] == pytest.approx(
        {"attribute": None, "check": 0.5, "pairwise": 0.3, "total": 0.4}
    )
    assert fanout.top(scores) == ["a"]
    assert fanout.top({"a": {"total": 0.5}, "b": {"total": 0.5}}) == ["a", "b"]


def test_choose_runs_the_regression_filter_first(fanout):
    def judge(pool):
        raise AssertionError("the judge must not be asked")

    kept = fanout.choose([candidate("a", breaks=True), candidate("b")], judge)
    assert kept["stage"] == "regression filter" and kept["chosen"] == ["b"]
    assert fanout.choose([candidate("a")], judge)["stage"] == "only candidate"
    assert fanout.choose([], judge)["chosen"] == []
    asked = []

    def scoring(pool):
        asked.append([c.id for c in pool])
        return {"scores": {"b": {"total": 0.2}, "c": {"total": 0.7}}}

    judged = fanout.choose([candidate("a", breaks=True), candidate("b"), candidate("c")], scoring)
    assert asked == [["b", "c"]] and judged["stage"] == "fan-out" and judged["chosen"] == ["c"]
    broke = fanout.choose([candidate("b", breaks=True), candidate("c", breaks=True)], scoring)
    assert broke["stage"] == "fan-out, all broke" and broke["survivors"] == []


def test_checks_are_parsed_and_capped(fanout):
    answer = "Here:\n- one\n-two\n- \n" + "".join(f"- c{n}\n" for n in range(9))
    assert fanout.parse_checks(answer) == ["one", "c0", "c1", "c2", "c3"]


def test_cache_counters_sum_every_label(fanout):
    text = (
        "# HELP sglang:cached_tokens_total x\n"
        'sglang:cached_tokens_total{cache_source="device"} 90.0\n'
        'sglang:prompt_tokens_total{is_streaming="false"} 60.0\n'
        'sglang:prompt_tokens_total{is_streaming="true"} 40.0\n'
        'sglang:cache_hit_rate{x="1"} 0.0\n'
    )
    assert fanout.cache_counters(text) == (90.0, 100.0)


class FakeServer:
    """Answers checks with two behaviours, probes in favour of the patch holding GOOD; no socket."""

    def __init__(self):
        self.calls, self.metrics = [], [(0.0, 0.0)]
        self.model = "mightling/fake"

    def counters(self):
        cached, prompt = self.metrics[-1]
        self.metrics.append((cached + 95.0, prompt + 100.0))
        return self.metrics[-2]

    def chat(self, payload):
        self.calls.append(payload)
        question = payload["messages"][-1]["content"].rsplit("Question: ", 1)[1]
        if payload["max_tokens"] > 1:
            return {"message": {"content": "- returns GOOD\n- raises nothing"}}
        good_number = (
            "1"
            if "GOOD"
            in payload["messages"][-1]["content"]
            .split("<candidate_1>")[1]
            .split("</candidate_1>")[0]
            else "2"
        )
        if "Answer with the candidate's number" in question:
            other = "2" if good_number == "1" else "1"
            return logprob_choice([(good_number, 0.8), (other, 0.2)])
        positive = f"candidate {good_number} " in question
        negative = any(
            word in question
            for word in ("unrelated", "not ask", "fail", "break", "error", "only tests")
        )
        yes = 0.8 if positive != negative else 0.2
        return logprob_choice([("Yes", yes), ("No", 1 - yes)])


def test_fan_out_picks_the_good_patch_and_records_the_cache_hit(fanout):
    server = FakeServer()
    context = fanout.Context(
        issue="f must return GOOD",
        source="",
        order=[candidate("bad", PATCH_B), candidate("good", PATCH_A)],
    )
    record = fanout.fan_out(server, context, random.Random(1), workers=4)
    assert record["checks"] == ["returns GOOD", "raises nothing"]
    assert record["probes"] == len(server.calls)
    assert record["unreadable"] == 0 and record["cache_hit"] == pytest.approx(0.95)
    assert fanout.top(record["scores"]) == ["good"]
    assert all(c["chat_template_kwargs"] == {"enable_thinking": False} for c in server.calls)
    assert all(c["max_tokens"] == 1 and c["logprobs"] for c in server.calls[1:])


class FakeResponse:
    """What `urlopen` returns: a context manager whose body is one JSON answer."""

    def __init__(self, answer):
        self.body = json.dumps(answer).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


@pytest.fixture
def hosted(fanout, tmp_path, monkeypatch):
    """An OpenRouter judge with a throwaway key, its requests answered here; returns (server, sent bodies)."""
    key = tmp_path / "key"
    key.write_text("test-key\n")
    sent = []
    answers = []

    def urlopen(request, timeout):
        sent.append({"body": json.loads(request.data), "auth": request.get_header("Authorization")})
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)

    monkeypatch.setattr(fanout.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(fanout.time, "sleep", lambda seconds: None)
    server = fanout.OpenRouterServer("vendor/model", "host/fp8", key, budget=0.01)
    return server, sent, answers


def usage(cost, prompt=100, cached=80):
    return {
        "cost": cost,
        "prompt_tokens": prompt,
        "prompt_tokens_details": {"cached_tokens": cached},
    }


def test_the_hosted_judge_pins_one_provider_and_drops_the_chat_template(fanout, hosted):
    server, sent, answers = hosted
    answers.append(
        {"choices": [logprob_choice([("Yes", 0.9), ("No", 0.1)])], "usage": usage(0.004)}
    )
    choice = server.chat({**fanout.PROBE_PARAMS, "messages": [{"role": "user", "content": "?"}]})
    body = sent[0]["body"]
    assert body["model"] == "vendor/model"
    assert body["provider"] == {"order": ["host/fp8"], "allow_fallbacks": False}
    assert body["reasoning"] == {"enabled": False}
    assert "chat_template_kwargs" not in body and body["max_tokens"] == 1
    assert sent[0]["auth"] == "Bearer test-key"
    assert fanout.answer_of(
        fanout.Probe(key="k", kind="attribute", text="?"), choice
    ) == pytest.approx(0.9)
    assert server.counters() == (80.0, 100.0) and server.cost == pytest.approx(0.004)


def test_the_hosted_judge_stops_at_its_budget_and_retries_a_stall(fanout, hosted):
    server, sent, answers = hosted
    answers.extend(
        [
            OSError("timed out"),
            {"choices": [{"message": {"content": "Yes"}}], "usage": usage(0.011)},
        ]
    )
    server.chat({"messages": []})
    assert len(sent) == 2
    with pytest.raises(fanout.BudgetSpent):
        server.chat({"messages": []})


def test_a_reasoning_judge_is_read_from_the_answer_after_its_thinking(fanout, hosted):
    server, sent, answers = hosted
    server.effort, server.thinking_tokens = "minimal", 512
    top = [{"token": "Yes", "logprob": math.log(0.8)}, {"token": "No", "logprob": math.log(0.2)}]
    content = [
        {"token": "</think>", "logprob": 0.0, "top_logprobs": []},
        {"token": "**", "logprob": -0.2, "top_logprobs": []},
        {**top[0], "top_logprobs": top},
    ]
    answers.append(
        {
            "choices": [
                {"message": {"content": "**Yes.** It does."}, "logprobs": {"content": content}}
            ],
            "usage": usage(0.0),
        }
    )
    choice = server.chat({**fanout.PROBE_PARAMS, "messages": []})
    assert sent[0]["body"]["reasoning"] == {"effort": "minimal", "exclude": True}
    assert sent[0]["body"]["max_tokens"] == 513
    assert choice["message"]["content"].startswith("Yes.")
    assert fanout.answer_of(
        fanout.Probe(key="k", kind="attribute", text="?"), choice
    ) == pytest.approx(0.8)
