"""The best-of-N chooser, version 1 (specs/DREAMFERENCE_BACKLOG.md items 1 and 6).

The one implementation of chooser v1, used by Night Shift's best-of-N (`NightShiftBestOfN`) and by
the chooser test on disk (`scripts/swe_bench_chooser_test.py`, which imports it), so the chooser a
SWE-bench night measures is the chooser Night Shift runs. Measured 2026-10-13 and 2026-10-15:
best-of-3 33/44 and 33.0/44 against single runs of 29-31; version 2 (32.33/44) was not adopted.

1. **Regression filter.** A candidate that breaks the repository's existing tests, or whose patch
   did not apply, is dropped. If every candidate breaks something, all go on to the judge.
2. **Pairwise judge.** The survivors are compared in pairs by the served model: one call per pair
   shows the issue (for Night Shift, the task) and the two diffs, never the agent's reasoning, and
   rates both 1-10. A win scores 1, a tie 0.5, weighted by the rating gap, max(|r_a - r_b| / 9, 0.1).
   Pairing: random disjoint pairs until every candidate has been compared twice, then Swiss rounds
   among neighbours in score within a window of 8. With three candidates that is the three pairs.
3. **The pick** is the candidate with the most points (`chosen` lists every one tied at the top).
"""

import json
import random
import re
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, Final

from dreamference.night_shift.chooser_candidate import ChooserCandidate

# A patch longer than this is cut, with a note, so two patches and the issue fit the context.
MAX_PATCH_CHARS: Final[int] = 40_000
MAX_ISSUE_CHARS: Final[int] = 20_000

# V1's tournament (arXiv 2603.04304): every candidate compared at least twice, then Swiss
# rounds among neighbours in score; the weight floor of a decided comparison.
MIN_COMPARISONS: Final[int] = 2
SWISS_WINDOW: Final[int] = 8
WEIGHT_FLOOR: Final[float] = 0.1
RATING: Final[re.Pattern] = re.compile(r"RATING_([AB]):\s*\**\s*(\d+(?:\.\d+)?)")

# The judge's sampling, as measured: Qwen3.8's recommended settings and room for its thinking.
JUDGE_MAX_TOKENS: Final[int] = 16384
JUDGE_TEMPERATURE: Final[float] = 0.6
JUDGE_TOP_P: Final[float] = 0.95
JUDGE_TIMEOUT_S: Final[float] = 900.0

SYSTEM_PROMPT: Final[str] = (
    "You are an expert code reviewer. Compare two solutions to a programming problem and rate their "
    "correctness. The problem is a GitHub issue in a Python repository; each solution is a patch "
    "(a diff) written to fix it. Neither patch has been run. A correct patch fixes the behaviour the "
    "issue describes and breaks no existing behaviour. If both look correct, prefer the more robust "
    "and fault-tolerant one. Ignore style, comments and the tests a patch adds."
)

USER_TEMPLATE: Final[str] = """<issue>
{issue}
</issue>

<solution_A>
{a}
</solution_A>

<solution_B>
{b}
</solution_B>

Rate the correctness of each solution from 1 (wrong) to 10 (certainly correct). End your answer with exactly these two lines:
RATING_A: <1-10>
RATING_B: <1-10>"""

Compare = Callable[[ChooserCandidate, ChooserCandidate], tuple[float, float] | None]


