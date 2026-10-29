"""`ling-admin main-model inspect --deep` probes.

They run on stand-ins for the server's metrics and log, so no test here reaches a server or a
container.
"""

from unittest.mock import patch

from dreamference.cli.model_deep_inspector import WORKLOAD_PROBES, ModelDeepInspector


def test_a_metrics_read_that_fails_midway_keeps_the_workloads_already_measured():
    # It returned [] there, throwing away every row measured before the failed read.
    counters = {"drafts": 0.0, "drafted": 0.0, "accepted": 0.0, "per_pos": [0.0, 0.0]}
    reads = []

    def spec_counters(cls, host):
        reads.append(host)
        if len(reads) == 3:
            return None
        counters["drafts"] += 10
        counters["drafted"] += 20
        counters["accepted"] += 15
        counters["per_pos"] = [p + 8 for p in counters["per_pos"]]
        return dict(counters)

    with (
        patch.object(ModelDeepInspector, "_spec_counters", classmethod(spec_counters)),
        patch("requests.post"),
    ):
        rows = ModelDeepInspector.profile_acceptance_by_workload("h", "http://h/v1", "m")
    assert len(WORKLOAD_PROBES) >= 2
    assert rows and rows[0]["category"] == WORKLOAD_PROBES[0][0]


def test_a_server_that_captured_no_cuda_graph_is_reported_not_a_crash():
    # An eager server logs an empty capture list; max() of it raised ValueError.
    log = "'cudagraph_capture_sizes': []\n"
    with patch.object(ModelDeepInspector, "_container_log", classmethod(lambda cls, c: log)):
        rows = ModelDeepInspector.graph_coverage("vllm", "8")
    assert "CUDA graphs" in rows
