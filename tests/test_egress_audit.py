"""`ling-admin audit egress` (specs/DREAMFERENCE_MIGHTLING_EGRESS.md §3, §7).

The parser and the verdict are tested on a recorded trace of a real session and on the same trace
with the channels patches 0013 and 0015 closed written back in. No test here runs strace, ling
or the model server: the full-screen session is played by a stand-in on a real pseudo-terminal.
"""

import hashlib
import json
import subprocess
import os
import stat

import pytest

from dreamference.audit import EgressAudit, EgressTrace, StraceParser, TuiSession
from dreamference.audit.egress_verdict import FAIL, PASS, TRACE_FAILED

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "egress")
ALLOWED = EgressAudit.allowed_ports("http://localhost:8000")


def fixture(name: str) -> EgressTrace:
    with open(os.path.join(FIXTURES, name)) as handle:
        return StraceParser.parse(handle.read())


def test_the_recorded_session_reaches_only_the_model_server_and_gmail():
    trace = fixture("exec_pass.strace")
    assert trace.destinations == {"127.0.0.1:8000": 2, "127.0.0.1:8767": 1}
    assert trace.dns_names == {} and trace.dns_servers == {}
    assert trace.networked_git == []
    # nscd's socket is glibc asking for a name-service cache that is not there.
    assert "/var/run/nscd/socket" in trace.unix_sockets
    # The launcher's own helpers and Codex's git probes, each counted once per successful exec.
    assert trace.processes["ling"] == 1
    assert trace.processes["ling-code"] == 2
    assert trace.processes["git"] >= 5
    verdict = EgressAudit.judge(trace, ALLOWED, replied=True)
    assert (verdict.status, verdict.problems, verdict.exit_code) == (PASS, [], 0)


def test_the_channels_the_patches_closed_fail_the_verdict_by_name():
    trace = fixture("exec_leaks.strace")
    assert trace.dns_names == {"ab.chatgpt.com": 2, "chatgpt.com": 1, "raw.githubusercontent.com": 1}
    assert trace.dns_servers == {"127.0.0.53:53": 3}
    for target in ("104.18.32.47:443", "[2606:4700:4400::6812:202f]:443", "140.82.121.3:443", "185.199.108.133:443"):
        assert trace.destinations[target] == 1, target
    assert trace.networked_git == [
        "git -c core.hooksPath=/dev/null ls-remote https://github.com/openai/plugins.git",
        "git-remote-https https://github.com/openai/plugins.git https://github.com/openai/plugins.git",
    ]
    verdict = EgressAudit.judge(trace, ALLOWED, replied=True)
    assert verdict.status == FAIL and verdict.exit_code == 1
    report = "\n".join(verdict.problems)
    for needle in ("ab.chatgpt.com", "chatgpt.com (1x)", "raw.githubusercontent.com", "ls-remote https://github.com/openai/plugins.git",
                   "104.18.32.47:443 (1x): not on this machine", "[2606:4700:4400::6812:202f]:443"):
        assert needle in report, needle
    # The launcher's redirect of the ChatGPT backend is loopback, and still a finding.
    assert "127.0.0.1:9 (1x): a call to the upstream vendor's backend that no patch closes" in report
    # A leak is a failure whether or not the session answered.
    assert EgressAudit.judge(trace, ALLOWED, replied=False).status == FAIL


def test_a_real_lookup_and_connect_are_read_from_a_recording():
    # Recorded, not written by hand: this is what glibc and the kernel actually print.
    trace = fixture("curl_example.strace")
    assert trace.dns_names == {"example.com": 2}            # the A and the AAAA query
    assert trace.dns_servers == {"127.0.0.53:53": 1}
    assert set(trace.destinations) == {
        "104.20.23.154:443", "172.66.147.243:443", "[2606:4700:10::6814:179a]:443", "[2606:4700:10::ac42:93f3]:443"}
    # The netlink request getaddrinfo sends is not a destination, and nscd's socket is not one either.
    assert trace.processes == {"curl": 1} and list(trace.unix_sockets) == ["/var/run/nscd/socket"]
    verdict = EgressAudit.judge(trace, ALLOWED, replied=True)
    assert verdict.status == FAIL and "asked a resolver for example.com (2x)" in verdict.problems


