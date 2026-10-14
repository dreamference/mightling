"""scripts/turn_end_compaction_bench.py: reading rollouts, classifying compactions, the probe, the report.

Everything here runs on synthetic rollouts in Codex's record shapes (as `rust-v0.158.0` writes them)
and on the stand-in model server's answers; nothing reads the user's sessions or reaches a model
server. The one test that drives the installed `ling` against the stand-in is marked
`installed_binary` and runs only with `MIGHTLING_TEC_DRY_RUN=1`.
"""

import importlib.util
import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("turn_end_compaction_bench",
                                                  ROOT / "scripts" / "turn_end_compaction_bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()

T0 = 1_791_000_000_000  # epoch ms


class Rollout:
    """Builds a rollout record by record, one millisecond clock."""

    def __init__(self):
        self.records = []
        self.now = T0

    def add(self, kind, payload, after_ms=10):
        self.now += after_ms
        stamp = bench.datetime.fromtimestamp(self.now / 1000, tz=bench.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        self.records.append({"timestamp": stamp, "type": kind, "payload": payload})
        return self.now

    def event(self, kind, after_ms=10, **fields):
        return self.add("event_msg", {"type": kind, **fields}, after_ms)

    def turn_start(self, turn):
        return self.event("task_started", turn_id=turn)

    def turn_end(self, turn, duration_ms=1000, aborted=False, after_ms=10):
        if aborted:
            return self.event("turn_aborted", after_ms, turn_id=turn, reason="interrupted", duration_ms=duration_ms)
        return self.event("task_complete", after_ms, turn_id=turn, duration_ms=duration_ms)

    def user(self, text, after_ms=10):
        return self.add("response_item", {"type": "message", "role": "user",
                                          "content": [{"type": "input_text", "text": text}]}, after_ms)

    def assistant(self, text, after_ms=10):
        return self.add("response_item", {"type": "message", "role": "assistant",
                                          "content": [{"type": "output_text", "text": text}]}, after_ms)

    def call(self, command, call_id="c1", output="Process exited with code 0\nok", code=0):
        self.add("response_item", {"type": "function_call", "name": "exec_command", "call_id": call_id,
                                   "arguments": json.dumps({"cmd": command})})
        return self.add("response_item", {"type": "function_call_output", "call_id": call_id,
                                          "output": output.replace("code 0", f"code {code}")})

    def tokens(self, input_tokens, total, after_ms=5):
        return self.event("token_count", after_ms, info={
            "last_token_usage": {"input_tokens": input_tokens, "output_tokens": total - input_tokens, "total_tokens": total},
            "model_context_window": 249_036})

    def compaction(self, turn, wall_ms, summary="Summary.", before=70_000, after=8_000, record_summary=True, cached=0):
        """A compaction: mid-turn/pre-turn ones record their summary first; post-turn ones do not."""
        started = self.now + 1
        self.now += wall_ms
        if record_summary:
            self.assistant(summary, after_ms=0)
        self.add("token_usage_record", {"response_id": "resp-c"}, 1)
        self.tokens(before, before + 300, after_ms=1)
        self.add("compacted", {"message": "Prefix\n" + summary, "compaction_response_id": "resp-c",
                               "latest_token_usage_record": {"response_id": "resp-c",
                                                             "usage": {"input_tokens": before, "cached_input_tokens": cached,
                                                                       "output_tokens": 300}}}, 1)
        self.tokens(0, after, after_ms=1)
        self.event("item_completed", 1, turn_id=turn, item={"type": "ContextCompaction", "id": "x"},
                   started_at_ms=started, completed_at_ms=started + wall_ms)

    def write(self, path):
        path.write_text("".join(json.dumps(record) + "\n" for record in self.records))
        return path


def _session(tmp_path, build):
    rollout = Rollout()
    rollout.add("session_meta", {"id": "01a1-session", "cwd": str(tmp_path), "timestamp": "2026-10-13T09:00:00Z"})
    build(rollout)
    return rollout.write(tmp_path / "rollout-2026-10-13T09-00-00-x.jsonl"), rollout


def test_the_turn_end_percentage_is_the_launchers():
    assert bench.turn_end_percent(262_144, 94_144) == 26
    assert bench.turn_end_threshold_tokens(262_144, 26) == 64_750
    assert bench.turn_end_percent(262_144, 10**9) == 66  # at most 70% of Codex's 90% cap
    assert bench.turn_end_percent(0, 94_144) is None


def test_a_mid_turn_compaction_is_timed_and_measured(tmp_path):
    def build(r):
        r.turn_start("t1")
        r.user("Fix the bug.")
        r.tokens(60_000, 60_500)
        r.call("pytest -q")
        r.tokens(95_000, 95_200)
        r.compaction("t1", 24_000, before=95_500, after=9_000)
        r.assistant("Fixed.")
        r.tokens(12_000, 12_100)
        r.turn_end("t1", duration_ms=40_000)

    path, _ = _session(tmp_path, build)
    records = bench.read_records(path)
    turns = bench.turns_of(records)
    (compaction,) = bench.compactions_of(records, turns)
    assert compaction["kind"] == "mid-turn"
    assert compaction["wall_s"] == 24.0
    assert (compaction["context_before"], compaction["context_after"]) == (95_500, 9_000)
    assert compaction["summary_output_tokens"] == 300
    assert turns[0]["status"] == "complete" and turns[0]["wall_s"] == 40.0 and turns[0]["context_at_end"] == 12_100


def test_a_compaction_after_the_final_answer_is_turn_end_even_when_a_typed_message_continues_the_turn(tmp_path):
    def build(r):
        r.turn_start("t1")
        r.user("Explain the runner.")
        r.call("cat runner.py")
        r.assistant("The runner does this.")
        r.tokens(66_000, 66_400)
        r.compaction("t1", 30_000, record_summary=False, before=66_400, after=7_000)
        r.user("Now add a dry run.")  # typed with Enter during the compaction: the same turn goes on
        r.assistant("Added.")
        r.tokens(9_000, 9_100)
        r.turn_end("t1")

    path, _ = _session(tmp_path, build)
    (compaction,) = bench.compactions_of(bench.read_records(path))
    assert compaction["kind"] == "turn-end"
    assert compaction["wall_s"] == 30.0


def test_a_compaction_before_the_turns_first_request_is_pre_turn(tmp_path):
    def build(r):
        r.turn_start("t1")
        r.user("Go on.")
        r.compaction("t1", 20_000)
        r.assistant("Going on.")
        r.turn_end("t1")

    path, _ = _session(tmp_path, build)
    assert [c["kind"] for c in bench.compactions_of(bench.read_records(path))] == ["pre-turn"]


def test_a_compaction_without_its_item_is_timed_from_the_records(tmp_path):
    def build(r):
        r.turn_start("t1")
        r.user("Go.")
        r.call("ls")
        r.tokens(95_000, 95_100)
        started = r.now
        r.now += 12_000
        r.assistant("Summary.", after_ms=0)
        r.add("compacted", {"message": "Summary."}, 5)
        r.assistant("Done.")
        r.turn_end("t1")
        r.started = started

    path, rollout = _session(tmp_path, build)
    (compaction,) = bench.compactions_of(bench.read_records(path))
    assert compaction["kind"] == "mid-turn"
    assert 11.9 <= compaction["wall_s"] <= 12.1


def test_turns_that_end_above_the_threshold_without_compacting_are_flagged(tmp_path):
    def build(r):
        r.turn_start("t1")
        r.user("Read everything.")
        r.assistant("Read.")
        r.tokens(70_000, 70_100)
        r.turn_end("t1")
        r.turn_start("t2")
        r.user("Small question.")
        r.assistant("Answer.")
        r.tokens(30_000, 30_050)
        r.turn_end("t2")

    path, _ = _session(tmp_path, build)
    records = bench.read_records(path)
    turns = bench.turns_of(records)
    assert bench.missed_turn_ends(turns, bench.compactions_of(records, turns), 64_750) == ["t1"]


def _probe_session(tmp_path, *, steer=True, aborted=False, lost=False):
    note = {}

    def build(r):
        r.turn_start("t1")
        r.user("Explain.")
        r.assistant("Explained.")
        r.tokens(66_000, 66_400)
        note["sent_ms"] = r.now + 2_000
        if aborted:
            r.turn_end("t1", aborted=True, after_ms=3_000)
        else:
            r.compaction("t1", 9_000, record_summary=False)
            if steer and not lost:
                r.user("Next: add a flag.")
                r.assistant("Added.")
            r.turn_end("t1")
        if not steer and not lost:
            r.turn_start("t2")
            r.user("Next: add a flag.")
            r.assistant("Added.")
            r.turn_end("t2")
        if aborted and not lost:
            r.turn_start("t2")
            r.user("Next: add a flag.")
            r.turn_end("t2")

    path, _ = _session(tmp_path, build)
    records = bench.read_records(path)
    turns = bench.turns_of(records)
    probe = {"sent_ms": note["sent_ms"], "key": "enter" if steer else "tab", "text": "Next: add a flag."}
    return bench.probe_outcome(records, turns, bench.compactions_of(records, turns), probe)


def test_a_message_typed_with_enter_during_a_turn_end_compaction_waits_and_continues_the_turn(tmp_path):
    outcome = _probe_session(tmp_path, steer=True)
    assert outcome["verdict"] == "waited"
    assert outcome["compaction_kind"] == "turn-end" and outcome["compaction_finished"]
    assert outcome["delivered_in"] == "same turn"
    assert outcome["typed_after_s"] == 2.0 and outcome["waited_s"] == 7.0


def test_a_message_queued_with_tab_is_taken_up_by_the_next_turn(tmp_path):
    outcome = _probe_session(tmp_path, steer=False)
    assert (outcome["verdict"], outcome["delivered_in"]) == ("waited", "next turn")


def test_a_message_that_never_reaches_the_model_is_lost(tmp_path):
    assert _probe_session(tmp_path, steer=True, lost=True)["verdict"] == "lost"


def test_a_compaction_cut_short_by_the_message_is_interrupted(tmp_path):
    outcome = _probe_session(tmp_path, aborted=True)
    assert outcome["verdict"] == "interrupted" and outcome["compaction"] is None
    assert outcome["turn_aborted"] and outcome["delivered"]


def test_the_nightly_audit_judges_every_summary(tmp_path):
    def build(r):
        r.turn_start("t1")
        r.user("Change night_shift_runner.py.")
        r.add("response_item", {"type": "custom_tool_call", "name": "apply_patch", "call_id": "p1",
                                "input": "*** Begin Patch\n*** Update File: dreamference/night_shift/night_shift_runner.py\n"})
        r.add("response_item", {"type": "custom_tool_call_output", "call_id": "p1", "output": "Success"})
        r.call("pytest -q", call_id="c2", output="Process exited with code 1\n1 failed, 3 passed in 0.5s", code=1)
        r.tokens(95_000, 95_100)
        r.compaction("t1", 20_000, summary="Edited dreamference/night_shift/night_shift_runner.py.")
        r.assistant("Done.")
        r.turn_end("t1")

    path, _ = _session(tmp_path, build)
    (verdict,) = bench.audit_verdicts(path)
    assert verdict["kept"]["files changed"] == [1, 1]
    assert verdict["verdict"].startswith("lost")
    assert "last test result" in verdict["lost"]
    analysis = bench.analyze_session(path, 64_750, None)
    assert analysis["compactions"][0]["audit"] == verdict
    assert analysis["counts"] == {"pre-turn": 0, "mid-turn": 1, "turn-end": 0}
    assert analysis["peak_context"] == 95_100


def _result(task, arm, *, mid=0, turn_end=(), wall=1500.0, hidden=5, probe=None, missed=()):
    common = {"audit": {"verdict": "kept all"}, "stray_tool_call": False, "context_before": 95_000,
              "context_after": 9_000, "summary_chars": 4_000}
    compactions = [{"kind": "mid-turn", "wall_s": 25.0, **common} for _ in range(mid)]
    compactions += [{"kind": "turn-end", "wall_s": seconds, **common} for seconds in turn_end]
    for number, compaction in enumerate(compactions, start=1):
        compaction["number"] = number
    return {"label": f"{task}-{arm}-1", "task": task, "arm": arm, "rep": 1, "status": "complete", "wall_s": wall,
            "counts": {"pre-turn": 0, "mid-turn": mid, "turn-end": len(turn_end)}, "compactions": compactions,
            "peak_context": 96_000 if mid else 80_000, "probe": probe, "missed_turn_ends": list(missed),
            "turns": [], "grade": {"hidden": {"counts": {"passed": hidden}}, "own": {"counts": {"passed": 40}}}}


WAITED = {"verdict": "waited", "key": "enter", "typed_after_s": 3.0, "compaction_finished": True,
          "turn_aborted": False, "delivered_in": "same turn", "waited_s": 30.0}


def test_the_rule_switches_on_when_turn_end_saves_mid_turn_compactions_quickly_and_keeps_input():
    sessions = [_result("a", "off", mid=2), _result("a", "on", mid=0, turn_end=(35.0, 41.0), probe=WAITED)]
    decision, lines = bench.decide(sessions, bench.aggregate(sessions))
    assert decision == "switch on", lines


def test_the_rule_keeps_it_off_when_a_typed_message_is_lost_or_compaction_is_slow():
    lost = dict(WAITED, verdict="lost")
    sessions = [_result("a", "off", mid=2), _result("a", "on", turn_end=(35.0,), probe=lost)]
    assert bench.decide(sessions, bench.aggregate(sessions))[0] == "keep off"
    sessions = [_result("a", "off", mid=2), _result("a", "on", turn_end=(90.0, 95.0), probe=WAITED)]
    assert bench.decide(sessions, bench.aggregate(sessions))[0] == "keep off"
    sessions = [_result("a", "off", mid=2), _result("a", "on", mid=2, turn_end=(35.0,), probe=WAITED)]
    assert bench.decide(sessions, bench.aggregate(sessions))[0] == "keep off"
    sessions = [_result("a", "off", mid=2), _result("a", "on", turn_end=(35.0,), probe=WAITED, missed=("t9",))]
    sessions[1]["missed_turn_ends_clear"] = ["t9"]
    assert bench.decide(sessions, bench.aggregate(sessions))[0] == "keep off"


def test_wall_time_and_hidden_tests_are_reported_but_two_sessions_cannot_decide_on_them():
    sessions = [_result("a", "off", mid=2), _result("a", "on", turn_end=(35.0,), probe=WAITED, wall=2400.0, hidden=3)]
    decision, lines = bench.decide(sessions, bench.aggregate(sessions))
    assert decision == "switch on"
    assert any(line.startswith("⚠️ a (reported, not deciding)") for line in lines)


def test_a_turn_end_just_above_the_threshold_is_a_note_not_a_failure():
    sessions = [_result("a", "off", mid=2), _result("a", "on", turn_end=(35.0,), probe=WAITED, missed=("t9",))]
    decision, lines = bench.decide(sessions, bench.aggregate(sessions))
    assert decision == "switch on"
    assert any("1 within 5% of it" in line for line in lines)


def test_a_turn_that_did_not_reach_the_model_whole_makes_the_run_inconclusive():
    sessions = [_result("a", "off", mid=2), _result("a", "on", turn_end=(35.0,), probe=WAITED)]
    sessions[1]["inputs_intact"] = False
    assert bench.decide(sessions, bench.aggregate(sessions))[0] == "inconclusive"


def test_the_rule_is_inconclusive_when_no_off_session_reached_the_limit():
    sessions = [_result("a", "off", mid=0), _result("a", "on", turn_end=(35.0,), probe=WAITED)]
    decision, lines = bench.decide(sessions, bench.aggregate(sessions))
    assert decision == "inconclusive"
    assert any("never compacted mid-turn" in line for line in lines)


def test_the_report_carries_the_decision_the_tables_and_the_runbook():
    sessions = [_result("a", "off", mid=2), _result("a", "on", turn_end=(35.0,), probe=WAITED)]
    text = bench.render_report({"host": "http://node.local:8000", "limit": 94_144, "percent": 26,
                                "threshold": 64_750, "binary": {}}, sessions)
    assert "**Decision: switch on.**" in text
    assert "| a-on-1 | enter | 3.0 | True | False | same turn | 30.0 | waited |" in text
    assert "## Runbook" in text and "node-b.local:8000" in text


def test_sessions_alternate_arm_order_and_probe_keys_by_repetition(tmp_path):
    plans = bench.plans_for(["night-shift", "click"], 2, tmp_path, ["enter", "tab"], ("off", "on"))
    assert [plan.label for plan in plans] == [
        "night-shift-off-1", "night-shift-on-1", "click-off-1", "click-on-1",
        "night-shift-on-2", "night-shift-off-2", "click-on-2", "click-off-2"]
    assert [plan.probe_key for plan in plans] == [None, "enter", None, "enter", "tab", None, "tab", None]


def test_v2_runs_twelve_sessions_three_arms_and_probes_both_turn_end_arms(tmp_path):
    plans = bench.plans_for(["night-shift", "click"], 2, tmp_path, ["enter", "tab"])
    assert [plan.label for plan in plans] == [
        "night-shift-off-1", "night-shift-on-1", "night-shift-drop-1", "click-off-1", "click-on-1", "click-drop-1",
        "night-shift-drop-2", "night-shift-on-2", "night-shift-off-2", "click-drop-2", "click-on-2", "click-off-2"]
    assert [plan.probe_key for plan in plans] == [None, "enter", "enter", None, "enter", "enter",
                                                  "tab", "tab", None, "tab", "tab", None]


def test_a_session_runs_in_throwaway_homes_with_masking_off_and_the_arm_on_the_command_line(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_MASK", "1")
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_TURN_END_COMPACTION", "1")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "real-home"))
    plan = bench.SessionPlan(bench.TASKS["click"], "off", 1, tmp_path / "s", None)
    env = bench.session_env(plan, "http://node.local:8000", None, code_tools=True, docs=False)
    assert env["CODEX_HOME"] == str(tmp_path / "s" / "home")
    assert env["DREAMFERENCE_CONFIG_PATH"] == str(tmp_path / "s" / "dreamference.toml")
    assert env["DREAMFERENCE_VLLM_HOST"] == "http://node.local:8000"
    assert (env["DREAMFERENCE_MIGHTLING_MASK"], env["DREAMFERENCE_MIGHTLING_TURN_END_COMPACTION"]) == ("0", "0")
    assert env["DREAMFERENCE_MIGHTLING_DOCS"] == "0" and "DREAMFERENCE_MIGHTLING_CODE_TOOLS" not in env
    assert bench.session_args("on", 94_144, 26, "workspace-write") == [
        "-a", "never", "-s", "workspace-write", "-c", "model_auto_compact_token_limit=94144",
        "-c", "model_post_turn_compact_threshold_percent=26"]
    assert bench.session_args("off", 94_144, 26, "workspace-write")[-1] == "model_post_turn_compact_threshold_percent=0"


def test_v2_arms_differ_only_in_their_two_switches_and_all_cap_the_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS", "1")
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_COMPACTION_TOOLS", "0")
    switches = {}
    for arm in bench.ARMS:
        plan = bench.SessionPlan(bench.TASKS["click"], arm, 1, tmp_path / arm, None)
        env = bench.session_env(plan, "http://node.local:8000", None, code_tools=True, docs=True)
        switches[arm] = (env["DREAMFERENCE_MIGHTLING_TURN_END_COMPACTION"], env["DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS"],
                         env["DREAMFERENCE_MIGHTLING_COMPACT_SUMMARY_WORDS"])
        # The cache fix is on in every arm: an inherited switch-off does not reach the session.
        assert "DREAMFERENCE_MIGHTLING_COMPACTION_TOOLS" not in env
    assert switches == {"off": ("0", "0", "800"), "on": ("1", "0", "800"), "drop": ("1", "1", "800")}
    assert bench.session_args("drop", 94_144, 20, "workspace-write")[-1] == "model_post_turn_compact_threshold_percent=20"
    # The owner's threshold: 20% of the usable window.
    assert bench.turn_end_threshold_tokens(262_144, bench.TURN_END_PERCENT_V2) == 49_808


def test_the_bench_refuses_to_run_without_a_named_server_or_against_this_machines_own(tmp_path):
    with pytest.raises(SystemExit):
        bench.require_host(None)
    with pytest.raises(SystemExit):
        bench.main(["run", "--host", "", "--out", str(tmp_path / "out")])
    for own in ("http://localhost:8000", "http://127.0.0.1:8000/", "http://0.0.0.0:8000", "http://[::1]:8000"):
        with pytest.raises(SystemExit):
            bench.require_host(own)
    assert bench.require_host("http://node-b.local:8000/") == "http://node-b.local:8000"


def test_a_turn_whose_paste_was_cut_short_is_missing(tmp_path):
    def build(r):
        r.turn_start("t1")
        r.user("Read every module in full, then explain the runner.")
        r.user("Second turn, cut")
        r.turn_end("t1")

    path, _ = _session(tmp_path, build)
    typed = {0: "Read every module  in full,\nthen explain the runner.", 1: "Second turn, cut short by the composer."}
    assert bench.inputs_missing(bench.read_records(path), typed) == [1]


def test_report_rebuilds_the_results_from_the_rollouts_left_on_disk(tmp_path):
    out = tmp_path / "run"
    label = "click-on-1"

    def build(r):
        r.turn_start("t1")
        r.user("Explain.")
        r.assistant("Explained.")
        r.tokens(66_000, 66_400)
        r.compaction("t1", 30_000, record_summary=False)
        r.turn_end("t1")

    session_dir = out / label / "home" / "sessions" / "2026" / "10" / "13"
    session_dir.mkdir(parents=True)
    rollout = Rollout()
    build(rollout)
    rollout.write(session_dir / "rollout-2026-10-13T09-00-00-x.jsonl")
    (out / "meta.json").write_text(json.dumps({"order": [label], "limit": 94_144}))
    (out / label / "session.json").write_text(json.dumps(
        {"label": label, "task": "click", "arm": "on", "rep": 1, "status": "driver error: died", "turn_end_threshold": 64_750,
         "probe_note": None}))
    assert bench.main(["report", "--out", str(out)]) == 0
    results = json.loads((out / "results.json").read_text())
    (session,) = results["sessions"]
    assert session["counts"] == {"pre-turn": 0, "mid-turn": 0, "turn-end": 1}
    assert session["compactions"][0]["wall_s"] == 30.0
    assert "click-on-1" in (out / "report.md").read_text()


def test_the_stand_in_answers_titles_compactions_and_steers_turn_two_into_a_shell_call():
    server = bench.FakeModelServer(compaction_delay=0)
    tools = [{"type": "function", "name": "exec_command"}]

    def ask(text, extra=()):
        items = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}, *extra]
        body = server.respond({"input": items, "tools": tools}).decode()
        return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]

    title = ask("Generate a concise, single-line task title of at most 36 characters")
    assert title[1]["item"]["type"] == "message"
    first = ask("DRYRUN-1 Say done.")
    assert first[2]["response"]["usage"]["input_tokens"] == 70_000
    call = ask("DRYRUN-2 Run one command, then say done.")
    assert call[1]["item"]["name"] == "exec_command" and call[2]["response"]["usage"]["input_tokens"] == 100_000
    summary = ask("DRYRUN-2 Run one command, then say done.",
                  [{"type": "message", "role": "user", "content": [{"type": "input_text",
                                                                    "text": "You are performing a CONTEXT CHECKPOINT COMPACTION."}]}])
    assert "Summary" in summary[1]["item"]["content"][0]["text"]
    final = ask("DRYRUN-2 Run one command, then say done.")
    assert final[1]["item"]["type"] == "message"
    assert [request["kind"] for request in server.requests] == ["title", "turn", "turn", "compaction", "turn"]
    assert all(request["tools"] == json.dumps(tools) for request in server.requests)


