"""What the vLLM launch command falls back to when the image, the cache or the host lacks something."""

from types import SimpleNamespace

import pytest

from dreamference.hardware.model_downloader import ModelDownloader
from dreamference.vllm_server import VLLMServerManager


@pytest.fixture
def command(monkeypatch):
    """`build_launch_command` over a recipe, a docker and an image that the test sets in `state`."""
    mgr = VLLMServerManager()
    state = {
        "recipe": {},
        "docker": True,
        "image_tensorizer": True,
        "tensorized_path": "/models/m.tensors",
    }
    monkeypatch.setattr(
        "dreamference.hardware.get_model_launch_overrides",
        lambda model, engine=None: dict(state["recipe"]),
    )
    monkeypatch.setattr("dreamference.hardware.resolve_model_hf_repo", lambda model: "org/model")
    monkeypatch.setattr("dreamference.hardware.model_declares_own_quantization", lambda model: True)
    monkeypatch.setattr(ModelDownloader, "is_model_tensorized", classmethod(lambda cls, key: True))
    monkeypatch.setattr(
        ModelDownloader,
        "get_tensorized_path",
        classmethod(lambda cls, key: state["tensorized_path"]),
    )
    monkeypatch.setattr(mgr, "is_docker_available", lambda: state["docker"])
    monkeypatch.setattr(mgr, "image_has_tensorizer", lambda image: state["image_tensorizer"])
    monkeypatch.setattr(mgr, "detect_image_vllm_version", lambda image: (0, 12))
    monkeypatch.setattr(mgr, "_docker_run_prefix", lambda *a, **k: ["docker", "run"])

    def build(**kwargs):
        return mgr.build_launch_command(model="model", gate=False, **kwargs)

    return SimpleNamespace(build=build, state=state)


def _value(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_without_docker_no_command_is_built(command):
    command.state["docker"] = False

    with pytest.raises(RuntimeError, match="Docker is required to run vLLM"):
        command.build(use_tensorizer=True)


def test_an_image_without_tensorizer_loads_the_checkpoint_the_default_way(command, capsys):
    command.state["image_tensorizer"] = False

    cmd = command.build(use_tensorizer=True)

    assert "--model-loader-extra-config" not in cmd
    assert "tensorizer" not in cmd
    assert "does not include the 'tensorizer' package" in capsys.readouterr().out


def test_a_model_marked_tensorized_without_a_tensor_file_loads_the_default_way(command):
    command.state["tensorized_path"] = None

    cmd = command.build(use_tensorizer=True)

    assert "--model-loader-extra-config" not in cmd
    assert "tensorizer" not in cmd


def test_a_pinned_revision_without_template_patches_keeps_the_checkpoints_template(command):
    command.state["recipe"] = {"revision": "abc123"}

    cmd = command.build()

    assert _value(cmd, "--revision") == "abc123"
    assert _value(cmd, "--tokenizer-revision") == "abc123"
    assert "--chat-template" not in cmd


def test_features_turned_off_leave_their_flags_out(command, monkeypatch):
    # A model none of whose tool-call parsers applies.
    monkeypatch.setattr(
        VLLMServerManager, "resolve_tool_call_parser", lambda self, model, parser, engine=None: None
    )

    cmd = command.build(
        enable_chunked_prefill=False,
        max_num_batched_tokens=4096,
        attention_backend="auto",
        enable_auto_tool_choice=False,
        guided_decoding_backend="",
    )

    for flag in (
        "--enable-chunked-prefill",
        "--max-num-batched-tokens",
        "--attention-backend",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "--structured-outputs-config",
        "--guided-decoding-backend",
    ):
        assert flag not in cmd
