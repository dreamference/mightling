"""The egress audits' verdicts on what each one reads from its trace.

The docs audit's reasons, the app audit's loopback allowlist and preview page, and the result
file a failing verdict leaves behind.
"""

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
