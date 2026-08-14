"""
Memory Pressure Watchdog for Dreamference.

Watches the kernel's PSI memory-stall accounting during a model load and kills the container
before the host becomes unusable.

This exists because the failure it guards against does not announce itself. On unified memory the
weights vLLM pins are unreclaimable and are not charged to any process the kernel's OOM killer
would choose, so an overcommitted load does not end in a kill — the kernel grinds in reclaim and
the machine stops responding. Six such freezes on 2026-08-14 produced NVRM NV_ERR_NO_MEMORY and
page-cache flushing in the journal, and not one oom-kill line.

systemd-oomd and earlyoom cover the same ground, but only when the host is configured for it:
oomd acts on cgroups explicitly opted in via ManagedOOMMemoryPressure, and the Docker scope sits
in system.slice, which is not opted in by default; earlyoom will not act until free *swap* is also
low, which driver-pinned pages can never make happen. This watchdog needs no root, no cgroup
configuration and no daemon — it reads /proc/pressure/memory and SIGKILLs the container's cgroup.

Two things are load-bearing about the timing, both learned from the 14:11 reset on 2026-08-14:

  * It has to act inside ~20 seconds. That freeze went from the first stalled frame to the power
    button in 20 s, and the previous thresholds (avg10 >= 60% held for 15 s) could not fire in
    under ~25 s, because avg10 is itself a 10-second decaying average and needs ~10 s just to
    climb. The fast rule now holds for 5 s, putting a trip at ~15 s from onset.
  * The kill path cannot allocate, fork, or exec. `docker kill` means exec'ing a ~50 MB Go binary
    and a round trip through dockerd — precisely the work that does not get scheduled during a
    reclaim livelock. The container's cgroup is resolved ahead of time, while the machine is still
    healthy, so that tripping is a read of one small file and a loop of kill(2).
"""

import os
import signal
import subprocess
import threading
import time
from typing import Callable, Final, List, Optional, Tuple

PSI_MEMORY_PATH: Final[str] = "/proc/pressure/memory"
# Percentage of wall time during which *every* runnable task was stalled on memory. The `full`
# line is the right signal here: `some` runs high during any large sequential read and would fire
# on healthy loads, whereas sustained `full` means nothing on the machine is making progress.
# 60% matches systemd-oomd's own DefaultMemoryPressureLimit.
PSI_FULL_LIMIT_PCT: Final[float] = 60.0
# How long that has to hold before acting. Deliberately short: avg10 is already a 10-second
# average, so this is a second filter against transient spikes, not the primary one, and every
# second spent here is a second of an unusable desktop.
PSI_TRIP_DURATION_S: Final[float] = 5.0
# A second rule on the 60-second average, which trips on sight. This is not redundant with the
# rule above — it catches the shape that one cannot see. Pressure that oscillates either side of
# 60% resets the fast rule's clock on every dip and can grind indefinitely without ever tripping
# it, while avg60 climbs straight through. A minute in which a quarter of all wall time had every
# task blocked on memory is already a machine nobody can type on.
PSI_SUSTAINED_AVG60_PCT: Final[float] = 25.0
PSI_SAMPLE_INTERVAL_S: Final[float] = 1.0

# Where the container's cgroup shows up, in the order worth trying. The first is cgroup v2 under
# Docker's default systemd driver, the second cgroup v2 under the cgroupfs driver, the third v1.
CGROUP_PROCS_CANDIDATES: Final[Tuple[str, ...]] = (
    "/sys/fs/cgroup/system.slice/docker-{id}.scope/cgroup.procs",
    "/sys/fs/cgroup/docker/{id}/cgroup.procs",
    "/sys/fs/cgroup/memory/docker/{id}/cgroup.procs",
)

TRIP_REASON_SPIKE: Final[str] = "avg10"
TRIP_REASON_SUSTAINED: Final[str] = "avg60"

# How many times to try turning the container name into a cgroup path before settling for the
# `docker kill` fallback. Resolution costs a fork, and the loop runs for the life of the server —
# retrying forever means forking once a second for hours against a container whose cgroup is never
# going to appear (it exited, or the layout is one this module does not know). A container that
# has not produced a cgroup within this many healthy samples is not about to.
MAX_CGROUP_RESOLVE_ATTEMPTS: Final[int] = 30


