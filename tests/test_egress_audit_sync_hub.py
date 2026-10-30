"""What the sync audit reads from the node's hub after the agent's run.

Whether the interactive thread was published and whether the `exec` session was held back, on a
stand-in for `ling` and for strace (nothing reaches a network).
"""

import os
import stat
from unittest.mock import patch

import pytest

from dreamference.audit import EgressAudit, SyncEgressAudit
from dreamference.audit.sync_egress_audit import EXEC_ID, THREAD_ID

PORT = 41234


@pytest.mark.parametrize(
    "files, published, exec_held",
    [
        ([], False, True),
        ([f"{THREAD_ID}.json", f"{EXEC_ID}.json"], True, False),
        ([f"{THREAD_ID}.json", f"{EXEC_ID}.jsonl"], True, False),
    ],
)
def test_the_hub_is_read_for_the_published_thread_and_the_held_exec_session(
    tmp_path, monkeypatch, files, published, exec_held
):
    # The stand-in agent leaves exactly `files` in the node's hub.
    publish = "".join(f" echo '{{}}' > \"$hub/{name}\";" for name in files)
    ling = tmp_path / "ling"
    ling.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        "  'web serve') mkdir -p \"$CODEX_HOME/web\"; echo '{}' > \"$CODEX_HOME/web/server.json\"; exec sleep 30 ;;\n"
        "  'web pair') echo 'Pairing code: 1234 5678' ;;\n"
        f'  \'sync agent\') hub="$(dirname "$HOME")/node/.mightling/sync/hub/rollouts"; mkdir -p "$hub";{publish} ;;\n'
        "esac\nexit 0\n"
    )
    ling.chmod(ling.stat().st_mode | stat.S_IEXEC)
    strace = tmp_path / "strace"
    strace.write_text('#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done; shift 2; exec "$@"\n')
    strace.chmod(strace.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    work = tmp_path / "work"
    work.mkdir()
    with patch.object(EgressAudit, "free_port", return_value=PORT):
        _, _, was_published, was_held, _ = SyncEgressAudit.trace(str(ling), str(work))
    assert (was_published, was_held) == (published, exec_held)
