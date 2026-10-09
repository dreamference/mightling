"""Fresh SWE-bench Verified tasks for the A/B rounds of the failure analysis.

specs/DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md §6.5: the proposals were written from the 100
tasks of ``sample-100.txt``, so they are measured on tasks outside it. This script finds them:

``candidates``
    The Verified tasks with an arm64 image that are neither in ``sample-100.txt`` nor validated or
    rejected on this machine yet, in the order of ``sha256(instance_id)`` (fixed, and not chosen by
    difficulty, as ``sample-25.txt`` was).

``validate``
    Validates candidates in order the way ``ling-admin swe-bench setup --validate`` does, until
    ``--count`` of them (default 60) have a result, in batches of at most ``--batch`` and never more
    than the disk above the reserve holds, and removes after each batch the images that batch
    pulled (an image is about 2.3 GB unpacked). A candidate skipped for disk or a failed pull is
    tried once more.

``draw``
    Draws ``--count`` tasks (default 50) from the validated tasks outside ``sample-100.txt`` with a
    recorded seed, stratified to ``sample-100.txt``'s difficulty mix (easy, medium, hard by the share
    of strong published submissions that solve each task, from the comparison script's data), and
    writes them with a header that says how they were drawn. Commit the list before any arm runs.

Run it with the repository's code on ``PYTHONPATH`` and the project's virtualenv::

    PYTHONPATH=<checkout> .venv/bin/python scripts/swe_bench_fresh.py candidates
    PYTHONPATH=<checkout> .venv/bin/python scripts/swe_bench_fresh.py validate --count 60
    PYTHONPATH=<checkout> .venv/bin/python scripts/swe_bench_fresh.py draw --count 50 --seed 20261009

It refuses to validate while another benchmark run holds the runner lock or a named systemd unit is
active, since both would pull and grade on the same Docker.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Final, List, Optional

from dreamference.swe_bench import SweBenchDocker, SweBenchEvaluator, SweBenchHarness, SweBenchImages, SweBenchSettings
from dreamference.swe_bench import swe_bench_settings

DATASET: Final[str] = "verified"
SAMPLE_100: Final[Path] = swe_bench_settings.CACHE_DIR / "sample-100.txt"
DEFAULT_LIST: Final[Path] = swe_bench_settings.CACHE_DIR / "fresh-50.txt"
CANDIDATES: Final[Path] = swe_bench_settings.CACHE_DIR / "fresh-candidates.txt"
LIVE_UNIT: Final[str] = "puffin-swe-im100-refine"
# An instance image unpacked (2.1 to 2.4 GB measured), with a margin.
IMAGE_BYTES: Final[int] = int(2.5 * 1024 ** 3)

# Tiers of the failure analysis (§2): the share of the strong submissions (60% or more overall)
# that solve a task.
EASY: Final[float] = 0.75
MEDIUM: Final[float] = 0.40


def read_ids(path: Path) -> List[str]:
    """Instance ids from a list file, one per line, ``#`` comments ignored."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    return [line.split("#", 1)[0].strip() for line in lines if line.split("#", 1)[0].strip()]


def order_key(instance_id: str) -> str:
    return hashlib.sha256(instance_id.encode()).hexdigest()


def candidates() -> List[str]:
    """Verified tasks with an arm64 image, outside sample-100, not yet validated or rejected here."""
    tags = SweBenchImages.tags()
    used = set(read_ids(SAMPLE_100))
    known = set(SweBenchImages.validated()) | set(SweBenchImages.rejected())
    ids = [row["instance_id"] for row in SweBenchHarness.rows(DATASET)]
    fresh = [i for i in ids if i not in used and i not in known and SweBenchImages.image_for(i, tags)]
    return sorted(fresh, key=order_key)


def busy(unit: str) -> Optional[str]:
    """Why validation must wait, or None."""
    from dreamference.night_shift import NightShiftQueue
    holder = NightShiftQueue.runner_holder()
    if holder:
        return f"{holder} holds the runner lock"
    active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit], check=False)
    if active.returncode == 0:
        return f"the unit {unit} is still running"
    return None


