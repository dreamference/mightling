"""
DGXCoder Command Line Interface (CLI) Controller.

This module provides the DGXCoderCLIController class which parses command line arguments
for subcommands (`init`, `chat`, `run`, `status`, `start_server`, `index`, `mcp`, `download`, `web`),
renders Rich terminal user interfaces, and coordinates backend component execution.
"""

import argparse
import os
import sys
import time
from typing import Final

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from dgxcoder.config import DGXCoderConfig
from dgxcoder.config.dgxcoder_config import DEFAULT_MODEL
from dgxcoder.runner import (
    GooseRunner, ClineRunner, ClineInstaller,
    AiderRunner, AiderInstaller,
    ContinueRunner, ContinueInstaller,
    OpenHandsRunner, OpenHandsInstaller
)
from dgxcoder.hardware import detect_gb10_hardware, download_model, download_all_models, clear_model_cache, clear_tensorizer_cache
from dgxcoder.vllm_server import VLLMServerManager, DEFAULT_VLLM_IMAGE
from dgxcoder.vllm_server.model_loading_monitor import create_model_loading_monitor
from dgxcoder.context_engine import ContextEngine
from dgxcoder.mcp_server import main as run_mcp_server
from dgxcoder.web_canvas import start_web_canvas_server

# Global Rich console instance for styled terminal outputs
console: Final[Console] = Console()

