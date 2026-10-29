"""`ling-admin audit egress --sync` (specs/DREAMFERENCE_MIGHTLING_SYNC.md §9).

The verdict is tested on written traces; the scenario's set-up on a stand-in for `ling` and for
strace, so no test here runs the real `ling`, strace or a server.
"""

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from dreamference.audit import EgressAudit, StraceParser, SyncEgressAudit
from dreamference.audit.egress_verdict import FAIL, PASS, TRACE_FAILED
from dreamference.audit.sync_egress_audit import EXEC_ID, THREAD_ID
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

PORT = 41234
STARTED = '100 execve("/home/u/.local/bin/ling", ["ling", "sync", "agent", "--once"], 0x0 /* 30 vars */) = 0\n'
CONNECT = (
    "100 connect(9, {{sa_family=AF_INET, sin_port=htons({port}), "
    'sin_addr=inet_addr("{address}")}}, 16) = {result}\n'
)
TO_HUB = CONNECT.format(port=PORT, address="127.0.0.1", result="-1 EINPROGRESS")


def test_a_cycle_that_reached_only_the_hub_and_published_passes():
    trace = StraceParser.parse(STARTED + TO_HUB)
    verdict = SyncEgressAudit.judge(trace, PORT, published=True, exec_held=True)
    assert (verdict.status, verdict.problems, verdict.exit_code) == (PASS, [], 0)
    assert (
        SyncEgressAudit.judge(trace, PORT, published=False, exec_held=True).status == TRACE_FAILED
    )
    assert (
        SyncEgressAudit.judge(
            StraceParser.parse(TO_HUB), PORT, published=True, exec_held=True
        ).status
        == TRACE_FAILED
    )


def test_any_other_destination_a_lookup_or_a_published_exec_session_fails():
    for line in (
        CONNECT.format(port=8000, address="127.0.0.1", result="0"),
        CONNECT.format(port=443, address="140.82.112.3", result="-1 EINPROGRESS"),
        CONNECT.format(port=PORT, address="192.168.1.9", result="0"),
        CONNECT.format(port=53, address="127.0.0.53", result="0"),
    ):
        verdict = SyncEgressAudit.judge(
            StraceParser.parse(STARTED + TO_HUB + line), PORT, published=True, exec_held=True
        )
        assert verdict.status == FAIL and verdict.exit_code == 1, line
    verdict = SyncEgressAudit.judge(
        StraceParser.parse(STARTED + TO_HUB), PORT, published=True, exec_held=False
    )
    assert verdict.status == FAIL
    assert "ling exec" in verdict.problems[0]


def test_the_device_holds_an_interactive_thread_and_an_exec_session(tmp_path):
    SyncEgressAudit.rollout(str(tmp_path), THREAD_ID, "vscode", "hello")
    files = sorted(Path(tmp_path, "sessions/2026/10/22").iterdir())
    assert [file.name for file in files] == [f"rollout-2026-10-22T10-00-00-{THREAD_ID}.jsonl"]
    first = json.loads(files[0].read_text().splitlines()[0])
    assert first["type"] == "session_meta" and first["payload"]["source"] == "vscode"
    assert THREAD_ID != EXEC_ID


