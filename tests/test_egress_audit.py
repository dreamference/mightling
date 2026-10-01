"""`puffin-admin audit egress` (specs/DREAMFERENCE_PUFFIN_EGRESS.md §3, §7).

The parser and the verdict are tested on a recorded trace of a real session and on the same trace
with the channels patches 0013 and 0015 closed written back in. No test here runs strace, puffin
or the model server.
"""

import json
import os
import stat

import pytest

from dreamference.audit import EgressAudit, EgressTrace, StraceParser
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
    assert trace.processes["puffin"] == 1
    assert trace.processes["puffin-code"] == 2
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
    assert "127.0.0.1:9 (1x): a ChatGPT-backend call that no patch closes" in report
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
    code = EgressAudit.run(write_json=True, puffin_bin="/opt/puffin", vllm_host="http://localhost:8000")
    out = capsys.readouterr().out
    assert code == 0 and "✅ Egress audit: pass" in out
    call = json.loads(calls.read_text())
    assert call["args"][:7] == ["-f", "-qq", "-e", "trace=connect,sendto,sendmsg,sendmmsg,execve", "-s", "256", "-o"]
    assert call["args"][8:11] == ["/opt/puffin", "exec", "--skip-git-repo-check"]
    assert call["args"][-1] == "Reply with exactly: pong"
    # Neither the user's home nor the user's repository: both are scratch, and gone afterwards.
    assert call["CODEX_HOME"] != str(home) and not os.path.exists(call["CODEX_HOME"])
    assert os.path.basename(call["cwd"]) == "repo" and not os.path.exists(call["cwd"])
    assert 'vllm_host = "http://localhost:8000"' in call["config"] and "code_index_enabled = false" in call["config"]
    # The JSON result is written to the real CODEX_HOME, and says which build it was.
    (result_file,) = list((home / "audit").glob("*.json"))
    result = json.loads(result_file.read_text())
    assert result["verdict"] == "pass" and result["destinations"] == {"127.0.0.1:8000": 2, "127.0.0.1:8767": 1}
    assert result["codex_tag"].startswith("rust-v") and "0001-brand-puffin-name.patch" in result["patches"]


def test_without_a_reply_the_audit_says_the_trace_failed(tmp_path, monkeypatch, capsys):
    strace = tmp_path / "bin" / "strace"
    strace.parent.mkdir()
    strace.write_text("#!/bin/sh\nexit 1\n")
    strace.chmod(0o755)
    monkeypatch.setenv("PATH", f"{strace.parent}{os.pathsep}{os.environ['PATH']}")
    assert EgressAudit.run(puffin_bin="/opt/puffin", vllm_host="http://localhost:8000") == 2
    out = capsys.readouterr().out
    assert "Egress audit: trace failed" in out and "puffin-admin server start" in out


@pytest.mark.parametrize("missing", ["strace", "puffin"])
def test_missing_tools_are_named(missing, tmp_path, monkeypatch, capsys):
    if missing == "strace":
        monkeypatch.setenv("PATH", str(tmp_path))
        assert EgressAudit.run(puffin_bin="/opt/puffin", vllm_host="http://x") == 2
        assert "strace is not installed" in capsys.readouterr().out
    else:
        monkeypatch.setattr("dreamference.runner.codex_installer.CodexInstaller.get_codex_executable", classmethod(lambda cls: None))
        if __import__("shutil").which("strace") is None:
            pytest.skip("strace is not installed here")
        assert EgressAudit.run(vllm_host="http://x") == 2
        assert "puffin is not built" in capsys.readouterr().out
