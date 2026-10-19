"""`scripts/adapters/`: the per-language adapter's data and training pipeline, on tiny CPU inputs."""

import importlib.util
import json
import os
import pickle
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTERS = os.path.join(ROOT, "scripts", "adapters")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ADAPTERS, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules():
    sys.path.insert(0, ADAPTERS)
    loaded = {name: load(name) for name in ("adapter_format", "build_sft", "select_trajectories")}
    yield loaded
    sys.path.remove(ADAPTERS)
    for name in loaded:
        sys.modules.pop(name, None)


TEXT_CONFIG = {"linear_key_head_dim": 2, "linear_num_key_heads": 2, "linear_value_head_dim": 2,
               "linear_num_value_heads": 3}


def test_the_sglang_export_splits_in_proj_qkv_exactly(modules):
    torch = pytest.importorskip("torch")
    fmt = modules["adapter_format"]
    base = "base_model.model.model.layers.0.linear_attn"
    a, b = torch.randn(4, 8), torch.randn(4 + 4 + 6, 4)
    out = fmt.export_for_sglang({f"{base}.in_proj_qkv.lora_A.weight": a, f"{base}.in_proj_qkv.lora_B.weight": b,
                                 f"{base}.out_proj.lora_A.weight": a}, TEXT_CONFIG)
    assert f"{base}.in_proj_qkv.lora_A.weight" not in out
    assert [out[f"{base}.in_proj_{p}.lora_B.weight"].shape[0] for p in "qkv"] == [4, 4, 6]
    rebuilt = torch.cat([out[f"{base}.in_proj_{p}.lora_B.weight"] @ out[f"{base}.in_proj_{p}.lora_A.weight"]
                         for p in "qkv"])
    assert torch.allclose(rebuilt, b @ a)
    assert torch.equal(out[f"{base}.out_proj.lora_A.weight"], a)


def test_the_export_refuses_a_qkv_of_the_wrong_height(modules):
    torch = pytest.importorskip("torch")
    with pytest.raises(ValueError):
        modules["adapter_format"].export_for_sglang(
            {"x.in_proj_qkv.lora_B.weight": torch.zeros(5, 2)}, TEXT_CONFIG)


def test_the_exported_config_names_the_split_modules(modules):
    config = modules["adapter_format"].export_config({"target_modules": ["in_proj_qkv", "in_proj_z", "q_proj"], "r": 16})
    assert config["target_modules"] == ["in_proj_k", "in_proj_q", "in_proj_v", "in_proj_z", "q_proj"]
    assert config["r"] == 16


def test_every_target_lands_in_a_module_sglang_serves(modules):
    fmt = modules["adapter_format"]
    assert all(fmt.SGLANG_MODULE_OF[m] in fmt.SGLANG_SUPPORTED for m in fmt.TARGET_MODULES)
    assert not set(fmt.NOT_SERVABLE) & set(fmt.TARGET_MODULES)


def test_labels_cover_assistant_turns_only(modules):
    sft = modules["build_sft"]
    header, think, end = [1, 2], [7, 7], 9
    #        system   | assistant header, empty think, text, end | user    | assistant, end
    ids = [5, 5, 9, 3] + [1, 2, 7, 7, 40, 41, 9] + [3, 6, 9] + [1, 2, 50, 9]
    labels = sft.assistant_labels(ids, header, think, end)
    trained = [t for t, l in zip(ids, labels) if l != sft.IGNORE]
    assert trained == [40, 41, 9, 50, 9]


def test_only_the_end_of_each_segment_is_kept(modules):
    sft = modules["build_sft"]
    requests = [{"input_ids": [1, 2, 3]}, {"input_ids": [1, 2, 3, 4, 5]}, {"input_ids": [1, 2, 3, 4, 5, 6]},
                {"input_ids": [1, 9]}]
    assert sorted(tuple(r["input_ids"]) for r in sft.last_of_segments(requests)) == [(1, 2, 3, 4, 5, 6), (1, 9)]


class GenerateReqInput:  # stands in for SGLang's class in the dump
    def __init__(self, input_ids, rid):
        self.input_ids, self.rid = input_ids, rid


