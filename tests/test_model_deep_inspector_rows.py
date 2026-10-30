"""The rows `ling-admin main-model inspect --deep` derives from the checkpoint and the server log.

Each row is a claim about the served model (what is quantized, how large the KV cache is, what a
request inherits, whether thinking is really off). The snapshot and the container log are
stand-ins, so nothing here reads a model directory, Docker or a server.
"""

import json
import subprocess

import pytest

from dreamference.cli.model_deep_inspector import ModelDeepInspector
from dreamference.hardware import ModelDownloader


@pytest.fixture
def snapshot(monkeypatch, tmp_path):
    """A local snapshot directory the helpers find for every alias."""
    monkeypatch.setattr(
        ModelDownloader, "get_model_snapshot_dir", staticmethod(lambda alias: tmp_path)
    )
    return tmp_path


@pytest.fixture
def served(monkeypatch):
    """Sets what the snapshot config, the snapshot files and the container log contain."""
    state = {"configs": {}, "files": {}, "log": ""}
    monkeypatch.setattr(
        ModelDeepInspector,
        "_snapshot_config",
        classmethod(lambda cls, alias: state["configs"].get(alias)),
    )
    monkeypatch.setattr(
        ModelDeepInspector,
        "_snapshot_file",
        classmethod(lambda cls, alias, name: state["files"].get(name)),
    )
    monkeypatch.setattr(
        ModelDeepInspector, "_container_log", classmethod(lambda cls, container: state["log"])
    )
    return state


def test_a_model_that_cannot_be_located_has_no_config(monkeypatch):
    def lookup_fails(alias):
        raise RuntimeError("unknown alias")

    monkeypatch.setattr(ModelDownloader, "get_model_snapshot_dir", staticmethod(lookup_fails))
    assert ModelDeepInspector._snapshot_config("m") is None
    assert ModelDeepInspector._snapshot_file("m", "generation_config.json") is None
    monkeypatch.setattr(ModelDownloader, "get_model_snapshot_dir", staticmethod(lambda a: None))
    assert ModelDeepInspector._snapshot_config("m") is None
    assert ModelDeepInspector._snapshot_file("m", "generation_config.json") is None


def test_a_missing_or_broken_snapshot_file_reads_as_absent(snapshot):
    assert ModelDeepInspector._snapshot_config("m") is None
    (snapshot / "config.json").write_text("{not json", encoding="utf-8")
    (snapshot / "generation_config.json").write_text("{not json", encoding="utf-8")
    assert ModelDeepInspector._snapshot_config("m") is None
    assert ModelDeepInspector._snapshot_file("m", "generation_config.json") is None
    (snapshot / "config.json").write_text(json.dumps({"model_type": "qwen"}), encoding="utf-8")
    assert ModelDeepInspector._snapshot_config("m") == {"model_type": "qwen"}


@pytest.mark.parametrize(
    "failure", [OSError("no docker"), subprocess.TimeoutExpired("docker", 60)], ids=str
)
def test_a_container_log_that_cannot_be_read_is_empty(monkeypatch, failure):
    def run(*a, **k):
        raise failure

    monkeypatch.setattr(subprocess, "run", run)
    assert ModelDeepInspector._container_log("c") == ""


def test_the_container_log_joins_stdout_and_stderr(monkeypatch):
    seen = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, "out\n", None),
    )
    assert ModelDeepInspector._container_log("dreamference-vllm-8000") == "out\n"
    assert seen == [["docker", "logs", "dreamference-vllm-8000"]]


QUANTIZED = {
    "quantization_config": {
        "quant_method": "auto-round",
        "bits": 4,
        "group_size": 128,
        "packing_format": "auto_round:auto_gptq",
        "extra_config": {
            "model.layers.0.mlp.shared_expert": {"bits": 16},
            "model.layers.1.mlp.shared_expert": {"bits": 16},
            "model.layers.0.mlp.gate": {},
        },
    },
    "text_config": {"num_hidden_layers": 48, "num_experts": 128, "num_experts_per_tok": 8},
}


