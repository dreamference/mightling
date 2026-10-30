"""The host takes a task's commit only from a bundle that holds one commit on its branch.

The sandboxed commit writes the bundle, so it may be hostile: a link to a host file, a pipe that
never ends, other refs, or a branch rewritten past its base (specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md
§5.3). Each test builds the bundle with git in a clone; nothing runs in a sandbox.
"""

import os
import time

import pytest
from test_night_shift import git, queue
from test_night_shift import setup as night_repo  # noqa: F401 - the fixture

from dreamference.night_shift import NightShiftSettings, NightShiftTaskRun


@pytest.fixture
def receiving(request, tmp_path):
    """A task run on the test repository, its branch at the base, and a clone to build bundles in."""
    night = request.getfixturevalue("night_repo")
    record = queue(night["night"], night["repo"])
    run = NightShiftTaskRun(
        night["night"], record, NightShiftSettings({}), night["ling"], deadline=time.time() + 60
    )
    head_id = record["base"]
    ref = f"refs/heads/{record['branch']}"
    git(night["repo"], "update-ref", ref, head_id)
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(night["repo"]), str(clone))
    git(clone, "config", "user.email", "night@test")
    git(clone, "config", "user.name", "Night Test")
    git(clone, "checkout", "-q", "-b", record["branch"])
    return {
        "run": run,
        "repo": night["repo"],
        "clone": clone,
        "ref": ref,
        "head_id": head_id,
        "received": tmp_path / "received.bundle",
    }


def _commit(clone, name: str) -> str:
    (clone / name).write_text(f"{name}\n")
    git(clone, "add", name)
    git(clone, "commit", "-q", "-m", f"Add {name}")
    return git(clone, "rev-parse", "HEAD").stdout.strip()


def _bundle(r, *refs: str):
    bundle = r["clone"].parent / "commit.bundle"
    git(r["clone"], "bundle", "create", "-q", str(bundle), *refs, f"^{r['head_id']}")
    return bundle


def _receive(r, bundle):
    return r["run"]._receive(bundle, r["received"], r["ref"], r["head_id"])


def _branch(r) -> str:
    return git(r["repo"], "rev-parse", r["ref"]).stdout.strip()


def test_one_commit_on_the_branch_is_taken(receiving):
    r = receiving
    tip = _commit(r["clone"], "hello.txt")
    assert _receive(r, _bundle(r, r["ref"])) == ("done", tip)
    assert _branch(r) == tip


def test_a_bundle_that_is_a_link_is_not_followed(receiving, tmp_path):
    # A link to a bundle elsewhere (or to any host file) is refused, not read through.
    r = receiving
    _commit(r["clone"], "hello.txt")
    elsewhere = _bundle(r, r["ref"])
    link = tmp_path / "linked.bundle"
    link.symlink_to(elsewhere)
    status, why = _receive(r, link)
    assert status == "failed" and why.startswith("the commit left no bundle")
    assert _branch(r) == r["head_id"] and not r["received"].exists()


def test_a_missing_bundle_fails_the_commit(receiving, tmp_path):
    r = receiving
    status, why = _receive(r, tmp_path / "never-written.bundle")
    assert status == "failed" and why.startswith("the commit left no bundle")
    assert _branch(r) == r["head_id"]


def test_a_bundle_that_is_a_pipe_is_refused_without_waiting_on_it(receiving, tmp_path):
    # Nothing ever writes to the pipe: reading it would block the night run for good.
    r = receiving
    pipe = tmp_path / "commit.bundle"
    os.mkfifo(pipe)
    started = time.monotonic()
    assert _receive(r, pipe) == ("failed", "the commit's bundle is not a regular file")
    assert time.monotonic() - started < 10
    assert _branch(r) == r["head_id"]


def test_a_bundle_with_another_ref_or_only_another_ref_is_refused(receiving):
    r = receiving
    _commit(r["clone"], "hello.txt")
    git(r["clone"], "branch", "-q", "other")
    for refs in ([r["ref"], "refs/heads/other"], ["refs/heads/other"]):
        status, why = _receive(r, _bundle(r, *refs))
        assert status == "failed"
        assert why.startswith(f"the commit's bundle does not hold one {r['ref']}")
        assert _branch(r) == r["head_id"]


def test_a_branch_moved_by_more_than_one_commit_is_refused(receiving):
    # Two commits: whatever the first one did would reach the branch unseen.
    r = receiving
    _commit(r["clone"], "first.txt")
    _commit(r["clone"], "second.txt")
    status, why = _receive(r, _bundle(r, r["ref"]))
    assert (status, why) == (
        "failed",
        f"the commit is not one commit on top of {r['head_id'][:12]}",
    )
    assert _branch(r) == r["head_id"]