def test_each_compactions_cache_hit_is_read_from_its_own_usage(tmp_path):
    def build(r):
        r.turn_start("t1")
        r.user("Explain.")
        r.call("ls")
        r.tokens(90_000, 90_500)
        r.compaction("t1", 20_000, before=90_000, cached=88_000)
        r.assistant("Explained.")
        r.tokens(66_000, 66_400)
        r.compaction("t1", 30_000, before=66_000, record_summary=False)
        r.turn_end("t1")

    path, _ = _session(tmp_path, build)
    first, second = bench.analyze_session(path, 49_808, None)["compactions"]
    assert (first["cached_tokens"], first["input_tokens"], first["cache_hit"]) == (88_000, 90_000, 0.978)
    assert (second["cached_tokens"], second["cache_hit"]) == (0, 0.0)


def test_drop_first_compactions_are_read_from_their_log(tmp_path):
    log = tmp_path / "masking" / "dropped" / "log.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("\n".join(json.dumps(entry) for entry in [
        {"accepted": True, "outputs": 12}, {"accepted": False, "outputs": 0}, {"accepted": True, "outputs": 3}]) + "\nbroken\n")
    assert bench.drops_of(tmp_path) == {"attempts": 3, "drops": 2, "declined": 1, "dropped_outputs": 15}
    assert bench.drops_of(tmp_path / "nowhere")["attempts"] == 0
    assert bench.drops_of(None)["attempts"] == 0


