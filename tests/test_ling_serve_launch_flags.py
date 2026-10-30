"""Which ling-serve flags a recipe's fields turn on."""

from dreamference.vllm_server.ling_serve_launch_builder import (
    KV_PAGES_FEATURE,
    KV_PAGES_FLAG,
    LingServeLaunchBuilder,
)


def _args(recipe, max_model_len=None):
    return LingServeLaunchBuilder.server_args("org/model", recipe, 8000, max_model_len)


def test_a_drafter_without_a_block_size_gets_no_draft_block():
    args = _args({"speculative_config": {"model": "org/drafter"}})
    assert "--draft" in args and "--draft-block" not in args


def test_a_drafter_with_a_block_size_gets_both_flags():
    args = _args({"speculative_config": {"model": "org/drafter", "num_speculative_tokens": 4}})
    assert args[args.index("--draft-block") + 1] == "4"


def test_a_speculative_config_without_a_model_adds_no_drafter():
    args = _args({"speculative_config": {"num_speculative_tokens": 4}})
    assert "--draft" not in args and "--draft-block" not in args


def test_without_kv_pages_sessions_and_batch_are_not_passed():
    args = _args({"sessions": 4, "batch": 2})
    assert "--sessions" not in args and "--batch" not in args and KV_PAGES_FLAG not in args


def test_kv_pages_passes_only_the_counts_the_recipe_sets():
    args = _args({"engine_features": [KV_PAGES_FEATURE], "sessions": 4})
    assert args[args.index("--sessions") + 1] == "4"
    assert "--batch" not in args and KV_PAGES_FLAG in args
    args = _args({"engine_features": [KV_PAGES_FEATURE], "batch": 2})
    assert "--sessions" not in args and args[args.index("--batch") + 1] == "2"
    assert KV_PAGES_FLAG in args


def test_no_max_context_without_a_model_length():
    assert "--max-context" not in _args({})
    args = _args({}, max_model_len=4096)
    assert args[args.index("--max-context") + 1] == "4096"
