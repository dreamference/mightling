"""The scrub leaves the image's sources writable by the agent, and the patch unchanged.

The images ship `/testbed` as root's and 0644 while the agent runs as the host's user
(MIGHTLING_PROMPT §6.1 item 1; MIGHTLING_ADVISOR_NODE §8.1 item 1: "`chmod -R a+rwX /testbed`
before the agent starts, shipped in `SCRUB_SCRIPT`").
"""

import os
import stat
import subprocess

from dreamference.swe_bench.swe_bench_instance_run import SCRUB_SCRIPT


def git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )


def test_the_scrub_makes_every_source_writable_without_changing_what_git_sees(tmp_path):
    repo = tmp_path / "testbed"
    (repo / "pkg").mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "pkg" / "mod.py").write_text("x = 1\n")
    (repo / "run.sh").write_text("#!/bin/sh\n")
    (repo / "run.sh").chmod(0o755)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    # As the image ships them: read-only files in a directory others cannot write to.
    (repo / "pkg" / "mod.py").chmod(0o444)
    (repo / "pkg").chmod(0o555)
    try:
        scrubbed = subprocess.run(
            ["bash", "-c", SCRUB_SCRIPT],
            env=dict(os.environ, TESTBED=str(repo)),
            capture_output=True,
            text=True,
        )
        assert scrubbed.returncode == 0, scrubbed.stderr
        mode = lambda path: stat.S_IMODE(path.stat().st_mode)  # noqa: E731
        assert mode(repo / "pkg" / "mod.py") & 0o666 == 0o666
        assert mode(repo / "pkg") & 0o777 == 0o777
        # X adds execute only where it was: a source file does not become executable.
        assert mode(repo / "pkg" / "mod.py") & 0o111 == 0
        assert mode(repo / "run.sh") & 0o777 == 0o777
        assert git(repo, "status", "--porcelain").stdout == ""
    finally:
        (repo / "pkg").chmod(0o755)
