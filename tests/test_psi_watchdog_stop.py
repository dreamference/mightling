"""Stopping a memory-pressure watchdog that was never started."""

from dreamference.vllm_server.psi_watchdog import MemoryPressureWatchdog


def test_a_watchdog_that_never_started_stops_without_a_thread_to_wait_for():
    watchdog = MemoryPressureWatchdog("dreamference-vllm-18001")

    watchdog.stop()

    assert watchdog._thread is None
    assert watchdog._stop.is_set()
