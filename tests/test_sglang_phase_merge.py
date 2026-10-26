"""SGLang v0.5.21's phase-gated history merge, and the recipe's runtime patch that removes the gate.

`/v1/responses` tags the text before a tool call `phase: "commentary"`; rebuilding the history it
merged consecutive assistant items only when their phases were equal, and a function_call has none,
so every announcement rendered as an assistant turn of its own (models-and-engines.md, the SGLang
traps). No docker: the pinned file's conversion is a verbatim excerpt in tests/fixtures/sglang/, and
the image's file comes from a replaced `docker` call.
"""

import ast
import hashlib
import itertools
import json
import pathlib
import types
from typing import Any, Optional

import pytest
from test_adapter_serving import install, verified  # noqa: F401 - fixtures

from dreamference.hardware.model_matrix_registry import (
    DEFAULT_MODEL_ALIAS,
    QWEN38_LORA_RECIPE,
    QWEN38_SGLANG_IMAGE,
    QWEN38_SGLANG_RECIPE,
    SGLANG_PHASE_MERGE_PATCH,
    SGLANG_SERVING_RESPONSES,
    ModelMatrixRegistry,
)
from dreamference.vllm_server.chat_template_patcher import ChatTemplatePatcher
from dreamference.vllm_server.sglang_runtime_patcher import SGLangRuntimePatcher
from dreamference.vllm_server.vllm_server_manager import VLLMServerManager

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "sglang"
EXCERPT = (FIXTURES / "serving_responses_v0.5.21_excerpt.py.txt").read_text(encoding="utf-8")
# Night 12's SGLang arm, django__django-15268: the response items Codex sent back on its last
# request (ids dropped, outputs shortened, the task text replaced). Three announcements before a
# tool call, then the early stop itself, an announcement with no call.
ROLLOUT = FIXTURES / "django-15268-night12-items.jsonl"
MINIMA = "qwen3.8-27b-minima-nvfp4-dflash2"
PHASE_GATE = 'and merged[-1].get("phase") == msg.get("phase")'


def patched(text: str) -> str:
    """The text with the recipe's phase-merge substitution applied, every anchor exactly once."""
    out, failed = ChatTemplatePatcher.apply(
        text, [tuple(pair) for pair in SGLANG_PHASE_MERGE_PATCH["substitutions"]]
    )
    assert failed == []
    return out


def conversion(source: str) -> Any:
    """The excerpt's class, executed with stand-ins for what the module imports."""
    namespace: dict[str, Any] = {
        "Any": Any,
        "Optional": Optional,
        "ResponsesRequest": object,
        "ResponsesResponse": object,
        "ChatCompletionMessageParam": dict,
        "orjson": types.SimpleNamespace(
            JSONDecodeError=ValueError,
            loads=json.loads,
            dumps=lambda value: json.dumps(value).encode(),
        ),
        "label_developer_content": lambda content: content,
        "encode_custom_tool_input": lambda text: json.dumps({"input": text}),
        "decode_reasoning_state": lambda state: None,
    }
    exec(compile(ast.parse(source), "serving_responses_excerpt", "exec"), namespace)
    return namespace["OpenAIServingResponses"]


def history(source: str) -> list[dict[str, Any]]:
    """The chat messages the conversion builds from the rollout's items."""
    items = [json.loads(line) for line in ROLLOUT.read_text(encoding="utf-8").splitlines()]
    serving = conversion(source)
    instance = serving.__new__(serving)
    instance.msg_store = {}
    request = types.SimpleNamespace(instructions="SYSTEM", previous_response_id=None, input=items)
    return serving._construct_input_messages(instance, request)


def shape(messages: list[dict[str, Any]]) -> dict[str, int]:
    """Counts of assistant blocks: text alone, text with its calls, and splits.

    A split is a text-only assistant block followed directly by an assistant block of tool calls:
    the announcement rendered as a turn of its own.
    """
    assistant = [m for m in messages if m["role"] == "assistant"]
    return {
        "text_only": sum(1 for m in assistant if m.get("content") and not m.get("tool_calls")),
        "text_and_calls": sum(1 for m in assistant if m.get("content") and m.get("tool_calls")),
        "split": sum(
            1
            for first, then in itertools.pairwise(messages)
            if first["role"] == then["role"] == "assistant"
            and first.get("content")
            and not first.get("tool_calls")
            and then.get("tool_calls")
        ),
    }