def read_memory_pressure_full() -> Optional[Tuple[float, float]]:
    """
    Reads the `full` line from the kernel's memory pressure accounting.

    Returns:
        Optional[Tuple[float, float]]: (avg10, avg60) — the percentages of the last 10 and 60
        seconds during which all tasks were stalled on memory — or None if PSI is unavailable
        (kernel built without CONFIG_PSI).
    """
    try:
        with open(PSI_MEMORY_PATH, "r") as f:
            for line in f:
                if not line.startswith("full"):
                    continue
                avg10: Optional[float] = None
                avg60: Optional[float] = None
                for field in line.split():
                    if field.startswith("avg10="):
                        avg10 = float(field.split("=", 1)[1])
                    elif field.startswith("avg60="):
                        avg60 = float(field.split("=", 1)[1])
                if avg10 is not None and avg60 is not None:
                    return avg10, avg60
    except (OSError, ValueError):
        pass
    return None


class MemoryPressureWatchdog:
    """
    Kills a container when sustained memory stalls indicate the host is about to lock up.

    Runs as a daemon thread so it cannot keep the process alive, and stops on its own once the
    load it was guarding finishes.

    Attributes:
        container_name (str): Docker container to kill when a threshold is breached.
        limit_pct (float): `full avg10` percentage that counts as dangerous.
        trip_duration_s (float): How long limit_pct must hold continuously before killing.
        sustained_pct (float): `full avg60` percentage that trips on sight.
        sample_interval_s (float): Delay between samples.
        tripped (bool): True if the watchdog fired.
    """

    def __init__(
        self,
        container_name: str,
        limit_pct: float = PSI_FULL_LIMIT_PCT,
        trip_duration_s: float = PSI_TRIP_DURATION_S,
        sustained_pct: float = PSI_SUSTAINED_AVG60_PCT,
        sample_interval_s: float = PSI_SAMPLE_INTERVAL_S,
        on_trip: Optional[Callable[[str, float, float], None]] = None,
    ):
        """
        Args:
            container_name (str): Docker container to kill when a threshold is breached.
            limit_pct (float): `full avg10` percentage that counts as dangerous.
            trip_duration_s (float): How long limit_pct must hold continuously before killing.
            sustained_pct (float): `full avg60` percentage that trips on sight.
            sample_interval_s (float): Delay between samples.
            on_trip (Optional[Callable[[str, float, float], None]]): Called with (reason, pressure,
                seconds held) when the watchdog fires, before the container is killed. `reason` is
                one of TRIP_REASON_SPIKE or TRIP_REASON_SUSTAINED.
        """
        self.container_name = container_name
        self.limit_pct = limit_pct
        self.trip_duration_s = trip_duration_s
        self.sustained_pct = sustained_pct
        self.sample_interval_s = sample_interval_s
        self.tripped = False
        self._on_trip = on_trip
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Resolved lazily while the host is healthy, so that tripping never has to fork.
        self._cgroup_procs: Optional[str] = None
        self._resolve_attempts = 0

    def start(self) -> bool:
        """
        Starts watching, unless the kernel does not expose PSI.

        Returns:
            bool: True if the watchdog is running, False if PSI is unavailable.
        """
        if read_memory_pressure_full() is None:
            return False
        self._thread = threading.Thread(target=self._run, name="psi-watchdog", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stops watching. Safe to call whether or not the watchdog was started or has fired."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.sample_interval_s * 2)

    def _run(self) -> None:
        """
        Samples pressure until told to stop, killing the container if either rule fires.

        Every sample is wrapped, because an exception here does not merely lose one reading — it
        ends the thread, and the guard is then silently absent for the rest of the load. A
        watchdog that has stopped watching is worse than one that never started, since nothing
        else reports its absence. Losing a sample to a transient error costs one second.
        """
        breach_started_at: Optional[float] = None

        while not self._stop.is_set():
            try:
                breach_started_at = self._sample(breach_started_at)
            except Exception:
                breach_started_at = None
            if self.tripped:
                return
            self._stop.wait(self.sample_interval_s)

    def _sample(self, breach_started_at: Optional[float]) -> Optional[float]:
        """
        Takes one pressure reading and acts on it.

        Args:
            breach_started_at (Optional[float]): monotonic time the current run of over-limit
                avg10 samples began, or None if the last sample was below the limit.

        Returns:
            Optional[float]: The breach start to carry into the next sample.
        """
        reading = read_memory_pressure_full()
        if reading is None:
            return None

        avg10, avg60 = reading

        # The sustained rule first: if it is already true, no amount of waiting on the spike
        # rule's clock is going to produce a better answer.
        if avg60 >= self.sustained_pct:
            self._trip(TRIP_REASON_SUSTAINED, avg60, 60.0)
            return None

        if avg10 >= self.limit_pct:
            now = time.monotonic()
            if breach_started_at is None:
                return now
            if now - breach_started_at >= self.trip_duration_s:
                self._trip(TRIP_REASON_SPIKE, avg10, now - breach_started_at)
            return breach_started_at

        # Below the limit: the clock resets. The fast rule is for sustained stalls, not for the
        # spikes a large sequential read produces on its way through, and the avg60 rule above is
        # what stops a well-timed dip from buying an unhealthy load free time.
        #
        # Also the moment to afford the one fork this class makes: resolving the cgroup while the
        # host is healthy keeps the trip path down to a file read and kill(2).
        self._ensure_cgroup_resolved()
        return None

    def _ensure_cgroup_resolved(self) -> None:
        """
        Resolves and caches the container's cgroup.procs path, if it has not been already.

        Called only from healthy samples. Resolution needs `docker inspect` to turn the container
        name into an ID, which is exactly the fork-and-exec that must not happen once the host is
        stalling — so it happens now, seconds after launch, and the result is kept.
        """
        if self._cgroup_procs is not None or self._resolve_attempts >= MAX_CGROUP_RESOLVE_ATTEMPTS:
            return
        self._resolve_attempts += 1
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.Id}}", self.container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return
        if result.returncode != 0:
            # Container has not started yet, or is already gone. Try again on the next sample.
            return

        container_id = result.stdout.strip()
        if not container_id:
            return
        for template in CGROUP_PROCS_CANDIDATES:
            path = template.format(id=container_id)
            if os.path.exists(path):
                self._cgroup_procs = path
                # Read it once so the dentry and inode are warm and the trip-path read cannot
                # itself block on I/O that the stall has made slow.
                self._read_cgroup_pids()
                return

    def _read_cgroup_pids(self) -> List[int]:
        """
        Reads the PIDs currently in the container's cgroup.

        Returns:
            List[int]: The PIDs, or an empty list if the cgroup is unknown or unreadable.
        """
        if self._cgroup_procs is None:
            return []
        try:
            with open(self._cgroup_procs, "r") as f:
                return [int(line) for line in f.read().split() if line.isdigit()]
        except (OSError, ValueError):
            return []

    def _trip(self, reason: str, pressure: float, held_for_s: float) -> None:
        """
        Kills the container and records that the watchdog fired.

        Args:
            reason (str): Which rule fired — TRIP_REASON_SPIKE or TRIP_REASON_SUSTAINED.
            pressure (float): The reading that triggered the kill.
            held_for_s (float): The window that reading covers.
        """
        self.tripped = True

        # Kill before reporting. Printing means acquiring a lock and touching stdout, and under
        # this much stall that can take seconds the host does not have; the memory has to come
        # back first and the explanation can follow.
        self._kill()

        if self._on_trip is not None:
            try:
                self._on_trip(reason, pressure, held_for_s)
            except Exception:
                pass

    def _kill(self) -> None:
        """
        Frees the container's memory as fast as the host allows.

        SIGKILL straight to the cgroup's PIDs rather than a graceful stop or `docker kill`: under
        this much stall a shutdown handler may never get scheduled, and asking dockerd to do it
        means waiting on a daemon that is stalled for the same reason everything else is. Falls
        back to `docker kill` only when the cgroup could not be resolved, which is better than
        nothing even though it is the slow path.
        """
        pids = self._read_cgroup_pids()
        if pids:
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    continue
            return

        try:
            subprocess.run(
                ["docker", "kill", self.container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            pass