def test_a_session_that_showed_nothing_is_not_a_pass():
    trace = fixture("exec_pass.strace")
    verdict = EgressAudit.judge(trace, ALLOWED, replied=False)
    assert (verdict.status, verdict.exit_code) == (TRACE_FAILED, 2)
    # strace could not attach: no line, no process.
    assert EgressAudit.judge(EgressTrace(), ALLOWED, replied=True).status == TRACE_FAILED


def test_a_local_port_off_the_allowlist_fails():
    trace = StraceParser.parse(
        '7 connect(3, {sa_family=AF_INET, sin_port=htons(8001), sin_addr=inet_addr("127.0.0.1")}, 16) = 0\n'
        '7 connect(4, {sa_family=AF_INET6, sin6_port=htons(8000), sin6_flowinfo=htonl(0), inet_pton(AF_INET6, "::1", &sin6_addr), sin6_scope_id=0}, 28) = 0\n'
        '7 execve("/bin/true", ["true"], 0x0 /* 1 var */) = 0\n')
    assert trace.destinations == {"127.0.0.1:8001": 1, "[::1]:8000": 1}
    verdict = EgressAudit.judge(trace, ALLOWED, replied=True)
    assert verdict.problems == ["connected to 127.0.0.1:8001 (1x): a local port that is not on the allowlist"]
    # The model server's port comes from its URL.
    assert 9000 in EgressAudit.allowed_ports("http://localhost:9000") and 8000 not in EgressAudit.allowed_ports("http://localhost:9000")
    assert 443 in EgressAudit.allowed_ports("https://models.internal")


def test_strace_strings_and_dns_payloads():
    assert StraceParser.unescape(r"\236\21\1 \0\1ab\x41\n\"q\\") == b"\x9e\x11\x01 \x00\x01abA\n\"q\\"
    assert StraceParser.strings_in(r'execve("/usr/bin/git", ["git", "a \"b\" c"], 0x1)') == [b"/usr/bin/git", b"git", b'a "b" c']
    query = b"\x9e\x11\x01\x20\x00\x01\x00\x00\x00\x00\x00\x01\x0baudit-probe\x07example\x03com\x00\x00\x01\x00\x01"
    assert StraceParser.dns_query_name(query) == "audit-probe.example.com"
    # A response (QR set), a short payload and a name cut off by strace's limit are not names.
    assert StraceParser.dns_query_name(query[:2] + b"\x81" + query[3:]) is None
    assert StraceParser.dns_query_name(b"GET / HTTP/1.1\r\n") is None
    assert StraceParser.dns_query_name(query[:20]) is None


def test_a_query_whose_name_cannot_be_read_still_fails():
    # glibc connects the socket first; a payload strace cut before the name ended leaves no name.
    trace = StraceParser.parse(
        '9 connect(3, {sa_family=AF_INET, sin_port=htons(53), sin_addr=inet_addr("192.168.50.1")}, 16) = 0\n'
        '9 sendto(3, "\\1\\2\\1\\0\\0\\1\\0\\0\\0\\0\\0\\0\\77aaaa"..., 300, MSG_NOSIGNAL, NULL, 0) = 300\n'
        '9 execve("/bin/true", ["true"], 0x0 /* 1 var */) = 0\n')
    assert trace.dns_names == {} and trace.dns_servers == {"192.168.50.1:53": 1}
    verdict = EgressAudit.judge(trace, ALLOWED, replied=True)
    assert verdict.status == FAIL and "192.168.50.1:53" in verdict.problems[0]


def test_git_commands_that_reach_nothing_are_not_flagged():
    lines = "\n".join(f'1 execve("/usr/bin/git", {json.dumps(argv)}, 0x0 /* 1 var */) = 0' for argv in (
        ["git", "--no-optional-locks", "rev-parse", "HEAD"],
        ["git", "ls-remote", "--get-url", "origin"],          # prints a URL; contacts nothing
        ["git", "-C", "fetch", "status"],                      # a directory named `fetch`
        ["git", "-c", "alias.x=fetch", "log", "--grep", "pull"],
        ["git", "add", "clone"],
    ))
    assert StraceParser.parse(lines).networked_git == []
    networked = StraceParser.parse(
        '1 execve("/usr/bin/git", ["git", "-C", "/repo", "fetch", "origin"], 0x0 /* 1 var */) = 0\n'
        '1 execve("/usr/bin/git", ["git", "pull"], 0x0 /* 1 var */) = -1 ENOENT (No such file or directory)\n')
    assert networked.networked_git == ["git -C /repo fetch origin"], "a failed execve ran nothing"