def test_with_three_arms_each_turn_end_arm_is_judged_against_off():
    on = _result("a", "on", mid=2, turn_end=(35.0,), probe=WAITED)
    drop = _result("a", "drop", mid=0, turn_end=(30.0, 40.0), probe=WAITED)
    for compaction in on["compactions"] + drop["compactions"]:
        compaction["cache_hit"] = 0.97
    drop["drops"] = {"attempts": 3, "drops": 2, "declined": 1, "dropped_outputs": 20}
    sessions = [_result("a", "off", mid=2), on, drop]
    table = bench.aggregate(sessions)
    decision, lines = bench.decide(sessions, table)
    assert decision == "on: keep off; drop: switch on", lines
    assert any(line.startswith("**drop**") for line in lines)
    assert any("prefix cache: median 97% of the prompt over 5 compactions, 0 below half" in line for line in lines)
    assert table["a/drop"]["drops_per_session"] == 2 and table["a/on"]["cache_hit_mean"] == 0.97
    text = bench.render_report({"host": "http://node.local:8000", "limit": 94_144, "percent": 20, "threshold": 49_808,
                                "binary": {}, "summary_words": 800}, sessions)
    assert "**Decision: on: keep off; drop: switch on.**" in text
    assert "Compaction cache hit (mean)" in text and "| 97% |" in text
    assert "caps the summary at 800 words" in text