class NightShiftChooser:
    """Chooser v1: the regression filter, then V1's pairwise tournament. Stateless classmethods."""

    @classmethod
    def clip(cls, text: str, limit: int) -> str:
        """Cuts a text at `limit` characters, saying so."""
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n[... cut: {len(text) - limit} more characters]"

    @classmethod
    def ratings(cls, answer: str) -> tuple[float, float] | None:
        """The last `RATING_A` and `RATING_B` of an answer, clamped to 1-10; None when either is missing."""
        found: dict[str, float] = {}
        for side, value in RATING.findall(answer):
            found[side] = min(10.0, max(1.0, float(value)))
        return (found["A"], found["B"]) if "A" in found and "B" in found else None

    @classmethod
    def messages(cls, issue: str, a: str, b: str) -> list[dict[str, str]]:
        """One judge call's messages.

        Args:
            issue: The issue (or the Night Shift task's text).
            a: Solution A's diff.
            b: Solution B's diff.

        Returns:
            List[Dict[str, str]]: The system and user messages.
        """
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(
                    issue=cls.clip(issue, MAX_ISSUE_CHARS),
                    a=cls.clip(a, MAX_PATCH_CHARS), b=cls.clip(b, MAX_PATCH_CHARS))}]

    @classmethod
    def pairings(cls, ids: list[str], scores: dict[str, float], played: set, counts: dict[str, int],
                 rng: random.Random) -> list[tuple[str, str]]:
        """The next round of V1's tournament: random disjoint pairs while some candidate has fewer
        than two comparisons, then a Swiss round (neighbours in score, within a window of 8). A pair
        is never played twice.

        Returns:
            List[Tuple[str, str]]: The round's pairs; empty when the tournament is over.
        """
        def unplayed(a: str, b: str) -> bool:
            return frozenset((a, b)) not in played

        if any(counts[i] < MIN_COMPARISONS for i in ids):
            order = sorted(ids, key=lambda i: (counts[i], rng.random()))
            round_pairs, used = [], set()
            for a in order:
                if a in used or counts[a] >= MIN_COMPARISONS:
                    continue
                partner = next((b for b in order if b != a and b not in used and unplayed(a, b)), None)
                if partner is not None:
                    round_pairs.append((a, partner))
                    used.update((a, partner))
            if round_pairs:
                return round_pairs
        ranked = sorted(ids, key=lambda i: -scores[i])
        round_pairs, used = [], set()
        for position, a in enumerate(ranked):
            if a in used:
                continue
            partner = next((b for b in ranked[position + 1:position + 1 + SWISS_WINDOW]
                            if b not in used and unplayed(a, b)), None)
            if partner is not None:
                round_pairs.append((a, partner))
                used.update((a, partner))
        return round_pairs

    @classmethod
    def tournament(cls, candidates: Sequence[ChooserCandidate], compare: Compare,
                   rng: random.Random, swiss_rounds: int = 1) -> dict[str, float]:
        """V1's pairwise tournament over the candidates.

        Args:
            candidates: The survivors of the regression filter.
            compare: One judge call: (A, B) -> (rating of A, rating of B), or None when unreadable.
            rng: The pairing's randomness.
            swiss_rounds: Swiss rounds after every candidate has its two comparisons.

        Returns:
            Dict[str, float]: Candidate id to points: per comparison, 1 / 0.5 / 0 for a win, tie or
            loss, times max(|rating gap| / 9, 0.1); an unreadable answer scores 0.5 each at the floor.
        """
        by_id = {c.id: c for c in candidates}
        ids = sorted(by_id)
        scores = dict.fromkeys(ids, 0.0)
        counts = dict.fromkeys(ids, 0)
        played: set = set()
        swiss = 0
        while len(ids) > 1:
            before_swiss = any(counts[i] < MIN_COMPARISONS for i in ids)
            if not before_swiss:
                if swiss >= swiss_rounds:
                    break
                swiss += 1
            round_pairs = cls.pairings(ids, scores, played, counts, rng)
            if not round_pairs:
                break
            for a, b in round_pairs:
                played.add(frozenset((a, b)))
                counts[a] += 1
                counts[b] += 1
                rated = compare(by_id[a], by_id[b])
                if rated is None:
                    scores[a] += 0.5 * WEIGHT_FLOOR
                    scores[b] += 0.5 * WEIGHT_FLOOR
                    continue
                weight = max(abs(rated[0] - rated[1]) / 9, WEIGHT_FLOOR)
                outcome = 1.0 if rated[0] > rated[1] else 0.0 if rated[0] < rated[1] else 0.5
                scores[a] += weight * outcome
                scores[b] += weight * (1 - outcome)
        return scores

    @classmethod
    def choose(cls, candidates: Sequence[ChooserCandidate], compare: Compare,
               rng: random.Random, swiss_rounds: int = 1) -> dict[str, Any]:
        """The chooser: the regression filter, then the pairwise judge over the survivors. It is given
        no verdict on the issue.

        Returns:
            Dict[str, Any]: `stage` (`none`, `only candidate`, `regression filter`, `judge`, or
            `judge, all broke`), `survivors`, `points`, and `chosen` (the top candidate ids).
        """
        if not candidates:
            return {"stage": "none", "survivors": [], "points": {}, "chosen": []}
        if len(candidates) == 1:
            return {"stage": "only candidate", "survivors": [candidates[0].id], "points": {},
                    "chosen": [candidates[0].id]}
        survivors = [c for c in candidates if not c.breaks]
        if len(survivors) == 1:
            return {"stage": "regression filter", "survivors": [survivors[0].id], "points": {},
                    "chosen": [survivors[0].id]}
        stage = "judge" if survivors else "judge, all broke"
        pool = survivors or list(candidates)
        points = cls.tournament(pool, compare, rng, swiss_rounds)
        top = max(points.values())
        return {"stage": stage, "survivors": [c.id for c in survivors], "points": points,
                "chosen": sorted(i for i, p in points.items() if p == top)}

    @classmethod
    def server_judge(cls, server: str, issue: str, seed: int | None = None,
                     timeout: float = JUDGE_TIMEOUT_S,
                     log: list[dict[str, Any]] | None = None) -> Compare:
        """A judge that asks the model server, as Night Shift runs it.

        The server is the night run's own (loopback or a paired node by name), reached without any
        proxy. A call that fails or cannot be read counts as unreadable (0.5 each at the floor), so
        a judge fault never stops the night; `log` gets one record per call either way.

        Args:
            server: The model server's base URL, with or without `/v1`.
            issue: What the candidates were written for (the task's text).
            seed: Pins the sampling, when the server takes a seed.
            timeout: Seconds to wait for one answer.
            log: A list each call's record is appended to.

        Returns:
            Compare: (A, B) -> (rating of A, rating of B), or None.
        """
        base = server.rstrip("/").removesuffix("/v1") + "/v1"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        model: dict[str, str] = {}

        def request(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
            data = None if payload is None else json.dumps(payload).encode()
            req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
            with opener.open(req, timeout=timeout) as response:
                return json.loads(response.read())

        def compare(a: ChooserCandidate, b: ChooserCandidate) -> tuple[float, float] | None:
            record: dict[str, Any] = {"a": a.id, "b": b.id}
            try:
                if "id" not in model:
                    model["id"] = str(((request("/models").get("data") or [{}])[0]).get("id") or "")
                payload: dict[str, Any] = {"model": model["id"], "max_tokens": JUDGE_MAX_TOKENS,
                                           "temperature": JUDGE_TEMPERATURE, "top_p": JUDGE_TOP_P,
                                           "messages": cls.messages(issue, a.patch, b.patch)}
                if seed is not None:
                    payload["seed"] = seed
                choice = (request("/chat/completions", payload).get("choices") or [{}])[0]
                message = choice.get("message") or {}
                answer = str(message.get("content") or message.get("reasoning_content") or "")
                rated = cls.ratings(answer)
                record.update({"ratings": rated, "answer_tail": answer[-600:]})
            except Exception as error:  # noqa: BLE001 - a judge fault must never stop a night run
                rated = None
                record["error"] = str(error)[:300]
            if log is not None:
                log.append(record)
            return rated

        return compare
