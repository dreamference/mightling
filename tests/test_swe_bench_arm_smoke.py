"""SweBenchArmSmoke: the verdict on a night arm's smoke task, read from the run's files (offline)."""

import json

import pytest

from dreamference.swe_bench import SweBenchArmSmoke, SweBenchRunStore

STDIN = "Reading additional input from stdin...\n"


def log(*events):
    return STDIN + "".join(json.dumps(event) + "\n" for event in events)


def state(status="done", outcome="ok", **extra):
    return {"instance_id": "sympy__sympy-13480", "status": status, "exec": outcome, "notes": [], **extra}


STARTED = {"type": "thread.started", "thread_id": "t"}
COMMAND = {"type": "item.completed", "item": {"id": "item_0", "type": "command_execution", "command": "ls"}}
MESSAGE = {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "Fixed."}}
COMPLETED = {"type": "turn.completed", "usage": {"input_tokens": 9000, "cached_input_tokens": 0, "output_tokens": 300}}
WARNING = {"type": "item.completed", "item": {"id": "item_2", "type": "error", "message": "Heads up: long threads"}}


def test_night_5s_startup_failure_is_refused_with_what_ling_said():
    # The candidate arm of night 5: ling stopped on the model catalog before any request.
    said = ("Error: failed to parse model_catalog_json path `/mightling-scratch/codex-home/model_catalog.json` "
            "as JSON: unknown variant `function`, expected `freeform` at line 30 column 5")
    reason = SweBenchArmSmoke.verdict(state("error", "error", session=None, wall_s=3), said + "\n", 0)
    assert reason and "`error`" in reason and "unknown variant `function`" in reason


def test_an_arm_that_ran_passes_whatever_its_patch():
    for status in ("done", "empty", "stalled"):
        assert SweBenchArmSmoke.verdict(state(status), log(STARTED, COMMAND, MESSAGE, COMPLETED), 0) is None


def test_the_smokes_time_limit_is_fine_once_the_model_answered():
    # Cut off mid-turn: no turn.completed, but the model's items reached the agent.
    assert SweBenchArmSmoke.verdict(state("timeout", "timeout"), log(STARTED, COMMAND), 0) is None


def test_no_answer_from_the_model_is_refused():
    reason = SweBenchArmSmoke.verdict(state("empty"), log(STARTED, {"type": "turn.started"}, WARNING), 0)
    assert reason and "no answer from the model" in reason
    # A turn that completed with no tokens proves nothing either.
    empty_turn = {"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}
    assert "no answer from the model" in SweBenchArmSmoke.verdict(state("empty"), log(STARTED, empty_turn), 0)
    # And a timeout that never reached the model is not the time limit's fault.
    assert "no answer from the model" in SweBenchArmSmoke.verdict(state("timeout", "timeout"), log(STARTED), 0)


def test_the_agent_erroring_after_it_reached_the_model_is_refused():
    # A non-zero exit with a patch on disk is still `done`; the outcome of ling exec decides.
    reason = SweBenchArmSmoke.verdict(state("done", "error"), log(STARTED, COMMAND), 0)
    assert reason and "ling exec ended `error`" in reason


def test_a_failed_turn_is_refused_and_a_retried_stream_error_is_not():
    failed = {"type": "turn.failed", "error": {"message": "unexpected status 400 Bad Request: tool type"}}
    assert "a turn failed: unexpected status 400" in SweBenchArmSmoke.verdict(state("empty"), log(STARTED, COMMAND, failed), 0)
    error = {"type": "error", "message": "Reconnecting... 1/5"}
    assert SweBenchArmSmoke.verdict(state(), log(STARTED, error, COMMAND, COMPLETED), 0) is None
    reason = SweBenchArmSmoke.verdict(state("timeout", "timeout"), log(STARTED, COMMAND, error), 0)
    assert reason and "Reconnecting" in reason


@pytest.mark.parametrize("status", ["running", "interrupted", "error"])
def test_a_task_that_did_not_finish_running_is_refused(status):
    reason = SweBenchArmSmoke.verdict(state(status, None, notes=["docker run failed: no space"]), "", 130)
    assert reason and f"`{status}`" in reason and "docker run failed" in reason


def test_a_smoke_that_never_started_is_refused_with_the_runs_status():
    reason = SweBenchArmSmoke.verdict(None, "", 1)
    assert reason and "never started" in reason and "exited 1" in reason


def test_a_round_that_exited_non_zero_is_refused_even_when_the_task_ran():
    assert "exited 1" in SweBenchArmSmoke.verdict(state(), log(STARTED, COMMAND, COMPLETED), 1)


def test_check_reads_the_runs_files_and_prints_the_verdict(capsys):
    store = SweBenchRunStore("n7-smoke-default-test")
    store.write_state("sympy__sympy-13480", state(wall_s=80))
    store.log_path("sympy__sympy-13480").parent.mkdir(parents=True, exist_ok=True)
    store.log_path("sympy__sympy-13480").write_text(log(STARTED, COMMAND, MESSAGE, COMPLETED))
    assert SweBenchArmSmoke.check("n7-smoke-default-test", "sympy__sympy-13480", 0, "default") == 0
    assert "✅ Smoke check of arm default" in capsys.readouterr().out
    assert SweBenchArmSmoke.check("n7-smoke-missing", "sympy__sympy-13480", 1, "drop-outputs") == 1
    assert "❌ Smoke check of arm drop-outputs" in capsys.readouterr().out