def test_the_stand_in_records_tools_and_serves_a_matching_prompt_from_its_cache():
    server = bench.FakeModelServer(compaction_delay=0)
    tools = [{"type": "function", "name": "exec_command"}, {"type": "function", "name": "code_def"}]

    def ask(text, offered, extra=()):
        items = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}, *extra]
        body = server.respond({"input": items, "tools": offered}).decode()
        return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]

    compact = [{"type": "message", "role": "user", "content": [{"type": "input_text",
                "text": "You are performing a CONTEXT CHECKPOINT COMPACTION. Keep the summary under 800 words."}]}]
    assert ask("DRYRUN-1 Say done.", tools)[2]["response"]["usage"]["input_tokens_details"]["cached_tokens"] == 0
    hot = ask("DRYRUN-1 Say done.", tools, compact)[2]["response"]["usage"]
    assert hot["input_tokens_details"]["cached_tokens"] == hot["input_tokens"] - 1000
    cold = ask("DRYRUN-1 Say done.", [], compact)[2]["response"]["usage"]
    assert cold["input_tokens_details"]["cached_tokens"] == 0
    # The tool-less compaction (what Codex sent before patch 0027) does not count as matching.
    assert bench.compaction_tools_checks(server.requests) == {"compactions": 2, "matching": 1, "capped": 2}


