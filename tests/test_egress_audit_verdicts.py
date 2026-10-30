"""The egress audits' verdicts on what each one reads from its trace.

The docs audit's reasons, the app audit's loopback allowlist and preview page, and the result
file a failing verdict leaves behind.
"""

import os
import stat
import sys
from pathlib import Path

from dreamference.audit import DocsEgressAudit, EgressAudit
from dreamference.audit.egress_trace import EgressTrace
from dreamference.audit.egress_verdict import FAIL, TRACE_FAILED
from dreamference.chat.gmail_search_service import HOST_PORT as GMAIL_HOST_PORT
from dreamference.chat.image_search_sidecar import IMAGE_SEARCH_HOST_PORT
from dreamference.chat.searxng_sidecar import SEARXNG_HOST_PORT


def test_the_docs_audit_names_every_name_it_asked_a_resolver_for():
    trace = EgressTrace(dns_names={"example.com": 1}, processes={"ling-docs": 1})
    verdict = DocsEgressAudit.judge(trace, indexed=True, found=True)
    assert (verdict.status, verdict.problems) == (FAIL, ["asked a resolver for example.com (1x)"])


def test_the_docs_audit_without_a_process_traced_is_a_failed_trace_not_a_pass():
    # A clean trace of nothing proves nothing: strace never attached.
    verdict = DocsEgressAudit.judge(EgressTrace(), indexed=True, found=True)
    assert (verdict.status, verdict.problems) == (TRACE_FAILED, ["strace recorded no process: it could not attach"])


def test_the_loopback_allowlist_is_the_model_server_and_the_services_on_their_own_ports():
    # Each port is the one its service listens on: a wrong number blocks the service and allows a stranger.
    allowed = EgressAudit.allowed_ports("http://127.0.0.1:8000")
    assert set(allowed) == {8000, GMAIL_HOST_PORT, SEARXNG_HOST_PORT, IMAGE_SEARCH_HOST_PORT}


FIXTURES = Path(__file__).parent / "fixtures" / "egress"


def test_the_app_may_reach_the_audits_own_preview_page(tmp_path, monkeypatch, capsys):
    # A stand-in for strace: plays back a passing trace plus a connect to the preview page's port,
    # and loads the page as the app's preview tab does.
    strace = tmp_path / "bin" / "strace"
    strace.parent.mkdir()
    strace.write_text(f"""#!{sys.executable}
import os, shutil, sys, urllib.parse, urllib.request
args = sys.argv[1:]
output = args[args.index("-o") + 1]
shutil.copy({str(FIXTURES / "exec_pass.strace")!r}, output)
preview = os.environ["MIGHTLING_APP_AUDIT_PREVIEW"]
port = urllib.parse.urlparse(preview).port
with open(output, "a") as handle:
    handle.write(f'4242 connect(30<TCP:[9000]>, {{{{sa_family=AF_INET, sin_port=htons({{port}}), '
                 f'sin_addr=inet_addr("127.0.0.1")}}}}, 16) = 0\\n')
urllib.request.urlopen(preview + "seen", timeout=5).read()
""")
    strace.chmod(strace.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{strace.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    # The machine's own services are not asked which bridges are enabled.
    monkeypatch.setattr(EgressAudit, "declared_exceptions", classmethod(lambda cls: []))
    code = EgressAudit.run(
        mightling_bin="/opt/ling", vllm_host="http://localhost:8000", app=True, app_bin="/opt/Mightling"
    )
    out = capsys.readouterr().out
    # The connect was read, and allowed only as the preview page.
    assert code == 0 and "1x  the audit's preview page" in out