class DGXCoderCLIController:
    """
    Controller class for DGXCoder CLI operations, Rich status panels, and subcommand routing.
    """

    @classmethod
    def display_header(cls) -> None:
        """Renders styled Rich header panel displaying DGXCoder branding and GB10 target architecture."""
        console.print(Panel.fit(
            "[bold green]⚡ DGXCoder[/bold green] - Autonomous Local Agentic Coding Engine\n"
            "[dim]Exclusive Target Hardware: NVIDIA GB10 (Blackwell Architecture | 128 GB Unified Memory)[/dim]",
            border_style="green"
        ))

    @classmethod
    def handle_status(cls) -> None:
        """
        Executes `dgxcoder status` command, displaying hardware metrics, vLLM health, Goose/Cline/Aider/Continue/OpenHands config,
        and context engine index telemetry in formatted Rich panels.
        """
        cls.display_header()
        hw = detect_gb10_hardware()
        config = DGXCoderConfig()
        vllm_mgr = VLLMServerManager(host=config.vllm_host)
        vllm_status = vllm_mgr.get_server_status()
        goose_runner = GooseRunner(config=config)
        ctx_engine = ContextEngine()
        ctx_summary = ctx_engine.get_summary() if ctx_engine.load_index() else None

        # 1. NVIDIA GB10 Hardware Telemetry Panel
        hw_table = Table(show_header=False, box=None)
        hw_table.add_column("Property", style="bold cyan")
        hw_table.add_column("Value", style="white")

        is_gb10_str = "[bold green]✅ Qualified (GB10 128GB Unified Memory Target)[/bold green]" if hw["is_gb10"] else "[yellow]⚠️ System running non-GB10 host[/yellow]"
        hw_table.add_row("System Target", is_gb10_str)
        hw_table.add_row("GPU Hardware", str(hw["gpu_name"]))
        hw_table.add_row("Driver Version", str(hw["driver_version"]))
        hw_table.add_row("Total Unified Memory", f"{hw['total_unified_memory_gb']} GB")
        hw_table.add_row("Used Memory", f"{hw['used_memory_gb']} GB")
        hw_table.add_row("Available Memory", f"{hw['available_memory_gb']} GB")
        hw_table.add_row("Architecture", str(hw["arch"]))

        console.print(Panel(hw_table, title="[bold]🖥️ NVIDIA GB10 Hardware Status[/bold]", border_style="blue"))

        # 2. vLLM Server & Agent Status Panel
        agent_table = Table(show_header=False, box=None)
        agent_table.add_column("Property", style="bold cyan")
        agent_table.add_column("Value", style="white")

        vllm_str = f"[green]Online ({config.vllm_host})[/green]" if vllm_status["healthy"] else f"[red]Offline ({config.vllm_host})[/red]"
        goose_str = "[green]Installed[/green]" if goose_runner.is_goose_installed() else "[yellow]Not Found[/yellow]"
        cline_str = "[green]Extension Ready[/green]" if ClineInstaller.is_cline_extension_installed() else "[yellow]Extension Available[/yellow]"
        aider_str = "[green]CLI Ready[/green]" if AiderInstaller.is_installed() else "[yellow]CLI Available[/yellow]"
        continue_str = "[green]Extension Ready[/green]" if ContinueInstaller.is_continue_extension_installed() else "[yellow]Extension Available[/yellow]"
        openhands_str = "[green]Docker Image Ready[/green]" if OpenHandsInstaller.is_image_downloaded() else "[yellow]Docker Available[/yellow]"

        agent_table.add_row("Local vLLM Server", vllm_str)
        agent_table.add_row("Active Served Models", ", ".join(vllm_status["models"]) if vllm_status["models"] else "None (vLLM idle)")
        agent_table.add_row("Active Agent Runner", f"[bold green]{config.agent_runner.upper()}[/bold green] (Default: GOOSE)")
        agent_table.add_row("Configured Model", config.model)
        from dgxcoder.hardware import is_model_tensorized
        tensorize_str = "[bold green]Saved & Active (.tensors)[/bold green]" if is_model_tensorized(config.model) else "[yellow]Standard Weights (HF Cache)[/yellow]"
        agent_table.add_row("Tensorize Format Status", tensorize_str)
        if config.draft_model:
            agent_table.add_row("Speculative Draft Model", f"{config.draft_model} ({config.num_speculative_tokens} tokens)")
        sandbox_str = f"[bold green]{config.sandbox.upper()} (Rootless Isolated)[/bold green]" if config.sandbox != "none" else "[yellow]Disabled (Native Host)[/yellow]"
        agent_table.add_row("Container Sandbox", sandbox_str)
        hf_token_str = f"[green]Configured ({config.hf_token[:4]}...{config.hf_token[-4:]})[/green]" if config.hf_token else "[yellow]Not Configured (Anonymous Hub Access)[/yellow]"
        agent_table.add_row("HuggingFace Auth Token", hf_token_str)
        agent_table.add_row("Prefix Caching / Chunked", "[bold green]Enabled (Blackwell GB10 Optimized)[/bold green]")
        agent_table.add_row("Multi-Step Scheduling", f"{config.num_scheduler_steps} steps/iter")
        agent_table.add_row("KV Cache Dtype", config.kv_cache_dtype)
        agent_table.add_row("Tool Call Parser", config.resolve_tool_call_parser())
        # Surfaced because on GB10 (SM121) the wrong MoE kernel does not error — it produces
        # corrupt output — so the selected backend is worth being able to read off `status`.
        from dgxcoder.hardware import get_model_launch_overrides
        recipe = get_model_launch_overrides(config.model)
        agent_table.add_row(
            "MoE Kernel Backend",
            recipe.get("moe_backend") or "[yellow]vLLM default (no model recipe)[/yellow]"
        )
        agent_table.add_row("Goose CLI Runtime", goose_str)
        agent_table.add_row("Cline Extension Runtime", cline_str)
        agent_table.add_row("Aider CLI Runtime", aider_str)
        agent_table.add_row("Continue IDE Runtime", continue_str)
        agent_table.add_row("OpenHands Docker Runtime", openhands_str)
        agent_table.add_row("DGXCoder Config Path", str(config.config_file_path))
        agent_table.add_row("Goose Config Path", str(config.config_path))

        console.print(Panel(agent_table, title="[bold]🤖 vLLM & Agent Status[/bold]", border_style="magenta"))

        # 3. Context Engine Index Panel
        if ctx_summary:
            ctx_table = Table(show_header=False, box=None)
            ctx_table.add_column("Property", style="bold cyan")
            ctx_table.add_column("Value", style="white")
            ctx_table.add_row("Indexed Workspace Files", str(ctx_summary["total_indexed_files"]))
            ctx_table.add_row("Total AST Symbols", str(ctx_summary["total_ast_symbols"]))
            ctx_table.add_row("Index Path", str(ctx_summary["index_file"]))
            if ctx_summary.get("sqlite_file"):
                ctx_table.add_row("SQLite Context Store", str(ctx_summary["sqlite_file"]))
            console.print(Panel(ctx_table, title="[bold]📚 Context Engine Index Status[/bold]", border_style="green"))

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        """
        Constructs ArgumentParser with subcommands for DGXCoder CLI operations.

        Returns:
            argparse.ArgumentParser: Configured argument parser object.
        """
        parser = argparse.ArgumentParser(
            prog="dgxcoder",
            description="DGXCoder: Autonomous local agentic coding engine powered by Goose, Cline, Aider, Continue, OpenHands & NVIDIA GB10"
        )
        agent_choices = ["goose", "cline", "aider", "continue", "openhands"]

        parser.add_argument("--config", default=None, help="Path to custom DGXCoder config file (.yaml or .json)")
        parser.add_argument("--sandbox", choices=["none", "apptainer", "podman", "docker"], default=None, help="Rootless container sandbox isolation engine")
        parser.add_argument("--agent", choices=agent_choices, default=None, help="Select primary AI agent runner (default: goose)")
        parser.add_argument("--hf-token", default=None, help="HuggingFace API access token (or set via HF_TOKEN env var)")
        subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

        # Command: dgxcoder init
        init_parser = subparsers.add_parser("init", help="Initialize .dgxcoder project workspace and agent configs")
        init_parser.add_argument("--model", default=None, help="Model name served on vLLM GB10 endpoint")
        init_parser.add_argument("--vllm-host", default=None, help="vLLM server URL")
        init_parser.add_argument("--draft-model", default=None, help="Speculative decoding draft model name")
        init_parser.add_argument("--sandbox", choices=["none", "apptainer", "podman", "docker"], default=None, help="Rootless container sandbox engine")
        init_parser.add_argument("--agent", choices=agent_choices, default=None, help="Primary AI agent runner")
        init_parser.add_argument("--hf-token", default=None, help="HuggingFace API access token")

        # Command: dgxcoder chat
        chat_parser = subparsers.add_parser("chat", help="Launch interactive pair programming session")
        chat_parser.add_argument("--model", default=None, help="Model name served on vLLM GB10 endpoint")
        chat_parser.add_argument("--draft-model", default=None, help="Speculative decoding draft model name")
        chat_parser.add_argument("--sandbox", choices=["none", "apptainer", "podman", "docker"], default=None, help="Rootless container sandbox engine")
        chat_parser.add_argument("--agent", choices=agent_choices, default=None, help="Primary AI agent runner")
        chat_parser.add_argument("--hf-token", default=None, help="HuggingFace API access token")
        chat_parser.add_argument("--debug", action="store_true", help="Enable verbose debug output")
        chat_parser.add_argument("--cave", action="store_true", default=False, help="Enable Cave Mode strict prompt (no explanations, only commands/code)")

        # Command: dgxcoder run
        run_parser = subparsers.add_parser("run", help="Run an autonomous coding task")
        run_parser.add_argument("prompt", type=str, help="Task prompt for AI agent")
        run_parser.add_argument("--model", default=None, help="Model name served on vLLM GB10 endpoint")
        run_parser.add_argument("--draft-model", default=None, help="Speculative decoding draft model name")
        run_parser.add_argument("--sandbox", choices=["none", "apptainer", "podman", "docker"], default=None, help="Rootless container sandbox engine")
        run_parser.add_argument("--agent", choices=agent_choices, default=None, help="Primary AI agent runner")
        run_parser.add_argument("--hf-token", default=None, help="HuggingFace API access token")
        run_parser.add_argument("--debug", action="store_true", help="Enable verbose debug output")
        run_parser.add_argument("--cave", action="store_true", default=False, help="Enable Cave Mode strict prompt (no explanations, only commands/code)")

        # Command: dgxcoder status
        subparsers.add_parser("status", help="Display local GB10 hardware & agent connection status")

        # Command: dgxcoder start_server
        start_server_parser = subparsers.add_parser("start_server", help="Launch local vLLM server optimized for GB10 unified memory")
        start_server_parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model name to serve (default: {DEFAULT_MODEL}; examples: {DEFAULT_MODEL}, llama-3.3-70b)")
        start_server_parser.add_argument("--port", type=int, default=8000, help="Port to expose OpenAI API endpoint")
        start_server_parser.add_argument("--quantization", default=None, help="Quantization method (int8, fp8, awq)")
        start_server_parser.add_argument("--draft-model", default=None, help="Speculative decoding draft model (e.g. qwen2.5-coder-1.5b)")
        start_server_parser.add_argument("--num-speculative-tokens", type=int, default=None, help="Number of speculative tokens to propose")
        start_server_parser.add_argument("--hf-token", default=None, help="HuggingFace API access token")
        start_server_parser.add_argument("--num-scheduler-steps", type=int, default=None, help="Multi-step scheduling iterations per step")
        start_server_parser.add_argument("--attention-backend", default=None, help="Attention backend (FLASHINFER, FLASH_ATTN, auto)")
        start_server_parser.add_argument("--kv-cache-dtype", default=None, help="KV cache precision (auto, fp8)")
        start_server_parser.add_argument("--api-key", default=None, help="OpenAI-compatible API key (optional; not set by default)")
        start_server_parser.add_argument("--enable-auto-tool-choice", action="store_true", default=True, help="Enable automatic tool choice for function calling (default: enabled)")
        start_server_parser.add_argument("--tool-call-parser", default=None, help="Tool call parser name (default: from the model's registry recipe, e.g. hermes, qwen3_xml)")
        start_server_parser.add_argument("--reasoning-parser", default=None, help="Reasoning-channel parser for models that emit separate thinking output (e.g. qwen3)")
        start_server_parser.add_argument("--moe-backend", default=None, help="Mixture-of-experts kernel backend (e.g. marlin, flashinfer_cutedsl_sm12x); GB10 requires an SM121-safe choice")
        start_server_parser.add_argument("--max-num-batched-tokens", type=int, default=None, help="Max tokens per batch for chunked prefill (GB10 optimization)")
        start_server_parser.add_argument("--guided-decoding-backend", default=None, help="Structured-outputs backend for deterministic JSON/tool calls (auto, xgrammar, guidance). Unset leaves vLLM's own default")
        start_server_parser.add_argument("--tensorize", action=argparse.BooleanOptionalAction, default=None, help="Save and load model in tensorize (.tensors) format (default: per-model, True unless the model opts out)")
        start_server_parser.add_argument("--docker-image", default=DEFAULT_VLLM_IMAGE, help="Docker image for vLLM (default: nvcr.io/nvidia/vllm:26.07-py3)")

        # Command: dgxcoder index
        index_parser = subparsers.add_parser("index", help="Index codebase AST & TF-IDF vector context")
        index_parser.add_argument("--dir", default=None, help="Directory to index")
        index_parser.add_argument("--force", action="store_true", help="Force reindexing")

        # Command: dgxcoder mcp
        subparsers.add_parser("mcp", help="Run stdio MCP server for JetBrains & VS Code extensions")

        # Command: dgxcoder download
        download_parser = subparsers.add_parser("download", help="Pre-download LLM & draft model weights into local HuggingFace cache")
        download_parser.add_argument("--model", default=None, help="Specific model to pre-download")
        download_parser.add_argument("--all", action="store_true", help="Pre-download all qualified GB10 models")
        download_parser.add_argument("--tensorize", action=argparse.BooleanOptionalAction, default=True, help="Auto-convert model to tensorize format after download (default: True)")

        # Command: dgxcoder clear-cache
        subparsers.add_parser("clear-cache", help="Clear local HuggingFace and tensorizer model caches")

        # Command: dgxcoder clear-tensorize-cache
        subparsers.add_parser("clear-tensorize-cache", help="Clear local tensorizer model cache only")

        # Command: dgxcoder endpoints
        subparsers.add_parser("endpoints", help="Print all available vLLM/OpenAI-compatible endpoints and credentials")

        # Command: dgxcoder stop_server
        stop_parser = subparsers.add_parser("stop_server", help="Stop and remove the running vLLM Docker container")
        stop_parser.add_argument("--port", type=int, default=8000, help="Port of the server to stop")

        # Command: dgxcoder web
        web_parser = subparsers.add_parser("web", help="Launch Web Canvas UI interactive pair-programming pane")
        web_parser.add_argument("--port", type=int, default=8501, help="Port for Web Canvas UI")

        return parser

    @classmethod
    def run_cli(cls) -> None:
        """Main execution entrypoint for CLI command parsing and subcommand dispatching."""
        parser = cls.build_parser()
        args = parser.parse_args()

        if not args.command:
            parser.print_help()
            sys.exit(0)

        # Handle stdio MCP server command immediately
        if args.command == "mcp":
            run_mcp_server()
            sys.exit(0)

        config_file = getattr(args, "config", None)
        vllm_host = getattr(args, "vllm_host", None)
        model = getattr(args, "model", None)
        draft_model = getattr(args, "draft_model", None)
        num_speculative_tokens = getattr(args, "num_speculative_tokens", None)
        sandbox = getattr(args, "sandbox", None)
        agent_runner = getattr(args, "agent", None)
        hf_token = getattr(args, "hf_token", None)
        num_scheduler_steps = getattr(args, "num_scheduler_steps", None)
        attention_backend = getattr(args, "attention_backend", None)
        kv_cache_dtype = getattr(args, "kv_cache_dtype", None)
        max_num_batched_tokens = getattr(args, "max_num_batched_tokens", None)
        guided_decoding_backend = getattr(args, "guided_decoding_backend", None)
        cave_mode = getattr(args, "cave", None)
        tensorize_opt = getattr(args, "tensorize", None)

        config = DGXCoderConfig(
            config_file=config_file,
            vllm_host=vllm_host,
            model=model,
            draft_model=draft_model,
            num_speculative_tokens=num_speculative_tokens,
            sandbox=sandbox,
            agent_runner=agent_runner,
            hf_token=hf_token,
            num_scheduler_steps=num_scheduler_steps,
            attention_backend=attention_backend,
            kv_cache_dtype=kv_cache_dtype,
            cave_mode=cave_mode,
            use_tensorizer=tensorize_opt
        )

        # Instantiate selected runner (Goose by default, or Cline/Aider/Continue/OpenHands)
        if config.agent_runner == "cline":
            runner = ClineRunner(config=config)
        elif config.agent_runner == "aider":
            runner = AiderRunner(config=config)
        elif config.agent_runner == "continue":
            runner = ContinueRunner(config=config)
        elif config.agent_runner == "openhands":
            runner = OpenHandsRunner(config=config)
        else:
            runner = GooseRunner(config=config)

        # Dispatch subcommand logic
        if args.command == "download":
            cls.display_header()
            auto_t = getattr(args, "tensorize", True)
            if getattr(args, "all", False):
                download_all_models(hf_token=config.hf_token, auto_tensorize=auto_t)
            else:
                target_model = args.model or config.model
                if target_model:
                    download_model(target_model, hf_token=config.hf_token, auto_tensorize=auto_t)
                    if config.draft_model:
                        download_model(config.draft_model, hf_token=config.hf_token, auto_tensorize=auto_t)
                else:
                    print("⚠️  No model specified. Use --model <model_name> or initialize config with 'dgxcoder init --model <model_name>'")
            sys.exit(0)

        elif args.command == "clear-cache":
            cls.display_header()
            clear_model_cache()
            sys.exit(0)

        elif args.command == "clear-tensorize-cache":
            cls.display_header()
            clear_tensorizer_cache()
            sys.exit(0)

        elif args.command == "init":
            cls.display_header()
            auto_t = config.use_tensorizer
            if config.model:
                download_model(config.model, hf_token=config.hf_token, auto_tensorize=auto_t)
                if config.draft_model:
                    download_model(config.draft_model, hf_token=config.hf_token, auto_tensorize=auto_t)
            saved_config_path = config.save_config()
            config.ensure_goose_config()
            ctx_engine = ContextEngine()
            summary = ctx_engine.index_workspace(force_reindex=True)

            console.print("[bold green]✅ DGXCoder workspace initialized successfully![/bold green]")
            console.print(f"   [cyan]DGXCoder Config:[/cyan] {saved_config_path}")
            console.print(f"   [cyan]Active Agent:[/cyan]    {config.agent_runner.upper()} (Default: GOOSE)")
            console.print(f"   [cyan]Goose Config:[/cyan]    {config.config_path}")
            console.print(f"   [cyan]Target Model:[/cyan]    {config.model}")
            if config.draft_model:
                console.print(f"   [cyan]Draft Model:[/cyan]     {config.draft_model} ({config.num_speculative_tokens} tokens)")
            console.print(f"   [cyan]Sandbox Isolation:[/cyan]{config.sandbox.upper() if config.sandbox != 'none' else 'Disabled (Native Host)'}")
            console.print(f"   [cyan]HF Auth Token:[/cyan]    {'Configured' if config.hf_token else 'Not Configured'}")
            console.print(f"   [cyan]Target Host:[/cyan]     {config.vllm_host}")
            console.print(f"   [cyan]Indexed Files:[/cyan]   {summary['total_indexed_files']} ({summary['total_ast_symbols']} AST symbols)")

        elif args.command == "chat":
            sys.exit(runner.run_session(debug=args.debug))

        elif args.command == "run":
            sys.exit(runner.run_session(prompt=args.prompt, debug=args.debug))

        elif args.command == "status":
            cls.handle_status()

        elif args.command == "endpoints":
            cls.display_header()
            from rich.table import Table
            table = Table(title="Available Endpoints (OpenAI-compatible)", show_header=True, header_style="bold magenta")
            table.add_column("Endpoint", style="cyan")
            table.add_column("Method", style="green")
            table.add_column("Description")
            table.add_row("/v1/models", "GET", "List available models")
            table.add_row("/v1/chat/completions", "POST", "Chat completions (OpenAI format)")
            table.add_row("/v1/completions", "POST", "Legacy text completions")
            table.add_row("/v1/embeddings", "POST", "Text embeddings (if supported)")
            table.add_row("/health", "GET", "vLLM server health (if enabled)")
            console.print(table)

            # Detect a local LAN IP for remote access (vLLM binds to 0.0.0.0)
            import socket
            local_ip = "localhost"
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                pass

            cred_table = Table(title="OpenAPI-compatible Credentials", show_header=False)
            cred_table.add_column("Key", style="bold cyan")
            cred_table.add_column("Value", style="white")
            cred_table.add_row("Base URL (localhost)", "http://localhost:8000/v1")
            cred_table.add_row("Base URL (LAN IP)", f"http://{local_ip}:8000/v1")
            cred_table.add_row("API Key", "Optional (use --api-key on serve; otherwise not required)")
            cred_table.add_row("Auth Header", "Authorization: Bearer <key> (when enabled)")
            console.print(cred_table)
            print("\n💡 Use with any OpenAI-compatible client by pointing base_url to the endpoint above.")

        elif args.command == "start_server":
            cls.display_header()
            vllm_mgr = VLLMServerManager(host=f"http://localhost:{args.port}")
            
            # Start monitoring thread before server launch
            monitor = create_model_loading_monitor(vllm_mgr)
            monitor.start()
            
            print(f"📊 Model Loading Monitor: Tracking initialization progress for '{args.model}'...\n")
            
            try:
                # Start vLLM server in background to allow progress monitoring
                vllm_mgr.start_server(
                    model=args.model,
                    port=args.port,
                    quantization=args.quantization,
                    draft_model=args.draft_model,
                    num_speculative_tokens=args.num_speculative_tokens,
                    hf_token=config.hf_token,
                    enable_prefix_caching=config.enable_prefix_caching,
                    enable_chunked_prefill=config.enable_chunked_prefill,
                    num_scheduler_steps=config.num_scheduler_steps,
                    attention_backend=config.attention_backend,
                    kv_cache_dtype=config.kv_cache_dtype,
                    api_key=args.api_key,
                    enable_auto_tool_choice=args.enable_auto_tool_choice,
                    tool_call_parser=args.tool_call_parser,
                    reasoning_parser=getattr(args, "reasoning_parser", None),
                    moe_backend=getattr(args, "moe_backend", None),
                    max_num_batched_tokens=args.max_num_batched_tokens,
                    guided_decoding_backend=args.guided_decoding_backend,
                    use_tensorizer=getattr(args, "tensorize", None),
                    background=True,
                    docker_image=getattr(args, "docker_image", DEFAULT_VLLM_IMAGE)
                )
                
                # Print progress while server is initializing
                import time
                last_status = None
                while not monitor.server_ready and vllm_mgr.process and vllm_mgr.process.poll() is None:
                    current_status = monitor.get_status()
                    # Only print if status changed to avoid spam
                    if current_status['stages_reached'] != last_status:
                        monitor.print_progress()
                        last_status = current_status['stages_reached']
                    time.sleep(1)
                
                # Server is ready
                if monitor.server_ready:
                    print(f"\n✅ Server Ready! API running at {vllm_mgr.host}")
                    print(f"   Model: {args.model}")
                    print(f"   Loaded in: {monitor.get_status()['elapsed_seconds']:.1f} seconds\n")
                
                # Do not block: exit after health check passes (server keeps running)
                    
            except KeyboardInterrupt:
                print("\n⏹️  Shutting down server...")
                if vllm_mgr.process:
                    vllm_mgr.process.terminate()
                    try:
                        vllm_mgr.process.wait(timeout=5)
                    except:
                        vllm_mgr.process.kill()
            finally:
                monitor.stop()

        elif args.command == "stop_server":
            cls.display_header()
            vllm_mgr = VLLMServerManager(host=f"http://localhost:{args.port}")
            vllm_mgr.stop_server(port=args.port)

        elif args.command == "index":
            cls.display_header()
            target_dir = args.dir or os.getcwd()
            ctx_engine = ContextEngine(workspace_root=target_dir)
            with console.status("[bold green]Indexing workspace AST & vectors...[/bold green]"):
                summary = ctx_engine.index_workspace(force_reindex=args.force)
            console.print(f"[bold green]✅ Workspace Indexed![/bold green]")
            console.print(f"   Files Indexed: {summary['total_indexed_files']}")
            console.print(f"   AST Symbols:   {summary['total_ast_symbols']}")

        elif args.command == "web":
            cls.display_header()
            start_web_canvas_server(port=args.port, daemon=False)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                console.print("\n[yellow]Stopping Web Canvas UI...[/yellow]")

def main() -> None:
    """Standalone CLI main function."""
    DGXCoderCLIController.run_cli()
