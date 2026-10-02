"""Night Shift's compaction limit (specs/DREAMFERENCE_PUFFIN_COMPACTION.md §4.1).

The parallelism of a night run is `floor(KV pool / task_context)`; that is only true if a task's
session compacts at `task_context`. These tests use the scripted stand-in for `puffin` of
`test_night_shift.py` and read the command lines it was given.
"""

import time

from dreamference.night_shift import NightShiftSettings, NightShiftTaskRun
from test_night_shift import calls, queue, setup  # noqa: F401 - `setup` is a fixture


def exec_calls(setup, monkeypatch, table):
    monkeypatch.setenv("FAKE_PUFFIN_MODE", "stall_then_act")
    record = queue(setup["night"], setup["repo"])
    run = NightShiftTaskRun(setup["night"], record, NightShiftSettings(table), setup["puffin"],
                            deadline=time.time() + 60)
    assert run.run() == "done"
    return calls(setup)


def test_every_exec_of_a_task_carries_the_compaction_limit(setup, monkeypatch):
    first, nudge = exec_calls(setup, monkeypatch, {})[:2]
    for call in (first, nudge):
        assert call[call.index("-c") + 1] == "model_auto_compact_token_limit=49152"
    # The limit is an option of `exec`, so it comes before `resume` and before the prompt.
    assert nudge.index("-c") < nudge.index("resume")


def test_the_limit_follows_task_context_unless_set(setup, monkeypatch):
    assert NightShiftSettings({}).compact_at == NightShiftSettings({}).task_context == 49152
    assert NightShiftSettings({"task_context": 65536}).compact_at == 65536
    assert NightShiftSettings({"task_context": 65536, "compact_at": 40000}).compact_at == 40000
    call = exec_calls(setup, monkeypatch, {"compact_at": 40000})[0]
    assert "model_auto_compact_token_limit=40000" in call


def test_zero_passes_no_limit(setup, monkeypatch):
    assert NightShiftSettings({"compact_at": 0}).compact_at == 0
    assert not any(arg.startswith("model_auto_compact_token_limit")
                   for call in exec_calls(setup, monkeypatch, {"compact_at": 0}) for arg in call)


# -- the launcher's settings, mirrored in the Python configuration ---------------------------------

def test_the_ledger_default_is_the_launchers(tmp_path, monkeypatch):
    import re
    from pathlib import Path

    from dreamference.config import dreamference_config as cfg_mod

    source = (Path(__file__).resolve().parent.parent / "puffin-rs" / "src" / "compaction.rs").read_text()
    match = re.search(r"pub const LEDGER_DEFAULT: bool = (true|false);", source)
    assert match, "LEDGER_DEFAULT not found in puffin-rs/src/compaction.rs"
    assert (match.group(1) == "true") == cfg_mod.DEFAULT_PUFFIN_COMPACTION_LEDGER


def test_the_ledger_setting_survives_a_save(tmp_path, monkeypatch):
    from dreamference.config import DreamferenceConfig
    from dreamference.config import dreamference_config as cfg_mod

    monkeypatch.delenv("DREAMFERENCE_PUFFIN_COMPACTION_LEDGER", raising=False)
    path = tmp_path / "dreamference.toml"
    chosen = not cfg_mod.DEFAULT_PUFFIN_COMPACTION_LEDGER
    DreamferenceConfig(config_file=str(path), puffin_compaction_ledger=chosen).save_config()
    assert DreamferenceConfig(config_file=str(path)).puffin_compaction_ledger is chosen
    # The default is not written, so a round trip does not fossilise it.
    DreamferenceConfig(config_file=str(path), puffin_compaction_ledger=not chosen).save_config()
    assert "puffin_compaction_ledger" not in path.read_text()
    monkeypatch.setenv("DREAMFERENCE_PUFFIN_COMPACTION_LEDGER", "on" if chosen else "off")
    assert DreamferenceConfig(config_file=str(path)).puffin_compaction_ledger is chosen
