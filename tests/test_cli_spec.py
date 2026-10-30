"""specs/DREAMFERENCE_CLI.md, checked against the parser and the commands' handlers.

The global options (§2), the defaults the spec names for each command, `run --debug` (§4.3), `init` (§4.1),
`server stop`/`remove` (§4.7), `endpoints` (§4.11), `web` (§4.12), the `clear-tensorize-cache`
alias (§3) and the environment table's defaults (§6). Nothing here starts a server or a container.
"""

import argparse
import json
import socket
import subprocess

import pytest

from dreamference.cli import dreamference_cli_controller as controller
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.config.dreamference_config import DreamferenceConfig
from dreamference.vllm_server import DiffusionServerManager


def subparser(path):
    parser = DreamferenceCLIController.build_parser()
    for name in path:
        action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        parser = action.choices[name]
    return parser


def option(path, flag):
    return next(a for a in subparser(path)._actions if flag in a.option_strings)


def test_the_global_options_come_before_the_subcommand():
    parser = DreamferenceCLIController.build_parser()
    assert option([], "--agent").choices == ["codex", "cline", "continue", "openhands"]
    assert not any("--debug" in a.option_strings for a in subparser([])._actions)
    # `init` and `run` repeat --agent and --hf-token; left off there, they keep the global value.
    # `run`'s --agent defaulted to None and replaced it, so `--agent cline run` ran Codex.
    for command in (["run", "hi"], ["init"]):
        args = parser.parse_args(
            ["--config", "x.toml", "--agent", "cline", "--hf-token", "t", *command]
        )
        assert (args.config, args.agent, args.hf_token) == ("x.toml", "cline", "t"), command
        args = parser.parse_args([*command, "--agent", "openhands"])
        assert args.agent == "openhands", command


def test_a_global_agent_picks_the_runner_for_run(clean_env, monkeypatch):
    from dreamference.runner import CodexRunner, ContinueRunner

    sessions = []
    for runner in (CodexRunner, ContinueRunner):
        monkeypatch.setattr(
            runner,
            "run_session",
            lambda self, prompt, debug=False, **k: (
                sessions.append((type(self).__name__, prompt)) or 0
            ),
        )
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["--agent", "continue", "run", "hello"])
    assert sessions == [("ContinueRunner", "hello")]


@pytest.mark.parametrize(
    "path, flag, default",
    [
        (["server", "stop"], "--port", 8000),
        (["server", "remove"], "--port", 8000),
        (["server", "logs"], "--port", 8000),
        (["logs"], "--port", 8000),
        (["benchmark_server"], "--port", 8000),
        (["benchmark_server"], "--model", "qwen3.8-27b-nvfp4-dflash2"),
        (["benchmark_server"], "--num-prompts", 8),
        (["benchmark_server"], "--max-concurrency", 1),
        (["web"], "--port", 8501),
        (["codex", "test"], "--test-threads", 8),
        (["codex", "test"], "--jobs", 6),
        (["codex", "test"], "--memory-max", "24G"),
        (["gmail", "search"], "--max-results", 10),
        (["gmail", "read"], "--max-chars", 8000),
        (["night", "archive"], "--weeks", 4),
        (["night", "pause"], "--for", "1h"),
        (["swe-bench", "run"], "--code-index", "off"),
        (["swe-bench", "run"], "--mask", "off"),
        (["swe-bench", "run"], "--restart-after-compactions", 0),
        (["node", "add"], "--ssh-port", 22),
        (["node", "power-cycle"], "--off-seconds", 20),
        (["node", "power-cycle"], "--wait-minutes", 10),
        (["node", "power-config", "plug"], "--kind", "shelly"),
        (["node", "power-config", "plug"], "--channel", 0),
        (["node", "power-config", "plug"], "--generation", 2),
        (["node", "provision"], "--from", "this"),
        (["node", "provision"], "--model-source", "copy"),
    ],
)
def test_the_parser_defaults_are_the_specs(path, flag, default):
    assert option(path, flag).default == default


@pytest.mark.parametrize(
    "path, flag, choices",
    [
        (["logs"], "target", ["server", "mcp"]),
        (["run"], "--compat", ["claude", "off"]),
        (["swe-bench", "run"], "--code-index", ["off", "universal", "exact"]),
        (["swe-bench", "run"], "--mask", ["off", "on"]),
        (["swe-bench", "run"], "--exec-tool", ["function", "freeform"]),
        (["node", "provision"], "--model-source", ["copy", "hf"]),
        (["node", "provision"], "--role", ["night", "engine"]),
        (["forge", "import"], "--anonymity", ["full", "denylist", "off"]),
    ],
)
def test_the_choices_are_the_specs(path, flag, choices):
    action = next(a for a in subparser(path)._actions if flag in a.option_strings or a.dest == flag)
    assert list(action.choices) == choices


