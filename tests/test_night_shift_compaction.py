"""Night Shift's per-task KV budget and compaction limit (specs/DREAMFERENCE_MIGHTLING_COMPACTION.md
§4.1, specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md §11).

A night run divides 90% of the KV pool between as many tasks as can each get `task_context`, and
holds each task to its share by passing it to every `ling exec` as the compaction limit, so the
tasks together fit in the pool. These tests use the scripted stand-in for `ling` of
`test_night_shift.py` and read the command lines it was given.
"""

import re
import time
from pathlib import Path

from dreamference.night_shift import NightShiftHost, NightShiftRunner, NightShiftSettings, NightShiftTaskRun
from dreamference.night_shift.night_shift_host import LAUNCHER_POOL_SHARE_PERCENT, NIGHT_POOL_SHARE
from test_night_shift import FakeHost, calls, fake_host, queue, setup  # noqa: F401 - fixtures


def exec_calls(setup, monkeypatch, table):
    monkeypatch.setenv("FAKE_MIGHTLING_MODE", "stall_then_act")
    record = queue(setup["night"], setup["repo"])
    run = NightShiftTaskRun(setup["night"], record, NightShiftSettings(table), setup["ling"],
                            deadline=time.time() + 60)
    assert run.run() == "done"
    return calls(setup)


def test_every_exec_of_a_task_carries_a_configured_limit(setup, monkeypatch):
    first, nudge = exec_calls(setup, monkeypatch, {"compact_at": 64000})[:2]
    for call in (first, nudge):
        assert call[call.index("-c") + 1] == "model_auto_compact_token_limit=64000"
    # The limit is an option of `exec`, so it comes before `resume` and before the prompt.
    assert nudge.index("-c") < nudge.index("resume")


def test_a_task_run_alone_passes_no_limit_unless_given_one(setup, monkeypatch):
    # The budget comes from the runner, which knows the pool; a task run built without one, and with
    # no compact_at, leaves the launcher's own limit in place.
    assert NightShiftSettings({}).compact_at is None
    assert NightShiftSettings({"compact_at": 0}).compact_at == 0
    assert not any(arg.startswith("model_auto_compact_token_limit")
                   for call in exec_calls(setup, monkeypatch, {}) for arg in call)


# -- the budget ------------------------------------------------------------------------------------

def test_the_pool_is_divided_so_the_tasks_fit_in_it_together():
    # Today's pool on the default model: two tasks of 70,608 tokens, 141,216 of 156,907 in all.
    assert NightShiftHost.task_budget(3, 156907.0, 65536, None) == (2, 70608)
    # A smaller pool holds one budget; a lone task gets no more than an interactive session would.
    assert NightShiftHost.task_budget(3, 144870.0, 65536, None) == (1, 86922)
    for pool in (40_000.0, 96_000.0, 144_870.0, 156_907.0, 400_000.0, 2_000_000.0):
        for minimum in (16_384, 49_152, 65_536, 131_072):
            parallel, limit = NightShiftHost.task_budget(3, pool, minimum, None)
            assert 1 <= parallel <= 3 and limit
            assert parallel * limit <= int(pool * NIGHT_POOL_SHARE)
            assert limit <= pool * LAUNCHER_POOL_SHARE_PERCENT / 100
            assert parallel == 1 or limit >= minimum


def test_compact_at_sets_the_limit_and_the_parallelism_follows_it():
    assert NightShiftHost.task_budget(3, 156907.0, 65536, 40000) == (3, 40000)
    assert NightShiftHost.task_budget(3, 156907.0, 65536, 100000) == (1, 100000)
    # 0 switches the budget off: the parallelism is computed as before and nothing is passed.
    assert NightShiftHost.task_budget(3, 156907.0, 65536, 0) == (2, None)


def test_an_unknown_pool_runs_one_task_at_a_time():
    assert NightShiftHost.task_budget(3, 0.0, 65536, None) == (1, None)
    assert NightShiftHost.task_budget(3, 0.0, 65536, 50000) == (1, 50000)


