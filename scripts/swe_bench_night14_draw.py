"""Night 14's task list, ``docs/dev/swe-bench/fresh-150k.txt``: the fresh tasks plus reused ones.

The owner's decision of 2026-10-20: night 14 runs about 150 tasks, every one of ``fresh-61j.txt`` (the
validated tasks no list of nights 1-13 holds) and the rest reused under the rotation rule (SWE_BENCH
spec §16) from the lists of nights three or more back, the mismatch list still out. ``swe_bench_fresh.py
draw`` cannot make this list (it leaves out sample-100, whose tasks the fresh part is, and stratifies by
difficulty only), so this script records how it was made and makes it again, byte for byte::

    PYTHONPATH=<checkout> .venv/bin/python scripts/swe_bench_night14_draw.py --seed 20261020 \
        --out ~/.cache/dreamference/swe-bench/fresh-150k.txt

The draw: a task may be reused when the last night that ran it is 10 or earlier, or 11 where nights 1-10
cannot fill its difficulty tier; never one of nights 12 and 13, the mismatch list or the smoke task.
After a ``random.Random(seed)`` shuffle of that pool, each pick is the first task of a tier still short
of sample-100's mix (over the whole list) whose repository is furthest under its target: Verified's
share over the repositories with a validated task left, times the count, less the fresh part's.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import random
import sys
import textwrap
from pathlib import Path
from typing import Dict, Final, List, Optional

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
LISTS: Final[Path] = ROOT / "docs" / "dev" / "swe-bench"
# The list each night ran, by the night scripts' logs: nights 4-6 ran night2-no-mismatch, and night 7's
# fresh-50c never ran (its smoke failed), though its tasks count as night 7's.
NIGHT_LISTS: Final[Dict[str, List[int]]] = {
    "fresh-50": [1], "night2-no-mismatch": [2, 4, 5, 6], "fresh-50b": [3], "fresh-50c": [7],
    "fresh-50d": [8], "fresh-50e": [9], "fresh-50f": [10], "fresh-30g": [11], "fresh-50h": [12],
    "fresh-50i": [13]}
REUSE_UP_TO: Final[int] = 10
FILL_FROM: Final[int] = 11
SHORT_NAMES: Final[Dict[str, str]] = {
    "django/django": "django", "sympy/sympy": "sympy", "sphinx-doc/sphinx": "sphinx",
    "scikit-learn/scikit-learn": "scikit-learn", "astropy/astropy": "astropy", "pytest-dev/pytest": "pytest",
    "pylint-dev/pylint": "pylint", "psf/requests": "requests", "mwaskom/seaborn": "seaborn",
    "pallets/flask": "flask", "matplotlib/matplotlib": "matplotlib", "pydata/xarray": "xarray"}


def load_fresh():
    """The ``swe_bench_fresh`` script as a module, for its list reader, tiers and the validated set."""
    spec = importlib.util.spec_from_file_location("swe_bench_fresh", ROOT / "scripts" / "swe_bench_fresh.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(count: int, seed: int) -> str:
    """Returns the list file's text: the header, then the ids sorted."""
    fresh_module = load_fresh()
    from dreamference.swe_bench import SweBenchHarness, SweBenchImages, swe_bench_settings
    cache = swe_bench_settings.CACHE_DIR

    def ids(name: str) -> List[str]:
        # The committed list, else the runner's copy (fresh-50h and fresh-50i were committed on their
        # nights' branches); a list found in neither would silently widen the pool, so it stops the draw.
        found = fresh_module.read_ids(LISTS / f"{name}.txt") or fresh_module.read_ids(cache / f"{name}.txt")
        if not found:
            raise SystemExit(f"❌ {name}.txt is neither in {LISTS} nor in {cache}.")
        return found

    last: Dict[str, int] = {}
    for name, nights in NIGHT_LISTS.items():
        for instance_id in ids(name):
            last[instance_id] = max(last.get(instance_id, 0), max(nights))
    rates = fresh_module.strong_rates(cache / "experiments")
    if rates is None:
        raise SystemExit("❌ The comparison data is missing: the draw is stratified by it.")

    def tier(instance_id: str) -> str:
        return fresh_module.tier(rates.get(instance_id, 0.0))

    rows = {row["instance_id"]: row for row in SweBenchHarness.rows(fresh_module.DATASET)}
    validated = set(SweBenchImages.validated())
    out = set(ids("verified-mismatch-exclude")) | set(fresh_module.read_ids(cache / "smoke-instance.txt"))
    fresh = ids("fresh-61j")
    pool = sorted(i for i in validated if i in last and last[i] <= FILL_FROM and i not in out and i not in fresh)
    older = [i for i in pool if last[i] <= REUSE_UP_TO]
    wanted = fresh_module.allocate(count, {"easy": 59, "medium": 16, "hard": 25})
    fresh_tiers = collections.Counter(tier(i) for i in fresh)
    tier_target = {t: max(0, wanted[t] - fresh_tiers[t]) for t in wanted}
    repos = {rows[i]["repo"] for i in fresh} | {rows[i]["repo"] for i in pool}
    verified = collections.Counter(row["repo"] for row in rows.values())
    share = sum(verified[r] for r in repos)
    repo_target = {r: count * verified[r] / share - sum(1 for i in fresh if rows[i]["repo"] == r) for r in repos}
    rng = random.Random(seed)
    order = sorted(pool)
    rng.shuffle(order)
    chosen: List[str] = []
    got_tier: collections.Counter = collections.Counter()
    got_repo: collections.Counter = collections.Counter()
    while len(chosen) < count - len(fresh):
        left = [i for i in order if i not in chosen]
        open_tiers = [t for t in tier_target if got_tier[t] < tier_target[t]] or list(tier_target)
        best: Optional[tuple] = None
        for instance_id in left:
            t = tier(instance_id)
            if t not in open_tiers:
                continue
            if last[instance_id] > REUSE_UP_TO and any(tier(j) == t and last[j] <= REUSE_UP_TO for j in left):
                continue
            key = repo_target[rows[instance_id]["repo"]] - got_repo[rows[instance_id]["repo"]]
            if best is None or key > best[0]:
                best = (key, instance_id)
        chosen.append(best[1])
        got_tier[tier(best[1])] += 1
        got_repo[rows[best[1]]["repo"]] += 1
    every = sorted(fresh + chosen)
    tiers = collections.Counter(tier(i) for i in every)
    in_repo = collections.Counter(rows[i]["repo"] for i in every)
    filled = [i for i in chosen if last[i] > REUSE_UP_TO]
    mix = ", ".join(f"{SHORT_NAMES[r]} {in_repo[r]} ({100 * in_repo[r] / count:.1f}%; Verified {100 * verified[r] / len(rows):.1f}%)"
                    for r in sorted(verified, key=lambda r: (-in_repo[r], -verified[r])))
    lines = [
        f"# {count} SWE-bench Verified tasks for night 14 (the owner's rule of 2026-10-20: about 150 a night), drawn 2026-10-20, seed {seed}.",
        f"# All {len(fresh)} of fresh-61j.txt (the validated tasks no list of nights 1-13 holds, outside the mismatch list and the smoke task) and {len(chosen)} reused under the",
        "# rotation rule (SWE_BENCH spec §16: lists of nights three or more back): never a task of night 12's fresh-50h.txt or night 13's fresh-50i.txt,",
        f"# verified-mismatch-exclude.txt (68) or the smoke task; a task last used in night 11 (fresh-30g.txt) only where nights 1-10 could not fill its tier "
        f"({len(filled)}, all {'/'.join(sorted({tier(i) for i in filled}))}).",
        f"# Pool: {len(older)} validated tasks last used in nights 1-10, {len(pool) - len(older)} more last used in night 11.",
        f"# Drawn: a seeded shuffle (random.Random(seed)), then {len(chosen)} picks, each the first task of a tier still short whose repository is furthest under its target",
        f"# (Verified's share over the repositories with a validated task left, times {count}, less fresh-61j's).",
        "# Stratified to sample-100.txt's mix (easy/medium/hard 59/16/25 of 100; easy = solved by >= 75% of strong submissions, hard < 40%):",
        f"# wanted {wanted['easy']}/{wanted['medium']}/{wanted['hard']} of {count}; fresh-61j gives "
        f"{fresh_tiers['easy']}/{fresh_tiers['medium']}/{fresh_tiers['hard']}, the reused {got_tier['easy']}/{got_tier['medium']}/{got_tier['hard']}; "
        f"got {tiers['easy']}/{tiers['medium']}/{tiers['hard']}.",
        "# Repositories: " + mix + ".",
        "# No validated task of matplotlib or xarray is left, and of scikit-learn only one, so django stands above its Verified share.",
        "# Seen before: the 61 fresh tasks in sample-100.txt only (the rounds before night 1); each reused task by the last night that ran it (nights 4-6 ran",
        "# night2-no-mismatch.txt; night 7's fresh-50c.txt never ran, its smoke failed):",
    ]
    names = {n: name for name, nights in NIGHT_LISTS.items() for n in nights}
    by_night: Dict[int, List[str]] = collections.defaultdict(list)
    for instance_id in chosen:
        by_night[last[instance_id]].append(instance_id)
    for night in sorted(by_night, reverse=True):
        body = f"night {night} ({names[night]}.txt), {len(by_night[night])}: " + ", ".join(sorted(by_night[night]))
        lines += ["#   " + line for line in textwrap.wrap(body, 150, subsequent_indent="  ", break_on_hyphens=False)]
    return "\n".join(lines) + "\n" + "".join(f"{i}\n" for i in every)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.write_text(build(args.count, args.seed))
    print(f"✅ {args.count} tasks written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