def test_the_defaults_resolved_after_parsing_are_the_specs():
    """The flags default to None and the command fills in the documented value."""
    from dreamference.night_shift.night_shift_settings import NightShiftSettings
    from dreamference.node.node_provisioner import DEFAULT_START_TIMEOUT_S
    from dreamference.swe_bench.swe_bench_settings import SweBenchSettings

    assert DEFAULT_START_TIMEOUT_S == 1200  # node provision --start-timeout
    assert SweBenchSettings({}).image_chunk == 25  # swe-bench run --image-chunk
    settings = NightShiftSettings({})
    assert settings.idle_minutes == 10  # night run --idle-minutes
    assert settings.window == "01:00-07:00"  # night enable --window


DEFAULT_ENV = (
    "DREAMFERENCE_CONFIG_PATH",
    "DREAMFERENCE_VLLM_HOST",
    "DREAMFERENCE_MODEL",
    "DREAMFERENCE_SERVER_ENGINE",
    "DREAMFERENCE_DIFFUSION_MODEL",
    "DREAMFERENCE_DRAFT_MODEL",
    "DREAMFERENCE_SPECULATIVE_TOKENS",
    "DREAMFERENCE_AGENT",
    "DREAMFERENCE_RUNNER",
    "DREAMFERENCE_USE_TENSORIZER",
    "DREAMFERENCE_MIGHTLING_GMAIL",
    "HF_TOKEN",
    "DREAMFERENCE_HF_TOKEN",
    "DREAMFERENCE_MIGHTLING_AIRGAPPED",
    "DREAMFERENCE_MIGHTLING_PROMPT",
    "DREAMFERENCE_MIGHTLING_COMPACTION_LEDGER",
    "DREAMFERENCE_MIGHTLING_REFINE",
    "DREAMFERENCE_MIGHTLING_REFINE_VERSION",
)


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    for name in DEFAULT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_environment_tables_defaults(clean_env):
    config = DreamferenceConfig(config_file=str(clean_env / "none.toml"))
    assert config.vllm_host == "http://localhost:8000"
    assert config.model == "qwen3.8-27b-nvfp4-dflash2"
    assert config.server_engine is None
    assert config.diffusion_model == "tiny-a2d-coder-0.5b-diffusion"
    assert config.draft_model is None
    assert config.num_speculative_tokens == 8
    assert config.agent_runner == "codex"
    assert config.use_tensorizer is False
    assert config.mightling_gmail is True
    assert config.hf_token is None
    assert config.mightling_airgapped == "off"
    assert config.mightling_prompt == "default"
    assert config.mightling_compaction_ledger is True
    assert config.mightling_refine is False
    assert config.mightling_refine_version == "v1"
    # Tuning keys: kv_cache_dtype is left to the recipe, guided decoding is xgrammar.
    assert config.kv_cache_dtype is None
    assert config.guided_decoding_backend == "xgrammar"


def test_either_agent_variable_picks_the_runner_and_dreamference_agent_wins(clean_env, monkeypatch):
    path = str(clean_env / "none.toml")
    monkeypatch.setenv("DREAMFERENCE_RUNNER", "Continue")
    assert DreamferenceConfig(config_file=path).agent_runner == "continue"
    monkeypatch.setenv("DREAMFERENCE_AGENT", "cline")
    assert DreamferenceConfig(config_file=path).agent_runner == "cline"


TUNING_KEYS = (
    "enable_prefix_caching",
    "enable_chunked_prefill",
    "num_scheduler_steps",
    "attention_backend",
    "kv_cache_dtype",
    "guided_decoding_backend",
)


def test_tuning_keys_have_no_environment_variable(clean_env, monkeypatch):
    path = str(clean_env / "none.toml")
    baseline = DreamferenceConfig(config_file=path)
    for key in TUNING_KEYS:
        monkeypatch.setenv("DREAMFERENCE_" + key.upper(), "7")
    config = DreamferenceConfig(config_file=path)
    assert {k: getattr(config, k) for k in TUNING_KEYS} == {
        k: getattr(baseline, k) for k in TUNING_KEYS
    }


def stub_codex(monkeypatch):
    from dreamference.runner import CodexInstaller

    monkeypatch.setattr(CodexInstaller, "install_if_missing", classmethod(lambda cls: True))
    monkeypatch.setattr(
        CodexInstaller, "get_codex_executable", classmethod(lambda cls: "/opt/ling")
    )
    calls = []
    monkeypatch.setattr("subprocess.call", lambda cmd, env=None, **k: calls.append((cmd, env)) or 0)
    return calls