def test_the_launchers_pool_share_is_mirrored():
    source = (Path(__file__).resolve().parent.parent / "ling-rs" / "src" / "compaction.rs").read_text()
    match = re.search(r"pub const POOL_SHARE_PERCENT: u64 = (\d+);", source)
    assert match, "POOL_SHARE_PERCENT not found in ling-rs/src/compaction.rs"
    assert int(match.group(1)) == LAUNCHER_POOL_SHARE_PERCENT


def test_a_night_run_holds_every_task_to_its_share(setup, fake_host, monkeypatch):
    monkeypatch.setenv("FAKE_MIGHTLING_MODE", "stall_then_act")
    monkeypatch.setattr(FakeHost, "samples", [{"running": 0.0, "served": 1.0, "kv_pool": 156907.0}])
    for index in range(3):
        queue(setup["night"], setup["repo"], task_text=f"Task {index}", test="test -f hello.txt",
              task_id=f"20261002-0100-b{index}0")
    assert NightShiftRunner.run(minutes=5, idle_minutes=0, night_dir=setup["night"], mightling_bin=setup["ling"],
                                vllm_host="http://x", settings=NightShiftSettings({})) == 0
    execs = [call for call in calls(setup) if call[:1] == ["exec"]]
    assert len(execs) == 6  # three tasks, each a first turn and one nudge
    for call in execs:
        assert call[call.index("-c") + 1] == "model_auto_compact_token_limit=70608"
    report = next((setup["night"] / "reports").glob("*.md")).read_text()
    assert "Up to 2 task(s) at once" in report and "compacts at 70608 tokens" in report


def test_compact_at_zero_passes_no_limit_and_says_so(setup, fake_host, monkeypatch):
    monkeypatch.setenv("FAKE_MIGHTLING_MODE", "change")
    monkeypatch.setattr(FakeHost, "samples", [{"running": 0.0, "served": 1.0, "kv_pool": 156907.0}])
    queue(setup["night"], setup["repo"], test="test -f hello.txt")
    NightShiftRunner.run(minutes=5, idle_minutes=0, night_dir=setup["night"], mightling_bin=setup["ling"],
                         vllm_host="http://x", settings=NightShiftSettings({"compact_at": 0}))
    execs = [call for call in calls(setup) if call[:1] == ["exec"]]
    assert execs and not any(arg.startswith("model_auto_compact_token_limit") for call in execs for arg in call)
    report = next((setup["night"] / "reports").glob("*.md")).read_text()
    assert "not held to the pool" in report


# -- the launcher's settings, mirrored in the Python configuration ---------------------------------

def test_the_ledger_default_is_the_launchers(tmp_path, monkeypatch):
    import re
    from pathlib import Path

    from dreamference.config import dreamference_config as cfg_mod

    source = (Path(__file__).resolve().parent.parent / "ling-rs" / "src" / "compaction.rs").read_text()
    match = re.search(r"pub const LEDGER_DEFAULT: bool = (true|false);", source)
    assert match, "LEDGER_DEFAULT not found in ling-rs/src/compaction.rs"
    assert (match.group(1) == "true") == cfg_mod.DEFAULT_MIGHTLING_COMPACTION_LEDGER


def test_the_ledger_setting_survives_a_save(tmp_path, monkeypatch):
    from dreamference.config import DreamferenceConfig
    from dreamference.config import dreamference_config as cfg_mod

    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_COMPACTION_LEDGER", raising=False)
    path = tmp_path / "dreamference.toml"
    chosen = not cfg_mod.DEFAULT_MIGHTLING_COMPACTION_LEDGER
    DreamferenceConfig(config_file=str(path), mightling_compaction_ledger=chosen).save_config()
    assert DreamferenceConfig(config_file=str(path)).mightling_compaction_ledger is chosen
    # The default is not written, so a round trip does not fossilise it.
    DreamferenceConfig(config_file=str(path), mightling_compaction_ledger=not chosen).save_config()
    assert "mightling_compaction_ledger" not in path.read_text()
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_COMPACTION_LEDGER", "on" if chosen else "off")
    assert DreamferenceConfig(config_file=str(path)).mightling_compaction_ledger is chosen