def test_the_report_lists_everything_seen():
    trace = fixture("exec_leaks.strace")
    lines = EgressAudit.render(trace, EgressAudit.judge(trace, ALLOWED, True), ALLOWED)
    text = "\n".join(lines)
    assert lines[0] == "❌ Egress audit: fail"
    assert "127.0.0.1:8000" in text and "model server" in text and "Gmail search service" in text
    assert "104.18.32.47:443" in text and "not on this machine" in text
    assert "DNS queries: ab.chatgpt.com (2x)" in text
    ok = fixture("exec_pass.strace")
    lines = EgressAudit.render(ok, EgressAudit.judge(ok, ALLOWED, True), ALLOWED)
    assert lines[0] == "✅ Egress audit: pass" and "DNS queries: none" in lines and "Networked git commands: none" in lines


def test_the_audit_runs_the_session_in_a_throwaway_repository_and_home(tmp_path, monkeypatch, capsys):
    # A stand-in for strace: it records how it was called and plays back the recorded trace.
    calls = tmp_path / "calls.json"
    strace = tmp_path / "bin" / "strace"
    strace.parent.mkdir()
    strace.write_text(f"""#!{os.sys.executable}
import json, os, shutil, sys
args = sys.argv[1:]
json.dump({{"args": args, "cwd": os.getcwd(), "CODEX_HOME": os.environ.get("CODEX_HOME"),
           "config": open(os.environ["DREAMFERENCE_CONFIG_PATH"]).read(),
           "host": os.environ.get("DREAMFERENCE_VLLM_HOST")}}, open({str(calls)!r}, "w"))
shutil.copy({os.path.join(FIXTURES, "exec_pass.strace")!r}, args[args.index("-o") + 1])
open(args[len(args) - 2], "w").write("pong")
""")
    strace.chmod(strace.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{strace.parent}{os.pathsep}{os.environ['PATH']}")
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    code = EgressAudit.run(write_json=True, mightling_bin="/opt/ling", vllm_host="http://localhost:8000")
    out = capsys.readouterr().out
    assert code == 0 and "✅ Egress audit: pass" in out
    call = json.loads(calls.read_text())
    assert call["args"][:7] == ["-f", "-qq", "-e", "trace=connect,sendto,sendmsg,sendmmsg,execve", "-s", "256", "-o"]
    assert call["args"][8:11] == ["/opt/ling", "exec", "--skip-git-repo-check"]
    assert call["args"][-1] == "Reply with exactly: pong"
    # Neither the user's home nor the user's repository: both are scratch, and gone afterwards.
    assert call["CODEX_HOME"] != str(home) and not os.path.exists(call["CODEX_HOME"])
    assert os.path.basename(call["cwd"]) == "repo" and not os.path.exists(call["cwd"])
    assert 'vllm_host = "http://localhost:8000"' in call["config"] and "code_index_enabled = false" in call["config"]
    # The JSON result is written to the real CODEX_HOME, and says which build it was.
    (result_file,) = list((home / "audit").glob("*.json"))
    result = json.loads(result_file.read_text())
    assert result["verdict"] == "pass" and result["destinations"] == {"127.0.0.1:8000": 2, "127.0.0.1:8767": 1}
    assert result["codex_tag"].startswith("rust-v")
    # /opt/ling is not the installed build: no build key and no patch list are credited to it.
    assert result["mightling_bin"] == "/opt/ling" and result["build_key"] == ""
    assert result["build_matches_checkout"] is False and result["patches"] is None


def test_the_identity_names_the_traced_binary_and_credits_patches_only_to_a_matching_install(tmp_path, monkeypatch):
    from dreamference.runner import codex_branded_builder as builder
    install = tmp_path / "install"
    (install / "bin").mkdir(parents=True)
    installed = install / "bin" / builder.BRANDED_EXECUTABLE_NAME
    installed.write_bytes(b"installed")
    scratch = tmp_path / "scratch-ling"
    scratch.write_bytes(b"without 0015")
    monkeypatch.setattr(builder, "INSTALL_DIR", str(install))
    monkeypatch.setattr(builder.CodexBrandedBuilder, "build_key", classmethod(lambda cls: "key-1"))
    (install / builder.BUILD_STAMP_NAME).write_text("key-1\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "ling 0.158.0", ""))

    ours = EgressAudit.build_identity(str(installed))
    assert ours["build_key"] == "key-1" and ours["build_matches_checkout"] is True
    assert "0001-brand-mightling-name.patch" in ours["patches"]
    assert ours["mightling_sha256"] == hashlib.sha256(b"installed").hexdigest()

    # A scratch build beside it is told apart by its hash, and is credited with nothing.
    theirs = EgressAudit.build_identity(str(scratch))
    assert theirs["build_key"] == "" and theirs["patches"] is None
    assert theirs["mightling_sha256"] == hashlib.sha256(b"without 0015").hexdigest()

    # The installed binary built from another checkout: its key is kept, the patch list is not.
    (install / builder.BUILD_STAMP_NAME).write_text("key-0\n")
    stale = EgressAudit.build_identity(str(installed))
    assert stale["build_key"] == "key-0" and stale["build_matches_checkout"] is False and stale["patches"] is None


def test_without_a_reply_the_audit_says_the_trace_failed(tmp_path, monkeypatch, capsys):
    strace = tmp_path / "bin" / "strace"
    strace.parent.mkdir()
    strace.write_text("#!/bin/sh\nexit 1\n")
    strace.chmod(0o755)
    monkeypatch.setenv("PATH", f"{strace.parent}{os.pathsep}{os.environ['PATH']}")
    assert EgressAudit.run(mightling_bin="/opt/ling", vllm_host="http://localhost:8000") == 2
    out = capsys.readouterr().out
    assert "Egress audit: trace failed" in out and "ling-admin server start" in out


@pytest.mark.parametrize("missing", ["strace", "ling"])
def test_missing_tools_are_named(missing, tmp_path, monkeypatch, capsys):
    if missing == "strace":
        monkeypatch.setenv("PATH", str(tmp_path))
        assert EgressAudit.run(mightling_bin="/opt/ling", vllm_host="http://x") == 2
        assert "strace is not installed" in capsys.readouterr().out
    else:
        monkeypatch.setattr("dreamference.runner.codex_installer.CodexInstaller.get_codex_executable", classmethod(lambda cls: None))
        if __import__("shutil").which("strace") is None:
            pytest.skip("strace is not installed here")
        assert EgressAudit.run(vllm_host="http://x") == 2
        assert "ling is not built" in capsys.readouterr().out


# -- the full-screen session (--tui) ---------------------------------------------------------------

def test_the_recorded_interface_session_reaches_only_the_model_server_and_gmail():
    trace = fixture("tui_pass.strace")
    assert trace.destinations == {"127.0.0.1:8000": 3, "127.0.0.1:8767": 1}
    assert trace.dns_names == {} and trace.dns_servers == {} and trace.networked_git == []
    # What `exec` never opens: the session bus (the interface asks the desktop's accessibility
    # service). A unix socket is listed, and is not a destination.
    assert "/run/user/1000/bus" in trace.unix_sockets
    assert trace.processes["ling"] == 1
    assert EgressAudit.judge(trace, ALLOWED, True).status == PASS


def test_the_reply_is_read_from_the_session_file(tmp_path):
    assert TuiSession.reply_in(str(tmp_path)) is None
    rollout = tmp_path / "sessions" / "2026" / "10" / "02" / "rollout-x.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user"}}) + "\n"
                       + "not json\n")
    assert TuiSession.reply_in(str(tmp_path)) is None  # the prompt alone is not a reply
    with open(rollout, "a") as handle:
        handle.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "pong"}}) + "\n")
    assert TuiSession.reply_in(str(tmp_path)) == "pong"