def test_run_debug_sets_the_documented_rust_log(clean_env, monkeypatch):
    from dreamference.runner import CodexRunner

    calls = stub_codex(monkeypatch)
    monkeypatch.delenv("RUST_LOG", raising=False)
    runner = CodexRunner(config=DreamferenceConfig(config_file=str(clean_env / "d.toml")))
    runner.run_session(prompt="hi", debug=True)
    cmd, env = calls[-1]
    assert cmd[0] == "/opt/ling" and "hi" in cmd
    assert env["RUST_LOG"] == "codex_mcp=trace,codex_core=debug,codex_app_server=debug,info"
    assert env["DREAMFERENCE_VLLM_HOST"] == "http://localhost:8000"
    # A RUST_LOG the user already set is kept; without --debug none is added.
    monkeypatch.setenv("RUST_LOG", "warn")
    runner.run_session(prompt="hi", debug=True)
    assert calls[-1][1]["RUST_LOG"] == "warn"
    monkeypatch.delenv("RUST_LOG")
    runner.run_session(prompt="hi", debug=False)
    assert "RUST_LOG" not in calls[-1][1]


def test_an_unrecognised_agent_runner_goes_to_codex(clean_env, monkeypatch):
    from dreamference.runner import CodexRunner

    monkeypatch.setenv("DREAMFERENCE_AGENT", "banana")
    sessions = []
    monkeypatch.setattr(
        CodexRunner,
        "run_session",
        lambda self, prompt, debug=False, **k: sessions.append((prompt, debug)) or 0,
    )
    with pytest.raises(SystemExit) as stopped:
        DreamferenceCLIController.run_cli(["run", "hello", "--debug"])
    assert stopped.value.code == 0
    assert sessions == [("hello", True)]


@pytest.mark.parametrize("cleared, code", [(True, 0), (False, 1)])
def test_the_clear_tensorize_cache_alias_clears_and_exits_by_the_result(monkeypatch, cleared, code):
    calls = []
    monkeypatch.setattr(controller, "clear_tensorizer_cache", lambda: calls.append(1) or cleared)
    with pytest.raises(SystemExit) as stopped:
        DreamferenceCLIController.run_cli(["clear-tensorize-cache"])
    assert stopped.value.code == code and calls == [1]


def test_init_writes_the_config_at_the_resolved_path_and_reindexes(clean_env, monkeypatch, capsys):
    from dreamference.context_engine import ContextEngine

    downloads, indexed = [], []
    monkeypatch.setattr(
        controller,
        "download_model_with_drafter",
        lambda model, hf_token=None, auto_tensorize=False: downloads.append(model),
    )
    monkeypatch.setattr(controller, "download_model", lambda *a, **k: downloads.append(a[0]))
    monkeypatch.setattr(
        ContextEngine,
        "index_workspace",
        lambda self, force_reindex=False: (
            indexed.append(force_reindex) or {"total_indexed_files": 0, "total_ast_symbols": 0}
        ),
    )
    target = clean_env / "sub" / "mine.toml"
    target.parent.mkdir()
    DreamferenceCLIController.run_cli(["--config", str(target), "init"])
    assert downloads == ["qwen3.8-27b-nvfp4-dflash2"]
    assert indexed == [True]
    text = target.read_text(encoding="utf-8")
    assert 'vllm_host = "http://localhost:8000"' in text and 'agent_runner = "codex"' in text
    assert str(target) in capsys.readouterr().out.replace("\n", "")


def recorded_docker(monkeypatch):
    commands = []

    def run(argv, *args, **kwargs):
        commands.append(list(argv))
        return subprocess.CompletedProcess(argv, 1)  # no such container: still a quiet no-op

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        DiffusionServerManager, "remove_leftover", classmethod(lambda cls, port=8001: None)
    )
    return commands


@pytest.mark.parametrize(
    "command, docker", [("stop", ["docker", "stop"]), ("remove", ["docker", "rm", "-f"])]
)
def test_server_stop_and_remove_take_down_the_server_and_its_gate(monkeypatch, command, docker):
    commands = recorded_docker(monkeypatch)
    DreamferenceCLIController.run_cli(["server", command])
    assert [*docker, "dreamference-vllm-8000"] in commands
    assert [*docker, "dreamference-gate-8000"] in commands
    commands.clear()
    DreamferenceCLIController.run_cli(["server", command, "--port", "8002"])
    assert [*docker, "dreamference-vllm-8002"] in commands
    assert [*docker, "dreamference-gate-8002"] in commands