# -- the patch against the pinned file -----------------------------------------------------------


def test_the_substitution_applies_once_to_the_pinned_files_merge_and_removes_the_phase_gate():
    assert EXCERPT.count(PHASE_GATE) == 1
    out = patched(EXCERPT)
    assert PHASE_GATE not in out
    # Nothing else of the merge changes: the role checks stay.
    assert out.count('merged[-1].get("role") == "assistant"') == 1
    ast.parse(out)


def test_the_patch_is_pinned_to_the_recipes_image_and_the_images_own_file():
    assert SGLANG_PHASE_MERGE_PATCH["image"] == QWEN38_SGLANG_RECIPE["docker_image"]
    assert SGLANG_PHASE_MERGE_PATCH["path"] == SGLANG_SERVING_RESPONSES
    assert len(SGLANG_PHASE_MERGE_PATCH["sha256"]) == 64
    for model in (DEFAULT_MODEL_ALIAS, MINIMA):
        recipe = ModelMatrixRegistry.get_launch_overrides(model)
        assert recipe["runtime_patches"] == [SGLANG_PHASE_MERGE_PATCH], model
        assert recipe["docker_image"] == QWEN38_SGLANG_IMAGE, model
    # The adapters' patch of the same file pins the same original, so the two become one file.
    (lora,) = QWEN38_LORA_RECIPE["runtime_patches"]
    for key in ("path", "sha256", "image"):
        assert lora[key] == SGLANG_PHASE_MERGE_PATCH[key]


def test_the_rollout_that_stopped_early_renders_its_announcements_with_their_calls():
    before = shape(history(EXCERPT))
    after = shape(history(patched(EXCERPT)))
    # Unpatched: three announcements each end an assistant turn before their call, plus the final
    # text (the early stop). Patched: each announcement shares its block with its call; the final
    # text, which has no call, is the one text-only block left.
    assert before == {"text_only": 4, "text_and_calls": 0, "split": 3}
    assert after == {"text_only": 1, "text_and_calls": 3, "split": 0}


# -- the launch ----------------------------------------------------------------------------------


def mounts(cmd: list[str]) -> list[str]:
    return [
        cmd[i + 1]
        for i, arg in enumerate(cmd)
        if arg == "-v" and SGLANG_SERVING_RESPONSES in cmd[i + 1]
    ]


def test_every_sglang_launch_mounts_the_patched_file_without_adapters(verified):  # noqa: F811
    recipe = ModelMatrixRegistry.get_launch_overrides(DEFAULT_MODEL_ALIAS)
    (patch,) = SGLangRuntimePatcher.for_launch(recipe, lora=False)
    written = SGLangRuntimePatcher.host_dir() / SGLangRuntimePatcher.file_name(patch)
    expected = f"{written}:{SGLANG_SERVING_RESPONSES}:ro"
    mgr = VLLMServerManager()
    for kwargs in ({}, {"gate": True}, {"gate": True, "api_key": "k"}):
        cmd = mgr.build_launch_command(model=DEFAULT_MODEL_ALIAS, **kwargs)
        assert "--enable-lora" not in cmd
        assert mounts(cmd) == [expected], kwargs
    assert len(mounts(mgr.build_launch_command(model=MINIMA, gate=True))) == 1


def test_with_adapters_the_one_mounted_file_carries_both_patches(verified):  # noqa: F811
    install()
    recipe = ModelMatrixRegistry.get_launch_overrides(DEFAULT_MODEL_ALIAS)
    (both,) = SGLangRuntimePatcher.for_launch(recipe, lora=True)
    assert both["substitutions"] == [
        *SGLANG_PHASE_MERGE_PATCH["substitutions"],
        *QWEN38_LORA_RECIPE["runtime_patches"][0]["substitutions"],
    ]
    cmd = VLLMServerManager().build_launch_command(model=DEFAULT_MODEL_ALIAS, gate=True)
    assert "--enable-lora" in cmd
    (mount,) = mounts(cmd)
    assert mount.startswith(
        f"{SGLangRuntimePatcher.host_dir() / SGLangRuntimePatcher.file_name(both)}:"
    )
    (alone,) = SGLangRuntimePatcher.for_launch(recipe, lora=False)
    assert SGLangRuntimePatcher.file_name(alone) != SGLangRuntimePatcher.file_name(both)


