"""`SweBenchHarness` pieces that need no harness; `docker` is a stand-in.

The dataset file it grades against, the eval script's per-file reset, and the memory cap on its
grading containers.
"""

import json
import subprocess

from dreamference.swe_bench import SweBenchDocker, SweBenchHarness


class Ticks:
    """A stop event that lets the loop run `count` times."""

    def __init__(self, count):
        self.count = count

    def wait(self, timeout):
        self.count -= 1
        return self.count < 0


def test_each_grading_container_of_this_run_is_capped_once(monkeypatch):
    listings = iter(
        [
            "sweb.eval.acme__widget-1.ling-7\nsweb.eval.acme__widget-2.ling-77\n",
            "sweb.eval.acme__widget-1.ling-7\nsweb.eval.acme__widget-3.ling-7\nsweb.eval.other.x.ling-7b\n",
            "",
        ]
    )
    calls = []

    def run(cls, args, timeout=None):
        calls.append(args)
        if args[0] == "ps":
            return subprocess.CompletedProcess(args, 0, next(listings), "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(SweBenchDocker, "run", classmethod(run))

    SweBenchHarness._cap_containers("ling-7", "8g", Ticks(3))

    updates = [args for args in calls if args[0] == "update"]
    assert updates == [
        ["update", "--memory", "8g", "--memory-swap", "8g", "sweb.eval.acme__widget-1.ling-7"],
        ["update", "--memory", "8g", "--memory-swap", "8g", "sweb.eval.acme__widget-3.ling-7"],
    ]
    assert sum(args[0] == "ps" for args in calls) == 3


def test_the_cap_loop_ends_as_soon_as_grading_does(monkeypatch):
    monkeypatch.setattr(
        SweBenchDocker,
        "run",
        classmethod(
            lambda cls, args, timeout=None: (_ for _ in ()).throw(
                AssertionError("docker asked after the stop")
            )
        ),
    )

    SweBenchHarness._cap_containers("ling-7", "8g", Ticks(0))


def test_a_reset_line_with_options_or_no_files_is_left_alone():
    base = "0123abc"
    script = "\n".join(
        [
            f"git checkout {base} tests/a.py",
            f"git checkout {base} -- tests/b.py",
            f"git checkout {base} ",
            f"git checkout {base}",
            f"  git checkout {base} tests/c.py",
            "git checkout fedcba tests/d.py",
        ]
    )

    lines = SweBenchHarness.per_file_reset(script, base).split("\n")

    assert lines[0].startswith("for f in tests/a.py; do")
    assert lines[1:] == script.split("\n")[1:]


def test_a_test_file_name_cannot_break_out_of_the_reset_loop():
    fixed = SweBenchHarness.per_file_reset("git checkout abc tests/x.py;touch$IFS/tmp/owned", "abc")

    assert fixed.startswith("for f in 'tests/x.py;touch$IFS/tmp/owned'; do ")


def test_the_dataset_file_leaves_out_rows_without_an_image(tmp_path):
    rows = [
        {
            "instance_id": "a",
            "image": "upstream/a",
            "eval_script": "git checkout b1 t.py",
            "base_commit": "b1",
        },
        {"instance_id": "b", "image": "upstream/b"},
        {"instance_id": "c", "image": "upstream/c", "eval_script": "pytest"},
    ]

    written = SweBenchHarness.write_dataset_file(
        tmp_path / "deep" / "d.jsonl", rows, {"a": "local/a", "b": "", "c": "local/c"}
    )

    assert written == ["a", "c"]
    a, c = (json.loads(line) for line in (tmp_path / "deep" / "d.jsonl").read_text().splitlines())
    assert (a["image"], c["image"]) == ("local/a", "local/c")
    assert a["eval_script"].startswith("for f in t.py;") and c["eval_script"] == "pytest"
    assert rows[0]["image"] == "upstream/a"  # the caller's rows are not changed
