"""What the model loading monitor decides from the engine's log and its health checks.

Only a variable the recipe itself supplied stops a load when vLLM rejects it; the variables the
NVIDIA container injects are kept off the console but still read; a failing log or health source
is not taken for a ready server; and the loop reports readiness and memory on its own clock.
"""

from dreamference.vllm_server import model_loading_monitor as mlm
from dreamference.vllm_server.model_loading_monitor import (
    ModelLoadingMonitor,
    create_model_loading_monitor,
)


class _Clock:
    """Stands in for the `time` module: each sleep moves the clock on and may end the loop."""

    def __init__(self, step=3.0, monitor=None, rounds=None):
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
            self.monitor.running = False


def _monitor(lines=(), healthy=False, **kwargs):
    return ModelLoadingMonitor(lambda: list(lines), lambda: healthy, **kwargs)


def test_an_unknown_variable_the_recipe_did_not_supply_does_not_stop_the_load(capsys):
    monitor = _monitor(
        ["Unknown vLLM environment variable detected: VLLM_SOMETHING_ELSE"],
        recipe_env_keys={"VLLM_USE_FLASHINFER_SAMPLER"},
    )
    monitor.running = True
    monitor._check_logs()
    assert monitor.fatal is None and monitor.running is True
    assert "VLLM_SOMETHING_ELSE" in capsys.readouterr().out


def test_the_containers_own_variables_are_kept_off_the_console_but_still_read(capsys):
    line = "Unknown vLLM environment variable detected: VLLM_VERSION"
    _monitor([line], recipe_env_keys=set())._check_logs()
    assert capsys.readouterr().out == ""
    # Kept quiet is not ignored: had the recipe pinned the same name, the load still stops.
    monitor = _monitor([line], recipe_env_keys={"VLLM_VERSION"})
    monitor.running = True
    monitor._check_logs()
    assert monitor.fatal is not None and "VLLM_VERSION" in monitor.fatal
    assert monitor.running is False


def test_a_log_source_that_fails_reports_no_stage():
    def broken():
        raise OSError("pipe closed")

    monitor = ModelLoadingMonitor(broken, lambda: False)
    monitor._check_logs()
    assert monitor.stages_reached == set() and monitor.fatal is None


def test_a_line_counts_for_the_first_stage_it_names_only():
    monitor = _monitor()
    monitor._process_log_line("Loading model weights; server ready soon")
    assert monitor.stages_reached == {"loading"}


def test_a_health_check_that_raises_is_not_ready_and_one_that_answers_is():
    def broken():
        raise ConnectionError("refused")

    monitor = ModelLoadingMonitor(list, broken)
    assert monitor._check_server_health() is False and monitor.server_ready is False
    monitor = _monitor(healthy=True)
    assert monitor._check_server_health() is True and monitor.server_ready is True


def test_waiting_for_ready_returns_at_once_when_ready_and_false_at_the_timeout(monkeypatch):
    clock = _Clock(step=0.5)
    monkeypatch.setattr(mlm, "time", clock)
    monitor = _monitor()
    assert monitor.wait_for_ready(timeout=3.0) is False
    assert clock.slept == 6
    monitor.server_ready = True
    assert monitor.wait_for_ready(timeout=3.0) is True
    assert clock.slept == 6


def test_the_loop_marks_ready_from_the_health_check_and_prints_memory_only_when_known(
    monkeypatch, capsys
):
    readings = iter([None, "12.0 GiB"])
    monitor = _monitor(["Loading model"], healthy=True, get_memory_fn=lambda: next(readings, None))
    clock = _Clock(step=11.0, monitor=monitor, rounds=2)
    monkeypatch.setattr(mlm, "time", clock)
    monitor.running = True
    monitor._monitor_loop()
    assert monitor.server_ready is True
    assert monitor.stages_reached == {"loading", "ready"}
    out = capsys.readouterr().out
    assert out.count("Reserved memory (Docker)") == 1 and "12.0 GiB" in out


def test_the_loop_leaves_a_server_that_fails_its_health_check_not_ready(monkeypatch):
    monitor = _monitor([], healthy=False)
    monkeypatch.setattr(mlm, "time", _Clock(step=3.0, monitor=monitor, rounds=3))
    monitor.running = True
    monitor._monitor_loop()
    assert monitor.server_ready is False and "ready" not in monitor.stages_reached


def test_a_started_monitor_is_not_started_twice_and_stops():
    monitor = _monitor()
    monitor.start()
    first = monitor.monitor_thread
    monitor.start()
    assert monitor.monitor_thread is first
    monitor.stop()
    assert monitor.running is False and not first.is_alive()
    assert monitor.get_status()["server_ready"] is False


def test_the_factory_reads_the_managers_logs_health_and_memory():
    class Manager:
        def get_new_logs(self):
            return []

        def check_health(self):
            return True

        def get_container_reserved_memory(self):
            return None

    manager = Manager()
    monitor = create_model_loading_monitor(manager, {"VLLM_X"})
    assert monitor.get_logs_fn == manager.get_new_logs
    assert monitor.check_health_fn == manager.check_health
    assert monitor.get_memory_fn == manager.get_container_reserved_memory
    assert monitor.recipe_env_keys == {"VLLM_X"}
