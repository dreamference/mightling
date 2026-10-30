"""The model gate's hold release and status lines, past the paths the main gate tests take.

Releasing a hold whose record is already gone, or cannot be removed, does not fail the run that
ends; and the status says what the gate in front of the server itself answers: closed and why,
paused and until when, and no "no gate answers" line when one did.
"""

from pathlib import Path

from test_model_gate import run_record

from dreamference.vllm_server import model_gate
from dreamference.vllm_server.model_gate import ModelGate


def test_releasing_a_hold_whose_record_is_already_gone_does_not_fail():
    assert ModelGate.run_record() is None
    ModelGate.clear_run("ours")
    assert ModelGate.state()["state"] == "open"


def test_releasing_a_hold_whose_record_cannot_be_removed_does_not_fail(monkeypatch):
    ModelGate.write_run(run_record(id="ours"))
    removed = []

    def unlink(path, missing_ok=False):
        removed.append(path)
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "unlink", unlink)
    ModelGate.clear_run("ours")
    assert removed == [model_gate.GATE_DIR / model_gate.RUN_FILE]