def validate(count: int, batch: int, unit: str, disk_reserve: Optional[str]) -> int:
    """
    Validates candidates, in order, until ``count`` of them have a recorded result (validated or
    rejected). Each batch is no larger than the disk above the reserve holds, its images are
    removed afterwards, and a candidate that could not be validated yet (no disk, no pull) is tried
    once more later.
    """
    if not SweBenchHarness.rows(DATASET) or SweBenchHarness.installed_version() != swe_bench_settings.HARNESS_VERSION:
        print("❌ The harness or the dataset snapshot is missing: run `ling-admin swe-bench setup` first.")
        return 1
    reason = busy(unit)
    if reason:
        print(f"❌ Not now: {reason}.")
        return 1
    from dreamference.night_shift.night_shift_host import GIB, NightShiftHost
    settings = SweBenchSettings()
    if disk_reserve:
        settings.disk_reserve = disk_reserve
    reserve = NightShiftHost.parse_size(settings.disk_reserve)
    order = candidates()
    CANDIDATES.write_text(f"# {len(order)} candidates, sha256 order, {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n"
                          + "".join(f"{i}\n" for i in order))
    print(f"🔎 Validating until {count} candidate(s) have a result, at most {batch} at a time, "
          f"keeping {settings.disk_reserve} of disk free (candidates: {CANDIDATES})", flush=True)
    tags = SweBenchImages.tags()
    good, bad, tries = [], {}, {}
    while len(good) + len(bad) < count:
        queue = [i for i in order if i not in good and i not in bad and tries.get(i, 0) < 2]
        if not queue:
            break
        free = shutil.disk_usage(swe_bench_settings.CACHE_DIR).free
        room = int((free - reserve) // IMAGE_BYTES)
        if room < 1:
            print(f"⚠️  Stopped: {free / GIB:.0f} GiB free and the reserve is {reserve / GIB:.0f} GiB, no room for an "
                  "image. Free some (`ling-admin swe-bench clean --images`) or pass --disk-reserve.")
            break
        chunk = queue[:min(batch, room, count - len(good) - len(bad))]
        images = {i: SweBenchImages.image_for(i, tags) for i in chunk}
        absent = [image for image in images.values() if image and SweBenchDocker.image_digest(image) is None]
        outcome = SweBenchEvaluator.validate(DATASET, chunk, settings)
        removed = SweBenchImages.remove([image for image in absent if SweBenchDocker.image_digest(image)])
        for instance_id in chunk:
            problem = outcome.get(instance_id, "not validated yet: no result")
            if problem is None:
                good.append(instance_id)
            elif problem.startswith("not validated yet"):
                tries[instance_id] = tries.get(instance_id, 0) + 1
                print(f"   {instance_id}: {problem} (attempt {tries[instance_id]})", flush=True)
            else:
                bad[instance_id] = problem
                print(f"   {instance_id}: {problem}", flush=True)
        print(f"   {len(good)} validated, {len(bad)} rejected so far; {removed} image(s) removed again", flush=True)
    fresh = [i for i in SweBenchImages.validated() if i not in set(read_ids(SAMPLE_100))]
    print(f"✅ {len(good)} validated and {len(bad)} rejected now; {len(fresh)} validated tasks lie outside "
          "sample-100.txt.")
    return 0 if len(good) + len(bad) >= count else 1


def strong_rates(experiments: Path) -> Optional[Dict[str, float]]:
    """Each Verified task's share of strong submissions that solve it, or None without the data."""
    script = Path(__file__).with_name("swe_bench_compare.py")
    if not (experiments / "evaluation" / "verified").is_dir() or not script.is_file():
        return None
    spec = importlib.util.spec_from_file_location("swe_bench_compare", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules["swe_bench_compare"] = module  # dataclasses look their module up by name
    spec.loader.exec_module(module)
    submissions, all_ids, _ = module.load_submissions(experiments)
    strong = [s for s in submissions if s.overall >= 0.6]
    if not strong:
        return None
    return {t: sum(t in s.resolved for s in strong) / len(strong) for t in all_ids}


def tier(rate: float) -> str:
    return "easy" if rate >= EASY else "medium" if rate >= MEDIUM else "hard"


def allocate(total: int, weights: Dict[str, int]) -> Dict[str, int]:
    """Splits ``total`` in proportion to ``weights`` by largest remainder."""
    whole = sum(weights.values()) or 1
    exact = {k: total * v / whole for k, v in weights.items()}
    counts = {k: int(x) for k, x in exact.items()}
    for k in sorted(exact, key=lambda k: exact[k] - counts[k], reverse=True)[:total - sum(counts.values())]:
        counts[k] += 1
    return counts


FAILURES_PURPOSE: Final[str] = ("the failure analysis's A/B rounds\n"
                                "# (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md §6.5)")


def draw(count: int, seed: int, out: Path, experiments: Path, purpose: str = FAILURES_PURPOSE) -> int:
    used = set(read_ids(SAMPLE_100))
    if not used:
        print(f"❌ {SAMPLE_100} is missing: the fresh tasks are defined against it.")
        return 1
    pool = sorted(i for i in SweBenchImages.validated() if i not in used)
    if len(pool) < count:
        print(f"❌ Only {len(pool)} validated tasks lie outside sample-100.txt; {count} are wanted. Validate more first.")
        return 1
    rng = random.Random(seed)
    rates = strong_rates(experiments)
    lines = [f"# {count} fresh SWE-bench Verified tasks for {purpose}, drawn {time.strftime('%Y-%m-%d')}",
             f"# from the {len(pool)} tasks validated on this machine outside sample-100.txt, seed {seed}."]
    if rates is None:
        chosen = sorted(rng.sample(pool, count))
        lines.append("# Not stratified: the comparison data (--experiments) was not found.")
    else:
        mix: Dict[str, int] = {"easy": 0, "medium": 0, "hard": 0}
        for instance_id in used:
            mix[tier(rates.get(instance_id, 0.0))] += 1
        wanted = allocate(count, mix)
        by_tier: Dict[str, List[str]] = {"easy": [], "medium": [], "hard": []}
        for instance_id in pool:
            by_tier[tier(rates.get(instance_id, 0.0))].append(instance_id)
        chosen = []
        short = 0
        for name in ("easy", "medium", "hard"):
            take = min(wanted[name], len(by_tier[name]))
            short += wanted[name] - take
            chosen += rng.sample(by_tier[name], take)
        if short:
            rest = sorted(set(pool) - set(chosen))
            chosen += rng.sample(rest, short)
        chosen = sorted(chosen)
        got = {name: sum(1 for i in chosen if tier(rates.get(i, 0.0)) == name) for name in mix}
        lines.append(f"# Stratified to sample-100.txt's mix (easy/medium/hard {mix['easy']}/{mix['medium']}/"
                     f"{mix['hard']} of 100; easy = solved by >= 75% of strong submissions, hard < 40%):")
        lines.append(f"# wanted {wanted['easy']}/{wanted['medium']}/{wanted['hard']}, drawn "
                     f"{got['easy']}/{got['medium']}/{got['hard']}"
                     + (f" ({short} filled from other tiers, which were short)" if short else "") + ".")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n" + "".join(f"{i}\n" for i in chosen))
    print(f"✅ {len(chosen)} tasks written to {out}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("candidates", help="print the candidates, in validation order")
    listing.add_argument("--count", type=int, default=60)
    checking = commands.add_parser("validate", help="validate candidates until --count have a result")
    checking.add_argument("--count", type=int, default=60)
    checking.add_argument("--batch", type=int, default=10)
    checking.add_argument("--disk-reserve", default=None,
                          help="disk to keep free, e.g. 60G (default: [swe_bench] disk_reserve, 100G)")
    checking.add_argument("--wait-for-unit", default=LIVE_UNIT,
                          help=f"refuse while this systemd user unit is active (default {LIVE_UNIT})")
    drawing = commands.add_parser("draw", help="draw the fresh task list")
    drawing.add_argument("--count", type=int, default=50)
    drawing.add_argument("--seed", type=int, required=True)
    drawing.add_argument("--out", type=Path, default=DEFAULT_LIST)
    drawing.add_argument("--purpose", default=FAILURES_PURPOSE,
                         help="what the list is for, written into its header (default: the failure analysis)")
    drawing.add_argument("--experiments", type=Path,
                         default=swe_bench_settings.CACHE_DIR / "experiments")
    args = parser.parse_args(argv)
    if args.command == "candidates":
        found = candidates()
        print(f"# {len(found)} candidates; the first {min(args.count, len(found))}:")
        print("\n".join(found[:args.count]))
        return 0
    if args.command == "validate":
        return validate(args.count, args.batch, args.wait_for_unit, args.disk_reserve)
    return draw(args.count, args.seed, args.out, args.experiments, args.purpose)


if __name__ == "__main__":
    sys.exit(main())