def test_a_checkpoint_that_is_not_downloaded_is_said_so(served):
    assert ModelDeepInspector.quantization_map("m", None) == {
        "Quantization map": "Checkpoint not downloaded"
    }


def test_modules_left_unquantized_are_counted_per_module_and_bit_width(served):
    served["configs"]["m"] = QUANTIZED
    rows = ModelDeepInspector.quantization_map("m", None)
    assert rows["Method"] == "auto-round (4-bit, group_size=128, packing=auto_round:auto_gptq)"
    # A module without its own bit width is held at the checkpoint's.
    assert rows["Held at higher precision"] == "gate x1 @ 4-bit, shared_expert x2 @ 16-bit"
    assert "across all 48 layers" in rows["Implication"]
    assert rows["MoE"].startswith("128 experts, 8 active/token")
    assert "Drafter" not in rows


def test_a_dense_uniform_checkpoint_has_no_moe_row(served):
    served["configs"]["m"] = {"quantization_config": None, "text_config": {"num_experts": 0}}
    rows = ModelDeepInspector.quantization_map("m", None)
    assert rows["Method"].startswith("none (?-bit")
    assert rows["Held at higher precision"] == "None — uniform quantization"
    assert "Implication" not in rows and "MoE" not in rows


def test_the_drafter_and_the_layers_it_reads_are_reported(served):
    served["configs"]["m"] = QUANTIZED
    served["configs"]["drafter"] = {
        "num_hidden_layers": 1,
        "hidden_size": 4096,
        "dflash_config": {"block_size": 16, "target_layer_ids": [1, 24, 46]},
    }
    rows = ModelDeepInspector.quantization_map("m", "drafter")
    assert rows["Drafter"] == "1 layers, hidden 4096, block_size 16"
    assert "target layers [1, 24, 46]" in rows["Drafter taps"]


def test_a_drafter_without_taps_or_snapshot_adds_only_what_is_known(served):
    served["configs"]["m"] = QUANTIZED
    assert "Drafter" not in ModelDeepInspector.quantization_map("m", "missing")
    served["configs"]["drafter"] = {"num_hidden_layers": 2, "dflash_config": None}
    rows = ModelDeepInspector.quantization_map("m", "drafter")
    assert rows["Drafter"] == "2 layers, hidden ?, block_size ?"
    assert "Drafter taps" not in rows


HYBRID = {
    "text_config": {
        "layer_types": ["linear_attention"] * 3 + ["full_attention"],
        "num_key_value_heads": 2,
        "head_dim": 256,
    }
}


@pytest.mark.parametrize(
    "config", [None, {"text_config": None}, {"text_config": {"layer_types": []}}]
)
def test_kv_geometry_without_layer_types_reports_nothing(served, config):
    served["configs"]["m"] = config
    assert ModelDeepInspector.kv_geometry("m", "c", "auto") == {}


def test_the_realised_pool_is_read_from_the_log_and_sized_by_the_kv_dtype(served):
    served["configs"]["m"] = HYBRID
    served["log"] = "INFO GPU KV cache size: 1,048,576 tokens\n"
    rows = ModelDeepInspector.kv_geometry("m", "c", "auto")
    assert rows["Layer mix"] == "1 full-attention + 3 linear-attention (GDN) of 4 total"
    # Only the full-attention layer holds KV: 2 heads x 256 x K and V x 2 bytes = 2 KiB.
    assert rows["KV per token"].startswith("2.0 KiB")
    assert rows["Realised pool"] == "1,048,576 tokens = 2.00 GiB of KV"
    assert ModelDeepInspector.kv_geometry("m", "c", "fp8")["Realised pool"].endswith(
        "1.00 GiB of KV"
    )


def test_an_unknown_kv_dtype_counts_two_bytes_and_a_silent_log_gives_no_pool(served):
    served["configs"]["m"] = HYBRID
    rows = ModelDeepInspector.kv_geometry("m", "c", "int3")
    assert "x 2B x" in rows["KV per token"]
    assert "Realised pool" not in rows


