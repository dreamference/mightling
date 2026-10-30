"""The egress audits' verdicts on what each one reads from its trace.

The docs audit's reasons, the app audit's loopback allowlist and preview page, and the result
file a failing verdict leaves behind.
"""

from dreamference.audit import DocsEgressAudit
from dreamference.audit.egress_trace import EgressTrace
from dreamference.audit.egress_verdict import FAIL


def test_the_docs_audit_names_every_name_it_asked_a_resolver_for():
    trace = EgressTrace(dns_names={"example.com": 1}, processes={"ling-docs": 1})
    verdict = DocsEgressAudit.judge(trace, indexed=True, found=True)
    assert (verdict.status, verdict.problems) == (FAIL, ["asked a resolver for example.com (1x)"])
