"""What the vLLM startup monitor decides from the engine's log, its memory and its own clock.

The engine's pid is taken from the first line that names it and never replaced; its memory comes
from /proc while the process can be read and from the system figure when it cannot; a log that has
gone silent past the stall threshold is flagged once, not on every tick.
"""

import io

from dreamference.vllm_server import vllm_startup_monitor as vsm
from dreamference.vllm_server.vllm_startup_monitor import VLLMStartupMonitor


class _Clock:
    """Stands in for the `time` module: each sleep moves the clock on and may end the loop."""

    def __init__(self, step=1.0, monitor=None, rounds=None):
        self.now = 1000.0
        self.step = step
        self.monitor = monitor
        self.rounds = rounds
        self.slept = 0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept += 1
        self.now += self.step
        if self.rounds is not None and self.slept >= self.rounds:
            self.monitor.is_running = False


def _monitor(logged, **kwargs):
    return VLLMStartupMonitor(log_callback=logged.append, **kwargs)


def _proc_status(monkeypatch, text):
    """Answers the read of /proc/<pid>/status with `text`, or raises it when it is an exception."""
    opened = []

    def fake_open(path, mode="r"):
        opened.append(path)
        if isinstance(text, BaseException):
            raise text
        return io.StringIO(text)

    monkeypatch.setattr(vsm, "open", fake_open, raising=False)
    return opened


def _system_memory(monkeypatch, used_gb):
    def memory():
        if isinstance(used_gb, BaseException):
            raise used_gb
        return {"used_gb": used_gb}

    monkeypatch.setattr("dreamference.hardware.get_system_memory", memory)


def test_the_engine_pid_is_taken_from_the_first_line_that_names_it_only():
    monitor = _monitor([])
    monitor.notify_log_received("INFO starting the API server")
    assert monitor.vllm_pid is None
    monitor.notify_log_received("(EngineCore_DP0 pid=4321) INFO EngineCore pid=4321 started")
    assert monitor.vllm_pid == 4321
    monitor.notify_log_received("INFO EngineCore pid: 9999 restarted")
    assert monitor.vllm_pid == 4321


def test_a_new_log_line_clears_the_warning_so_the_next_silence_is_flagged_again():
    monitor = _monitor([])
    monitor.warned = True
    monitor.notify_log_received()
    assert monitor.warned is False and monitor.vllm_pid is None


def test_the_engines_memory_is_read_from_its_own_process_status(monkeypatch):
    _system_memory(monkeypatch, 99.0)
    opened = _proc_status(monkeypatch, "Name:\tpython\nVmRSS:\t 2097152 kB\nThreads:\t4\n")
    monitor = _monitor([])
    monitor.vllm_pid = 4321
    assert monitor._read_used_memory_gb() == 2.0
    assert opened == ["/proc/4321/status"]


def test_a_process_status_that_cannot_be_read_falls_back_to_the_system_figure(monkeypatch):
    _system_memory(monkeypatch, 7.5)
    monitor = _monitor([])
    monitor.vllm_pid = 4321
    for failure in (FileNotFoundError("gone"), PermissionError("hidden"), "VmRSS:\tlots kB\n"):
        _proc_status(monkeypatch, failure)
        assert monitor._read_used_memory_gb() == 7.5
    _proc_status(monkeypatch, "Name:\tpython\n")
    assert monitor._read_used_memory_gb() == 7.5, "a status without VmRSS gives no reading"


def test_without_a_pid_the_process_status_is_not_read(monkeypatch):
    _system_memory(monkeypatch, 3.0)
    opened = _proc_status(monkeypatch, "VmRSS:\t1 kB\n")
    assert _monitor([])._read_used_memory_gb() == 3.0
    assert opened == []


def test_memory_that_cannot_be_read_at_all_reads_as_nothing_used(monkeypatch):
    _system_memory(monkeypatch, RuntimeError("no meminfo"))
    assert _monitor([])._read_used_memory_gb() == 0.0


def test_the_estimate_stops_short_of_done_and_gives_no_percent_without_a_target(monkeypatch):
    readings = iter([150.0, 15.0, 5.0])
    monitor = _monitor([], expected_memory_gb=100.0)
    monkeypatch.setattr(monitor, "_read_used_memory_gb", lambda: next(readings))
    monitor.baseline_used_gb = 10.0
    assert monitor.estimate_progress() == (99, 140.0, 100.0)
    monitor.expected_memory_gb = 0.0
    assert monitor.estimate_progress() == (None, 5.0, 0.0)
    assert monitor.estimate_progress() == (None, 0.0, 0.0), "memory freed is no negative growth"


