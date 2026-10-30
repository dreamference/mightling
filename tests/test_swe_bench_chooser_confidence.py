"""`scripts/swe_bench_chooser_confidence.py`: confidence figures, selectors and one task's pass, with a fake server."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def confidence():
    """The script's module and the fan-out module it loads, both dropped afterwards."""
    names = ("swe_bench_chooser_confidence", "swe_bench_chooser_fanout", "swe_bench_chooser_test")
    spec = importlib.util.spec_from_file_location(names[0], ROOT / "scripts" / f"{names[0]}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    for name in names:
        sys.modules.pop(name, None)


def test_stats_give_the_mean_the_lowest_tenth_and_the_weakest_window(confidence):
    values = [-0.1] * 40
    values[10:14] = [-2.0] * 4
    figures = confidence.stats(values)
    assert figures["tokens"] == 40
    assert figures["sum"] == pytest.approx(-0.1 * 36 - 8.0)
    assert figures["mean"] == pytest.approx((-0.1 * 36 - 8.0) / 40)
    assert figures["low10"] == pytest.approx(-2.0)
    assert figures["win32"] == pytest.approx((-0.1 * 28 - 8.0) / 32)


def test_stats_of_a_short_or_empty_patch(confidence):
    assert confidence.stats([-1.0, -3.0])["win32"] == pytest.approx(-2.0)
    assert confidence.stats([]) == {
        "tokens": 0,
        "sum": None,
        "mean": None,
        "low10": None,
        "win32": None,
    }


def test_selectors_read_length_confidence_and_the_combination(confidence):
    record = {
        "candidates": {
            "a": {"tokens": 10, "mean": -0.2},
            "b": {"tokens": 30, "mean": -0.4},
            "c": {"tokens": 20, "mean": -0.3},
        }
    }
    assert confidence.top(confidence.selector_values(record, "shortest")) == ["a"]
    assert confidence.top(confidence.selector_values(record, "longest")) == ["b"]
    assert confidence.top(confidence.selector_values(record, "mean")) == ["a"]
    combined = confidence.selector_values(record, "mean+v3", {"a": 0.1, "b": 0.9, "c": 0.8})
    assert combined == pytest.approx({"a": 0.5, "b": 0.5, "c": 0.6875})
    assert confidence.top(combined) == ["c"]
    assert confidence.selector_values(record, "mean+v3", None) == {"a": None, "b": None, "c": None}


def test_scaling_and_ties(confidence):
    assert confidence.scaled({"a": 1.0, "b": 1.0, "c": None}) == {"a": 0.5, "b": 0.5, "c": None}
    assert confidence.top({"a": None, "b": None}) == ["a", "b"]
    assert confidence.rank_correlation([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert confidence.rank_correlation([1, 2], [1, 2]) is None


class FakeScoringServer:
    """Tokenizes by character and gives each candidate's patch a fixed log-probability per token."""

    def __init__(self, per_token):
        self.per_token = per_token
        self.calls = []

    def tokenize(self, text):
        return [ord(ch) for ch in text]

    def logprobs(self, prefix, tail):
        self.calls.append((len(prefix), "".join(map(chr, tail))))
        return [self.per_token["".join(map(chr, tail))]] * len(tail)

    def counters(self):
        return None


def test_one_pass_scores_every_candidate_behind_the_same_prefix(confidence):
    server = FakeScoringServer({"good": -0.1, "bad patch": -1.0})
    pool = [SimpleNamespace(id="g", patch="good"), SimpleNamespace(id="b", patch="bad patch")]
    found = confidence.score_pool(server, "issue and code", pool, workers=2)
    assert server.calls[0] == (len("issue and code"), "good")  # the first goes alone
    assert {length for length, _ in server.calls} == {len("issue and code")}
    assert found["tokens"] == len("issue and code")
    assert found["candidates"]["g"]["mean"] == pytest.approx(-0.1)
    assert found["candidates"]["b"]["tokens"] == len("bad patch")
    assert found["cache_hit"] is None
