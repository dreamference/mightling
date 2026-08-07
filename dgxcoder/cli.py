import argparse
import os
import sys
import time
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax

from dgxcoder.config import DGXCoderConfig
from dgxcoder.runner import GooseRunner
from dgxcoder.hardware import detect_gb10_hardware, MODEL_MATRIX, check_model_compatibility
from dgxcoder.vllm_server import VLLMServerManager
from dgxcoder.context_engine import ContextEngine
from dgxcoder.mcp_server import main as run_mcp_server
from dgxcoder.web_canvas import start_web_canvas_server

console = Console()

def display_header():
    console.print(Panel.fit(
        "[bold green]⚡ DGXCoder[/bold green] - Autonomous Local Agentic Coding Engine\n"
        "[dim]Exclusive Target Hardware: NVIDIA GB10 (Blackwell Architecture | 128 GB Unified Memory)[/dim]",
        border_style="green"
    ))

def handle_status():
    display_header()
    hw = detect_gb10_hardware()
    config = DGXCoderConfig()
    vllm_mgr = VLLMServerManager(host=config.vllm_host)
    vllm_status = vllm_mgr.get_server_status()
    runner = GooseRunner(config=config)
    ctx_engine = ContextEngine()
    ctx_summary = ctx_engine.get_summary() if ctx_engine.load_index() else None

    # Hardware Panel
    hw_table = Table(show_header=False, box=None)
    hw_table.add_column("Property", style="bold cyan")
    hw_table.add_column("Value", style="white")

    is_gb10_str = "[bold green]✅ Qualified (GB10 128GB Unified Memory Target)[/bold green]" if hw["is_gb10"] else "[yellow]⚠️ System running non-GB10 host[/yellow]"
    hw_table.add_row("System Target", is_gb10_str)
    hw_table.add_row("GPU Hardware", hw["gpu_name"])
    hw_table.add_row("Driver Version", hw["driver_version"])
    hw_table.add_row("Total Unified Memory", f"{hw['total_unified_memory_gb']} GB")
    hw_table.add_row("Used Memory", f"{hw['used_memory_gb']} GB")
    hw_table.add_row("Available Memory", f"{hw['available_memory_gb']} GB")
    hw_table.add_row("Architecture", hw["arch"])

    console.print(Panel(hw_table, title="[bold]🖥️ NVIDIA GB10 Hardware Status[/bold]", border_style="blue"))

    # vLLM & Agent Status
    agent_table = Table(show_header=False, box=None)
    agent_table.add_column("Property", style="bold cyan")
    agent_table.add_column("Value", style="white")

    vllm_str = f"[green]Online ({config.vllm_host})[/green]" if vllm_status["healthy"] else f"[red]Offline ({config.vllm_host})[/red]"
    goose_str = "[green]Installed[/green]" if runner.is_goose_installed() else "[yellow]Not Found (Dry-Run Mode Available)[/yellow]"
    
    agent_table.add_row("Local vLLM Server", vllm_str)
    agent_table.add_row("Active Served Models", ", ".join(vllm_status["models"]) if vllm_status["models"] else "None (vLLM idle)")
    agent_table.add_row("Configured Model", config.model)
    if config.draft_model:
        agent_table.add_row("Speculative Draft Model", f"{config.draft_model} ({config.num_speculative_tokens} tokens)")
    sandbox_str = f"[bold green]{config.sandbox.upper()} (Rootless Isolated)[/bold green]" if config.sandbox != "none" else "[yellow]Disabled (Native Host)[/yellow]"
    agent_table.add_row("Container Sandbox", sandbox_str)
    agent_table.add_row("Goose CLI Runtime", goose_str)
    agent_table.add_row("DGXCoder Config Path", str(config.config_file_path))
    agent_table.add_row("Goose Config Path", str(config.config_path))

    console.print(Panel(agent_table, title="[bold]🤖 vLLM & Goose Agent Status[/bold]", border_style="magenta"))

    # Context Indexing Status
    if ctx_summary:
        ctx_table = Table(show_header=False, box=None)
        ctx_table.add_column("Property", style="bold cyan")
        ctx_table.add_column("Value", style="white")
        ctx_table.add_row("Indexed Workspace Files", str(ctx_summary["total_indexed_files"]))
        ctx_table.add_row("Total AST Symbols", str(ctx_summary["total_ast_symbols"]))
        ctx_table.add_row("Index Path", ctx_summary["index_file"])
        console.print(Panel(ctx_table, title="[bold]📚 Context Engine Index Status[/bold]", border_style="green"))

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dgxcoder",
        description="DGXCoder: Autonomous local agentic coding engine powered by Goose & NVIDIA GB10"
    )
    parser.add_argument("--config", default=None, help="Path to custom DGXCoder config file (.yaml or .json)")
    parser.add_argument("--sandbox", choices=["none", "apptainer", "podman", "docker"], default=None, help="Rootless container sandbox isolation engine")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize .dgxcoder project workspace and Goose MCP config")
    init_parser.add_argument("--model", default=None, help="Model name served on vLLM GB10 endpoint")
    init_parser.add_argument("--vllm-host", default=None, help="vLLM server URL")
    init_parser.add_argument("--draft-model", default=None, help="Speculative decoding draft model name")
    init_parser.add_argument("--sandbox", choices=["none", "apptainer", "podman", "docker"], default=None, help="Rootless container sandbox engine")

    # chat
    chat_parser = subparsers.add_parser("chat", help="Launch interactive Goose pair programming session")
    chat_parser.add_argument("--model", default=None, help="Model name served on vLLM GB10 endpoint")
    chat_parser.add_argument("--draft-model", default=None, help="Speculative decoding draft model name")
    chat_parser.add_argument("--sandbox", choices=["none", "apptainer", "podman", "docker"], default=None, help="Rootless container sandbox engine")
    chat_parser.add_argument("--debug", action="store_true", help="Enable verbose Goose debug output")

    # run
    run_parser = subparsers.add_parser("run", help="Run an autonomous coding task with Goose")
    run_parser.add_argument("prompt", type=str, help="Task prompt for Goose agent")
    run_parser.add_argument("--model", default=None, help="Model name served on vLLM GB10 endpoint")
    run_parser.add_argument("--draft-model", default=None, help="Speculative decoding draft model name")
    run_parser.add_argument("--sandbox", choices=["none", "apptainer", "podman", "docker"], default=None, help="Rootless container sandbox engine")
    run_parser.add_argument("--debug", action="store_true", help="Enable verbose Goose debug output")

    # status
    subparsers.add_parser("status", help="Display local GB10 hardware & Goose connection status")

    # serve / vllm
    serve_parser = subparsers.add_parser("serve", help="Launch local vLLM server optimized for GB10 unified memory")
    serve_parser.add_argument("--model", default=None, help="Model name to serve")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to expose OpenAI API endpoint")
    serve_parser.add_argument("--quantization", default=None, help="Quantization method (int8, fp8, awq)")
    serve_parser.add_argument("--draft-model", default=None, help="Speculative decoding draft model (e.g. qwen2.5-coder-1.5b)")
    serve_parser.add_argument("--num-speculative-tokens", type=int, default=None, help="Number of speculative tokens to propose")

    # index
    index_parser = subparsers.add_parser("index", help="Index codebase AST & TF-IDF vector context")
    index_parser.add_argument("--dir", default=None, help="Directory to index")
    index_parser.add_argument("--force", action="store_true", help="Force reindexing")

    # mcp
    subparsers.add_parser("mcp", help="Run stdio MCP server for JetBrains & VS Code extensions")

    # web / canvas
    web_parser = subparsers.add_parser("web", help="Launch Web Canvas UI interactive pair-programming pane")
    web_parser.add_argument("--port", type=int, default=8501, help="Port for Web Canvas UI")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "mcp":
        run_mcp_server()
        sys.exit(0)

    config_file = getattr(args, "config", None)
    vllm_host = getattr(args, "vllm_host", None)
    model = getattr(args, "model", None)
    draft_model = getattr(args, "draft_model", None)
    num_speculative_tokens = getattr(args, "num_speculative_tokens", None)
    sandbox = getattr(args, "sandbox", None)

    config = DGXCoderConfig(
        config_file=config_file,
        vllm_host=vllm_host,
        model=model,
        draft_model=draft_model,
        num_speculative_tokens=num_speculative_tokens,
        sandbox=sandbox
    )
    runner = GooseRunner(config=config)

    if args.command == "init":
        display_header()
        saved_config_path = config.save_config()
        config.ensure_goose_config()
        ctx_engine = ContextEngine()
        summary = ctx_engine.index_workspace(force_reindex=True)

        console.print("[bold green]✅ DGXCoder & Goose workspace initialized successfully![/bold green]")
        console.print(f"   [cyan]DGXCoder Config:[/cyan] {saved_config_path}")
        console.print(f"   [cyan]Goose Config:[/cyan]    {config.config_path}")
        console.print(f"   [cyan]Target Model:[/cyan]    {config.model}")
        if config.draft_model:
            console.print(f"   [cyan]Draft Model:[/cyan]     {config.draft_model} ({config.num_speculative_tokens} tokens)")
        console.print(f"   [cyan]Sandbox Isolation:[/cyan]{config.sandbox.upper() if config.sandbox != 'none' else 'Disabled (Native Host)'}")
        console.print(f"   [cyan]Target Host:[/cyan]     {config.vllm_host}")
        console.print(f"   [cyan]Indexed Files:[/cyan]   {summary['total_indexed_files']} ({summary['total_ast_symbols']} AST symbols)")

    elif args.command == "chat":
        sys.exit(runner.run_session(debug=args.debug))

    elif args.command == "run":
        sys.exit(runner.run_session(prompt=args.prompt, debug=args.debug))

    elif args.command == "status":
        handle_status()

    elif args.command == "serve":
        display_header()
        vllm_mgr = VLLMServerManager(host=f"http://localhost:{args.port}")
        vllm_mgr.start_server(
            model=args.model,
            port=args.port,
            quantization=args.quantization,
            draft_model=args.draft_model,
            num_speculative_tokens=args.num_speculative_tokens,
            background=False
        )

    elif args.command == "index":
        display_header()
        target_dir = args.dir or os.getcwd()
        ctx_engine = ContextEngine(workspace_root=target_dir)
        with console.status("[bold green]Indexing workspace AST & vectors...[/bold green]"):
            summary = ctx_engine.index_workspace(force_reindex=args.force)
        console.print(f"[bold green]✅ Workspace Indexed![/bold green]")
        console.print(f"   Files Indexed: {summary['total_indexed_files']}")
        console.print(f"   AST Symbols:   {summary['total_ast_symbols']}")

    elif args.command == "web":
        display_header()
        start_web_canvas_server(port=args.port, daemon=False)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping Web Canvas UI...[/yellow]")

if __name__ == "__main__":
    main()
