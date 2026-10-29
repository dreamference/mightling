"""The run store: which run a command without a name means."""

import os

from dreamference.swe_bench import SweBenchRunStore


def test_the_latest_run_is_the_last_started_not_the_last_written_to():
    """Writing a report into an older run does not make it the latest.

    `eval`, `report` and `status` without a name take the latest run.
    """
    older, newer = SweBenchRunStore("2026-10-01-a"), SweBenchRunStore("2026-10-02-a")
    older.write_manifest({"name": older.name})
    newer.write_manifest({"name": newer.name})
    os.utime(older.manifest_path, (1_000_000, 1_000_000))
    os.utime(older.directory, (1_000_000, 1_000_000))
    os.utime(newer.manifest_path, (2_000_000, 2_000_000))
    os.utime(newer.directory, (2_000_000, 2_000_000))
    # `swe-bench report` on the older run writes its report.md there, which moves the directory's
    # modification time to now.
    (older.directory / "report.md").write_text("# report\n")
    assert SweBenchRunStore.runs() == [older.name, newer.name]
    assert SweBenchRunStore.latest() == newer.name