def test_endpoints_prints_the_documented_tables(monkeypatch, capsys):
    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def connect(self, address):
            assert address == ("8.8.8.8", 80)  # a UDP connect: no packet leaves

        def getsockname(self):
            return ("192.0.2.7", 40000)

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", FakeSocket)
    DreamferenceCLIController.run_cli(["endpoints"])
    out = capsys.readouterr().out
    for path, method in (
        ("/v1/models", "GET"),
        ("/v1/chat/completions", "POST"),
        ("/v1/completions", "POST"),
        ("/v1/embeddings", "POST"),
        ("/health", "GET"),
    ):
        assert any(path in line and method in line for line in out.splitlines()), path
    assert "http://localhost:8000/v1" in out
    assert "http://192.0.2.7:8000/v1" in out
    assert "Authorization: Bearer <key>" in out
    assert ":8001" not in out  # diffusion is switched off


def test_web_serves_on_loopback_at_8501(monkeypatch):
    from dreamference import web_canvas

    bound = []

    class FakeServer:
        def __init__(self, address, handler):
            bound.append((address, handler))

        def serve_forever(self):
            pass

    monkeypatch.setattr(web_canvas.socketserver, "TCPServer", FakeServer)
    web_canvas.start_web_canvas_server().join()
    web_canvas.start_web_canvas_server(port=8600, daemon=False).join()
    assert bound == [
        (("127.0.0.1", 8501), web_canvas.CanvasHandler),
        (("127.0.0.1", 8600), web_canvas.CanvasHandler),
    ]


def test_web_api_status_answers_hardware_vllm_and_context(monkeypatch):
    import io

    from dreamference import web_canvas

    monkeypatch.setattr(web_canvas, "detect_gb10_hardware", lambda: {"gpu": "stub"})
    monkeypatch.setattr(
        web_canvas.VLLMServerManager, "get_server_status", lambda self: {"running": False}
    )
    monkeypatch.setattr(web_canvas.ContextEngine, "load_index", lambda self: False)
    handler = web_canvas.CanvasHandler.__new__(web_canvas.CanvasHandler)
    handler.path = "/api/status"
    handler.wfile = io.BytesIO()
    sent = []
    handler.send_response = sent.append
    handler.send_header = lambda *a: None
    handler.end_headers = lambda: None
    handler.do_GET()
    assert sent == [200]
    assert json.loads(handler.wfile.getvalue()) == {
        "hardware": {"gpu": "stub"},
        "vllm": {"running": False},
        "context": {"total_indexed_files": 0},
    }


def server_start_arguments(monkeypatch, argv):
    from dreamference.vllm_server import VLLMServerManager

    class Monitor:
        server_ready = False

        def start(self):
            pass

        def stop(self):
            pass

    passed = {}

    def captured(self, **kwargs):
        passed.update(kwargs)
        raise SystemExit(1)

    monkeypatch.setattr(controller, "create_model_loading_monitor", lambda *a, **k: Monitor())
    monkeypatch.setattr(VLLMServerManager, "start_server", captured)
    monkeypatch.setattr(
        DiffusionServerManager, "remove_leftover", classmethod(lambda cls, port=8001: None)
    )
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(argv)
    return passed


def test_the_tool_call_parser_and_batch_size_are_config_file_keys(clean_env, monkeypatch):
    """§6 tuning keys: config file or flag.

    With neither, None reaches the launcher, which then takes the model recipe's value.
    """
    path = clean_env / "d.toml"
    path.write_text(
        'tool_call_parser = "qwen3_xml"\nmax_num_batched_tokens = 4096\n', encoding="utf-8"
    )
    config = DreamferenceConfig(config_file=str(path))
    assert (config.tool_call_parser, config.max_num_batched_tokens) == ("qwen3_xml", 4096)
    passed = server_start_arguments(monkeypatch, ["--config", str(path), "server", "start"])
    assert (passed["tool_call_parser"], passed["max_num_batched_tokens"]) == ("qwen3_xml", 4096)
    passed = server_start_arguments(
        monkeypatch,
        [
            "--config",
            str(path),
            "server",
            "start",
            "--tool-call-parser",
            "hermes",
            "--max-num-batched-tokens",
            "2048",
        ],
    )
    assert (passed["tool_call_parser"], passed["max_num_batched_tokens"]) == ("hermes", 2048)
    silent = clean_env / "silent.toml"
    passed = server_start_arguments(monkeypatch, ["--config", str(silent), "server", "start"])
    assert (passed["tool_call_parser"], passed["max_num_batched_tokens"]) == (None, None)