def test_the_scenario_pairs_a_device_and_traces_its_agent_with_the_node_named(
    tmp_path, monkeypatch
):
    # A stand-in for `ling` that records how it was called, writes the server's marker for `web
    # serve`, prints a pairing code for `web pair`, and publishes the interactive thread for the
    # agent the way the real hub would leave it.
    calls = tmp_path / "calls"
    ling = tmp_path / "ling"
    ling.write_text(
        "#!/bin/sh\n"
        f"echo \"$HOME|$DREAMFERENCE_VLLM_HOST|$*\" >> '{calls}'\n"
        'case "$1 $2" in\n'
        "  'web serve') mkdir -p \"$CODEX_HOME/web\"; echo '{}' > \"$CODEX_HOME/web/server.json\"; exec sleep 30 ;;\n"
        "  'web pair') echo 'Pairing code: 1234 5678' ;;\n"
        '  \'sync agent\') hub="$(dirname "$HOME")/node/.mightling/sync/hub/rollouts"; mkdir -p "$hub";'
        f" echo '{{}}' > \"$hub/{THREAD_ID}.json\" ;;\n"
        "esac\nexit 0\n"
    )
    ling.chmod(ling.stat().st_mode | stat.S_IEXEC)
    strace = tmp_path / "strace"
    # The stand-in strace runs what follows its `-o <file>` argument.
    strace.write_text('#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done; shift 2; exec "$@"\n')
    strace.chmod(strace.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    work = tmp_path / "work"
    work.mkdir()
    with patch.object(EgressAudit, "free_port", return_value=PORT):
        _, port, published, exec_held, outputs = SyncEgressAudit.trace(str(ling), str(work))
    assert (port, published, exec_held) == (PORT, True, True)
    lines = calls.read_text().splitlines()
    commands = [line.split("|")[2] for line in lines]
    assert commands == [
        f"web serve --port {PORT}",
        f"sync enable --no-unit --port {PORT}",
        "web pair",
        f"sync enable --no-unit --port {PORT} --code 12345678 --name audit-device",
        "sync agent --once",
    ]
    # The node and the device are two throwaway homes; the device names the node, so nothing browses.
    homes = [line.split("|")[0] for line in lines]
    assert (
        homes[0].endswith("/node") and homes[3].endswith("/device") and homes[4].endswith("/device")
    )
    assert lines[4].split("|")[1] == "http://127.0.0.1:8000"
    assert (work / "node/.config/dreamference/node-id").read_text().strip() == "audit-node"
    assert Path(
        work, f"device/.mightling/sessions/2026/10/22/rollout-2026-10-22T10-00-00-{EXEC_ID}.jsonl"
    ).is_file()
    assert set(outputs) == {"hub", "pair", "device", "agent"}


def test_the_cli_runs_it_and_refuses_a_prompt(capsys):
    with (
        patch.object(SyncEgressAudit, "run", return_value=0) as sync_run,
        patch.object(EgressAudit, "run", side_effect=AssertionError("not the session audit")),
    ):
        with pytest.raises(SystemExit) as exit_info:
            DreamferenceCLIController.run_cli(["audit", "egress", "--sync", "--json"])
        assert exit_info.value.code == 0
        sync_run.assert_called_once_with(write_json=True)
        sync_run.reset_mock()
        with pytest.raises(SystemExit) as exit_info:
            DreamferenceCLIController.run_cli(["audit", "egress", "--sync", "--prompt", "hello"])
        assert exit_info.value.code == 2
        sync_run.assert_not_called()
    assert "--prompt has no effect with --sync" in capsys.readouterr().out


def test_the_sync_and_docs_audits_fail_a_networked_git_command():
    # Only the session audit judged networked git; a sync cycle or a ling-docs run that started
    # `git fetch` (one that reached nothing, say) passed.
    from dreamference.audit import DocsEgressAudit
    from dreamference.audit.egress_trace import EgressTrace

    trace = EgressTrace(processes={"ling": 1, "git": 1}, networked_git=["git fetch origin"])
    sync = SyncEgressAudit.judge(trace, PORT, published=True, exec_held=True)
    docs = DocsEgressAudit.judge(trace, indexed=True, found=True)
    for verdict in (sync, docs):
        assert verdict.status == FAIL
        assert any("git fetch origin" in problem for problem in verdict.problems)


def test_a_cycle_cut_off_at_the_timeout_is_a_failed_trace_and_leaves_nothing_running(
    tmp_path, monkeypatch, capsys
):
    # strace was killed alone at the timeout: the agent went on untraced, and a thread it had
    # already published made the cut-off trace a pass.
    import time

    from dreamference.audit import sync_egress_audit
    from dreamference.audit.egress_trace import EgressTrace

    child = tmp_path / "child"
    monkeypatch.setattr(sync_egress_audit, "STEP_TIMEOUT_S", 1)
    said = SyncEgressAudit.step(
        "sh", ["-c", f"sleep 30 & echo $! > '{child}'; wait"], {**os.environ, "HOME": str(tmp_path)}
    )
    assert said.startswith(sync_egress_audit.TIMED_OUT)
    pid = int(child.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)

    trace = EgressTrace(processes={"ling": 1}, destinations={f"127.0.0.1:{PORT}": 1})
    cut = SyncEgressAudit.judge(trace, PORT, published=True, exec_held=True, finished=False)
    assert cut.status == TRACE_FAILED
    outputs = {"agent": said}
    with (
        patch.object(SyncEgressAudit, "trace", return_value=(trace, PORT, True, True, outputs)),
        patch("shutil.which", return_value="/usr/bin/strace"),
    ):
        assert SyncEgressAudit.run(ling="/opt/ling") == 2
    assert "trace failed" in capsys.readouterr().out