def test_the_binary_is_checked_for_v2s_switches(tmp_path):
    binary = tmp_path / "bin" / "ling"
    binary.parent.mkdir()
    binary.write_bytes(b"\0" * (1 << 20) + b"DREAMFERENCE_MIGHTLING_TURN_END_COMPACTION\0DREAMFERENCE_MIGHTLING_COMPACT_SUMMARY_WORDS")
    info = bench.binary_info(binary)
    assert info["knows_turn_end_switch"] and info["knows_summary_cap"]
    assert not info["knows_compaction_tools"] and not info["knows_drop_old_outputs"]
    assert not info["code_mode_host_beside"]
    (binary.parent / "codex-code-mode-host").write_bytes(b"x")
    assert bench.binary_info(binary)["code_mode_host_beside"]


def test_the_stand_in_serves_models_and_metrics_on_a_loopback_port():
    server = bench.FakeModelServer()
    url = server.start()
    try:
        assert server.port not in (8000, 18000)
        models = json.loads(urllib.request.urlopen(f"{url}/v1/models", timeout=5).read())
        assert models["data"][0]["max_model_len"] == 262_144
        assert b"sglang:max_total_num_tokens" in urllib.request.urlopen(f"{url}/metrics", timeout=5).read()
    finally:
        server.stop()