def test_the_progress_suffix_says_only_what_the_memory_supports(monkeypatch):
    readings = iter([60.0, 12.5, 10.0])
    monitor = _monitor([], expected_memory_gb=100.0)
    monkeypatch.setattr(monitor, "_read_used_memory_gb", lambda: next(readings))
    monitor.baseline_used_gb = 10.0
    assert monitor._format_progress_suffix() == "~50% (50.0/100 GB since start)"
    monitor.expected_memory_gb = None
    assert monitor._format_progress_suffix() == "+2.5 GB used since start"
    assert monitor._format_progress_suffix() == "awaiting memory growth"


def test_progress_is_logged_when_due_or_advanced_and_not_otherwise(monkeypatch):
    logged = []
    pct = iter([10, 10, 10, 11])
    monitor = _monitor(logged, progress_interval_sec=5.0, expected_memory_gb=100.0)
    monkeypatch.setattr(monitor, "estimate_progress", lambda: (next(pct), 0.0, 100.0))
    monkeypatch.setattr(monitor, "_format_progress_suffix", lambda: "~x%")
    monitor._maybe_emit_progress(now=100.0, elapsed_total=3)
    assert len(logged) == 1 and monitor.last_reported_pct == 10
    monitor._maybe_emit_progress(now=101.0, elapsed_total=4)
    assert len(logged) == 1, "neither due nor advanced"
    monitor._maybe_emit_progress(now=101.0, elapsed_total=4, force=True)
    assert len(logged) == 2
    monitor._maybe_emit_progress(now=102.0, elapsed_total=5)
    assert len(logged) == 3 and monitor.last_reported_pct == 11
    assert "(5s elapsed)" in logged[-1]


def test_a_progress_line_without_a_percent_keeps_the_last_reported_one(monkeypatch):
    logged = []
    monitor = _monitor(logged, progress_interval_sec=0.0)
    monitor.last_reported_pct = 40
    monkeypatch.setattr(monitor, "estimate_progress", lambda: (None, 1.0, None))
    monitor._maybe_emit_progress(now=50.0, elapsed_total=1)
    assert len(logged) == 1 and monitor.last_reported_pct == 40


def test_a_silent_log_past_the_stall_threshold_is_flagged_once(monkeypatch):
    logged = []
    monitor = _monitor(
        logged, warn_timeout_sec=2.0, stuck_threshold_sec=4.0, progress_interval_sec=1000.0
    )
    monkeypatch.setattr(monitor, "_read_used_memory_gb", lambda: 0.0)
    clock = _Clock(step=1.0, monitor=monitor, rounds=8)
    monkeypatch.setattr(vsm, "time", clock)
    monitor.start_time = monitor.last_log_time = clock.now
    monitor.last_progress_time = clock.now
    monitor.is_running = True
    monitor._monitor_loop()
    quiet = [line for line in logged if "Quiet for" in line]
    stuck = [line for line in logged if "No new logs for" in line]
    assert len(quiet) == 1 and "Quiet for 2s" in quiet[0]
    assert len(stuck) == 0, "the quiet warning already stands for this silence"
    assert monitor.warned is True


def test_a_silence_first_seen_past_the_stall_threshold_is_flagged_as_stuck_once(monkeypatch):
    logged = []
    monitor = _monitor(logged, warn_timeout_sec=2.0, stuck_threshold_sec=4.0)
    monkeypatch.setattr(monitor, "_read_used_memory_gb", lambda: 0.0)
    clock = _Clock(step=5.0, monitor=monitor, rounds=3)
    monkeypatch.setattr(vsm, "time", clock)
    monitor.start_time = monitor.last_log_time = clock.now
    monitor.is_running = True
    monitor._monitor_loop()
    stuck = [line for line in logged if "No new logs for" in line]
    assert len(stuck) == 1 and "No new logs for 5s (total 5s" in stuck[0]
    assert not [line for line in logged if "Quiet for" in line]
    assert monitor.last_progress_time == 1005.0


def test_a_loop_that_was_stopped_before_it_ran_logs_nothing(monkeypatch):
    logged = []
    monitor = _monitor(logged)
    monkeypatch.setattr(vsm, "time", _Clock())
    monitor.is_running = False
    monitor._monitor_loop()
    assert logged == []


def test_stopping_a_monitor_whose_thread_never_ran_does_not_wait_on_it():
    class _Dead:
        joined = False

        def is_alive(self):
            return False

        def join(self, timeout=None):
            self.joined = True

    monitor = _monitor([])
    monitor.is_running = True
    monitor.monitor_thread = _Dead()
    monitor.stop()
    assert monitor.is_running is False and monitor.monitor_thread.joined is False
    monitor.monitor_thread = None
    monitor.stop()
