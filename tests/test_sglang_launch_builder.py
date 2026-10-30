"""What SGLangLaunchBuilder puts on SGLang's command line for a recipe."""

from dreamference.vllm_server.chat_template_patcher import CONTAINER_TEMPLATE_DIR
from dreamference.vllm_server.sglang_launch_builder import SGLangLaunchBuilder


def _args(recipe, max_model_len=None):
    return SGLangLaunchBuilder.server_args(
        "example/target",
        recipe,
        port=8001,
        max_model_len=max_model_len,
        gpu_memory_utilization=0.5,
        tool_call_parser=None,
        reasoning_parser=None,
    )


def _flag(args, name):
    return args[args.index(name) + 1] if name in args else None


def test_the_engine_is_named_in_any_case():
    assert SGLangLaunchBuilder.is_sglang({"engine": "SGLang"})
    assert not SGLangLaunchBuilder.is_sglang({"engine": "vllm"})
    assert not SGLangLaunchBuilder.is_sglang({})


def test_the_context_length_is_passed_only_when_set():
    assert _flag(_args({}, max_model_len=65536), "--context-length") == "65536"
    assert "--context-length" not in _args({})


def test_a_drafter_recipe_becomes_sglang_speculative_flags():
    args = _args(
        {
            "speculative_config": {
                "model": "example/drafter",
                "method": "dflash",
                "num_speculative_tokens": 4,
                "quantization": "fp8",
            }
        }
    )
    assert _flag(args, "--speculative-algorithm") == "DFLASH"
    assert _flag(args, "--speculative-draft-model-path") == "example/drafter"
    assert _flag(args, "--speculative-num-draft-tokens") == "4"
    assert _flag(args, "--speculative-draft-model-quantization") == "fp8"
    plain = _args({"speculative_config": {"model": "example/drafter"}})
    assert "--speculative-draft-model-quantization" not in plain


def test_a_patched_chat_template_needs_a_pinned_revision():
    # The patched template is named after the commit it was made from; without one there is none.
    patches = [["anchor", "replacement"]]
    assert "--chat-template" not in _args({"chat_template_patches": patches})
    pinned = _args({"chat_template_patches": patches, "revision": "0" * 40})
    assert _flag(pinned, "--chat-template").startswith(CONTAINER_TEMPLATE_DIR + "/")


def test_a_recipe_without_a_drafter_gets_no_speculative_flags():
    # Self-speculation (a method and a depth, no draft model) is vLLM's; SGLang is given none of it.
    args = _args({"speculative_config": {"method": "mtp", "num_speculative_tokens": 3}})
    assert not any(a.startswith("--speculative") for a in args)


def test_the_parsers_are_passed_only_when_named():
    args = SGLangLaunchBuilder.server_args(
        "example/target",
        {},
        port=8001,
        max_model_len=None,
        gpu_memory_utilization=0.5,
        tool_call_parser="qwen3_coder",
        reasoning_parser="qwen3",
    )
    assert _flag(args, "--tool-call-parser") == "qwen3_coder"
    assert _flag(args, "--reasoning-parser") == "qwen3"
    assert not {"--tool-call-parser", "--reasoning-parser"} & set(_args({}))