@pytest.mark.installed_binary
@pytest.mark.skipif(os.environ.get("MIGHTLING_TEC_DRY_RUN") != "1",
                    reason="drives the installed ling for ~2 min; set MIGHTLING_TEC_DRY_RUN=1")
def test_the_driver_sees_both_kinds_of_compaction_the_probe_and_the_compaction_tools_against_the_stand_in(tmp_path):
    ling = Path(os.environ.get("MIGHTLING_BIN") or bench.ling_binary(None))
    result = bench.dry_run(tmp_path / "dry", ling, compaction_delay=8.0)
    assert all(result["checks"].values()), result["checks"]


def _item(kind, ms, role=None):
    payload = {"type": kind, **({"role": role} if role else {})}
    return {"type": "response_item", "payload": payload, "_ms": ms}


def test_the_probe_fires_on_a_turn_end_compaction_only():
    answer = _item("message", T0, "assistant")
    # A turn-end compaction: the final answer was written before the marker appeared.
    assert bench.turn_end_compaction_showing([answer], T0 + 500)
    # A mid-turn compaction: the marker appeared after a tool output; then its summary, an assistant
    # message, is written while the marker is still on screen (night-shift-on-1, 2026-10-13).
    tool = _item("function_call_output", T0)
    summary = _item("message", T0 + 160_000, "assistant")
    assert not bench.turn_end_compaction_showing([tool], T0 + 500)
    assert not bench.turn_end_compaction_showing([tool, summary], T0 + 500)
    assert not bench.turn_end_compaction_showing([answer], None)