def test_a_config_without_head_geometry_never_reads_the_log(served, monkeypatch):
    served["configs"]["m"] = {"text_config": {"layer_types": ["full_attention"]}}
    monkeypatch.setattr(
        ModelDeepInspector, "_container_log", classmethod(lambda cls, c: pytest.fail("log read"))
    )
    assert list(ModelDeepInspector.kv_geometry("m", "c", "auto")) == ["Layer mix"]


@pytest.mark.parametrize("temperature, warned", [(0.6, True), (0, False), (None, False)])
def test_only_a_sampling_checkpoint_default_warns_that_requests_inherit_it(
    served, temperature, warned
):
    served["files"]["generation_config.json"] = {"temperature": temperature, "top_p": 0.95}
    rows = ModelDeepInspector.sampling_provenance("m", "c", [])
    assert rows["Checkpoint defaults"] == f"temperature={temperature}, top_p=0.95"
    assert ("Inherited if unset" in rows) is warned
    if warned:
        assert "samples at 0.6" in rows["Inherited if unset"]


def test_without_a_generation_config_or_flags_the_client_decides(served):
    rows = ModelDeepInspector.sampling_provenance("m", "c", ["vllm", "serve"])
    assert rows == {"Server overrides": "None (client decides)", "Template kwargs": "None"}


def test_server_overrides_and_template_kwargs_come_from_the_launch_command(served):
    cmd = [
        "vllm",
        "serve",
        "--override-generation-config={}",
        "--temperature=0",
        "--default-chat-template-kwargs",
        '{"enable_thinking": false}',
    ]
    rows = ModelDeepInspector.sampling_provenance("m", "c", cmd)
    assert rows["Server overrides"] == "--override-generation-config={}, --temperature=0"
    assert rows["Template kwargs"] == '{"enable_thinking": false}'


def test_a_template_kwargs_flag_without_a_value_is_none_not_a_crash(served):
    rows = ModelDeepInspector.sampling_provenance("m", "c", ["--default-chat-template-kwargs"])
    assert rows["Template kwargs"] == "None"


@pytest.mark.parametrize(
    "log, verdict",
    [
        (r"prompt: '<|im_start|>assistant\n<think>\n\n</think>\n\n'", "[green]verified"),
        (r"prompt: '<|im_start|>assistant\n<think>\n'", "[yellow]thinking is ON"),
        ("prompt: 'plain completion'", None),
    ],
    ids=["closed", "open", "no-template"],
)
def test_the_rendered_prompt_decides_whether_thinking_is_really_off(served, log, verdict):
    served["log"] = log
    rows = ModelDeepInspector.sampling_provenance("m", "c", [])
    assert rows.get("Rendered prompt", "")[: len(verdict or "")] == (verdict or "")
    assert ("Rendered prompt" in rows) is (verdict is not None)


GRAPH_LOG = """
compilation_config {'cudagraph_capture_sizes': [64, 32, 16, 8, 4, 2, 1]}
Saved to /root/.cache/vllm/torch_compile_cache/ab12cd/rank_0_0/backbone
Saved to /root/.cache/vllm/torch_compile_cache/ab12cd/rank_0_0/eagle_head
torch.compile takes 412.37 s in total
"""


def test_graph_coverage_reports_headroom_against_the_configured_concurrency(served):
    served["log"] = GRAPH_LOG
    rows = ModelDeepInspector.graph_coverage("c", "16")
    assert rows["CUDA graphs"] == "captured for batch sizes [64, 32, 16, 8, 4, 2, 1]"
    assert rows["Concurrency headroom"] == (
        "max-num-seqs=16; graphs cover up to 64 (3 capture point(s) at or above the current setting)"
    )
    assert rows["Compiled graphs"] == "backbone, eagle_head (cache ab12cd)"
    assert rows["Compile time"] == "412.4s on the last cold start"


@pytest.mark.parametrize("max_num_seqs", [None, "auto"])
def test_no_headroom_row_without_a_numeric_concurrency(served, max_num_seqs):
    served["log"] = GRAPH_LOG
    assert "Concurrency headroom" not in ModelDeepInspector.graph_coverage("c", max_num_seqs)


def test_an_empty_log_gives_no_graph_rows(served):
    assert ModelDeepInspector.graph_coverage("c", "16") == {}
