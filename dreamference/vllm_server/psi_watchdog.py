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
import socket
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

# Docker's control socket, used as a kill path when signalling the container's PIDs directly is not
# permitted — which is the normal case, because `puffin-admin` runs as an ordinary user and the container's
# processes run as root. Talking to the socket costs a connect and one small write; it does not fork
# or exec, which is what rules out the `docker` CLI on a stalling host.
DEFAULT_DOCKER_SOCKET: Final[str] = "/var/run/docker.sock"
DOCKER_KILL_TIMEOUT_S: Final[float] = 10.0

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
        self.killed = False
        self._on_trip = on_trip
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Resolved lazily while the host is healthy, so that tripping never has to fork.
        self._cgroup_procs: Optional[str] = None
        self._resolve_attempts = 0
        # The container's full ID, kept from the same `docker inspect` that finds the cgroup. The
        # socket kill path addresses the container by ID, and resolving it at trip time would mean
        # the fork this class exists to avoid.
        self._container_id: Optional[str] = None
        # Whether this process may actually signal the container's processes. Probed while healthy
        # rather than assumed: reading cgroup.procs needs no privilege, but signalling the root-owned
        # processes it lists does, so "I can see the PIDs" and "I can kill them" are different
        # questions and the code used to conflate them.
        self._direct_kill_ok: Optional[bool] = None

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
        self._container_id = container_id
        for template in CGROUP_PROCS_CANDIDATES:
            path = template.format(id=container_id)
            if os.path.exists(path):
                self._cgroup_procs = path
                # Read it once so the dentry and inode are warm and the trip-path read cannot
                # itself block on I/O that the stall has made slow.
                self._probe_direct_kill(self._read_cgroup_pids())
                return

    def _probe_direct_kill(self, pids: List[int]) -> None:
        """
        Determines whether this process is allowed to signal the container's processes.

        Signal 0 asks the kernel the permission question without delivering anything, so this is
        free and safe to run against a healthy container. The answer is almost always no: the
        container runs as root and `puffin-admin` does not, so `os.kill` raises EPERM. Knowing that in
        advance is what lets `_kill` choose a path that works instead of discovering the problem
        while the host is stalling — which is what happened before, silently, because EPERM was
        swallowed by the same handler that ignores already-dead PIDs.

        Args:
            pids (List[int]): PIDs currently in the container's cgroup.
        """
        for pid in pids:
            try:
                os.kill(pid, 0)
                self._direct_kill_ok = True
                return
            except PermissionError:
                self._direct_kill_ok = False
                return
            except OSError:
                # Process exited between the read and the probe; try the next one.
                continue

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
        self.killed = self._kill()

        if self.killed and self._on_trip is not None:
            try:
                self._on_trip(reason, pressure, held_for_s)
            except Exception:
                pass
        elif not self.killed:
            # Never claim a kill that did not happen. The caller's message says the memory has been
            # given back, and acting on that when the container is still running is worse than the
            # silence — it sends the operator looking for a different cause while the host freezes.
            try:
                print(
                    f"\n🛑 Memory stalled {pressure:.0f}% of the last {held_for_s:.0f}s ({reason}) "
                    f"and every path to kill '{self.container_name}' failed.\n"
                    f"   The container is still running and the host is still at risk. Kill it now:\n"
                    f"     docker kill {self.container_name}\n"
                )
            except Exception:
                pass

    def _kill(self) -> bool:
        """
        Frees the container's memory as fast as the host allows.

        Three paths, in order of how much the host has to be working for them to succeed:

        1. SIGKILL straight to the cgroup's PIDs. No allocation, no fork, no daemon — but only
           available when this process may signal them, which means running as root.
        2. A kill request written to Docker's control socket. One connect and one small write, no
           process creation. This is the path that actually runs in normal use.
        3. The `docker` CLI. Forks and execs a large Go binary and waits on dockerd, which is the
           work least likely to be scheduled during a reclaim livelock — so it is the last resort,
           not the fallback it used to be.

        The previous version returned after step 1 whenever the cgroup listed any PIDs, treating a
        successful *read* as a successful *kill*. Since EPERM was caught alongside "process already
        gone", an unprivileged watchdog killed nothing, reported a kill, and left the host to freeze.

        Returns:
            bool: True if one of the paths reports having killed the container.
        """
        if self._direct_kill_ok:
            pids = self._read_cgroup_pids()
            signalled = False
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    signalled = True
                except PermissionError:
                    # Permission was probed while healthy, so this means it changed underneath us.
                    # Stop trusting the fast path and fall through to the socket.
                    self._direct_kill_ok = False
                    signalled = False
                    break
                except OSError:
                    continue
            if signalled:
                return True

        if self._kill_via_docker_socket():
            return True

        try:
            result = subprocess.run(
                ["docker", "kill", self.container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return getattr(result, "returncode", 1) == 0

    def _kill_via_docker_socket(self) -> bool:
        """
        Asks dockerd to SIGKILL the container over its unix socket, without forking.

        Addressed by container ID when one was resolved and by name otherwise; the Engine API
        accepts either. Everything here is deliberately hand-rolled rather than routed through an
        HTTP client library, because the point is to make exactly one connect and one write while
        the machine is stalling.

        Returns:
            bool: True if dockerd acknowledged the kill (204, or 2xx generally).
        """
        docker_host = os.getenv("DOCKER_HOST", "")
        if docker_host.startswith("unix://"):
            sock_path = docker_host[len("unix://"):]
        elif docker_host:
            # A TCP or ssh endpoint; this path only speaks to a local socket.
            return False
        else:
            sock_path = DEFAULT_DOCKER_SOCKET

        target = self._container_id or self.container_name
        request = (
            f"POST /containers/{target}/kill?signal=SIGKILL HTTP/1.1\r\n"
            f"Host: docker\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode()

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(DOCKER_KILL_TIMEOUT_S)
                sock.connect(sock_path)
                sock.sendall(request)
                status = sock.recv(64)
        except (OSError, socket.timeout):
            return False

        # b'HTTP/1.1 204 No Content' on success; 404 if it already exited, which is not a failure
        # of this watchdog but is not a kill either.
        parts = status.split(b" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            return False
        return 200 <= int(parts[1]) < 300