def test_dumps_are_read_without_sglang(modules, tmp_path):
    entries = [(GenerateReqInput([1, 2, 3], "a"), {"output_ids": [4, 5], "text": "x",
                                                    "meta_info": {"completion_tokens": 2, "finish_reason": "stop"}},
                1.0, 2.0),
               (GenerateReqInput([1, 2], "b"), {"output_ids": [9], "text": "partial",
                                                 "meta_info": {"completion_tokens": 3}}, 1.0, 2.0)]
    path = tmp_path / "dump.pkl"
    path.write_bytes(pickle.dumps(entries))
    rows = list(modules["build_sft"].extract([path]))
    assert [r["rid"] for r in rows] == ["a", "b"]
    assert rows[0]["output_ids"] == [4, 5]
    assert rows[1]["output_ids"] is None and rows[1]["output_text"] == "partial"


def write_run(root, name, grading, rollout_lines):
    run = root / name
    (run / "eval" / "1").mkdir(parents=True)
    (run / "eval" / "1" / "grading.json").write_text(json.dumps({"grader": "x", "results": grading}))
    (run / "manifest.json").write_text(json.dumps({"dataset": "d"}))
    for instance_id in grading:
        sessions = run / "scratch" / instance_id / "codex-home" / "sessions" / "2026" / "10" / "19"
        sessions.mkdir(parents=True)
        (sessions / "rollout-1.jsonl").write_text("".join(json.dumps(line) + "\n" for line in rollout_lines))
    return run


def test_selection_keeps_resolved_attempts_only(modules, tmp_path):
    sel = modules["select_trajectories"]
    usage = {"type": "token_usage_record", "payload": {"usage": {"input_tokens": 100, "output_tokens": 5}}}
    good = {"resolved": True, "f2p": 1, "f2p_failed": 0, "p2p": 3, "p2p_failed": 0}
    run = write_run(tmp_path, "r1", {"a__a-1": good, "b__b-2": {"resolved": False, "f2p": 1},
                                     "c__c-3": dict(good, p2p_failed=1)},
                    [usage, {"type": "compacted", "payload": {}}, usage])
    kept = sel.select([run], per_task=2, max_tokens=1000)
    assert [a["instance_id"] for a in kept] == ["a__a-1"]
    assert kept[0]["compactions"] == 1 and kept[0]["requests"] == 2 and kept[0]["last_tokens"] == 105
    assert sel.select([run], per_task=2, max_tokens=50) == []


def test_the_overlap_guard_refuses_lists_verified_repos_and_the_held_out_set(modules, tmp_path):
    sel = modules["select_trajectories"]
    rows = [{"instance_id": "django__django-1", "repo": "django/django"},
            {"instance_id": "x__y-1", "repo": "x/y"},
            {"instance_id": "tokio-rs__tokio-1", "repo": "tokio-rs/tokio", "base_commit": "abc"},
            {"instance_id": "other-id", "repo": "tokio-rs/tokio", "base_commit": "abc"},
            {"instance_id": "serde-rs__serde-1", "repo": "serde-rs/serde", "base_commit": "def"}]
    result = sel.overlap(rows, {"x__y-1"}, {"django/django"}, {"tokio-rs__tokio-1"}, {"tokio-rs/tokio@abc"})
    assert result["kept"] == ["serde-rs__serde-1"]
    assert result["night_list"] == ["x__y-1"]
    assert result["verified_repo"] == ["django__django-1"]
    assert result["held_out"] == ["tokio-rs__tokio-1", "other-id"]


def test_the_served_layer_matches_its_dense_weights_on_the_cpu():
    pytest.importorskip("torch")
    sys.path.insert(0, ADAPTERS)
    try:
        served = load("served_base")
        assert served.check_layer() == []
        assert served.check_activation_rounding() == []
    finally:
        sys.path.remove(ADAPTERS)
        sys.modules.pop("served_base", None)


def test_the_plan_needs_neither_torch_nor_a_gpu(tmp_path, capsys):
    train = load("train_lora")
    config = train.load_config(__import__("pathlib").Path(ADAPTERS) / "configs" / "rust-r16-48k.toml")
    config["train"]["samples"] = str(tmp_path / "missing.jsonl")
    train.plan(config)
    out = capsys.readouterr().out
    assert "memory at max_seq_len" in out and "optimizer steps" in out
    sys.modules.pop("train_lora", None)
