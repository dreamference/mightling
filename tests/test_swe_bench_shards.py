"""`SweBenchShards`: one list split into K balanced shards, the same on every machine.

Pure functions over task ids and the frozen tiers of `SweBenchDifficulty`: nothing here reads the
machine's SWE-bench state, starts a container or reaches the model server.
"""

from collections import Counter

import pytest

from dreamference.swe_bench import SweBenchDifficulty, SweBenchShards
from dreamference.swe_bench.swe_bench_difficulty import EASY, HARD, MEDIUM, TIERS, UNKNOWN


def repo_of(instance_id):
    return instance_id.rsplit("-", 1)[0].replace("__", "/")


def verified_list(easy=89, medium=24, hard=37):
    """150 real Verified tasks, sample-100's mix scaled, taken in id order from each tier."""
    return sorted(EASY)[:easy] + sorted(MEDIUM)[:medium] + sorted(HARD)[:hard]


def spread(shards, key):
    counts = [Counter(key(i) for i in shard) for shard in shards]
    keys = set().union(*counts)
    return max(max(c[k] for c in counts) - min(c[k] for c in counts) for k in keys)


def test_the_frozen_tiers_cover_verified_once_each():
    assert len(EASY) + len(MEDIUM) + len(HARD) == len(EASY | MEDIUM | HARD) == 500
    assert SweBenchDifficulty.tier(sorted(HARD)[0]) == "hard"
    assert SweBenchDifficulty.tier("acme__widget-1") == UNKNOWN


@pytest.mark.parametrize("text, expected", [("1/3", (1, 3)), ("3/3", (3, 3)), ("1/1", (1, 1)),
                                            ("0/3", None), ("4/3", None), ("a/3", None), ("1/3/4", None),
                                            ("3", None), ("", None), (None, None), ("1/65", None)])
def test_a_shard_is_written_i_of_k(text, expected):
    assert SweBenchShards.parse(text) == expected


@pytest.mark.parametrize("count", [2, 3, 4, 5])
def test_shards_partition_the_list_and_balance_size_tier_and_repository(count):
    ids = verified_list()
    repos = {i: repo_of(i) for i in ids}
    shards = SweBenchShards.split(ids, repos, count)
    assert sorted(i for shard in shards for i in shard) == sorted(ids)
    assert max(map(len, shards)) - min(map(len, shards)) <= 1
    assert spread(shards, SweBenchDifficulty.tier) <= 1
    assert spread(shards, repos.get) <= 2


def test_the_split_depends_on_the_set_of_ids_alone():
    ids = verified_list()
    repos = {i: repo_of(i) for i in ids}
    once = SweBenchShards.split(ids, repos, 3)
    assert SweBenchShards.split(list(reversed(ids)) + ids[:5], repos, 3) == once
    assert [SweBenchShards.pick(ids, repos, n, 3) for n in (1, 2, 3)] == once
    assert SweBenchShards.digest(list(reversed(ids))) == SweBenchShards.digest(ids)


def test_three_thirds_of_sample_100s_mix_each_keep_it():
    ids = verified_list()
    thirds = SweBenchShards.split(ids, {i: repo_of(i) for i in ids}, 3)
    mixes = sorted(tuple(Counter(SweBenchDifficulty.tier(i) for i in third)[t] for t in TIERS) for third in thirds)
    # 89/24/37 in three: each third 30/8/12 to one task.
    assert all(abs(e - 30) <= 1 and m == 8 and abs(h - 12) <= 1 for e, m, h in mixes), mixes


def test_one_shard_is_the_whole_list_and_more_shards_than_tasks_leave_some_empty():
    ids = ["acme__widget-1", "acme__widget-2", "beta__gadget-7"]
    repos = {i: repo_of(i) for i in ids}
    assert SweBenchShards.split(ids, repos, 1) == [sorted(ids)]
    shards = SweBenchShards.split(ids, repos, 4)
    assert sorted(map(len, shards)) == [0, 1, 1, 1]
    # The two of one repository go to different shards.
    assert all(len({repos[i] for i in shard}) == len(shard) for shard in shards)


def test_shard_lists_are_written_with_a_header_and_the_same_tasks_as_pick(tmp_path):
    ids = verified_list()
    repos = {i: repo_of(i) for i in ids}
    source = tmp_path / "fresh-150k.txt"
    source.write_text("# header\n" + "".join(f"{i}\n" for i in ids))
    written = SweBenchShards.write(source, ids, repos, 3, tmp_path / "out")
    assert [path.name for path in written] == ["fresh-150k-1of3.txt", "fresh-150k-2of3.txt", "fresh-150k-3of3.txt"]
    for index, path in enumerate(written, start=1):
        lines = path.read_text().splitlines()
        header = [line for line in lines if line.startswith("#")]
        assert header[0].startswith(f"# Shard {index} of 3 of fresh-150k.txt (150 tasks, sha256 {SweBenchShards.digest(ids)[:12]}")
        assert f"--subset fresh-150k.txt --shard {index}/3" in "\n".join(header)
        assert any(line.startswith("# Mix: easy/medium/hard ") for line in header)
        assert [line for line in lines if not line.startswith("#")] == SweBenchShards.pick(ids, repos, index, 3)


def test_balance_by_spreads_another_lists_tasks_evenly_and_keeps_tier_and_repository_balanced():
    ids = verified_list()
    repos = {i: repo_of(i) for i in ids}
    # A "fresh" part concentrated in a few repositories and the easy tier, as fresh-61j is in fresh-150k.
    fresh = [i for i in ids if SweBenchDifficulty.tier(i) == "easy"][:45] + sorted(MEDIUM)[:7] + sorted(HARD)[:9]
    plain = SweBenchShards.split(ids, repos, 3)
    groups = SweBenchShards.groups(ids, {"fresh.txt": fresh})
    balanced = SweBenchShards.split(ids, repos, 3, groups)
    assert sorted(i for shard in balanced for i in shard) == sorted(ids)
    held = [len(set(shard) & set(fresh)) for shard in balanced]
    assert max(held) - min(held) <= 1, held
    assert max(map(len, balanced)) - min(map(len, balanced)) <= 1
    assert spread(balanced, SweBenchDifficulty.tier) <= 1
    assert spread(balanced, repos.get) <= 2
    # Without a stratum the split is `run --shard`'s, unchanged.
    assert SweBenchShards.split(ids, repos, 3, None) == plain


def test_groups_name_the_lists_that_hold_each_task():
    groups = SweBenchShards.groups(["a", "b", "c"], {"x.txt": ["a", "b"], "y.txt": ["b"]})
    assert groups == {"a": "x.txt", "b": "x.txt+y.txt", "c": ""}


def test_balanced_shard_lists_say_they_are_not_run_shards(tmp_path):
    ids = verified_list()
    repos = {i: repo_of(i) for i in ids}
    fresh = ids[:61]
    source = tmp_path / "fresh-150k.txt"
    source.write_text("".join(f"{i}\n" for i in ids))
    written = SweBenchShards.write(source, ids, repos, 3, tmp_path, {"fresh-61j.txt": fresh})
    groups = SweBenchShards.groups(ids, {"fresh-61j.txt": fresh})
    for index, path in enumerate(written, start=1):
        text = path.read_text()
        header = "\n".join(line for line in text.splitlines() if line.startswith("#"))
        assert "--balance-by fresh-61j.txt" in header and "NOT the tasks of" in header
        assert f"--shard {index}/3" in header and "of fresh-61j.txt's 61" in header
        assert [line for line in text.splitlines() if not line.startswith("#")] == \
            SweBenchShards.split(ids, repos, 3, groups)[index - 1]
