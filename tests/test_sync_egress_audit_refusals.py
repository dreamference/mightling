"""`ling-admin audit egress --sync` when it cannot trace (specs/DREAMFERENCE_MIGHTLING_EGRESS.md §7).

No test here runs strace or ling: the tools are missing, or `ling` is a path that does not exist.
"""

import json
from unittest.mock import patch

from dreamference.audit import SyncEgressAudit
from dreamference.audit.egress_verdict import TRACE_FAILED


def test_without_strace_the_sync_audit_fails_its_trace_and_starts_nothing(capsys):
    with (
        patch("shutil.which", return_value=None),
        patch.object(SyncEgressAudit, "trace", side_effect=AssertionError("nothing to trace with")),
    ):
        assert SyncEgressAudit.run(ling="/opt/ling") == 2
    assert "strace is not installed" in capsys.readouterr().out


def test_without_a_built_ling_the_sync_audit_fails_its_trace_and_starts_nothing(capsys):
    with (
        patch("shutil.which", return_value="/usr/bin/strace"),
        patch(
            "dreamference.runner.codex_installer.CodexInstaller.get_codex_executable",
            return_value=None,
        ),
        patch.object(SyncEgressAudit, "trace", side_effect=AssertionError("nothing to trace")),
    ):
        assert SyncEgressAudit.run() == 2
    assert "ling is not built" in capsys.readouterr().out


def test_a_hub_that_cannot_start_is_a_failed_trace_and_its_result_says_so(
    tmp_path, monkeypatch, capsys
):
    # `ling web serve` cannot start: nothing was traced, so the cycle neither passes nor names a
    # destination, and the result file keeps the failed verdict with no hash of a missing binary.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    ling = str(tmp_path / "no-such-ling")
    with patch("shutil.which", return_value="/usr/bin/strace"):
        assert SyncEgressAudit.run(ling=ling, write_json=True) == 2
    out = capsys.readouterr().out
    assert f"Sync egress audit: {TRACE_FAILED}" in out and "web serve:" in out
    (result_file,) = list((tmp_path / "codex-home" / "audit").glob("*-sync.json"))
    result = json.loads(result_file.read_text())
    assert result["verdict"] == TRACE_FAILED and result["problems"]
    assert result["ling"] == ling and result["ling_sha256"] is None
    assert result["destinations"] == {}
