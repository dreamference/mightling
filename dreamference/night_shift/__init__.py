"""
Night Shift: coding tasks queued with `/night add` run overnight, each in its own git worktree, and
leave a branch and a morning report (specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md).
"""

from dreamference.night_shift.chooser_candidate import ChooserCandidate
from dreamference.night_shift.night_shift_best_of_n import NightShiftBestOfN
from dreamference.night_shift.night_shift_chooser import NightShiftChooser
from dreamference.night_shift.night_shift_compaction_audit import NightShiftCompactionAudit
from dreamference.night_shift.night_shift_host import NightShiftHost
from dreamference.night_shift.night_shift_index import NightShiftIndex
from dreamference.night_shift.night_shift_queue import NightShiftQueue
from dreamference.night_shift.night_shift_recurring import NightShiftRecurring
from dreamference.night_shift.night_shift_remote import NightShiftRemote
from dreamference.night_shift.night_shift_report import NightShiftReport
from dreamference.night_shift.night_shift_runner import NightShiftRunner
from dreamference.night_shift.night_shift_scheduler import NightShiftScheduler
from dreamference.night_shift.night_shift_settings import NightShiftSettings
from dreamference.night_shift.night_shift_task_run import NightShiftTaskRun

__all__ = [
    "ChooserCandidate",
    "NightShiftBestOfN",
    "NightShiftChooser",
    "NightShiftCompactionAudit",
    "NightShiftHost",
    "NightShiftIndex",
    "NightShiftQueue",
    "NightShiftRecurring",
    "NightShiftRemote",
    "NightShiftReport",
    "NightShiftRunner",
    "NightShiftScheduler",
    "NightShiftSettings",
    "NightShiftTaskRun",
]