def test_two_patches_of_one_file_that_pin_different_originals_are_a_registry_error():
    recipe = {
        "runtime_patches": [SGLANG_PHASE_MERGE_PATCH],
        "lora": {"runtime_patches": [{**SGLANG_PHASE_MERGE_PATCH, "sha256": "0" * 64}]},
    }
    assert len(SGLangRuntimePatcher.for_launch(recipe, lora=False)) == 1
    with pytest.raises(ValueError, match="different originals"):
        SGLangRuntimePatcher.for_launch(recipe, lora=True)


# -- failing closed ------------------------------------------------------------------------------


@pytest.fixture
def image_holds(monkeypatch):
    """The image's file is the given text; records the docker commands run."""
    state: dict[str, Any] = {"text": EXCERPT, "calls": []}

    def docker(cls, command):
        state["calls"].append(command[:2])
        if command[1] == "cp":
            pathlib.Path(command[3]).write_text(state["text"], encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="container-id\n")

    monkeypatch.setattr(SGLangRuntimePatcher, "_docker", classmethod(docker))
    return state


def pinned_to(text: str) -> dict[str, Any]:
    """The recipe's patch, pinned to a stand-in file's sha256."""
    return {**SGLANG_PHASE_MERGE_PATCH, "sha256": hashlib.sha256(text.encode()).hexdigest()}


def test_the_patched_file_is_written_from_the_images_own_copy(image_holds):
    patch = pinned_to(EXCERPT)
    assert SGLangRuntimePatcher.prepare(QWEN38_SGLANG_IMAGE, [patch]) is None
    written = SGLangRuntimePatcher.host_dir() / SGLangRuntimePatcher.file_name(patch)
    assert PHASE_GATE not in written.read_text(encoding="utf-8")


def test_a_missing_target_fails_loud_and_writes_nothing(image_holds):
    # As after an image bump whose file no longer has the gate (or has it reworded).
    image_holds["text"] = EXCERPT.replace(
        PHASE_GATE, 'and merged[-1].get("phase") is msg.get("phase")'
    )
    patch = pinned_to(image_holds["text"])
    problem = SGLangRuntimePatcher.prepare(QWEN38_SGLANG_IMAGE, [patch])
    assert problem == "1 anchor(s) of the patch to serving_responses.py no longer match"
    assert not (SGLangRuntimePatcher.host_dir() / SGLangRuntimePatcher.file_name(patch)).exists()


def test_a_file_that_is_not_the_pinned_one_is_never_patched(image_holds):
    problem = SGLangRuntimePatcher.prepare(QWEN38_SGLANG_IMAGE, [SGLANG_PHASE_MERGE_PATCH])
    assert (
        problem
        == f"serving_responses.py in {QWEN38_SGLANG_IMAGE} is not the file the patch was written for"
    )


def test_another_image_is_refused_before_docker_runs_even_with_a_cached_file(image_holds):
    patch = pinned_to(EXCERPT)
    assert SGLangRuntimePatcher.prepare(QWEN38_SGLANG_IMAGE, [patch]) is None
    image_holds["calls"].clear()
    other = "lmsysorg/sglang@sha256:" + "f" * 64
    assert SGLangRuntimePatcher.prepare(other, [patch]) == (
        f"the patch to serving_responses.py is pinned to {QWEN38_SGLANG_IMAGE}, not {other}"
    )
    assert image_holds["calls"] == []
    # A file written for one image is never the name another image's launch would mount.
    assert SGLangRuntimePatcher.file_name(patch) != SGLangRuntimePatcher.file_name(
        {**patch, "image": other}
    )
