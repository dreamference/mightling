from dgxcoder.vllm_server import VLLMServerManager

def test_vllm_build_launch_command_speculative():
    mgr = VLLMServerManager()
    cmd = mgr.build_launch_command(
        model="qwen2.5-coder-32b",
        draft_model="qwen2.5-coder-1.5b",
        num_speculative_tokens=5
    )
    assert "--speculative-model" in cmd
    idx = cmd.index("--speculative-model")
    assert cmd[idx + 1] == "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    assert "--num-speculative-tokens" in cmd
    tokens_idx = cmd.index("--num-speculative-tokens")
    assert cmd[tokens_idx + 1] == "5"
    assert "--enable-prefix-caching" in cmd
    assert "--enable-chunked-prefill" in cmd
    assert "--num-scheduler-steps" in cmd
