"""The forge's storage budget (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §5.2).

On the forge of record the forge keeps to 10 GB in all: repositories and indexes (an alarm at
1 GB), job logs and artefacts (expiring after 7 days, by Forgejo), and one local dump (older dumps
live on two other nodes). When the node's free space drops below its floor plus 10 GB (150 GB, or
175 GB while a SWE-bench night runs), the forge prunes artefacts first, then logs, then the local
dump, oldest first, and says so. Repositories are never pruned.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Final

from dreamference.forge.forge_settings import (
    BUDGET_GB,
    PRUNE_MARGIN_GB,
    REPOSITORY_ALARM_GB,
    ForgeSettings,
)

GB: Final[int] = 1000**3
# What may be pruned, in order, relative to the data folder (the container's /var/lib/gitea).
PRUNABLE: Final[tuple] = ("data/actions_artifacts", "data/actions_log", "log", "dumps")


class ForgeStorage:
    """Measures and prunes."""

    @classmethod
    def size(cls, path: Path) -> int:
        """Measure a file's or a folder's size.

        Args:
            path: A file or folder.

        Returns:
            int: Its size in bytes (0 when missing).
        """
        if path.is_file():
            return path.stat().st_size
        total = 0
        for item in path.rglob("*") if path.is_dir() else []:
            try:
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
            except OSError:
                continue
        return total

    @classmethod
    def usage(cls) -> dict[str, int]:
        """Measure what the forge uses on the disk, by kind.

        Returns:
            dict[str, int]: Bytes used by `repositories`, `logs_artefacts`, `dumps`, `other`
            (the database, indexes, the work clones) and `total`.
        """
        data = ForgeSettings.data_dir()
        share = ForgeSettings.share_dir()
        repositories = cls.size(data / "git")
        logs = sum(cls.size(data / part) for part in PRUNABLE[:3])
        dumps = cls.size(data / "dumps") + cls.size(share / "dumps")
        total = cls.size(share) - cls.size(share / "backups")
        return {
            "repositories": repositories,
            "logs_artefacts": logs,
            "dumps": dumps,
            "other": max(0, total - repositories - logs - dumps),
            "total": total,
        }

    @classmethod
    def night_running(cls) -> bool:
        """Tell whether a SWE-bench night holds this node.

        Returns:
            bool: Whether a SWE-bench night holds this node (a `ling-swe-*` unit is active).
        """
        try:
            result = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "list-units",
                    "--state=active",
                    "--no-legend",
                    "--plain",
                    "ling-swe-*",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return bool(result.stdout.strip())

    @classmethod
    def free_gb(cls) -> float:
        """Measure the free space where the forge's data lives.

        Returns:
            float: Free space where the forge's data lives, in GB.
        """
        path = ForgeSettings.share_dir()
        while not path.exists():
            path = path.parent
        return shutil.disk_usage(path).free / GB

    @classmethod
    def candidates(cls) -> list[Path]:
        """List the prunable files, in pruning order.

        Returns:
            list[Path]: Prunable files, in pruning order: by kind (artefacts, logs, dumps), then
            oldest first.
        """
        found: list[Path] = []
        for part in PRUNABLE:
            folder = ForgeSettings.data_dir() / part
            files = (
                [item for item in folder.rglob("*") if item.is_file()] if folder.is_dir() else []
            )
            found += sorted(files, key=cls.modified)
        dumps = ForgeSettings.share_dir() / "dumps"
        if dumps.is_dir():
            found += sorted(
                (item for item in dumps.rglob("*") if item.is_file()),
                key=cls.modified,
            )
        return found

    @classmethod
    def modified(cls, item: Path) -> float:
        """When a file was last written, for the pruning order.

        Args:
            item: A prunable file.

        Returns:
            float: Its modification time; 0 when it is gone already (Forgejo rotates its own).
        """
        try:
            return item.stat().st_mtime
        except OSError:
            return 0.0

    @classmethod
    def prune(cls, free_gb: float | None = None, night: bool | None = None) -> list[str]:
        """Prune until the node is back above its floor plus the margin, or nothing is left.

        Args:
            free_gb: The free space now (measured when None).
            night: Whether a night runs (asked when None).

        Returns:
            list[str]: What was done, one line each; empty when nothing was needed.
        """
        free = cls.free_gb() if free_gb is None else free_gb
        floor = ForgeSettings.disk_floor_gb(cls.night_running() if night is None else night)
        wanted = floor + PRUNE_MARGIN_GB
        if free >= wanted:
            return []
        lines = [
            f"⚠️  {free:.1f} GB free, below the floor of {floor:.0f} GB plus {PRUNE_MARGIN_GB:.0f} GB: pruning."
        ]
        removed = 0
        for item in cls.candidates():
            if free >= wanted:
                break
            try:
                size = item.stat().st_size
                item.unlink()
            except OSError:
                continue
            free += size / GB
            removed += size
        lines.append(f"🧹 Removed {removed / GB:.2f} GB of artefacts, logs and local dumps.")
        if free < wanted:
            lines.append(
                "❌ Still below the floor: nothing prunable is left. `ling-admin forge move <node>` "
                "hands the forge to a node with room."
            )
        return lines

    @classmethod
    def warnings(cls, usage: dict[str, int]) -> list[str]:
        """Check the usage against the storage budget and the repository alarm.

        Args:
            usage: `usage()`'s figures.

        Returns:
            list[str]: The budget's warnings.
        """
        found = []
        if usage["repositories"] > REPOSITORY_ALARM_GB * GB:
            found.append(
                f"repositories use {usage['repositories'] / GB:.1f} GB, above the {REPOSITORY_ALARM_GB:.0f} GB alarm"
            )
        if usage["total"] > BUDGET_GB * GB:
            found.append(
                f"the forge uses {usage['total'] / GB:.1f} GB, above its {BUDGET_GB:.0f} GB budget"
            )
        return found
