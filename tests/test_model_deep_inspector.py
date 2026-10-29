"""`ling-admin main-model inspect --deep` probes, on stand-ins for the server's metrics and log, so
no test here reaches a server or a container.
"""

from unittest.mock import patch

from dreamference.cli.model_deep_inspector import ModelDeepInspector


def test_a_server_that_captured_no_cuda_graph_is_reported_not_a_crash():
    # An eager server logs an empty capture list; max() of it raised ValueError.
    log = "'cudagraph_capture_sizes': []\n"
    with patch.object(ModelDeepInspector, "_container_log", classmethod(lambda cls, c: log)):
        rows = ModelDeepInspector.graph_coverage("vllm", "8")
    assert "CUDA graphs" in rows
