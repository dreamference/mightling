"""`ling-admin audit egress --docs` run end to end on a stand-in for strace (specs/DREAMFERENCE_MIGHTLING_EGRESS.md).

No test here runs strace or `ling-docs`: a stand-in on PATH plays strace and writes the trace.
"""

import json

from dreamference.audit import DocsEgressAudit

GITHUB = "140.82.112.3:443"


def _strace(tmp_path, monkeypatch, body: str, alone: bool = False) -> None:
    strace = tmp_path / "bin" / "strace"
    strace.parent.mkdir(exist_ok=True)
    strace.write_text(body)
    strace.chmod(0o755)
    path = str(strace.parent)
    if not alone:
        path = f"{path}:/usr/bin:/bin"
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))


def test_a_ling_docs_whose_trace_cannot_start_fails_the_trace_and_says_why(
    tmp_path, monkeypatch, capsys
):
    # strace is found but cannot be run: its interpreter does not exist, so starting each step
    # raises. PATH holds only the stand-in, so no other strace further down PATH runs instead.
    _strace(tmp_path, monkeypatch, "#!/nonexistent/interpreter\n", alone=True)
    # Executable but unreadable: the result still gets written, without a hash.
    ling_docs = tmp_path / "ling-docs"
    ling_docs.write_text("#!/bin/sh\n")
    ling_docs.chmod(0o111)
    assert DocsEgressAudit.run(ling_docs=str(ling_docs), write_json=True) == 2
    out = capsys.readouterr().out
    assert "Docs egress audit: trace failed" in out
    assert "strace recorded no process" in out
    # Each step's error is shown, so the reason the trace failed is on the screen.
    for step in ("index", "search"):
        assert f"   {step}: [Errno 2] No such file or directory: 'strace'" in out
    [written] = (tmp_path / "codex-home" / "audit").glob("*-docs.json")
    result = json.loads(written.read_text())
    assert result["verdict"] == "trace failed"
    assert result["ling_docs_sha256"] is None and result["destinations"] == {}


def test_a_ling_docs_run_that_connects_out_fails_and_names_where_it_went(
    tmp_path, monkeypatch, capsys
):
    # The stand-in writes a trace in which ling-docs connects to a public address, and prints what
    # a successful index and search print, so only the destination can fail the audit.
    host, port = GITHUB.split(":")
    _strace(
        tmp_path,
        monkeypatch,
        '#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done\n'
        'cat > "$2" <<EOF\n'
        '100 execve("/opt/ling-docs", ["ling-docs"], 0x0 /* 30 vars */) = 0\n'
        f"100 connect(9, {{sa_family=AF_INET, sin_port=htons({port}), "
        f'sin_addr=inet_addr("{host}")}}, 16) = -1 EINPROGRESS\n'
        "EOF\n"
        "echo 'documents: ok'\necho 'Puffins nest in burrows.'\n",
    )
    ling_docs = tmp_path / "ling-docs"
    ling_docs.write_text("#!/bin/sh\n")
    ling_docs.chmod(0o755)
    assert DocsEgressAudit.run(ling_docs=str(ling_docs)) == 1
    out = capsys.readouterr().out
    assert "Docs egress audit: fail" in out
    assert f"   - connected or sent to {GITHUB} (2x)" in out
    assert f"Network destinations: {GITHUB}" in out
    # A failed audit is not a failed trace: the steps' output is not repeated.
    assert "   index: " not in out
