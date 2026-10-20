"""
One task list split into K shards for K machines (`ling-admin swe-bench run --shard I/K`, and
`swe-bench shard`, which writes the shards as lists): every shard holds the same number of tasks to
one, and as near the same mix of difficulty tiers and repositories as the list allows, so the
machines' runs are alike and `swe-bench merge` can put them back together as one run.

The split depends on the set of task ids alone (the order they are given in, the machine and the
leaderboard's data play no part: the tiers are `SweBenchDifficulty`'s, frozen), so every machine
computes the same shards from the same list.
"""

import hashlib
from collections import Counter
from pathlib import Path
from typing import Dict, Final, List, Optional, Tuple

from dreamference.swe_bench.swe_bench_difficulty import TIERS, SweBenchDifficulty

# The largest number of shards a list is split into.
MAX_SHARDS: Final[int] = 64


class SweBenchShards:
    """Splits a list of tasks into balanced shards."""

    @classmethod
    def parse(cls, text: Optional[str]) -> Optional[Tuple[int, int]]:
        """
        Reads a `--shard` value.

        Args:
            text: `I/K`, with 1 <= I <= K <= `MAX_SHARDS`.

        Returns:
            Optional[Tuple[int, int]]: (I, K), or None when the text is not one.
        """
        index_text, slash, count_text = str(text or "").strip().partition("/")
        if not slash or not index_text.isdigit() or not count_text.isdigit():
            return None
        index, count = int(index_text), int(count_text)
        return (index, count) if 1 <= index <= count <= MAX_SHARDS else None

    @classmethod
    def digest(cls, instance_ids: List[str]) -> str:
        """
        Args:
            instance_ids: A list's tasks.

        Returns:
            str: The SHA-256 of the sorted ids, one per line: what the shards of one list share.
        """
        return hashlib.sha256("".join(f"{i}\n" for i in sorted(set(instance_ids))).encode()).hexdigest()

    @classmethod
    def split(cls, instance_ids: List[str], repos: Dict[str, str], count: int,
              groups: Optional[Dict[str, str]] = None) -> List[List[str]]:
        """
        Splits tasks into `count` shards. The largest repository is dealt first, each repository's
        tasks tier by tier in the order of their ids' SHA-256; each task goes to a shard that still
        has room (the shards end `n // count` or one more), and among those to the one with the
        fewest tasks of the same repository and tier (and group), then of the same tier, then (with
        `groups`) of the same group, then of the same repository, then the fewest tasks, then the
        first.

        Args:
            instance_ids: The tasks (duplicates are counted once).
            repos: Each task's repository; a task missing from it is a repository of its own name.
            count: How many shards, at least 1.
            groups: A further stratum to balance, each task's group (`swe-bench shard --balance-by`:
                which other lists hold it); None, the split `run --shard` makes.

        Returns:
            List[List[str]]: The shards, each sorted.
        """
        ids = sorted(set(instance_ids))

        def repo(instance_id: str) -> str:
            return repos.get(instance_id, instance_id)

        def group(instance_id: str) -> str:
            return (groups or {}).get(instance_id, "")

        size = Counter(repo(i) for i in ids)
        rank = {tier: n for n, tier in enumerate(TIERS)}
        order = sorted(ids, key=lambda i: (-size[repo(i)], repo(i), rank.get(SweBenchDifficulty.tier(i), len(TIERS)),
                                           hashlib.sha256(i.encode()).hexdigest()))
        shards: List[List[str]] = [[] for _ in range(max(1, count))]
        held: Counter = Counter()
        low, extra = divmod(len(ids), len(shards))
        for instance_id in order:
            where, tier, kind = repo(instance_id), SweBenchDifficulty.tier(instance_id), group(instance_id)
            full = sum(1 for shard in shards if len(shard) > low)
            room = [n for n, shard in enumerate(shards) if len(shard) < low or (len(shard) == low and full < extra)]
            best = min(room, key=lambda n: (held[(n, kind, where, tier)], held[(n, tier)],
                                            held[(n, "group", kind)] if groups else 0, held[(n, where)],
                                            len(shards[n]), n))
            shards[best].append(instance_id)
            for key in ((best, kind, where, tier), (best, tier), (best, "group", kind), (best, where)):
                held[key] += 1
        return [sorted(shard) for shard in shards]

    @classmethod
    def pick(cls, instance_ids: List[str], repos: Dict[str, str], index: int, count: int) -> List[str]:
        """
        Args:
            instance_ids: The whole list's tasks.
            repos: Each task's repository.
            index: Which shard, from 1.
            count: Of how many.

        Returns:
            List[str]: That shard's tasks, sorted (`run --shard`'s split, with no further stratum).
        """
        return cls.split(instance_ids, repos, count)[index - 1]

    @classmethod
    def groups(cls, instance_ids: List[str], lists: Dict[str, List[str]]) -> Dict[str, str]:
        """
        Args:
            instance_ids: Tasks.
            lists: Other lists by name, each its tasks.

        Returns:
            Dict[str, str]: Each task's group: the names of the lists that hold it, joined with `+`
            (empty for a task in none of them).
        """
        held = {name: set(ids) for name, ids in lists.items()}
        return {i: "+".join(name for name in lists if i in held[name]) for i in instance_ids}

    @classmethod
    def mix(cls, instance_ids: List[str], repos: Dict[str, str]) -> str:
        """
        Args:
            instance_ids: Tasks.
            repos: Each task's repository.

        Returns:
            str: Their tiers (`easy/medium/hard a/b/c`, and `d unknown` when some are outside
            Verified) and repositories, largest first, for a list's header.
        """
        tiers = Counter(SweBenchDifficulty.tier(i) for i in instance_ids)
        known = "/".join(str(tiers[tier]) for tier in TIERS)
        unknown = sum(n for tier, n in tiers.items() if tier not in TIERS)
        where = Counter(repos.get(i, i).split("/")[-1] for i in instance_ids)
        largest_first = sorted(where.items(), key=lambda item: (-item[1], item[0]))
        repositories = ", ".join(f"{name} {n}" for name, n in largest_first)
        return f"easy/medium/hard {known}" + (f", {unknown} unknown" if unknown else "") + f"; {repositories}"

    @classmethod
    def write(cls, source: Path, instance_ids: List[str], repos: Dict[str, str], count: int,
              out_dir: Path, balance_by: Optional[Dict[str, List[str]]] = None) -> List[Path]:
        """
        Writes a list's shards as lists of their own, `<list>-<I>of<K>.txt` beside each other, each
        with a header naming the list, its digest and the shard's mix.

        Args:
            source: The list file.
            instance_ids: Its tasks.
            repos: Each task's repository.
            count: How many shards.
            out_dir: Where to write them.
            balance_by: Other lists by name: the shards also hold their tasks as evenly as the split
                can, and are then not the shards `run --shard` makes, which the header says.

        Returns:
            List[Path]: The files written, in shard order.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = source.name.removesuffix(".txt")
        groups = cls.groups(instance_ids, balance_by) if balance_by else None
        written = []
        for index, shard in enumerate(cls.split(instance_ids, repos, count, groups), start=1):
            path = out_dir / f"{stem}-{index}of{count}.txt"
            lines = [f"# Shard {index} of {count} of {source.name} ({len(set(instance_ids))} tasks, sha256 "
                     f"{cls.digest(instance_ids)[:12]} of its sorted ids): {len(shard)} tasks,"]
            if balance_by:
                held = ", ".join(f"{sum(1 for i in shard if i in set(ids))} of {name}'s {len(set(ids) & set(instance_ids))}"
                                 for name, ids in balance_by.items())
                lines += [f"# split by `ling-admin swe-bench shard --balance-by {' --balance-by '.join(balance_by)}` "
                          "(SweBenchShards), balanced by difficulty tier, by",
                          f"# membership of {', '.join(balance_by)} ({held}) and by repository. NOT the tasks of "
                          f"`swe-bench run --subset {source.name} --shard {index}/{count}`,",
                          "# which balances by tier and repository only: run this file as the list itself."]
            else:
                lines += ["# split by `ling-admin swe-bench shard` (SweBenchShards: the same tasks as `swe-bench run "
                          f"--subset {source.name} --shard {index}/{count}`),",
                          "# balanced by difficulty tier and repository."]
            lines += ["# Tiers frozen in SweBenchDifficulty; easy = solved by >= 75% of strong submissions, hard < 40%.",
                      f"# Mix: {cls.mix(shard, repos)}.",
                      f"# The whole list: {cls.mix(list(set(instance_ids)), repos)}."]
            path.write_text("\n".join(lines) + "\n" + "".join(f"{i}\n" for i in shard))
            written.append(path)
        return written