def interface_stand_in(tmp_path, monkeypatch, answers: bool):
    """A stand-in for strace that plays the interface on the pseudo-terminal it is given."""
    pytest.importorskip("pexpect")
    pytest.importorskip("pyte")
    calls = tmp_path / "calls.json"
    strace = tmp_path / "bin" / "strace"
    strace.parent.mkdir()
    strace.write_text(f"""#!{os.sys.executable}
import json, os, shutil, sys, time
args = sys.argv[1:]
home = os.environ["CODEX_HOME"]
record = {{"args": args, "cwd": os.getcwd(), "CODEX_HOME": home, "pid": os.getpid(), "tty": sys.stdin.isatty(),
          "term": os.environ.get("TERM"), "config": open(os.path.join(home, "config.toml")).read(), "typed": []}}
def save():
    json.dump(record, open({str(calls)!r}, "w"))
save()
print(">_ Mightling (v0.0.0)", flush=True)
print("› ", end="", flush=True)
record["typed"].append(sys.stdin.readline().strip()); save()
if not {answers!r}:
    time.sleep(600)
sessions = os.path.join(home, "sessions", "2026", "10", "02")
os.makedirs(sessions)
with open(os.path.join(sessions, "rollout-test.jsonl"), "w") as handle:
    handle.write(json.dumps({{"type": "event_msg", "payload": {{"type": "task_complete", "last_agent_message": "pong"}}}}) + "\\n")
shutil.copy({os.path.join(FIXTURES, "tui_pass.strace")!r}, args[args.index("-o") + 1])
record["typed"].append(sys.stdin.readline().strip()); save()
""")
    strace.chmod(strace.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{strace.parent}{os.pathsep}{os.environ['PATH']}")
    for name in ("SETTLE_AFTER_READY_S", "SETTLE_AFTER_REPLY_S", "TYPE_PAUSE_S"):
        monkeypatch.setattr(f"dreamference.audit.tui_session.{name}", 0.2)
    return calls


def test_the_interface_is_opened_prompted_and_quit_on_a_pseudo_terminal(tmp_path, monkeypatch, capsys):
    calls = interface_stand_in(tmp_path, monkeypatch, answers=True)
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    code = EgressAudit.run(write_json=True, mightling_bin="/opt/ling", vllm_host="http://localhost:8000", tui=True)
    out = capsys.readouterr().out
    assert code == 0 and "✅ Egress audit: pass" in out and "full-screen `ling` session" in out
    call = json.loads(calls.read_text())
    # The interface itself: `ling` with no subcommand, on a terminal.
    assert call["args"][:7] == ["-f", "-qq", "-e", "trace=connect,sendto,sendmsg,sendmmsg,execve", "-s", "256", "-o"]
    assert call["args"][8:] == ["/opt/ling"]
    assert call["tty"] is True and call["term"] == "xterm-256color"
    # It typed the prompt, and after the reply it quit.
    assert call["typed"] == ["Reply with exactly: pong", "/quit"]
    assert EgressAudit.tui_stage == "quit"
    # The throwaway home trusts the throwaway repository, so the session opens on the composer.
    assert f'[projects."{os.path.realpath(call["cwd"])}"]' in call["config"] and 'trust_level = "trusted"' in call["config"]
    assert call["CODEX_HOME"] != str(home) and not os.path.exists(call["CODEX_HOME"]) and not os.path.exists(call["cwd"])
    (result_file,) = list((home / "audit").glob("*-tui.json"))
    result = json.loads(result_file.read_text())
    assert result["session"] == "tui" and result["verdict"] == "pass"
    assert result["destinations"] == {"127.0.0.1:8000": 3, "127.0.0.1:8767": 1}


def test_an_interface_that_never_answers_is_stopped_and_is_not_a_pass(tmp_path, monkeypatch, capsys):
    calls = interface_stand_in(tmp_path, monkeypatch, answers=False)
    monkeypatch.setattr("dreamference.audit.egress_audit.SESSION_TIMEOUT_S", 3)
    assert EgressAudit.run(mightling_bin="/opt/ling", vllm_host="http://localhost:8000", tui=True) == 2
    out = capsys.readouterr().out
    assert "Egress audit: trace failed" in out and "The interface opened and took the prompt" in out
    assert EgressAudit.tui_stage == "composer"
    # Nothing is left running.
    pid = json.loads(calls.read_text())["pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_tui_without_its_terminal_modules_says_how_to_add_them(monkeypatch, capsys):
    if __import__("shutil").which("strace") is None:
        pytest.skip("strace is not installed here")
    monkeypatch.setattr(TuiSession, "missing_modules", classmethod(lambda cls: ["pexpect", "pyte"]))
    assert EgressAudit.run(mightling_bin="/opt/ling", vllm_host="http://x", tui=True) == 2
    out = capsys.readouterr().out
    assert "trace failed" in out and "pip install pexpect pyte" in out


# -- after `ling-admin codex build` ---------------------------------------------------------------

@pytest.fixture
def after_build(monkeypatch):
    """`EgressAudit.after_build` with the model server and the sessions replaced."""
    runs = []
    state = {"served": ("model", 4096), "codes": {False: 0, True: 0}}
    monkeypatch.setattr("dreamference.night_shift.NightShiftHost.served_model",
                        classmethod(lambda cls, host, timeout=3.0: state["served"]))
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(TuiSession, "missing_modules", classmethod(lambda cls: []))

    def run(cls, prompt=None, write_json=False, mightling_bin=None, vllm_host=None, tui=False):
        runs.append({"tui": tui, "write_json": write_json, "vllm_host": vllm_host})
        return state["codes"][tui]
    monkeypatch.setattr(EgressAudit, "run", classmethod(run))
    return state, runs


def test_after_a_build_both_kinds_of_session_are_traced_and_recorded(after_build, capsys):
    _, runs = after_build
    assert EgressAudit.after_build(vllm_host="http://localhost:8000") == 0
    assert runs == [{"tui": False, "write_json": True, "vllm_host": "http://localhost:8000"},
                    {"tui": True, "write_json": True, "vllm_host": "http://localhost:8000"}]


def test_after_a_build_without_a_model_server_the_audit_does_not_wait(after_build, capsys):
    state, runs = after_build
    state["served"] = None
    assert EgressAudit.after_build(vllm_host="http://localhost:8000") is None
    out = capsys.readouterr().out
    assert runs == [] and "Egress audit skipped" in out and "ling-admin audit egress --tui" in out


def test_after_a_build_an_unexpected_destination_is_said_plainly(after_build, capsys):
    state, runs = after_build
    state["codes"] = {False: 2, True: 1}  # a failed trace must not hide a failing verdict
    assert EgressAudit.after_build(vllm_host="http://localhost:8000") == 1
    assert "❌ This build reaches something it should not" in capsys.readouterr().out
    state["codes"] = {False: 0, True: 2}
    assert EgressAudit.after_build(vllm_host="http://localhost:8000") == 2
    assert "could not show what this build does" in capsys.readouterr().out


def test_after_a_build_a_broken_audit_is_reported_not_raised(after_build, monkeypatch, capsys):
    def broken(cls, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(EgressAudit, "run", classmethod(broken))
    assert EgressAudit.after_build(vllm_host="http://localhost:8000") is None
    assert "Egress audit did not run (boom)" in capsys.readouterr().out


@pytest.mark.parametrize("argv, was_current, built, audited", [
    (["codex", "build"], False, True, True),                  # a new binary: audit it
    (["codex", "build"], True, True, False),                  # nothing was built
    (["codex", "build", "--force"], True, True, True),        # rebuilt on request
    (["codex", "build", "--no-audit"], False, True, False),
    (["codex", "build"], False, False, False),                # the build failed: nothing to audit
])
def test_codex_build_audits_a_new_binary_and_keeps_its_own_exit_code(argv, was_current, built, audited, monkeypatch):
    from dreamference.cli import dreamference_cli_controller as controller
    from dreamference.runner.codex_branded_builder import CodexBrandedBuilder
    calls = []
    monkeypatch.setattr(controller.DreamferenceCLIController, "_refuse_during_night_run", classmethod(lambda cls, what: None))
    monkeypatch.setattr("dreamference.node.NodeIdentity.ensure", classmethod(lambda cls: None))
    monkeypatch.setattr(CodexBrandedBuilder, "is_current", classmethod(lambda cls: was_current))
    monkeypatch.setattr(CodexBrandedBuilder, "build", classmethod(lambda cls, force=False: built))
    # A failing verdict: the build's exit code must not change with it.
    monkeypatch.setattr(EgressAudit, "after_build", classmethod(lambda cls: calls.append("audit") or 1))
    monkeypatch.setattr("sys.argv", ["ling-admin", *argv])
    with pytest.raises(SystemExit) as exit_info:
        controller.main()
    assert exit_info.value.code == (0 if built else 1)
    assert calls == (["audit"] if audited else [])
