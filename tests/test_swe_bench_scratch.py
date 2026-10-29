"""The host's access to an instance's scratch, which the container can fill with links."""

import contextlib
import json
import os

from dreamference.swe_bench import SweBenchRunStore
from dreamference.swe_bench.swe_bench_hooks import GATE_DIRECTORY, SweBenchHooks
from dreamference.swe_bench.swe_bench_scratch import SweBenchScratch


def plant(tmp_path):
    """A scratch whose names link out of it, as a hostile image would leave it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("private key\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "patch.diff").symlink_to("../outside/secret")
    (scratch / "linked").symlink_to("../outside")
    os.mkfifo(scratch / "fifo")
    return scratch, outside


def test_a_read_never_follows_a_link_or_waits_on_a_fifo(tmp_path):
    scratch, _ = plant(tmp_path)
    (scratch / "real.txt").write_text("mine\n")
    assert SweBenchScratch.read_text(scratch, "real.txt") == "mine\n"
    assert SweBenchScratch.read_text(scratch, "patch.diff") == ""
    assert SweBenchScratch.read_text(scratch, "linked/secret") == ""
    assert SweBenchScratch.read_text(scratch, "fifo") == ""
    assert SweBenchScratch.read_text(scratch, "../outside/secret") == ""
    assert SweBenchScratch.read_text(scratch, "/etc/hostname") == ""


def test_a_write_replaces_a_link_and_never_writes_through_one(tmp_path):
    scratch, outside = plant(tmp_path)
    SweBenchScratch.write_text(scratch, "patch.diff", "the restart diff\n")
    assert (outside / "secret").read_text() == "private key\n"
    assert not (scratch / "patch.diff").is_symlink()
    assert (scratch / "patch.diff").read_text() == "the restart diff\n"
    with contextlib.suppress(OSError):
        SweBenchScratch.write_text(scratch, "linked/conditions.json", "{}")
    assert sorted(p.name for p in outside.iterdir()) == ["secret"]
    SweBenchScratch.write_text(scratch, "issue-gate/conditions.json", "{}")
    assert (scratch / "issue-gate" / "conditions.json").read_text() == "{}"
    assert not list(scratch.rglob(".*.tmp"))


def test_a_listing_never_enters_a_linked_directory(tmp_path):
    scratch, outside = plant(tmp_path)
    (outside / "rollout-x.jsonl").write_text("{}\n")
    sessions = scratch / "codex-home" / "sessions"
    (sessions / "2026").mkdir(parents=True)
    (sessions / "2026" / "rollout-a.jsonl").write_text("{}\n")
    (sessions / "elsewhere").symlink_to(outside)
    (sessions / "rollout-b.jsonl").symlink_to(outside / "rollout-x.jsonl")
    assert SweBenchScratch.files(
        scratch, "codex-home/sessions", "rollout*.jsonl", recursive=True
    ) == [
        "codex-home/sessions/2026/rollout-a.jsonl",
        "codex-home/sessions/rollout-b.jsonl",
    ]
    # Listed (a link is a name in the directory) but never read through.
    assert SweBenchScratch.read_text(scratch, "codex-home/sessions/rollout-b.jsonl") == ""
    (scratch / "codex-home" / "linked").symlink_to(sessions)
    assert SweBenchScratch.files(scratch, "codex-home/linked", "*") == []


def test_the_gate_outcome_never_reads_through_a_link(tmp_path):
    scratch, outside = plant(tmp_path)
    records = {
        "conditions.json": {"targets": [{"id": "a", "label": "outside"}]},
        "state.json": {"invocations": {"edit": 2}},
    }
    gate = scratch / GATE_DIRECTORY
    gate.mkdir()
    for name, record in records.items():
        (outside / name).write_text(json.dumps(record))
        (gate / name).symlink_to(outside / name)
    assert SweBenchHooks.outcome(scratch) == SweBenchHooks.outcome(tmp_path / "empty")
    for name in records:
        (gate / name).unlink()
        (gate / name).write_text((outside / name).read_text())
    assert SweBenchHooks.outcome(scratch)["targets"] == ["outside"]


def test_compactions_never_read_a_rollout_outside_the_scratch(tmp_path):
    store = SweBenchRunStore("scratch-links")
    scratch = store.directory / "scratch" / "acme__widget-1"
    sessions = scratch / "codex-home" / "sessions"
    sessions.mkdir(parents=True)
    (tmp_path / "rollout-x.jsonl").write_text(
        json.dumps({"type": "compacted", "payload": {"message": "summary"}}) + "\n"
    )
    (sessions / "rollout-x.jsonl").symlink_to(tmp_path / "rollout-x.jsonl")
    (sessions / "elsewhere").symlink_to(tmp_path)
    result = store.compactions("acme__widget-1")
    assert not result or not result.get("count")
