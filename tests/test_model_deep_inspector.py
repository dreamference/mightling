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


def test_an_fp8_e5m2_kv_cache_is_counted_at_one_byte_a_value():
    # Only fp8 and fp8_e4m3 were in the table: the other fp8 format the engines take fell back to
    # two bytes and doubled the KV per token and the realised pool.
    config = {
        "text_config": {
            "layer_types": ["full_attention"] * 4,
            "num_key_value_heads": 2,
            "head_dim": 128,
        }
    }
    with (
        patch.object(ModelDeepInspector, "_snapshot_config", classmethod(lambda cls, m: config)),
        patch.object(ModelDeepInspector, "_container_log", classmethod(lambda cls, c: "")),
    ):
        rows = ModelDeepInspector.kv_geometry("m", "c", "fp8_e5m2")
    assert rows["KV per token"].startswith("2.0 KiB") and "x 1B x" in rows["KV per token"]
