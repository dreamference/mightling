"""
Mightling admin command line interface (`ling-admin`) controller.

This module provides the DreamferenceCLIController class which parses command line arguments
for subcommands (`init`, `run`, `status`, `server start`, `index`, `mcp`, `model download`, `web`),
renders Rich terminal user interfaces, and coordinates backend component execution.
"""

import argparse
import os
import sys
import time
from typing import Final, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from dreamference.config import DreamferenceConfig
from dreamference.config.dreamference_config import DEFAULT_MODEL, DEFAULT_DIFFUSION_MODEL
from dreamference.runner import (
    ClineRunner, ClineInstaller,
    ContinueRunner, ContinueInstaller,
    OpenHandsRunner, OpenHandsInstaller,
    CodexRunner, CodexInstaller
)
from dreamference.hardware import detect_gb10_hardware, download_model, download_all_models, clear_model_cache, clear_tensorizer_cache, model_key_for_served_id
from dreamference.vllm_server import VLLMServerManager, DiffusionServerManager, DEFAULT_VLLM_IMAGE, DEFAULT_DIFFUSION_PORT
from dreamference.vllm_server.model_loading_monitor import create_model_loading_monitor
from dreamference.mcp_server import main as run_mcp_server
from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

# Global Rich console instance for styled terminal outputs
console: Final[Console] = Console()

# Where `ling-admin benchmark_server` stages vLLM's sonnet corpus. The text itself is embedded in
# sonnet_dataset.py rather than read out of the image or fetched, because images disagree about
# where they keep it and this project is meant to work without a network.
SONNET_HOST_PATH: Final[str] = "/tmp/dreamference-sonnet.txt"
SONNET_CONTAINER_PATH: Final[str] = "/tmp/sonnet.txt"

class DreamferenceCLIController:
    """
    Controller class for ling-admin operations, Rich status panels, and subcommand routing.
    """

    @classmethod
    def display_header(cls) -> None:
        """Renders the Rich header panel: Mightling by Dreamference, and the GB10 target architecture."""
        console.print(Panel.fit(
            "[bold green]⚡ Mightling[/bold green] [dim]by Dreamference[/dim] - Autonomous Local Agentic Coding Engine\n"
            "[dim]Exclusive Target Hardware: NVIDIA GB10 (Blackwell Architecture | 128 GB Unified Memory)[/dim]",
            border_style="green"
        ))

    @classmethod
    def _provision_sonnet_dataset(cls, container: str) -> "str | None":
        """
        Makes vLLM's sonnet benchmark dataset available inside a running server container.

        Written from the literal in `sonnet_dataset` to a host file and copied in, rather than
        located inside the image. Images disagree about where they keep it — the project image
        under /opt/vllm, the DFlash image under /vllm-workspace — and a benchmark that has to know
        each image's layout breaks every time a model pins a new one. The container is already
        running with fixed mounts, so `docker cp` is what gets a host file across the boundary.

        Rewritten on every run rather than cached: the file is 22 KB, and a stale or truncated
        /tmp copy would silently change what is being measured.

        Args:
            container (str): Name of the running vLLM container to copy the dataset into.

        Returns:
            str | None: Path to the dataset *inside* the container, or None if it could not be
                provisioned, in which case the reason has already been printed.
        """
        import subprocess

        from dreamference.cli.sonnet_dataset import SONNET_TEXT

        host_path = SONNET_HOST_PATH
        try:
            with open(host_path, "w", encoding="utf-8") as handle:
                handle.write(SONNET_TEXT)
        except OSError as e:
            print(f"❌ Could not stage the sonnet dataset at {host_path}: {e}")
            return None

        copy = subprocess.run(
            ["docker", "cp", host_path, f"{container}:{SONNET_CONTAINER_PATH}"],
            capture_output=True, text=True,
        )
        if copy.returncode != 0:
            print(
                f"❌ Could not copy the dataset into '{container}': "
                f"{copy.stderr.strip() or copy.stdout.strip()}"
            )
            return None
        return SONNET_CONTAINER_PATH

    @classmethod
    def _render_deep_inspection(cls, out_console, config, vllm_host: str, api_base: str, model_name: str) -> None:
        """
        Renders the deep-inspection sections behind `--deep`.

        Kept out of the default path because every probe here costs time: the workload profile
        sends live requests, and the rest shells out to read the container's whole log.

        Args:
            out_console: Rich console to render into.
            config: Active DreamferenceConfig, used for the model alias and host.
            vllm_host (str): Base URL of the running server.
            api_base (str): Chat-completions endpoint.
            model_name (str): Model ID as the server advertises it.
        """
        import json
        import subprocess
        from urllib.parse import urlparse

        from dreamference.cli.model_deep_inspector import ModelDeepInspector
        from dreamference.hardware import get_speculative_draft_repo

        port = urlparse(api_base).port or 8000
        container = f"dreamference-vllm-{port}"

        running_cmd: "list[str]" = []
        try:
            insp = subprocess.run(
                ["docker", "inspect", container], capture_output=True, text=True, timeout=15
            )
            if insp.returncode == 0:
                data = json.loads(insp.stdout)
                if data:
                    running_cmd = data[0].get("Config", {}).get("Cmd", []) or []
        except (subprocess.SubprocessError, OSError, ValueError):
            pass

        def flag(name: str) -> Optional[str]:
            if name in running_cmd:
                i = running_cmd.index(name)
                if i + 1 < len(running_cmd):
                    return running_cmd[i + 1]
            return None

        def render(title: str, rows: "dict[str, str]") -> None:
            if not rows:
                return
            out_console.print("")
            out_console.print(f"[bold yellow]{title}[/bold yellow]")
            width = max(len(k) for k in rows)
            for key, value in rows.items():
                out_console.print(f"   [cyan]{key + ':':<{width + 1}}[/cyan] {value}")

        render(
            "🧬 Quantization Map",
            ModelDeepInspector.quantization_map(
                config.model, get_speculative_draft_repo(config.model)
            ),
        )
        render(
            "🧠 KV Geometry",
            ModelDeepInspector.kv_geometry(config.model, container, flag("--kv-cache-dtype") or "auto"),
        )
        render(
            "🎲 Sampling & Template Provenance",
            ModelDeepInspector.sampling_provenance(config.model, container, running_cmd),
        )
        render(
            "⚙️  Compile & Graph Coverage",
            ModelDeepInspector.graph_coverage(container, flag("--max-num-seqs")),
        )

        out_console.print("")
        out_console.print("[bold yellow]📊 Acceptance by Workload[/bold yellow]")
        out_console.print("   [dim]sending probe requests per class...[/dim]")
        profile = ModelDeepInspector.profile_acceptance_by_workload(vllm_host, api_base, model_name)
        if not profile:
            out_console.print("   [yellow]No speculative counters — is speculation enabled?[/yellow]")
            return

        current_n = None
        spec_raw = flag("--speculative-config")
        if spec_raw:
            try:
                current_n = json.loads(spec_raw).get("num_speculative_tokens")
            except ValueError:
                pass

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Workload")
        table.add_column("Acceptance", justify="right")
        table.add_column("Tokens/step", justify="right")
        table.add_column("Slots >=50%", justify="right")
        table.add_column("Suggested n", justify="right")
        for row in profile:
            table.add_row(
                row["category"],
                f"{row['acceptance']:.1f}%",
                f"{row['tau']:.2f}",
                str(sum(1 for s in row["slots"] if s >= 0.5)),
                str(row["suggested_n"]),
            )
        out_console.print(table)

        suggestions = {row["category"]: row["suggested_n"] for row in profile}
        if current_n is not None and suggestions:
            spread = f"{min(suggestions.values())}-{max(suggestions.values())}"
            out_console.print(
                f"   [dim]Running n={current_n}. Per-workload optima span {spread}, so one value "
                f"is a compromise — tune it toward the traffic this box actually serves.[/dim]"
            )

    @classmethod
    def _run_correctness_canary(cls, session, api_base: str, model_name: str, headers: dict) -> "dict[str, str]":
        """
        Checks that the served model still produces valid output, not merely fast output.

        Throughput without a correctness check is the wrong number to trust on this hardware. On
        SM121 a mismatched kernel does not raise — it emits garbage, which is why the codebase
        already carries an NVFP4 canary. That canary only fires for aliases containing "nvfp4", so
        the INT4 default has had no coverage at all, and quantized weights plus a nondeterministic
        Marlin reduction are exactly the combination worth a standing check.

        Three probes, each failing for a different reason: arithmetic catches numerically broken
        kernels, a compile check catches syntactically plausible mush, and a real tool call catches
        a parser mismatch — the last being the failure that would break agent use while ordinary
        chat still looked fine.

        Args:
            session: Requests session already configured for the endpoint.
            api_base (str): Chat-completions URL.
            model_name (str): Model ID as the server advertises it.
            headers (dict): Headers to send.

        Returns:
            dict[str, str]: One row per probe.
        """
        rows: "dict[str, str]" = {}

        def ask(prompt: str, tools=None, max_tokens: int = 256) -> Optional[dict]:
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0,
                # These probe correctness, not reasoning: a model that thinks by default spent the
                # arithmetic probe's 16 tokens thinking and the canary read that as a wrong answer.
                "chat_template_kwargs": {"enable_thinking": False},
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            try:
                response = session.post(api_base, json=payload, headers=headers, timeout=90)
                if response.status_code != 200:
                    return None
                return response.json()["choices"][0]["message"]
            except Exception:
                return None

        arithmetic = ask("What is 17 * 23? Reply with only the number.", max_tokens=16)
        if arithmetic is None:
            rows["Canary: arithmetic"] = "[red]unreachable[/red]"
        elif "391" in (arithmetic.get("content") or ""):
            rows["Canary: arithmetic"] = "[green]pass[/green] (17*23=391)"
        else:
            rows["Canary: arithmetic"] = (
                f"[red]FAIL[/red] — expected 391, got {(arithmetic.get('content') or '')[:40]!r}"
            )

        code = ask("Write a Python function add(a, b) returning their sum. Code only, no prose.")
        content = (code or {}).get("content") or ""
        snippet = content
        if "```" in snippet:
            # Strip one fenced block, which is how this model formats code.
            parts = snippet.split("```")
            if len(parts) >= 2:
                snippet = parts[1]
                if snippet.startswith("python"):
                    snippet = snippet[len("python"):]
        if code is None:
            rows["Canary: code"] = "[red]unreachable[/red]"
        else:
            try:
                compile(snippet, "<canary>", "exec")
                rows["Canary: code"] = "[green]pass[/green] (output parses as Python)"
            except SyntaxError as e:
                rows["Canary: code"] = f"[red]FAIL[/red] — output does not parse ({e.msg})"

        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }]
        called = ask("What is the weather in Paris? Use the tool.", tools=tools, max_tokens=128)
        if called is None:
            rows["Canary: tool call"] = "[red]unreachable[/red]"
        elif called.get("tool_calls"):
            call = called["tool_calls"][0]["function"]
            rows["Canary: tool call"] = (
                f"[green]pass[/green] ({call.get('name')} {str(call.get('arguments'))[:40]})"
            )
        else:
            # The parser is the usual suspect: qwen3_xml reads the XML form, hermes the JSON one,
            # and the wrong choice yields prose describing a call it never makes.
            rows["Canary: tool call"] = (
                "[red]FAIL[/red] — no tool_calls returned; check --tool-call-parser"
            )

        return rows

    @classmethod
    def _read_kv_pool_facts(cls, container: str, max_model_len: Optional[str]) -> "dict[str, str]":
        """
        Reports the KV pool the engine actually built and what it implies for concurrency.

        vLLM prints both numbers once during startup and never again, and they are the two that
        answer "can I raise the context length or serve more streams?" — which on this hardware is
        the question behind every recipe change, because the arena is sized against a host that
        freezes rather than OOMs when it is wrong.

        Args:
            container (str): Running container name, whose startup log carries the figures.
            max_model_len (Optional[str]): Configured context length, used to sanity-check the pool.

        Returns:
            dict[str, str]: Display-ready rows; empty if the log no longer holds the startup lines.
        """
        import re
        import subprocess

        try:
            logs = subprocess.run(
                ["docker", "logs", container], capture_output=True, text=True, timeout=30
            )
        except (subprocess.SubprocessError, OSError):
            return {}

        blob = (logs.stdout or "") + (logs.stderr or "")
        rows: "dict[str, str]" = {}

        pool = re.search(r"GPU KV cache size:\s*([\d,]+)\s*tokens", blob)
        if pool:
            tokens = int(pool.group(1).replace(",", ""))
            detail = f"{tokens:,} tokens"
            if max_model_len and max_model_len.isdigit():
                detail += f"  ({tokens / int(max_model_len):.2f} x max-model-len)"
            rows["KV Pool"] = detail

        concurrency = re.search(
            r"Maximum concurrency for ([\d,]+) tokens per request:\s*([\d.]+)x", blob
        )
        if concurrency:
            rows["Max Concurrency"] = (
                f"{concurrency.group(2)}x at {concurrency.group(1)} tokens/request"
            )

        return rows

    @classmethod
    def _detect_recipe_drift(cls, model: str, port: int, running_cmd: "list[str]") -> "dict[str, str]":
        """
        Compares the flags the server is running against the ones its recipe would generate now.

        The registry is meant to be the source of truth for launch flags, but several layers sit
        between it and the process — CLI arguments, config defaults, and the launcher's own
        fallbacks — and each of them can quietly win. That is not hypothetical: a config-level
        `kv_cache_dtype` default overrode the recipe's value and the engine refused to start, and
        the mismatch was only visible by reading a failed container's log. Comparing the two here
        turns that class of problem into one line.

        Args:
            model (str): Model whose recipe defines the expected flags.
            port (int): Port the running server uses, so the rebuilt command matches.
            running_cmd (list[str]): The container's actual argv.

        Returns:
            dict[str, str]: One row per differing flag, or a single row confirming they agree.
        """
        def parse(argv: "list[str]") -> "dict[str, str]":
            flags: "dict[str, str]" = {}
            i = 0
            while i < len(argv):
                token = argv[i]
                if token.startswith("--"):
                    if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                        flags[token] = argv[i + 1]
                        i += 2
                        continue
                    flags[token] = "(set)"
                i += 1
            return flags

        try:
            expected_full = VLLMServerManager().build_launch_command(model=model, port=port)
        except Exception:
            return {}

        # Keep only the server's own arguments: from the first flag after the engine's entry
        # point, `serve <model>` for vLLM and `python3 -m sglang.launch_server` for SGLang.
        def server_args(argv: "list[str]") -> "list[str]":
            for entry in ("serve", "sglang.launch_server"):
                if entry in argv:
                    argv = argv[argv.index(entry) + 1:]
                    break
            flags_at = next((i for i, token in enumerate(argv) if token.startswith("--")), len(argv))
            return argv[flags_at:]

        # The expected command also carries docker's own flags before the image; an entry point
        # must be present to know where the server's begin.
        if not any(entry in expected_full for entry in ("serve", "sglang.launch_server")):
            return {}
        expected_argv = server_args(expected_full)
        running_argv = server_args(running_cmd)

        expected = parse(expected_argv)
        actual = parse(running_argv)

        rows: "dict[str, str]" = {}
        for key in sorted(set(expected) | set(actual)):
            want = expected.get(key)
            have = actual.get(key)
            if want != have:
                rows[f"  drift {key}"] = f"running={have or 'absent'}  recipe={want or 'absent'}"

        if not rows:
            return {"Recipe Match": "Running flags match the registry recipe"}
        return {"Recipe Match": f"[yellow]{len(rows)} flag(s) differ from the recipe[/yellow]", **rows}

    @classmethod
    def _collect_engine_facts(cls, port: int, vllm_host: str, model_alias: str) -> "dict[str, str]":
        """
        Reports what the running vLLM engine actually chose, rather than which features are absent.

        Read from the container's own launch command and the server's /metrics, not inferred from
        the image name or from environment variables nothing sets. The previous version of this
        panel asked a fixed checklist — FlashAttention-3, FlashInfer, TensorRT-LLM — and answered
        "No" to all of it while the server was running FlashAttention with DFlash speculation. Every
        one of those answers was either unanswerable (FA3 was probed with `pip show flash-attn`,
        but vLLM vendors its own `vllm_flash_attn`), or a deliberate choice reported as a missing
        feature (FlashInfer is "No" because this model picked a different backend), or a flag vLLM
        does not accept (`--backend tensorrt-llm`).

        Args:
            port (int): Port the server is listening on, used to find its container.
            vllm_host (str): Base URL of the server, used to read /metrics.
            model_alias (str): Configured model, used to rebuild the recipe for drift detection.

        Returns:
            dict[str, str]: Ordered label -> value pairs, already formatted for display. Anything
                that could not be determined is reported as such rather than as "No".
        """
        import json
        import re
        import subprocess

        facts: "dict[str, str]" = {}
        container = f"dreamference-vllm-{port}"

        cmd: "list[str]" = []
        image = ""
        try:
            insp = subprocess.run(
                ["docker", "inspect", container], capture_output=True, text=True, timeout=15
            )
            if insp.returncode == 0:
                data = json.loads(insp.stdout)
                if data:
                    cmd = data[0].get("Config", {}).get("Cmd", []) or []
                    image = data[0].get("Config", {}).get("Image", "") or ""
        except (subprocess.SubprocessError, OSError, ValueError):
            pass

        def flag(name: str) -> Optional[str]:
            """Value following `name` on the container's actual command line, if present."""
            if name in cmd:
                i = cmd.index(name)
                if i + 1 < len(cmd):
                    return cmd[i + 1]
            return None

        if image:
            version = VLLMServerManager().detect_image_vllm_version(image)
            version_str = f" (vLLM {version[0]}.{version[1]})" if version else ""
            facts["Image"] = f"{image}{version_str}"
        else:
            facts["Image"] = "Unknown (container not running)"

        facts["Attention"] = flag("--attention-backend") or "vLLM default"
        facts["MoE Backend"] = flag("--moe-backend") or "vLLM default"
        facts["Quantization"] = flag("--quantization") or "from checkpoint config"
        facts["KV Cache Dtype"] = flag("--kv-cache-dtype") or "auto"
        facts["Max Model Len"] = flag("--max-model-len") or "Unknown"

        if "--no-enable-prefix-caching" in cmd:
            facts["Prefix Caching"] = "Off (recipe)"
        elif "--enable-prefix-caching" in cmd:
            facts["Prefix Caching"] = "On"
        else:
            facts["Prefix Caching"] = "vLLM default"

        spec_raw = flag("--speculative-config")
        if spec_raw:
            try:
                spec = json.loads(spec_raw)
                drafter = spec.get("model") or "self (in-checkpoint head)"
                facts["Speculative"] = (
                    f"{spec.get('method', '?')} n={spec.get('num_speculative_tokens', '?')} "
                    f"via {drafter}"
                )
            except ValueError:
                facts["Speculative"] = spec_raw
        else:
            facts["Speculative"] = "Disabled"

        # Acceptance is deliberately NOT reported here. vLLM's counters are cumulative from server
        # start, so a single figure blends every workload the server has ever seen — the same box
        # read 86% across code requests and 26% once a prose benchmark was included, and neither
        # number describes anything you can act on. It is measured per prompt instead, as a delta
        # around each request in the sample table below, where the workload is known.
        facts.update(cls._read_kv_pool_facts(container, facts.get("Max Model Len")))

        load_format = flag("--load-format")
        facts["Tensorizer"] = "Active" if load_format == "tensorizer" else "Not used"

        # A cold compile is 8-12 minutes, so whether the cache survived the last restart is worth
        # knowing before deciding a slow startup is a problem.
        try:
            from dreamference.vllm_server.vllm_server_manager import VLLM_CACHE_HOME

            compiled = VLLM_CACHE_HOME / "torch_compile_cache"
            if compiled.is_dir() and any(compiled.iterdir()):
                facts["Compile Cache"] = f"Warm ({sum(1 for _ in compiled.iterdir())} graph(s))"
            else:
                facts["Compile Cache"] = "Cold — next start recompiles (8-12 min)"
        except OSError:
            facts["Compile Cache"] = "Unknown"

        if cmd:
            # Against the recipe of the model actually running, not the configured one: `server
            # start --model` may serve another, and comparing an SGLang server with a vLLM recipe
            # reported every flag as drift.
            served_key = model_alias
            try:
                import requests

                served = requests.get(f"{vllm_host}/v1/models", timeout=5).json()["data"][0]["id"]
                served_key = model_key_for_served_id(served, preferred=model_alias) or model_alias
            except Exception:
                pass
            facts.update(cls._detect_recipe_drift(served_key, port, cmd))

        return facts

    @classmethod
    def _run_google(cls, command: str | None) -> None:
        """Runs `ling-admin google start|stop|status` and exits.

        Args:
            command (str | None): The subcommand.
        """
        actions = {"start": cls._google_start, "stop": cls._google_stop, "status": cls._google_status}
        action = actions.get(command or "")
        if action is None:
            print("usage: ling-admin google {start,stop,status}")
            sys.exit(2)
        sys.exit(action())

    @classmethod
    def _google_start(cls) -> int:
        """Starts the Google service (specs/DREAMFERENCE_MIGHTLING_APPS.md §5.1).

        Returns:
            int: The exit code.
        """
        from dreamference.chat.google_service import GOOGLE_HOST_PORT, GoogleService

        if not GoogleService.start():
            print(f"❌ The Google service did not start. {GoogleService.problem}")
            return 1
        print(f"✅ The Google service is running on http://127.0.0.1:{GOOGLE_HOST_PORT}")
        print("💡 Connect accounts with /apps in ling, or in the web UI's Settings.")
        return 0

    @classmethod
    def _google_stop(cls) -> int:
        """Removes the Google service container; connected accounts stay stored.

        Returns:
            int: The exit code.
        """
        from dreamference.chat.google_service import GoogleService

        return 0 if GoogleService.stop() else 1

    @classmethod
    def _google_status(cls) -> int:
        """Prints the container's state and each account's granted apps.

        Returns:
            int: The exit code.
        """
        from dreamference.chat.gmail_client import GmailClient
        from dreamference.chat.google_service import GoogleService

        print(f"Container: {GoogleService.state() or 'absent'}")
        answer = GmailClient.status()
        for account in answer.get("accounts") or []:
            scopes = ", ".join(scope.rstrip("/").rsplit("/", 1)[-1] for scope in account.get("scopes", []))
            print(f"  {account.get('email')}: {scopes}")
        if answer.get("error"):
            print(f"⚠️  {answer['error']}")
        return 0

    @classmethod
    def _refuse_during_night_run(cls, what: str) -> None:
        """
        Stops `ling-admin <what>` while a Night Shift run or a SWE-bench run holds the runner
        lock: a build, an index run or a model load beside its sessions is what put the model
        server at risk before (specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md §6.2).

        Args:
            what: The command, for the message.
        """
        from dreamference.night_shift import NightShiftQueue
        holder = NightShiftQueue.runner_holder()
        if holder:
            where = "swe-bench status" if "SWE-bench" in holder else "night status"
            print(f"❌ {holder[0].upper()}{holder[1:]} is in progress, so `ling-admin {what}` waits: "
                  f"see `ling-admin {where}`.")
            sys.exit(1)

    @classmethod
    def handle_status(cls) -> None:
        """
        Executes `ling-admin status` command, displaying hardware metrics, vLLM health, Codex/Cline/Continue/OpenHands config,
        and context engine index telemetry in formatted Rich panels.
        """
        cls.display_header()
        hw = detect_gb10_hardware()
        config = DreamferenceConfig()
        vllm_mgr = VLLMServerManager(host=config.vllm_host)
        vllm_status = vllm_mgr.get_server_status()
        # Imported here, not at module scope: the context engine pulls in torch, which
        # costs ~2.1s and 0.7 GB. `ling-admin mcp` never needs it, and Codex starts one of
        # those per session on a box that is already tight on memory.
        from dreamference.context_engine import ContextEngine
        ctx_engine = ContextEngine()
        ctx_summary = ctx_engine.get_summary() if ctx_engine.load_index() else None

        # 1. NVIDIA GB10 Hardware Telemetry Panel
        hw_table = Table(show_header=False, box=None)
        hw_table.add_column("Property", style="bold cyan")
        hw_table.add_column("Value", style="white")

        is_gb10_str = "[bold green]✅ Qualified (GB10 128GB Unified Memory Target)[/bold green]" if hw["is_gb10"] else "[yellow]⚠️ System running non-GB10 host[/yellow]"
        hw_table.add_row("System Target", is_gb10_str)
        if hw.get("machine"):
            hw_table.add_row("Machine", str(hw["machine"]))
        if hw.get("os_name"):
            hw_table.add_row("Operating System", str(hw["os_name"]))
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

        if vllm_status["healthy"]:
            vllm_str = f"[green]Online ({config.vllm_host})[/green]"
        elif vllm_status.get("loading_status"):
            vllm_str = f"[yellow]Starting ({vllm_status['loading_status']})[/yellow]"
        else:
            vllm_str = f"[red]Offline ({config.vllm_host})[/red]"
        cline_str = "[green]Extension Ready[/green]" if ClineInstaller.is_cline_extension_installed() else "[yellow]Extension Available[/yellow]"
        continue_str = "[green]Extension Ready[/green]" if ContinueInstaller.is_continue_extension_installed() else "[yellow]Extension Available[/yellow]"
        openhands_str = "[green]Docker Image Ready[/green]" if OpenHandsInstaller.is_image_downloaded() else "[yellow]Docker Available[/yellow]"
        codex_str = "[green]CLI Ready[/green]" if CodexInstaller.is_installed() else "[yellow]CLI Available[/yellow]"

        agent_table.add_row("Local vLLM Server", vllm_str)
        agent_table.add_row("Active Served Models", ", ".join(vllm_status["models"]) if vllm_status["models"] else "None (vLLM idle)")
        agent_table.add_row("Active Agent Runner", f"[bold green]{config.agent_runner.upper()}[/bold green] (Default: CODEX)")
        agent_table.add_row("Configured Model", config.model)
        from dreamference.hardware import is_model_tensorized
        tensorize_str = "[bold green]Saved & Active (.tensors)[/bold green]" if is_model_tensorized(config.model) else "[yellow]Standard Weights (HF Cache)[/yellow]"
        agent_table.add_row("Tensorize Format Status", tensorize_str)
        if config.draft_model:
            agent_table.add_row("Speculative Draft Model", f"{config.draft_model} ({config.num_speculative_tokens} tokens)")
        hf_token_str = f"[green]Configured ({config.hf_token[:4]}...{config.hf_token[-4:]})[/green]" if config.hf_token else "[yellow]Not Configured (Anonymous Hub Access)[/yellow]"
        agent_table.add_row("HuggingFace Auth Token", hf_token_str)
        agent_table.add_row("Prefix Caching / Chunked", "[bold green]Enabled (Blackwell GB10 Optimized)[/bold green]")
        agent_table.add_row("Multi-Step Scheduling", f"{config.num_scheduler_steps} steps/iter")
        agent_table.add_row("KV Cache Dtype", config.kv_cache_dtype or "from model recipe")
        agent_table.add_row("Tool Call Parser", config.resolve_tool_call_parser())
        # Surfaced because on GB10 (SM121) the wrong MoE kernel does not error — it produces
        # corrupt output — so the selected backend is worth being able to read off `status`.
        from dreamference.hardware import get_model_launch_overrides
        recipe = get_model_launch_overrides(config.model)
        agent_table.add_row(
            "MoE Kernel Backend",
            recipe.get("moe_backend") or "[yellow]vLLM default (no model recipe)[/yellow]"
        )
        agent_table.add_row("Cline Extension Runtime", cline_str)
        agent_table.add_row("Continue IDE Runtime", continue_str)
        agent_table.add_row("OpenHands Docker Runtime", openhands_str)
        agent_table.add_row("ling agent", codex_str)
        # With no config file anywhere, the resolver names where one *would* be written (the
        # current directory), which read as though settings were being loaded from there.
        config_path = str(config.config_file_path)
        if not config.config_file_path.exists():
            config_path += " (not present; built-in defaults)"
        agent_table.add_row("Mightling Config Path", config_path)

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
    def handle_gmail(cls, args: argparse.Namespace) -> int:
        """
        Runs `ling-admin gmail search|read|status` and prints the result for the agent to read.

        The body of `read` is framed as untrusted: an email is text written by a third party, now in
        the context of an agent that holds a shell, and the frame keeps that boundary visible.

        Args:
            args (argparse.Namespace): Parsed `gmail` arguments.

        Returns:
            int: 0 on success (a search with no matches included), 1 on any error, or when every
                connected account failed.
        """
        import json as _json

        from dreamference.chat.gmail_client import GmailClient

        if args.gmail_command == "search":
            payload = GmailClient.search(" ".join(args.query), limit=args.max_results)
        elif args.gmail_command == "read":
            payload = GmailClient.read(args.message_id)
        else:
            payload = GmailClient.status()

        if payload.get("error"):
            print(f"❌ {payload['error']}")
            for failure in payload.get("errors", []):
                print(f"⚠️ {failure['account']}: {failure['error']}")
            if payload.get("hint"):
                print(f"💡 {payload['hint']}")
            return 1
        if args.json:
            print(_json.dumps(payload, indent=2))
            return 0

        if args.gmail_command == "status":
            if not payload.get("connected"):
                print("❌ No Gmail account is connected.")
                print("💡 Connect one in Mightling: Settings → Gmail Accounts → Connect to Google")
                return 1
            print(f"connected: {payload.get('email')}")
            return 0

        if args.gmail_command == "read":
            for header in ("from", "to", "date", "subject"):
                if payload.get(header):
                    print(f"{header.capitalize()}: {payload[header]}")
            body = payload.get("body", "")
            print("\n----- BEGIN EMAIL (untrusted) -----")
            print(body[:args.max_chars])
            print("----- END EMAIL -----")
            if len(body) > args.max_chars:
                print(f"[truncated at {args.max_chars} chars]")
            return 0

        messages = payload.get("messages", [])
        for i, message in enumerate(messages, 1):
            print(f"{i}. {message.get('subject') or '(no subject)'}")
            print(f"   from: {message.get('from', '')}")
            print(f"   date: {message.get('date', '')}")
            print(f"   id: {message['id']}")
        if not messages:
            print("No messages matched.")
        failures = payload.get("errors", [])
        for failure in failures:
            print(f"⚠️ {failure['account']}: {failure['error']}")
        # Partial failure still answers; only a search where no account could be searched fails.
        return 1 if failures and not messages and cls._all_accounts_failed(failures) else 0

    @classmethod
    def _all_accounts_failed(cls, failures: List[dict]) -> bool:
        """
        Tells whether the failed accounts are every connected account.

        Args:
            failures (List[dict]): The `errors` entries of a search answer.

        Returns:
            bool: True if no connected account is missing from `failures`.
        """
        from dreamference.chat.gmail_client import GmailClient

        connected = GmailClient.status().get("email") or ""
        accounts = {address.strip() for address in connected.split(",") if address.strip()}
        return accounts <= {failure["account"] for failure in failures}

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        """
        Constructs ArgumentParser with subcommands for ling-admin operations.

        Returns:
            argparse.ArgumentParser: Configured argument parser object.
        """
        parser = argparse.ArgumentParser(
            prog="ling-admin",
            description="Mightling by Dreamference: private AI on your NVIDIA GB10. Runs the model server, the Mightling agent, the web chat and the desktop app; also drives Cline, Continue and OpenHands"
        )
        agent_choices = ["codex", "cline", "continue", "openhands"]

        parser.add_argument("--config", default=None, help="Path to custom Mightling config file (.toml, .yaml or .json)")
        parser.add_argument("--agent", choices=agent_choices, default=None, help="Select primary AI agent runner (default: codex)")
        parser.add_argument("--hf-token", default=None, help="HuggingFace API access token (or set via HF_TOKEN env var)")
        subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

        # Command: ling-admin init
        init_parser = subparsers.add_parser("init", help="Initialize .dreamference project workspace and agent configs")
        init_parser.add_argument("--model", default=None, help="Model name served on vLLM GB10 endpoint")
        init_parser.add_argument("--vllm-host", default=None, help="vLLM server URL")
        init_parser.add_argument("--draft-model", default=None, help="Speculative decoding draft model name")
        init_parser.add_argument("--agent", choices=agent_choices, default=None, help="Primary AI agent runner")
        init_parser.add_argument("--hf-token", default=None, help="HuggingFace API access token")

        # Command: ling-admin run
        run_parser = subparsers.add_parser("run", help="Run an autonomous coding task")
        run_parser.add_argument("prompt", type=str, help="Task prompt for AI agent")
        run_parser.add_argument("--model", default=None, help="Model name served on vLLM GB10 endpoint")
        run_parser.add_argument("--draft-model", default=None, help="Speculative decoding draft model name")
        run_parser.add_argument("--agent", choices=agent_choices, default=None, help="Primary AI agent runner")
        run_parser.add_argument("--hf-token", default=None, help="HuggingFace API access token")
        run_parser.add_argument("--debug", action="store_true", help="Enable verbose debug output")
        run_parser.add_argument("--cave", action="store_true", default=False, help="Enable Cave Mode strict prompt (no explanations, only commands/code)")

        # Command: ling-admin status
        subparsers.add_parser("status", help="Display local GB10 hardware & agent connection status")

        # Command: ling-admin index
        index_parser = subparsers.add_parser("index", help="Index codebase AST & TF-IDF vector context")
        index_parser.add_argument("--dir", default=None, help="Directory to index")
        index_parser.add_argument("--force", action="store_true", help="Force reindexing")

        # Command: ling-admin mcp
        subparsers.add_parser("mcp", help="Run stdio MCP server for JetBrains & VS Code extensions")

        # Command: ling-admin model
        model_parser = subparsers.add_parser("model", help="Model operations")
        model_subparsers = model_parser.add_subparsers(dest="model_command", help="Model commands")
        


        # Command: ling-admin main-model
        main_model_parser = subparsers.add_parser("main-model", help="Main model operations")
        main_model_subparsers = main_model_parser.add_subparsers(dest="main_model_command", help="Main model commands")
        main_model_set_parser = main_model_subparsers.add_parser("set", help="Set the main model")
        main_model_set_parser.add_argument("model_name", type=str, help="Name of the model to set as main")
        main_model_set_parser.add_argument(
            "--no-onyx", action="store_true",
            help="Skip re-registering the model with a running Onyx deployment",
        )

        main_model_inspect_parser = main_model_subparsers.add_parser("inspect", help="Inspect the currently running main model by running sample prompts")
        main_model_inspect_parser.add_argument(
            "--deep", action="store_true",
            help="Also report quantization map, KV geometry, sampling provenance, graph coverage "
                 "and per-workload speculative acceptance (sends extra requests; slower)",
        )

        # Command: ling-admin diffusion-model. Absent while diffusion is switched off
        # (DIFFUSION_ENABLED), so it is neither listed nor accepted.
        diffusion_on = ModelMatrixRegistry.diffusion_enabled()
        diffusion_model_parser = None
        if diffusion_on:
            diffusion_model_parser = subparsers.add_parser("diffusion-model", help="Diffusion model operations")
            diffusion_model_subparsers = diffusion_model_parser.add_subparsers(dest="diffusion_model_command", help="Diffusion model commands")
            diffusion_model_set_parser = diffusion_model_subparsers.add_parser("set", help="Set the diffusion model served beside the main one")
            diffusion_model_set_parser.add_argument("model_name", type=str, help="Name of the diffusion model to set")

        # Command: ling-admin model download
        # Command: ling-admin model list
        model_subparsers.add_parser("list", help="List available model names and HuggingFace repos")
        
        # Command: ling-admin model download
        download_parser = model_subparsers.add_parser("download", help="Pre-download LLM & draft model weights into local HuggingFace cache")
        download_parser.add_argument("--model", default=None, help="Specific model to pre-download")
        download_parser.add_argument("--all", action="store_true", help="Pre-download all qualified GB10 models")
        download_parser.add_argument("--tensorize", action=argparse.BooleanOptionalAction, default=False, help="Auto-convert model to tensorize format after download (default: False)")
        # Command: ling-admin clear-tensorize-cache
        subparsers.add_parser("clear-tensorize-cache", help="Clear local tensorizer model cache only")

        # Command: ling-admin clear
        clear_parser = subparsers.add_parser("clear", help="Clear operations")
        clear_subparsers = clear_parser.add_subparsers(dest="clear_command", help="Clear commands")
        
        # Command: ling-admin clear model-cache
        clear_subparsers.add_parser("model-cache", help="Clear local HuggingFace and tensorizer model caches")
        
        # Command: ling-admin clear tensorize-cache
        clear_subparsers.add_parser("tensorize-cache", help="Clear local tensorizer model cache only")

        # Command: ling-admin endpoints
        subparsers.add_parser("endpoints", help="Print the model server's endpoints (the standard /v1 API) and credentials")

        # Command: ling-admin server
        server_parser = subparsers.add_parser("server", help="Manage the vLLM server container (start, stop, remove)")
        server_subparsers = server_parser.add_subparsers(dest="server_command", help="Server operations")



        # Command: ling-admin server start
        start_server_parser = server_subparsers.add_parser("start", help="Launch local vLLM server optimized for GB10 unified memory")
        # default=None, resolved to the *configured* model in the handler. A concrete default
        # here silently outranked `ling-admin main-model set`: the config said one model and
        # `server start` launched another -- found live, when a recipe switch started the old
        # checkpoint on the old image and only the /v1/models listing told the truth.
        start_server_parser.add_argument("--model", default=None, help=f"Model name to serve (default: the configured main model; examples: {DEFAULT_MODEL}, llama-3.3-70b)")
        start_server_parser.add_argument("--port", type=int, default=8000, help="Port for the model server's /v1 API")
        start_server_parser.add_argument("--quantization", default=None, help="Quantization method (int8, fp8, awq)")
        start_server_parser.add_argument("--draft-model", default=None, help="Speculative decoding draft model (e.g. qwen2.5-coder-1.5b)")
        start_server_parser.add_argument("--num-speculative-tokens", type=int, default=None, help="Number of speculative tokens to propose")
        start_server_parser.add_argument("--hf-token", default=None, help="HuggingFace API access token")
        start_server_parser.add_argument("--num-scheduler-steps", type=int, default=None, help="Multi-step scheduling iterations per step")
        start_server_parser.add_argument("--attention-backend", default=None, help="Attention backend (FLASHINFER, FLASH_ATTN, auto)")
        start_server_parser.add_argument("--kv-cache-dtype", default=None, help="KV cache precision (auto, fp8)")
        start_server_parser.add_argument("--api-key", default=None, help="API key for the model server's /v1 API (optional; not set by default)")
        start_server_parser.add_argument("--enable-auto-tool-choice", action="store_true", default=True, help="Enable automatic tool choice for function calling (default: enabled)")
        start_server_parser.add_argument("--tool-call-parser", default=None, help="Tool call parser name (default: from the model's registry recipe, e.g. hermes, qwen3_xml)")
        start_server_parser.add_argument("--reasoning-parser", default=None, help="Reasoning-channel parser for models that emit separate thinking output (e.g. qwen3)")
        start_server_parser.add_argument("--moe-backend", default=None, help="Mixture-of-experts kernel backend (e.g. marlin, flashinfer-b12x); GB10 requires an SM121-safe choice")
        start_server_parser.add_argument("--max-num-batched-tokens", type=int, default=None, help="Max tokens per batch for chunked prefill (GB10 optimization)")
        start_server_parser.add_argument("--guided-decoding-backend", default=None, help="Structured-outputs backend for deterministic JSON/tool calls (auto, xgrammar, guidance). Unset leaves vLLM's own default")
        start_server_parser.add_argument("--tensorize", action=argparse.BooleanOptionalAction, default=None, help="Save and load model in tensorize (.tensors) format (default: False)")
        start_server_parser.add_argument("--docker-image", default=None, help="Docker image for vLLM. Unset uses the model's own docker_image recipe entry, then the pinned default")
        # The diffusion flags stay accepted while diffusion is off, so an old script does not
        # break, but their help is suppressed and they change nothing.
        def diffusion_help(text: str) -> str:
            return text if diffusion_on else argparse.SUPPRESS
        start_server_parser.add_argument("--diffusion-model", default=None, help=diffusion_help(f"Diffusion model to serve beside the main one (default: the configured diffusion model, {DEFAULT_DIFFUSION_MODEL})"))
        start_server_parser.add_argument("--diffusion-port", type=int, default=DEFAULT_DIFFUSION_PORT, help=diffusion_help("Port for the diffusion sidecar's /v1 API"))
        start_server_parser.add_argument("--no-diffusion", action="store_true", help=diffusion_help("Skip starting the diffusion sidecar"))
        # Command: ling-admin server stop
        stop_parser = server_subparsers.add_parser("stop", help="Stop the running vLLM and diffusion Docker containers" if diffusion_on else "Stop the running model server")
        stop_parser.add_argument("--port", type=int, default=8000, help="Port of the server to stop")
        stop_parser.add_argument("--diffusion-port", type=int, default=DEFAULT_DIFFUSION_PORT, help=diffusion_help("Port of the diffusion sidecar to stop"))

        # Command: ling-admin server remove
        remove_parser = server_subparsers.add_parser("remove", help="Remove the vLLM and diffusion Docker containers" if diffusion_on else "Remove the model server's container")
        remove_parser.add_argument("--port", type=int, default=8000, help="Port of the server to remove")
        remove_parser.add_argument("--diffusion-port", type=int, default=DEFAULT_DIFFUSION_PORT, help=diffusion_help("Port of the diffusion sidecar to remove"))

        # Command: ling-admin server logs
        server_logs_parser = server_subparsers.add_parser("logs", help="Tail the vLLM Docker container logs")
        server_logs_parser.add_argument("--port", type=int, default=8000, help="Port of the server to tail logs for")

        # Command: ling-admin logs
        logs_parser = subparsers.add_parser("logs", help="Tail the vLLM Docker container logs")
        logs_parser.add_argument("target", nargs="?", choices=["server", "mcp"],
                                 help="server: vLLM container logs. mcp: the agent's MCP server lifecycle, "
                                      "read from ~/.mightling/logs_2.sqlite, or $CODEX_HOME (the TUI logs there, not to a file)")
        logs_parser.add_argument("--port", type=int, default=8000, help="Port of the server to tail logs for")

        # Command: ling-admin codex
        codex_parser = subparsers.add_parser("codex", help="Build ling and manage its app-server daemon")
        codex_subparsers = codex_parser.add_subparsers(dest="codex_command", help="Build, run and test ling")
        codex_build_parser = codex_subparsers.add_parser(
            "build", help="Build ling from the pinned upstream source in codex/ and the patches in codex-patches/"
        )
        codex_build_parser.add_argument("--force", action="store_true", help="Rebuild even if the installed build is current")
        codex_build_parser.add_argument("--no-audit", action="store_true", help="Do not trace the new build's network use afterwards (`ling-admin audit egress`)")
        codex_subparsers.add_parser("start", help="Start ling's app-server daemon in the background")
        codex_subparsers.add_parser("stop", help="Stop ling's app-server daemon")
        codex_test_parser = codex_subparsers.add_parser(
            "test", help="Run the upstream test suite on ling's patched tree, except the tests in codex-tests/mightling-skips.toml"
        )
        codex_test_parser.add_argument("-E", "--filter", default=None, help="nextest filterset to narrow the run to")
        codex_test_parser.add_argument("--test-threads", type=int, default=8, help="Tests run at once (default 8)")
        codex_test_parser.add_argument("--jobs", type=int, default=6, help="Parallel compile jobs (default 6)")
        codex_test_parser.add_argument("--memory-max", default="24G", help="Memory the run may use (default 24G)")
        codex_test_parser.add_argument(
            "--accept-snapshots", action="store_true",
            help="Rewrite the selected TUI snapshots and keep those that differ from upstream's by the name alone",
        )

        # Command: ling-admin code -- the code index's tools. `ling-code` itself is a Rust
        # binary built beside `ling` (`codex build`); what it runs to index (codebase-memory, the
        # scip CLI, scip-python) is pinned by checksum and installed here, the one step that uses
        # the network (specs/DREAMFERENCE_MIGHTLING_CODE_INDEX.md §5).
        code_parser = subparsers.add_parser("code", help="Install the pinned tools of ling-code's code index")
        code_subparsers = code_parser.add_subparsers(dest="code_command", help="Code index commands")
        code_subparsers.add_parser(
            "setup", help="Install codebase-memory-mcp, the scip CLI and the language indexers, each checked against its pin"
        )

        # Command: ling-admin host (the settings a model load is refused without)
        host_parser = subparsers.add_parser("host", help="Check or apply the host settings a model load and ling's sandbox need (swap, sysctls, earlyoom, sysstat, bubblewrap)")
        host_subparsers = host_parser.add_subparsers(dest="host_command")
        host_subparsers.add_parser("check", help="Show what `server start` would refuse over, changing nothing")
        host_subparsers.add_parser("setup", help="Apply the settings; each command is printed first and sudo asks for your password")

        # Command: ling-admin night (Night Shift: run queued tasks overnight)
        night_parser = subparsers.add_parser("night", help="Run the Night Shift queue overnight (tasks are queued with /night add)")
        night_subparsers = night_parser.add_subparsers(dest="night_command")
        night_enable_parser = night_subparsers.add_parser("enable", help="Install the systemd user timer that runs the queue every night")
        night_enable_parser.add_argument("--window", default=None, help="HH:MM-HH:MM (default: [night] window, 01:00-07:00)")
        night_subparsers.add_parser("disable", help="Remove the Night Shift timer")
        night_subparsers.add_parser("status", help="Show the timer, the window and the queue of every repository")
        night_run_parser = night_subparsers.add_parser("run", help="Work through the queue now, until the window ends")
        night_run_parser.add_argument("--until", default=None, help="HH:MM to stop at (default: the end of the window)")
        night_run_parser.add_argument("--minutes", type=float, default=None, help="Run for this many minutes instead")
        night_run_parser.add_argument("--idle-minutes", type=float, default=None, help="Minutes the model must have been idle first (default 10)")
        night_run_parser.add_argument("--ignore-open-sessions", action="store_true", help="Do not wait for open ling sessions to close (for testing; their requests still pause the run)")

        # Command: ling-admin swe-bench (run ling over SWE-bench instances and grade the patches)
        from dreamference.swe_bench.swe_bench_command import SweBenchCommand
        SweBenchCommand.add_parser(subparsers)

        # Command: ling-admin audit (what does ling do on the network?)
        audit_parser = subparsers.add_parser("audit", help="Check what a Mightling session does on the network")
        audit_subparsers = audit_parser.add_subparsers(dest="audit_command")
        audit_egress_parser = audit_subparsers.add_parser(
            "egress", help="Trace one real ling session and list every network destination and process, with a verdict")
        audit_egress_mode = audit_egress_parser.add_mutually_exclusive_group()
        audit_egress_mode.add_argument("--tui", action="store_true", help="Trace the full-screen interface on a pseudo-terminal instead of `ling exec` (needs pexpect and pyte)")
        audit_egress_mode.add_argument("--app", action="store_true", help="Trace the desktop app (ling-app) with both windows hidden, on the display DISPLAY names")
        audit_egress_mode.add_argument("--web", action="store_true", help="Trace the web server, `ling web serve`, answering one Ask thread instead of `ling exec`")
        audit_egress_parser.add_argument("--prompt", default=None, help="Prompt for the traced session (default: a one-word reply)")
        audit_egress_parser.add_argument("--json", action="store_true", help="Also write the full result to $CODEX_HOME/audit/<timestamp>.json")

        # Command: ling-admin node (offer this machine to the local network as a Mightling node)
        node_parser = subparsers.add_parser("node", help="Advertise this machine on the local network so clients find it with no address typed")
        node_subparsers = node_parser.add_subparsers(dest="node_command")
        node_enable_parser = node_subparsers.add_parser("enable", help="Advertise the node and publish the web UI and web search to the local network")
        node_enable_parser.add_argument("--no-web", action="store_true", help="Keep the web UI on this machine; clients get ling and web search only")
        node_subparsers.add_parser("disable", help="Stop advertising and put the web UI and web search back on this machine only")
        node_status_parser = node_subparsers.add_parser("status", help="Show the node id, what is advertised and published, and what a browse of the network returns; with a name, that paired node's status")
        node_status_parser.add_argument("name", nargs="?", default=None, help="A paired node: show its `ling-admin status` instead")
        node_subparsers.add_parser("id", help="Print this node's id, writing it first if this machine has none yet")
        node_subparsers.add_parser("list", help="List every node on the local network: its model, its load, and whether it is paired")
        node_add_parser = node_subparsers.add_parser("add", help="Pair with another node over SSH, once, so it can be managed from here")
        node_add_parser.add_argument("name", help="The node's name, address or id, as `node list` shows it")
        node_add_parser.add_argument("--user", default=None, help="The account on that node (default: this user's name)")
        node_add_parser.add_argument("--ssh-port", type=int, default=22, help="That node's SSH port (default 22)")
        node_remove_parser = node_subparsers.add_parser("remove", help="Unpair a node: remove the key on both sides")
        node_remove_parser.add_argument("name", help="The paired node")
        node_set_parser = node_subparsers.add_parser("set", help="Assign a model to a paired node and start it there")
        node_set_parser.add_argument("name", help="The paired node")
        node_set_parser.add_argument("--model", required=True, help="A key of that node's model matrix")
        node_start_parser = node_subparsers.add_parser("start", help="Start a paired node's model server")
        node_start_parser.add_argument("name", help="The paired node")
        node_stop_parser = node_subparsers.add_parser("stop", help="Stop a paired node's model server")
        node_stop_parser.add_argument("name", help="The paired node")
        node_run_parser = node_subparsers.add_parser("run", help="Run a command on a paired node, in this repository at HEAD; its changes come back as a branch")
        node_run_parser.add_argument("name", help="The paired node")
        node_run_parser.add_argument("--memory", default=None, help="The job's memory cap (default 8G; the node sets the ceiling)")
        node_run_parser.add_argument("--time", default=None, help="The job's time limit (default 90m; the node sets the ceiling)")
        node_run_parser.add_argument("--test", default=None, help="A command that decides pass or fail, run after the job's own")
        node_run_parser.add_argument("--gpu", action="store_true", help="Ask for the GPU (nodes refuse this for now)")
        node_run_parser.add_argument("--setup", default=None, help="The command that builds the job's environment, run once per lock-file content and kept on the node")
        node_run_parser.add_argument("--out", default=None, help="A folder the job writes that comes back as files (to ~/.mightling/jobs/received/<id>), never as a commit")
        node_run_parser.add_argument("--bind", action="append", default=None, help="A path on the node to bind read-only; the node's [node] bindable decides which are allowed (repeatable)")
        node_run_parser.add_argument("job_command", nargs=argparse.REMAINDER, help="-- then the command and its arguments")
        node_jobs_parser = node_subparsers.add_parser("jobs", help="List the jobs on a paired node, or on every paired node")
        node_jobs_parser.add_argument("name", nargs="?", default=None, help="A paired node (default: all)")
        node_logs_parser = node_subparsers.add_parser("logs", help="Show a job's output again, or continue it")
        node_logs_parser.add_argument("job", help="The job id")
        node_cancel_parser = node_subparsers.add_parser("cancel", help="Stop a running job")
        node_cancel_parser.add_argument("job", help="The job id")
        node_fetch_parser = node_subparsers.add_parser("fetch", help="Bring a job's result branch into the repository it was sent from, and its --out files")
        node_fetch_parser.add_argument("job", help="The job id")
        # The commands the other side of a pairing runs; not for typing.
        node_sync_parser = node_subparsers.add_parser("sync-model", help="Copy a model's files from this machine's cache to a paired node, so it need not download them")
        node_sync_parser.add_argument("name", help="The paired node")
        node_sync_parser.add_argument("model", help="A key of the model matrix")
        node_sync_parser.add_argument("--address", default=None, help="Reach the node at this address instead, such as its QSFP link's")
        node_provision_parser = node_subparsers.add_parser("provision", help="Set up new GB10s from this one: install Mightling, the root half, the model, pairing, start (FLEET spec); re-run on paired nodes, it is the fleet update")
        node_provision_parser.add_argument("hosts", nargs="*", help="Host names, addresses or paired nodes; none lists unprovisioned GB10s on the network")
        node_provision_parser.add_argument("--all", action="store_true", help="Every paired node: the fleet update")
        node_provision_parser.add_argument("--user", default=None, help="The account on the machines (default: this user's name)")
        node_provision_parser.add_argument("--model", default=None, help="The model each node is assigned (default: this machine's configured model)")
        node_provision_parser.add_argument("--from", dest="source", default="this", help="What to install: this (default) or release[=X.Y.Z]")
        node_provision_parser.add_argument("--per-host-password", action="store_true", help="With several hosts, ask each machine's password separately (default: one password for all)")
        node_provision_parser.add_argument("--mesh", action="store_true", help="Also pair every node with every other (not built yet)")
        node_provision_parser.add_argument("--web", action="store_true", help="Also install and configure the web UI there")
        node_provision_parser.add_argument("--no-start", action="store_true", help="Leave the model server stopped")
        node_provision_parser.add_argument("--restart", action="store_true", help="Restart a running model server")
        node_provision_parser.add_argument("--os-update", action="store_true", help="NVIDIA's OS and firmware update first, with a reboot")
        node_provision_parser.add_argument("--dry-run", action="store_true", help="Connect and read only, then print what each machine would change")
        node_provision_parser.add_argument("--via", default=None, help="With one host: copy and install over this address instead (a QSFP link's)")
        node_provision_parser.add_argument("--match", default=None, help="With no hosts: a name pattern for the browse instead of spark-/gx10-/zgx-")
        node_provision_parser.add_argument("--start-timeout", type=int, default=None, help="Seconds to wait for a started model server (default 1200)")
        node_subparsers.add_parser("prepare", help="(Run with sudo) the root steps of a node install, for the user who ran sudo, and nothing else")
        node_subparsers.add_parser("askpass", help=argparse.SUPPRESS)
        node_job_exec_parser = node_subparsers.add_parser("job-exec", help="(Run inside a job's systemd unit) carry out one job")
        node_job_exec_parser.add_argument("job", help="The job id")
        node_subparsers.add_parser("authorize", help="(Run by `node add` on the other node) authorise a public key, read from standard input, for node operations only")
        node_serve_parser = node_subparsers.add_parser("serve-job", help="(Run by sshd as a paired key's forced command) carry out one node operation")
        node_serve_parser.add_argument("--key", default=None, help="The connecting key's tag")

        # Command: dreamference benchmark_server
        bench_parser = subparsers.add_parser("benchmark_server", help="Run vLLM serve benchmark using Sonnet dataset")
        bench_parser.add_argument("--port", type=int, default=8000, help="Port of the server to benchmark")
        bench_parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name to benchmark")
        bench_parser.add_argument("--dataset-path", default=None, help="Path to the sonnet dataset inside the container. Unset probes the known locations for the image the server is running")
        bench_parser.add_argument("--num-prompts", type=int, default=8, help="Number of prompts to benchmark")
        bench_parser.add_argument("--max-concurrency", type=int, default=1, help="Max concurrency for requests")

        # Command: ling-admin chat
        #
        # A subcommand group rather than an `--agent onyx` runner, because Onyx is a service and
        # not a terminal session. Every entry in the --agent switch is a CLI that ling-admin
        # execs and waits on; Onyx is a set of long-lived containers with a lifecycle of its own,
        # so it mirrors `ling-admin server` instead.
        # "chat" is the command's name (it was `puffin` before the product became Mightling, and
        # `ling-admin mightling start` would read as nonsense); "onyx" remains, as it was before.
        onyx_parser = subparsers.add_parser(
            "chat", aliases=["onyx"],
            help="Manage the Mightling web chat UI (Onyx Lite) backed by local vLLM"
        )
        onyx_subparsers = onyx_parser.add_subparsers(dest="onyx_command", help="Onyx operations")

        onyx_start_parser = onyx_subparsers.add_parser(
            "start", help="Deploy (or restart) Onyx Lite and wait until it is healthy"
        )
        onyx_start_parser.add_argument(
            "--no-wait", action="store_true", help="Return as soon as containers start"
        )

        onyx_configure_parser = onyx_subparsers.add_parser(
            "configure", help="Point Onyx at the local vLLM model as its default provider"
        )
        onyx_configure_parser.add_argument(
            "--email", default=None,
            help="Your own admin e-mail (registered if no account exists); default: a generated account",
        )
        onyx_configure_parser.add_argument(
            "--password", default=None,
            help="Your own admin password, with --email; stored in ~/.config/dreamference/chat-admin.json",
        )
        onyx_configure_parser.add_argument(
            "--no-web", action="store_true",
            help="Skip registering SearXNG as Onyx's web search provider",
        )
        onyx_configure_parser.add_argument(
            "--no-brand", action="store_true",
            help="Skip rebranding the deployment as Mightling",
        )
        onyx_configure_parser.add_argument(
            "--no-voice", action="store_true",
            help="Skip the local Whisper server and the microphone button",
        )
        onyx_configure_parser.add_argument(
            "--no-gmail", action="store_true",
            help="Skip the Gmail service and its search tool",
        )
        onyx_configure_parser.add_argument(
            "--no-image-search", action="store_true",
            help="Skip the image search sidecar and its tool",
        )

        # Google sign-in is additive in Onyx 4.5: it appears beside the password form rather than
        # replacing it, so this is its own command rather than a flag on `configure`.
        onyx_google_parser = onyx_subparsers.add_parser(
            "google-auth", help="Add Google sign-in to the login page, keeping username/password"
        )
        onyx_google_parser.add_argument(
            "--client-id", required=True, help="Google OAuth client ID"
        )
        onyx_google_parser.add_argument(
            "--client-secret", required=True, help="Google OAuth client secret"
        )
        onyx_gmail_parser = onyx_subparsers.add_parser(
            "gmail", help="Connect Gmail and give the assistant a mailbox search tool"
        )
        onyx_gmail_parser.add_argument(
            "--refresh", action="store_true", help=argparse.SUPPRESS,
        )
        onyx_subparsers.add_parser("status", help="Show Onyx version, containers and health")
        onyx_subparsers.add_parser(
            "password", help="Show the web chat's admin e-mail and generated password"
        )

        # Command: ling-admin desktop {run,build,status}
        #
        # The desktop shell is a window onto the same deployment `ling-admin chat` manages, so it is a
        # sibling command rather than an `--agent` entry: nothing is exec'd and waited on here
        # except the window itself.
        desktop_parser = subparsers.add_parser(
            "desktop", help="Mightling desktop app (a native window onto the local deployment)"
        )
        desktop_subparsers = desktop_parser.add_subparsers(
            dest="desktop_command", help="Desktop app operations"
        )
        desktop_subparsers.add_parser(
            "install", help="Install the desktop app's packages (Electron, from npm) and the AppArmor profile its sandbox needs"
        )
        desktop_subparsers.add_parser("run", help="Open the Mightling desktop window")
        desktop_subparsers.add_parser("build", help="Build a distributable desktop bundle")
        desktop_subparsers.add_parser(
            "status", help="Report whether the desktop app can be built and launched"
        )
        onyx_logs_parser = onyx_subparsers.add_parser("logs", help="Show Onyx container logs")
        onyx_logs_parser.add_argument(
            "--follow", "-f", action="store_true", help="Stream new log lines"
        )
        onyx_subparsers.add_parser("stop", help="Stop the Onyx containers, keeping their data")
        onyx_subparsers.add_parser(
            "uninstall", help="Permanently delete the Onyx deployment and all its data"
        )

        # Web access is not here. Searching and fetching are the agent's commands, `ling-search`
        # and `ling-fetch`: Rust binaries built from ling-web-rs/ and installed beside `ling`,
        # so they work from any shell without this virtualenv. `ling-admin fetch` was retired on
        # 2026-09-30, as `ling-admin search` was before it.

        # Command: ling-admin gmail -- read-only mail access for the Mightling agent, in the same shape
        # as search/fetch and for the same reason (a shell command the model uses reliably). It is a
        # client of the service the web UI already runs, not a second IMAP path; connecting an
        # account is still `ling-admin chat gmail`.
        gmail_parser = subparsers.add_parser("gmail", help="Search and read connected Gmail accounts (read-only)")
        gmail_subparsers = gmail_parser.add_subparsers(dest="gmail_command", required=True)
        gmail_search_parser = gmail_subparsers.add_parser("search", help="Search with Gmail query syntax")
        gmail_search_parser.add_argument("query", nargs="+", help='Gmail query, e.g. from:alice newer_than:7d')
        gmail_search_parser.add_argument("-n", "--max-results", type=int, default=10, help="Messages to return (max 20)")
        gmail_search_parser.add_argument("--json", action="store_true", help="Emit raw JSON")
        gmail_read_parser = gmail_subparsers.add_parser("read", help="Read one message by the id search printed")
        gmail_read_parser.add_argument("message_id", help="Message id exactly as `gmail search` printed it")
        gmail_read_parser.add_argument("--max-chars", type=int, default=8000, help="Body characters to print")
        gmail_read_parser.add_argument("--json", action="store_true", help="Emit raw JSON")
        gmail_status_parser = gmail_subparsers.add_parser("status", help="Show which accounts are connected")
        gmail_status_parser.add_argument("--json", action="store_true", help="Emit raw JSON")

        # Command: ling-admin searxng (the search container behind ling-search and the web UI)
        searxng_parser = subparsers.add_parser("searxng", help="Manage the local SearXNG search container")
        searxng_subparsers = searxng_parser.add_subparsers(dest="searxng_command")
        searxng_subparsers.add_parser("start", help="Start SearXNG on 127.0.0.1:8888 (recreates one made on Docker's default bridge)")

        # Command: ling-admin google (the service behind Gmail, Drive and Calendar in /apps)
        google_parser = subparsers.add_parser("google", help="Manage the local Google service (Gmail, Drive, Calendar)")
        google_subparsers = google_parser.add_subparsers(dest="google_command")
        google_subparsers.add_parser(
            "start", help="Start the Google service on 127.0.0.1:8767 (adopts the web UI's if it exists)"
        )
        google_subparsers.add_parser(
            "stop", help="Remove the Google service container; connected accounts stay stored"
        )
        google_subparsers.add_parser(
            "status", help="Show whether it runs and which accounts hold which apps"
        )

        # Command: ling-admin web
        web_parser = subparsers.add_parser("web", help="Launch Web Canvas UI interactive pair-programming pane")
        web_parser.add_argument("--port", type=int, default=8501, help="Port for Web Canvas UI")

        # Kept on the parser so run_cli can print a group's help when no subcommand is given.
        # Without it `ling-admin server` (or clear, model, …) matched no branch and exited 0
        # having printed nothing; desktop and ling named locals of build_parser and hit NameError.
        parser.command_groups = {
            "model": (model_parser, "model_command"),
            "main-model": (main_model_parser, "main_model_command"),
            "clear": (clear_parser, "clear_command"),
            "server": (server_parser, "server_command"),
            "chat": (onyx_parser, "onyx_command"),
            "onyx": (onyx_parser, "onyx_command"),
            "desktop": (desktop_parser, "desktop_command"),
            "searxng": (searxng_parser, "searxng_command"),
            "google": (google_parser, "google_command"),
        }
        if diffusion_model_parser is not None:
            parser.command_groups["diffusion-model"] = (diffusion_model_parser, "diffusion_model_command")
        return parser

    @classmethod
    def run_cli(cls, argv: Optional[List[str]] = None) -> None:
        """
        Main execution entrypoint for CLI command parsing and subcommand dispatching.

        Args:
            argv (Optional[List[str]]): Arguments to parse; None reads `sys.argv`.
        """
        parser = cls.build_parser()
        args = parser.parse_args(argv)

        if not args.command:
            parser.print_help()
            sys.exit(0)

        group = parser.command_groups.get(args.command)
        if group is not None and not getattr(args, group[1], None):
            group[0].print_help()
            sys.exit(1)

        # Puffin became Mightling: what a 1.4.x node left under the old names is moved once
        # (specs/DREAMFERENCE_RENAME_MIGHTLING.md §4.2). Not where stdout is a protocol: `mcp`, and
        # the node commands a paired sender's SSH session runs.
        machine_read = args.command == "mcp" or (
            args.command == "node" and getattr(args, "node_command", None) in ("serve-job", "job-exec"))
        if not machine_read:
            from dreamference.cli.legacy_name_migration import LegacyNameMigration
            LegacyNameMigration.run()

        # bubblewrap's sandbox, checked on every run: where it is missing, the user is asked to
        # fix it with sudo or to turn off what needs it (specs/DREAMFERENCE_SETUP.md §3.3).
        from dreamference.vllm_server import SandboxPrerequisite
        subcommand = getattr(args, args.command.replace("-", "_") + "_command", None)
        if not SandboxPrerequisite.gate(args.command, subcommand):
            sys.exit(1)

        # Handle stdio MCP server command immediately
        if args.command == "mcp":
            run_mcp_server()
            sys.exit(0)

        config_file = getattr(args, "config", None)
        vllm_host = getattr(args, "vllm_host", None)
        model = getattr(args, "model", None)
        diffusion_model = getattr(args, "diffusion_model", None)
        draft_model = getattr(args, "draft_model", None)
        num_speculative_tokens = getattr(args, "num_speculative_tokens", None)
        agent_runner = getattr(args, "agent", None)
        hf_token = getattr(args, "hf_token", None)
        num_scheduler_steps = getattr(args, "num_scheduler_steps", None)
        attention_backend = getattr(args, "attention_backend", None)
        kv_cache_dtype = getattr(args, "kv_cache_dtype", None)
        max_num_batched_tokens = getattr(args, "max_num_batched_tokens", None)
        guided_decoding_backend = getattr(args, "guided_decoding_backend", None)
        cave_mode = getattr(args, "cave", None)
        tensorize_opt = getattr(args, "tensorize", None)

        config = DreamferenceConfig(
            config_file=config_file,
            vllm_host=vllm_host,
            model=model,
            diffusion_model=diffusion_model,
            draft_model=draft_model,
            num_speculative_tokens=num_speculative_tokens,
            agent_runner=agent_runner,
            hf_token=hf_token,
            num_scheduler_steps=num_scheduler_steps,
            attention_backend=attention_backend,
            kv_cache_dtype=kv_cache_dtype,
            cave_mode=cave_mode,
            use_tensorizer=tensorize_opt,
            guided_decoding_backend=guided_decoding_backend
        )

        # Instantiate selected runner (Codex by default, or Cline/Continue/OpenHands)
        if config.agent_runner == "cline":
            runner = ClineRunner(config=config)
        elif config.agent_runner == "continue":
            runner = ContinueRunner(config=config)
        elif config.agent_runner == "openhands":
            runner = OpenHandsRunner(config=config)
        else:
            runner = CodexRunner(config=config)

        # Dispatch subcommand logic
        if args.command == "model":
            if args.model_command == "list":
                cls.display_header()

                table = Table(title="Available Mightling Models")
                table.add_column("Model Name", style="cyan", no_wrap=True)
                table.add_column("HuggingFace Repo ID", style="magenta")
                
                for key, spec in ModelMatrixRegistry.MATRIX.items():
                    if ModelMatrixRegistry.is_offered(key):
                        table.add_row(key, spec.hf_repo_id)
                
                console.print(table)
                sys.exit(0)

            elif args.model_command == "download":
                cls.display_header()
                auto_t = getattr(args, "tensorize", False)
                if getattr(args, "all", False):
                    download_all_models(hf_token=config.hf_token, auto_tensorize=auto_t)
                else:
                    target_model = args.model or config.model
                    if ModelMatrixRegistry.removed_in(target_model):
                        print(f"❌ {ModelMatrixRegistry.removed_message(target_model, configured=not args.model)}")
                        sys.exit(1)
                    if target_model and not ModelMatrixRegistry.is_offered(target_model):
                        print(f"❌ '{target_model}' is not a model Mightling offers.")
                        sys.exit(1)
                    if target_model:
                        download_model(target_model, hf_token=config.hf_token, auto_tensorize=auto_t)
                        if config.draft_model:
                            download_model(config.draft_model, hf_token=config.hf_token, auto_tensorize=auto_t)
                    else:
                        print("⚠️  No model specified. Use --model <model_name> or initialize config with 'ling-admin init --model <model_name>'")
                sys.exit(0)

        elif args.command == "diffusion-model":
            if args.diffusion_model_command == "set":
                cls.display_header()
                out_console = Console()
                # The mirror of main-model set's refusal: an autoregressive checkpoint handed to
                # the diffusion sidecar loads through AutoModelForMaskedLM and produces garbage
                # (or an opaque load error), so only registry entries that declare themselves
                # diffusion are accepted. Unknown keys are refused too — the sidecar has no
                # pre-flight gates to catch a bad guess later.
                from dreamference.hardware import model_is_diffusion
                if not model_is_diffusion(args.model_name):
                    out_console.print(
                        f"[bold red]❌ '{args.model_name}' is not a diffusion model in the registry.[/bold red]\n"
                        f"   Diffusion checkpoints generate by block denoising and are served by the "
                        f"diffusion sidecar, not vLLM. For the main model use: "
                        f"[cyan]ling-admin main-model set {args.model_name}[/cyan]")
                    sys.exit(1)
                config.diffusion_model = args.model_name
                saved_path = config.save_config()
                out_console.print(f"[bold green]✅ Diffusion model set to '{args.model_name}'[/bold green]")
                out_console.print(f"   [cyan]Config saved to:[/cyan] {saved_path}")
                sys.exit(0)

        elif args.command == "main-model":
            if args.main_model_command == "set":
                cls.display_header()
                out_console = Console()
                # A diffusion checkpoint pointed at vLLM fails only at launch, with an error that
                # never mentions the real problem. Refuse it here, where the fix is nameable.
                from dreamference.hardware import model_is_diffusion
                if ModelMatrixRegistry.removed_in(args.model_name):
                    print(f"❌ {ModelMatrixRegistry.removed_message(args.model_name)}")
                    sys.exit(1)
                if not ModelMatrixRegistry.is_offered(args.model_name):
                    out_console.print(f"[bold red]❌ '{args.model_name}' is not a model Mightling offers.[/bold red]")
                    sys.exit(1)
                if model_is_diffusion(args.model_name):
                    out_console.print(
                        f"[bold red]❌ '{args.model_name}' is a diffusion model and cannot be served "
                        f"by vLLM as the main model.[/bold red]\n"
                        f"   Use: [cyan]ling-admin diffusion-model set {args.model_name}[/cyan]")
                    sys.exit(1)
                config.model = args.model_name
                saved_path = config.save_config()
                out_console.print(f"[bold green]✅ Main model set to '{args.model_name}'[/bold green]")
                out_console.print(f"   [cyan]Config saved to:[/cyan] {saved_path}")

                # A model change is not local to vLLM: Onyx's LLM provider is registered by
                # name, its vision flag follows the checkpoint, and the image search sidecar
                # carries the served model id in its environment. Left alone, all three keep
                # pointing at the previous model until someone remembers `ling-admin chat configure`
                # -- so it runs here, when Onyx is up. configure() is idempotent, and skipping
                # when Onyx is absent keeps `main-model set` usable before any deployment.
                if not args.no_onyx:
                    import subprocess as _subprocess

                    onyx_up = _subprocess.run(
                        ["docker", "ps", "--filter",
                         "label=com.docker.compose.service=api_server",
                         "--format", "{{.Names}}"],
                        capture_output=True, text=True, timeout=30, check=False,
                    ).stdout.strip()
                    if onyx_up:
                        out_console.print(
                            "[cyan]🔁 Re-registering the model with Onyx "
                            "(skip with --no-onyx)...[/cyan]")
                        from dreamference.chat import OnyxRunner

                        OnyxRunner(config).configure()
                sys.exit(0)
            elif args.main_model_command == "inspect":
                cls.display_header()
                import requests

                out_console = Console()
                out_console.print("[bold cyan]🔍 Inspecting Main Model[/bold cyan]")

                # One session for every probe against the local server, rather than the module
                # level requests.get/post helpers. Each of those builds a throwaway Session with
                # its own connection pool and never closes it, so eleven probes left eleven
                # sockets in CLOSE-WAIT for the life of the process — visible in `ss` after any
                # `ling-admin main-model inspect`. A shared session also reuses the one connection
                # instead of reconnecting per probe. External hosts stay on the module API.
                session = requests.Session()

                # Capability probes ask a short factual question and grep the answer, so the
                # model's thinking channel is not merely wasted budget — it silently breaks them.
                # A reasoning model opens <think> immediately, the reasoning parser buffers that
                # span until its closing tag, and a probe capped at 50-100 tokens truncates first.
                # The parser then flushes nothing: both `content` and `reasoning_content` come back
                # empty with finish_reason='length', and every probe reads that as "unsupported".
                # Measured on Qwen3.5-122B: the antArtifact probe reported No at 100 and 400
                # tokens, and the correct answer in 34 with thinking off. `Tags: None detected`
                # was the same false negative at max_tokens 50.
                #
                # Unknown chat_template_kwargs are simply unused variables to a Jinja template, so
                # this is inert on models that do not have a thinking channel to disable.
                no_thinking = {"chat_template_kwargs": {"enable_thinking": False}}

                vllm_host = config.vllm_host
                api_base = f"{vllm_host}/v1/chat/completions"
                api_key = "gb10-local-token"

                # Try to get the running model from the server first
                model_name = config.model
                max_model_len = "Unknown"
                try:
                    models_response = session.get(f"{vllm_host}/v1/models", timeout=5)
                    if models_response.status_code == 200:
                        models_data = models_response.json()
                        if models_data.get("data"):
                            model_name = models_data["data"][0]["id"]
                            max_model_len = str(models_data["data"][0].get("max_model_len", "Unknown"))
                except requests.exceptions.RequestException:
                    pass # Fallback to config.model

                if not model_name:
                    out_console.print("[bold red]❌ No main model set in configuration and server is unreachable.[/bold red]")
                    sys.exit(1)

                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }

                # Check reasoning capability
                is_reasoning = False
                payload_reasoning = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": "How many Rs are in strawberry? Think step by step."}],
                    "max_tokens": 50,
                    "temperature": 0.1
                }
                try:
                    r_response = session.post(api_base, json=payload_reasoning, headers=headers, timeout=15)
                    if r_response.status_code == 200:
                        msg = r_response.json().get("choices", [{}])[0].get("message", {})
                        content = msg.get("content") or ""
                        if "<think>" in content or msg.get("reasoning_content") or msg.get("reasoning"):
                            is_reasoning = True
                except Exception:
                    pass

                # Check supported roles
                supported_roles = []
                test_roles = {
                    "system": [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": "Hi"}],
                    "user": [{"role": "user", "content": "Hi"}],
                    "assistant": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}, {"role": "user", "content": "Next"}],
                    "tool": [{"role": "user", "content": "Do it"}, {"role": "assistant", "content": "doing", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "test", "arguments": "{}"}}]}, {"role": "tool", "content": "done", "tool_call_id": "call_1"}]
                }
                for role, msgs in test_roles.items():
                    payload_role = {
                        "model": model_name,
                        "messages": msgs,
                        "max_tokens": 1,
                    }
                    try:
                        resp = session.post(api_base, json=payload_role, headers=headers, timeout=5)
                        if resp.status_code == 200:
                            supported_roles.append(role)
                    except Exception:
                        pass
                roles_str = ", ".join(supported_roles) if supported_roles else "Unknown"

                # Check ChatML tag support
                supports_chatml = False
                payload_chatml = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": "Print the ChatML tag '<|im_start|>' exactly as written."}],
                    "max_tokens": 100,
                    "temperature": 0,
                    **no_thinking
                }
                try:
                    c_response = session.post(api_base, json=payload_chatml, headers=headers, timeout=15)
                    if c_response.status_code == 200:
                        msg = c_response.json().get("choices", [{}])[0].get("message", {})
                        content = msg.get("content") or ""
                        if "<|im_start|>" in content:
                            supports_chatml = True
                except Exception:
                    pass

                # Check antArtifact support
                supports_artifact = False
                payload_artifact = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": "Create a one line python script and wrap it in <antArtifact> tags."}],
                    "max_tokens": 100,
                    "temperature": 0,
                    **no_thinking
                }
                try:
                    a_response = session.post(api_base, json=payload_artifact, headers=headers, timeout=15)
                    if a_response.status_code == 200:
                        msg = a_response.json().get("choices", [{}])[0].get("message", {})
                        content = msg.get("content") or ""
                        if "<antArtifact" in content:
                            supports_artifact = True
                except Exception:
                    pass

                # Check tags supported by the model by asking it directly
                supported_tags = []
                payload_tags = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": "Analyze your own architecture and output the exact XML tags you use for tool calling, reasoning, and artifacts (e.g. <tool_call>, <think>, <answer>). Output ONLY a comma separated list of tags, with no other text."}],
                    "max_tokens": 50,
                    "temperature": 0,
                    "top_p": 0.01,
                    "seed": 42,
                    **no_thinking
                }
                try:
                    t_response = session.post(api_base, json=payload_tags, headers=headers, timeout=15)
                    if t_response.status_code == 200:
                        msg = t_response.json().get("choices", [{}])[0].get("message", {})
                        content = msg.get("content") or ""
                        import re
                        tags = re.findall(r'<\|?[a-zA-Z0-9_]+\|?>', content)
                        unique_tags = sorted(list(set(tags)))
                        supported_tags = unique_tags
                except Exception:
                    pass
                tags_str = ", ".join(supported_tags) if supported_tags else "None detected"
                # Check Streaming, TTFT and decode rate.
                #
                # One stream yields all three, so the decode figure is free. The prompt is a coding
                # task on purpose: on a speculative-decoding server the workload decides the
                # number, because acceptance on code runs roughly twice what it does on prose, and
                # a figure taken from "Say 'Test'" would describe nothing this project does.
                supports_streaming = False
                ttft = 0.0
                decode_tps = 0.0
                decode_tokens = 0
                payload_stream = {
                    "model": model_name,
                    "messages": [{
                        "role": "user",
                        "content": "Write a Python function that reverses a linked list. Code only.",
                    }],
                    "max_tokens": 128,
                    "temperature": 0,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                try:
                    start_stream = time.time()
                    # `with` rather than a bare call: this probe measures time-to-first-token and
                    # then breaks, deliberately abandoning the rest of the body. A streamed
                    # response whose body is never finished holds its connection open, and the
                    # server's FIN then leaves the socket in CLOSE-WAIT for the life of the
                    # process — `ling-admin main-model inspect` was leaking one per probe.
                    with session.post(
                        api_base, json=payload_stream, headers=headers, timeout=15, stream=True
                    ) as s_response:
                        if s_response.status_code == 200:
                            import json as _json

                            for line in s_response.iter_lines():
                                if not line:
                                    continue
                                decoded_line = line.decode('utf-8')
                                if not decoded_line.startswith("data: "):
                                    continue
                                if decoded_line == "data: [DONE]":
                                    break
                                if not supports_streaming:
                                    ttft = time.time() - start_stream
                                    supports_streaming = True
                                # The usage chunk arrives last and carries the authoritative token
                                # count, which beats counting deltas: with speculative decoding a
                                # single chunk can carry several accepted tokens.
                                try:
                                    chunk = _json.loads(decoded_line[len("data: "):])
                                except ValueError:
                                    continue
                                usage = chunk.get("usage") or {}
                                if usage.get("completion_tokens"):
                                    decode_tokens = int(usage["completion_tokens"])
                            elapsed = time.time() - start_stream
                            # Subtract TTFT so this reports decode rate rather than end-to-end
                            # throughput; prefill is a separate cost and already shown above.
                            if decode_tokens > 1 and elapsed > ttft:
                                decode_tps = (decode_tokens - 1) / (elapsed - ttft)
                except Exception:
                    pass

                # Check if model is MoE via HF config
                is_moe = "Unknown"
                try:
                    hf_resp = requests.get(f"https://huggingface.co/{model_name}/raw/main/config.json", timeout=3)
                    if hf_resp.status_code == 200:
                        model_cfg = hf_resp.json()
                        moe_keys = ["num_experts_per_tok", "num_local_experts", "moe_dim", "n_routed_experts", "num_experts"]
                        if any(k in model_cfg for k in moe_keys) or "moe" in model_cfg.get("model_type", "").lower():
                            is_moe = "Yes"
                        else:
                            is_moe = "No"
                except Exception:
                    name_lower = model_name.lower()
                    if any(x in name_lower for x in ["moe", "mixtral", "deepseek-coder-v2", "deepseek-v3", "deepseek-r1"]):
                        is_moe = "Yes (heuristic)"

                # Check Tool Calling Capability
                supports_tools = False
                payload_tool_test = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": "What is the weather in Tokyo?"}],
                    "tools": [{
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "Get current weather",
                            "parameters": {
                                "type": "object",
                                "properties": {"location": {"type": "string"}},
                                "required": ["location"]
                            }
                        }
                    }],
                    "max_tokens": 150,
                    "temperature": 0
                }
                try:
                    tool_resp = session.post(api_base, json=payload_tool_test, headers=headers, timeout=15)
                    if tool_resp.status_code == 200:
                        msg = tool_resp.json().get("choices", [{}])[0].get("message", {})
                        if "tool_calls" in msg and msg["tool_calls"]:
                            supports_tools = True
                except Exception:
                    pass

                # Check JSON Mode
                supports_json = False
                payload_json = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": "Output a JSON object with key 'hello' and value 'world'."}],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 50,
                    "temperature": 0,
                    **no_thinking
                }
                try:
                    json_resp = session.post(api_base, json=payload_json, headers=headers, timeout=5)
                    if json_resp.status_code == 200:
                        supports_json = True
                except Exception:
                    pass

                # Check KV Cache metrics
                kv_cache_usage = "Unknown"
                try:
                    metrics_resp = session.get(f"{vllm_host}/metrics", timeout=2)
                    if metrics_resp.status_code == 200:
                        import re
                        match = re.search(r'vllm:kv_cache_usage_perc\{[^}]+\}\s+([0-9.]+)', metrics_resp.text)
                        if match:
                            val = float(match.group(1)) * 100
                            kv_cache_usage = f"{val:.1f}%"
                except Exception:
                    pass

                # What the engine actually chose, read from the container's own command line and
                # the live /metrics — see _collect_engine_facts for why the old fixed checklist
                # could not answer its own questions.
                from urllib.parse import urlparse
                port = urlparse(api_base).port or 8000
                engine_facts = cls._collect_engine_facts(port, vllm_host, config.model)

                out_console.print(f"   [cyan]Model:[/cyan]    {model_name}")
                out_console.print(f"   [cyan]Endpoint:[/cyan] {api_base}")
                out_console.print(f"   [cyan]Context:[/cyan]     {max_model_len} tokens")
                out_console.print(f"   [cyan]KV Cache:[/cyan]    {kv_cache_usage}")
                out_console.print(f"   [cyan]Architecture:[/cyan] {is_moe} (MoE)")
                out_console.print(f"   [cyan]Roles:[/cyan]       {roles_str}")
                out_console.print(f"   [cyan]Tags:[/cyan]        {tags_str}")
                out_console.print(f"   [cyan]Reasoning:[/cyan] {'Yes' if is_reasoning else 'No'}")
                out_console.print(f"   [cyan]Tool Calls:[/cyan]  {'Yes' if supports_tools else 'No'}")
                out_console.print(f"   [cyan]JSON Mode:[/cyan]   {'Yes' if supports_json else 'No'}")
                out_console.print(f"   [cyan]antArtifact:[/cyan] {'Yes' if supports_artifact else 'No'}")
                out_console.print(f"   [cyan]ChatML:[/cyan]      {'Yes' if supports_chatml else 'No'}")
                if supports_streaming:
                    decode_str = f", decode: {decode_tps:.1f} tok/s on code" if decode_tps else ""
                    out_console.print(f"   [cyan]Streaming:[/cyan]   Yes (TTFT: {ttft:.3f}s{decode_str})")
                else:
                    out_console.print(f"   [cyan]Streaming:[/cyan]   No")
                out_console.print("")
                hardware_gpus = []
                try:
                    import subprocess
                    smi_res = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True)
                    if smi_res.returncode == 0:
                        hardware_gpus = [g.strip() for g in smi_res.stdout.strip().split("\n") if g.strip()]
                except Exception:
                    pass
                
                gpu_count = len(hardware_gpus)
                gpu_names = ", ".join(hardware_gpus) if gpu_count > 0 else "None detected"

                out_console.print("[bold yellow]🖥️  Hardware Information[/bold yellow]")
                out_console.print(f"   [cyan]GPU Count:[/cyan]       {gpu_count}")
                out_console.print(f"   [cyan]GPU Models:[/cyan]      {gpu_names}")
                out_console.print("")
                
                out_console.print("[bold yellow]🚀 Engine Configuration[/bold yellow]")
                width = max((len(k) for k in engine_facts), default=0)
                for key, value in engine_facts.items():
                    out_console.print(f"   [cyan]{key + ':':<{width + 1}}[/cyan] {value}")
                out_console.print("")

                # Speed means nothing without evidence the output is still valid -- on SM121 a
                # wrong kernel corrupts silently rather than erroring.
                out_console.print("[bold yellow]🧪 Correctness Canary[/bold yellow]")
                canary = cls._run_correctness_canary(session, api_base, model_name, headers)
                c_width = max((len(k) for k in canary), default=0)
                for key, value in canary.items():
                    out_console.print(f"   [cyan]{key + ':':<{c_width + 1}}[/cyan] {value}")

                if getattr(args, "deep", False):
                    cls._render_deep_inspection(out_console, config, vllm_host, api_base, model_name)
                out_console.print("")

                # Trimmed to the prompts whose value is in reading the output. The short probes
                # ("Say 'Hello, World!'", "What is 2 + 2?") moved to the correctness canary above,
                # which asserts a specific answer rather than printing something for a human to
                # eyeball, and the instruction-hierarchy prompt was dropped as well. What remains
                # are two long-form pairs: one prose, one code, each run with and without thinking.
                # Both halves of each pair state enable_thinking explicitly. Leaving the plain
                # variant silent used to mean "thinking on", but the server now defaults it off via
                # --default-chat-template-kwargs, so silence made the pair render an identical
                # prompt twice and the comparison measured nothing. Stating it on both sides keeps
                # the contrast real regardless of what the server's default becomes.
                sample_prompts = [
                    {"prompt": "Write a Wikipedia article on Richard Feynman (Thinking).", "kwargs": {"chat_template_kwargs": {"enable_thinking": True}}},
                    {"prompt": "Write a Wikipedia article on Richard Feynman (No Thinking).", "kwargs": {"chat_template_kwargs": {"enable_thinking": False}}},
                    {"prompt": "Write a Python implementation of red-black trees (Thinking).", "kwargs": {"chat_template_kwargs": {"enable_thinking": True}}},
                    {"prompt": "Write a Python implementation of red-black trees (No Thinking).", "kwargs": {"chat_template_kwargs": {"enable_thinking": False}}}
                ]

                table = Table(show_header=True, header_style="bold magenta")
                table.add_column("Prompt")
                table.add_column("Status")
                table.add_column("Latency (s)")
                table.add_column("Tokens/sec")
                table.add_column("Accept / tau")
                table.add_column("Response snippet")

                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }

                for p_data in sample_prompts:
                    prompt = p_data["prompt"]
                    sys_prompt = p_data.get("system")
                    msgs = []
                    if sys_prompt:
                        msgs.append({"role": "system", "content": sys_prompt})
                    msgs.append({"role": "user", "content": prompt})
                    
                    payload = {
                        "model": model_name,
                        "messages": msgs,
                        "max_tokens": 8192,
                        "temperature": 0.1 if "No Thinking" not in prompt else 0,
                        "stream": True,
                        "stream_options": {"include_usage": True}
                    }
                    if "kwargs" in p_data:
                        payload.update(p_data["kwargs"])
                    # Acceptance is only meaningful against a known workload, so it is measured
                    # as a delta around this one request rather than read as a running total.
                    from dreamference.cli.model_deep_inspector import ModelDeepInspector
                    spec_before = ModelDeepInspector._spec_counters(vllm_host)
                    start_time = time.time()
                    # Bound before the try so the finally below can close it unconditionally —
                    # if the post itself raises, the name would otherwise be undefined there.
                    response = None
                    try:
                        out_console.print(f"[bold green]▶ Running Prompt:[/bold green] {prompt}")
                        response = session.post(api_base, json=payload, headers=headers, timeout=15, stream=True)
                        if response.status_code == 200:
                            import json
                            full_content = ""
                            full_reasoning = ""
                            comp_tokens = 0
                            
                            for line in response.iter_lines():
                                if line:
                                    decoded = line.decode('utf-8')
                                    if decoded.startswith("data: ") and decoded != "data: [DONE]":
                                        try:
                                            chunk = json.loads(decoded[6:])
                                            # Grab usage if present
                                            if chunk.get("usage"):
                                                comp_tokens = chunk["usage"].get("completion_tokens", 0)
                                            
                                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                                            c = delta.get("content", "")
                                            r = delta.get("reasoning_content", "") or delta.get("reasoning", "")
                                            
                                            if r:
                                                full_reasoning += r
                                            if c:
                                                full_content += c
                                        except Exception:
                                            pass
                            latency = time.time() - start_time
                            
                            display_text = ""
                            if full_reasoning:
                                display_text += f"<think>{full_reasoning}</think> "
                            display_text += full_content
                            if not display_text.strip():
                                display_text = "Empty response"
                                
                            snippet = display_text.replace("\n", " ").strip()
                            tps = comp_tokens / latency if latency > 0 else 0
                            tps_str = f"{tps:.1f}" if comp_tokens > 0 else "N/A"
                            
                            # The long-form prompts are the ones worth a whole-completion rate; the
                            # short ones finish too fast for it to mean anything. Matched
                            # case-insensitively so a prompt reworded in the list above does not
                            # silently drop out of this branch.
                            lowered = prompt.lower()
                            if ("richard feynman" in lowered or "red-black trees" in lowered) and latency > 0:
                                completions_per_sec = 1.0 / latency
                                tps_str = f"{tps_str} ({completions_per_sec:.2f} completions/s)"
                            
                            accept_str = "N/A"
                            spec_after = ModelDeepInspector._spec_counters(vllm_host)
                            if spec_before and spec_after:
                                d_drafts = spec_after["drafts"] - spec_before["drafts"]
                                d_drafted = spec_after["drafted"] - spec_before["drafted"]
                                d_accepted = spec_after["accepted"] - spec_before["accepted"]
                                if d_drafts > 0 and d_drafted > 0:
                                    accept_str = (
                                        f"{100 * d_accepted / d_drafted:.1f}% / "
                                        f"{1 + d_accepted / d_drafts:.2f}"
                                    )

                            table.add_row(prompt, "[green]OK[/green]", f"{latency:.2f}", tps_str, accept_str, snippet)
                        else:
                            latency = time.time() - start_time
                            out_console.print(f"[red]Error {response.status_code}[/red]\n")
                            table.add_row(prompt, f"[red]Error {response.status_code}[/red]", f"{latency:.2f}", "N/A", "N/A", response.text.replace("\n", " ")[:50])
                    except requests.exceptions.RequestException as e:
                        latency = time.time() - start_time
                        out_console.print(f"[red]Failed: {e}[/red]\n")
                        table.add_row(prompt, "[red]Failed[/red]", f"{latency:.2f}", "N/A", "N/A", str(e).replace("\n", " ")[:50])
                    finally:
                        # Streamed responses do not release their connection until the body is
                        # finished or the response is closed, and neither is guaranteed here: a
                        # non-200 skips the iteration entirely, and a mid-stream error leaves it
                        # part-read. Without this the sockets sit in CLOSE-WAIT until the process
                        # exits — harmless for one run, unbounded for anything that loops.
                        if response is not None:
                            response.close()

                out_console.print(table)
                session.close()
                sys.exit(0)


        elif args.command == "logs" and getattr(args, "target", None) == "mcp":
            # Codex's TUI writes tracing into a SQLite database rather than a log file, which makes
            # the one question worth asking -- did my MCP servers actually come up? -- annoyingly
            # hard to answer. This pulls out just the lifecycle lines.
            import sqlite3

            cls.display_header()
            db = os.path.join(CodexInstaller.home_dir(), "logs_2.sqlite")
            if not os.path.exists(db):
                print(f"❌ No ling log database at {db}. Run a session with `RUST_LOG=codex_mcp=trace ling` first.")
                sys.exit(1)
            try:
                conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                rows = list(conn.execute("SELECT * FROM logs ORDER BY rowid DESC LIMIT 8000"))
            except sqlite3.Error as e:
                print(f"❌ Could not read {db}: {e}")
                sys.exit(1)

            seen = []
            for row in rows:
                text = " ".join(str(x) for x in row)
                if "server_name=" not in text:
                    continue
                marker = next(
                    (m for m in ("Service initialized as client", "task cancelled",
                                 "serve finished", "error") if m in text), None
                )
                if not marker:
                    continue
                start = text.find("server_name=")
                name = text[start + len("server_name="):].split("}")[0]
                entry = (name, marker)
                if entry not in seen:
                    seen.append(entry)

            if not seen:
                print("No MCP lifecycle entries found. Run `RUST_LOG=codex_mcp=trace ling` to record some.")
            else:
                print("[bold]MCP server lifecycle (most recent first)[/bold]\n")
                for name, marker in seen:
                    icon = "✅" if marker == "Service initialized as client" else "⚠️ "
                    print(f"   {icon} {name:16} {marker}")
                print("\nNote: a server can initialize and then be cancelled — ling still reports")
                print("      it as 'not initialized' in its startup banner.")
            sys.exit(0)

        elif args.command == "desktop":
            from dreamference.chat import DesktopRunner
            # `ling-admin` exists only on a node: the window built here then shows this
            # machine's web UI and never looks for another node's.
            from dreamference.node import NodeIdentity
            NodeIdentity.ensure()

            if args.desktop_command == "install":
                sys.exit(DesktopRunner.install())
            elif args.desktop_command == "run":
                sys.exit(DesktopRunner.run())
            elif args.desktop_command == "build":
                sys.exit(DesktopRunner.build())
            elif args.desktop_command == "status":
                sys.exit(DesktopRunner.status())

        elif args.command in ("chat", "onyx"):
            from dreamference.chat import OnyxRunner

            onyx_runner = OnyxRunner(config=config)
            if args.onyx_command == "start":
                sys.exit(onyx_runner.start(wait=not args.no_wait))
            elif args.onyx_command == "configure":
                kwargs = {}
                if args.email:
                    kwargs["email"] = args.email
                if args.password:
                    kwargs["password"] = args.password
                sys.exit(onyx_runner.configure(
                    enable_web=not args.no_web, brand=not args.no_brand,
                    enable_voice=not args.no_voice, enable_gmail=not args.no_gmail,
                    enable_image_search=not args.no_image_search, **kwargs
                ))
            elif args.onyx_command == "google-auth":
                sys.exit(0 if onyx_runner.enable_google_login(
                    args.client_id, args.client_secret
                ) else 1)
            elif args.onyx_command == "gmail":
                sys.exit(0 if onyx_runner.connect_gmail() else 1)
            elif args.onyx_command == "status":
                sys.exit(onyx_runner.status())
            elif args.onyx_command == "password":
                sys.exit(onyx_runner.show_admin_credentials())
            elif args.onyx_command == "logs":
                sys.exit(onyx_runner.logs(follow=args.follow))
            elif args.onyx_command == "stop":
                sys.exit(onyx_runner.stop())
            elif args.onyx_command == "uninstall":
                sys.exit(onyx_runner.uninstall())

        elif args.command == "gmail":
            sys.exit(cls.handle_gmail(args))

        elif args.command == "init":
            cls.display_header()
            auto_t = config.use_tensorizer
            if config.model:
                download_model(config.model, hf_token=config.hf_token, auto_tensorize=auto_t)
                if config.draft_model:
                    download_model(config.draft_model, hf_token=config.hf_token, auto_tensorize=auto_t)
            from dreamference.config.config_generator import generate_default_init_config
            from dreamference.config.config_path_resolver import ConfigPathResolver
            
            target_config_path = getattr(args, "config", None)
            resolved_path = ConfigPathResolver.resolve_path(target_config_path)
            saved_config_path = generate_default_init_config(resolved_path)
            from dreamference.context_engine import ContextEngine
            ctx_engine = ContextEngine()
            summary = ctx_engine.index_workspace(force_reindex=True)

            # The module-level console. Rebinding `console` anywhere in run_cli made it a local for
            # the whole function, so every other branch that printed with it (e.g. `endpoints`)
            # failed with UnboundLocalError.
            console.print("[bold green]✅ Mightling workspace initialized successfully![/bold green]")
            console.print(f"   [cyan]Mightling Config:[/cyan]       {saved_config_path}")
            console.print(f"   [cyan]Active Agent:[/cyan]    {config.agent_runner.upper()} (Default: CODEX)")
            console.print(f"   [cyan]Target Model:[/cyan]    {config.model}")
            if config.draft_model:
                console.print(f"   [cyan]Draft Model:[/cyan]     {config.draft_model} ({config.num_speculative_tokens} tokens)")
            console.print(f"   [cyan]HF Auth Token:[/cyan]    {'Configured' if config.hf_token else 'Not Configured'}")
            console.print(f"   [cyan]Target Host:[/cyan]     {config.vllm_host}")
            console.print(f"   [cyan]Indexed Files:[/cyan]   {summary['total_indexed_files']} ({summary['total_ast_symbols']} AST symbols)")

        elif args.command == "run":
            sys.exit(runner.run_session(prompt=args.prompt, debug=args.debug))

        elif args.command == "status":
            cls.handle_status()

        elif args.command == "clear":
            if args.clear_command == "model-cache":
                cls.display_header()
                sys.exit(0 if clear_model_cache() else 1)

            elif args.clear_command == "tensorize-cache":
                cls.display_header()
                sys.exit(0 if clear_tensorizer_cache() else 1)

        elif args.command == "clear-tensorize-cache":
            # The older spelling of `clear tensorize-cache`; it used to parse and then do nothing.
            cls.display_header()
            sys.exit(0 if clear_tensorizer_cache() else 1)
        elif args.command == "endpoints":
            cls.display_header()
            table = Table(title="Available Endpoints (standard /v1 API)", show_header=True, header_style="bold magenta")
            table.add_column("Endpoint", style="cyan")
            table.add_column("Method", style="green")
            table.add_column("Description")
            table.add_row("/v1/models", "GET", "List available models")
            table.add_row("/v1/chat/completions", "POST", "Chat completions")
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
            if ModelMatrixRegistry.diffusion_enabled():
                cred_table.add_row("Diffusion model URL", f"http://localhost:{DEFAULT_DIFFUSION_PORT}/v1")
            cred_table.add_row("API Key", "Optional (use --api-key on serve; otherwise not required)")
            cred_table.add_row("Auth Header", "Authorization: Bearer <key> (when enabled)")
            console.print(cred_table)
            print("\n💡 Use with any client that speaks the standard /v1 chat-completions API: point its base_url at the endpoint above.")

        elif args.command == "server":


            if args.server_command == "start":
                cls._refuse_during_night_run("server start")
                cls.display_header()
                # An explicit --model wins; otherwise the configured main model serves, so
                # `main-model set` and `server start` can never disagree again.
                from_config = not args.model
                args.model = args.model or config.model
                # A model a release removed would otherwise be taken for a HuggingFace repository
                # name and fail at download, after the host checks; say so before anything starts.
                if ModelMatrixRegistry.removed_in(args.model):
                    print(f"❌ {ModelMatrixRegistry.removed_message(args.model, configured=from_config)}")
                    sys.exit(1)
                vllm_mgr = VLLMServerManager(host=f"http://localhost:{args.port}")
                # A machine that loads a model is a node; an advertised one says `loading`.
                from dreamference.node import NodeAdvertiser
                NodeAdvertiser.on_server_starting(args.model, args.port)
                # On a node the Google service runs by default, so /apps can connect accounts
                # (specs/DREAMFERENCE_MIGHTLING_APPS.md §5.1); its failure never blocks the model.
                from dreamference.chat.google_service import GoogleService
                if GoogleService.ensure_on_node() is False:
                    print(f"⚠️  {GoogleService.problem}")

                # The diffusion sidecar starts *before* the vLLM launch on purpose: vLLM's
                # pre-flight reads current free memory, so a sidecar already resident is
                # accounted for — the reverse order lets a marginal KV check pass and then lose
                # the sidecar's memory mid-load. Its failure never blocks the main model.
                diffusion_mgr = None
                if not ModelMatrixRegistry.diffusion_enabled():
                    # Switched off: a sidecar an older Mightling left running (it restarts at boot)
                    # goes before the pre-flight reads free memory, and nothing is said.
                    DiffusionServerManager.remove_leftover(args.diffusion_port)
                elif not args.no_diffusion:
                    diffusion_model = args.diffusion_model or config.diffusion_model
                    diffusion_mgr = DiffusionServerManager(host=f"http://localhost:{args.diffusion_port}")
                    try:
                        # A False return is a docker failure already reported by the manager;
                        # nulling the handle here is what keeps the post-launch health poll from
                        # spending 30 seconds on a container that never existed.
                        if not diffusion_mgr.start_server(
                            model=diffusion_model,
                            main_model=args.model,
                            port=args.diffusion_port,
                            hf_token=config.hf_token,
                        ):
                            diffusion_mgr = None
                    except Exception as exc:
                        print(f"⚠️  Diffusion sidecar failed to start: {exc}")
                        diffusion_mgr = None
            
                # Start monitoring thread before server launch
                from dreamference.hardware import get_model_launch_overrides
                recipe_env_keys = set(get_model_launch_overrides(args.model).get("env", {}).keys())
                monitor = create_model_loading_monitor(vllm_mgr, recipe_env_keys=recipe_env_keys)
                monitor.start()
            
                print(f"📊 Model Loading Monitor: Tracking initialization progress for '{args.model}'...\n")
            
                try:
                    # Start vLLM server in background to allow progress monitoring
                    vllm_mgr.start_server(
                        model=args.model,
                        port=args.port,
                        quantization=args.quantization,
                        draft_model=args.draft_model,
                        # The config has resolved flag > env > file > default; the raw flag is None
                        # when omitted, which used to reach the command line as "None".
                        num_speculative_tokens=config.num_speculative_tokens,
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
                        guided_decoding_backend=args.guided_decoding_backend or config.guided_decoding_backend,
                        use_tensorizer=getattr(args, "tensorize", None),
                        background=True,
                        docker_image=getattr(args, "docker_image", None)
                    )
                
                    # Print progress while server is initializing
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
                        NodeAdvertiser.on_server_ready()
                        # One discarded request first: a fresh engine serves its first batch at
                        # about two-thirds speed, and that should not be the user's request.
                        print("🔥 Warming up with one throwaway request...")
                        warm_up_seconds = vllm_mgr.warm_up()
                        if warm_up_seconds is None:
                            print("⚠️  Warm-up request failed; the first real request may be slower.")
                        else:
                            print(f"   Warm-up done in {warm_up_seconds:.1f}s")
                        print(f"\n✅ Server Ready! API running at {vllm_mgr.host}")
                        print(f"   Model: {args.model}")
                        print(f"   Loaded in: {monitor.get_status()['elapsed_seconds']:.1f} seconds\n")

                        if "nvfp4" in args.model.lower():
                            print("🧪 Running NVFP4 kernel backend canary test...")
                            try:
                                import requests
                                from dreamference.hardware import resolve_model_hf_repo
                                served_model = resolve_model_hf_repo(args.model)
                                # Default sampling on purpose: on 2026-09-29 this canary caught
                                # SGLang's untruncated-sampling kernel emitting token 0 ('!') on
                                # the completions path, which greedy decoding never reaches. The
                                # first completions request after a boot can take well over 10 s.
                                resp = requests.post(f"{vllm_mgr.host}/v1/completions", json={
                                    "model": served_model,
                                    "prompt": "Hello",
                                    "max_tokens": 10
                                }, timeout=60)
                                if resp.status_code == 200:
                                    text = resp.json()["choices"][0]["text"].strip()
                                    if text and all(c == "!" for c in text if c.strip()):
                                        print("❌ NVFP4 Canary Failed: Output corrupted (all '!'). Wrong SM12x CUTLASS backend selected.")
                                    else:
                                        print("✅ NVFP4 Canary Passed: Output is healthy.")
                                else:
                                    print(f"⚠️  NVFP4 Canary skipped: API returned {resp.status_code}")
                            except Exception as e:
                                print(f"⚠️  NVFP4 Canary failed to execute: {e}")

                        # By now the sidecar's 0.6B load has usually finished under the main
                        # model's. A brief poll reports its state either way — its /health is the
                        # only place a transformers-version mismatch with the checkpoint's remote
                        # code surfaces — but never fails the start: the model keeps loading in
                        # the background and the endpoint comes up when it does.
                        if diffusion_mgr is not None:
                            import time as _time
                            deadline = _time.time() + 30
                            while _time.time() < deadline and not diffusion_mgr.check_health():
                                _time.sleep(2)
                            state = diffusion_mgr.get_load_state()
                            if state == "ok":
                                print(f"🌫️  Diffusion model ready at {diffusion_mgr.host}/v1")
                            elif state not in ("loading", None):
                                # The load failed and will not recover by waiting; this used to
                                # be reported as "still loading" beside the error text.
                                print(f"⚠️  Diffusion sidecar at {diffusion_mgr.host}/v1 failed to load: {state}\n"
                                      f"   The main model is unaffected. Details: docker logs dreamference-diffusion-{args.diffusion_port}")
                            else:
                                print(f"🌫️  Diffusion sidecar at {diffusion_mgr.host}/v1 — "
                                      f"still loading in background (state: {state})")

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
                    # Whatever ended the start without a ready server (a refused pre-flight, a
                    # docker failure, Ctrl-C): an advertised node must not go on saying `loading`,
                    # or clients wait ten minutes for a model that is not coming.
                    if not monitor.server_ready:
                        NodeAdvertiser.on_server_stopped()
            if args.server_command == "stop":
                cls.display_header()
                vllm_mgr = VLLMServerManager(host=f"http://localhost:{args.port}")
                vllm_mgr.stop_server(port=args.port)
                from dreamference.node import NodeAdvertiser
                NodeAdvertiser.on_server_stopped()
                if ModelMatrixRegistry.diffusion_enabled():
                    DiffusionServerManager(host=f"http://localhost:{args.diffusion_port}").stop_server(port=args.diffusion_port)
                else:
                    DiffusionServerManager.remove_leftover(args.diffusion_port)
            elif args.server_command == "remove":
                cls.display_header()
                vllm_mgr = VLLMServerManager(host=f"http://localhost:{args.port}")
                vllm_mgr.remove_server(port=args.port)
                from dreamference.node import NodeAdvertiser
                NodeAdvertiser.on_server_stopped()
                if ModelMatrixRegistry.diffusion_enabled():
                    DiffusionServerManager(host=f"http://localhost:{args.diffusion_port}").remove_server(port=args.diffusion_port)
                else:
                    DiffusionServerManager.remove_leftover(args.diffusion_port)
            elif args.server_command == "logs":
                cls.display_header()
                vllm_mgr = VLLMServerManager(host=f"http://localhost:{args.port}")
                try:
                    vllm_mgr.show_request_logs(port=args.port)
                except KeyboardInterrupt:
                    print("\nStopped tailing logs.")

        elif args.command == "audit":
            from dreamference.audit import EgressAudit
            if args.audit_command == "egress":
                # 0 on a pass, 1 on an unexpected destination, 2 when the trace itself failed.
                sys.exit(EgressAudit.run(prompt=args.prompt, write_json=args.json, tui=args.tui, app=args.app, web=args.web))
            print("usage: ling-admin audit {egress}")
            sys.exit(2)

        elif args.command == "swe-bench":
            from dreamference.swe_bench.swe_bench_command import SweBenchCommand
            sys.exit(SweBenchCommand.dispatch(args))

        elif args.command == "node":
            from dreamference.node import NodeAdvertiser
            if args.node_command == "enable":
                sys.exit(0 if NodeAdvertiser.enable(no_web=args.no_web) else 1)
            if args.node_command == "disable":
                sys.exit(0 if NodeAdvertiser.disable() else 1)
            if args.node_command == "status" and not args.name:
                print(NodeAdvertiser.status())
                sys.exit(0)
            if args.node_command == "id":
                from dreamference.node import NodeIdentity
                print(NodeIdentity.ensure())
                sys.exit(0)
            from dreamference.node import NodePairing, NodeRemote, NodeServe
            if args.node_command == "status":
                sys.exit(NodeRemote.status(args.name))
            if args.node_command == "list":
                print("\n".join(NodeRemote.list_lines()))
                sys.exit(0)
            if args.node_command == "add":
                sys.exit(0 if NodePairing.add(args.name, user=args.user, ssh_port=args.ssh_port) else 1)
            if args.node_command == "remove":
                sys.exit(0 if NodePairing.remove(args.name) else 1)
            if args.node_command == "set":
                sys.exit(NodeRemote.set_model(args.name, args.model))
            if args.node_command == "start":
                sys.exit(NodeRemote.start(args.name))
            if args.node_command == "stop":
                sys.exit(NodeRemote.stop(args.name))
            if args.node_command == "sync-model":
                from dreamference.node import NodeModelSync
                sys.exit(NodeModelSync.sync(args.name, args.model, address=args.address))
            if args.node_command in ("run", "jobs", "logs", "cancel", "fetch", "job-exec"):
                from dreamference.node import NodeJob, NodeJobSender
                if args.node_command == "run":
                    # argparse hands everything after the node's name to the command, options
                    # included, so the options written after the name are read here.
                    options, job_command = NodeJobSender.split_run_arguments(args.job_command)
                    sys.exit(NodeJobSender.run(args.name, job_command,
                                               memory=options.memory or args.memory,
                                               time_limit=options.time or args.time,
                                               test=options.test or args.test, gpu=options.gpu or args.gpu,
                                               setup=options.setup or args.setup, out=options.out or args.out,
                                               binds=(args.bind or []) + options.bind))
                if args.node_command == "jobs":
                    sys.exit(NodeJobSender.jobs(args.name))
                if args.node_command == "logs":
                    sys.exit(NodeJobSender.logs(args.job))
                if args.node_command == "cancel":
                    sys.exit(NodeJobSender.cancel(args.job))
                if args.node_command == "fetch":
                    sys.exit(NodeJobSender.fetch(args.job))
                sys.exit(NodeJob.execute(args.job))
            if args.node_command == "provision":
                from dreamference.node.node_provisioner import NodeProvisioner
                options = {name: getattr(args, name) for name in (
                    "all", "user", "model", "source", "per_host_password", "mesh", "web", "no_start",
                    "restart", "os_update", "dry_run", "via", "match", "start_timeout")}
                sys.exit(NodeProvisioner(args.hosts, options).run())
            if args.node_command == "prepare":
                from dreamference.node.node_prepare import NodePrepare
                sys.exit(0 if NodePrepare.run() else 1)
            if args.node_command == "askpass":
                from dreamference.node.fleet_askpass import FleetAskpass
                sys.exit(FleetAskpass.ask())
            if args.node_command == "authorize":
                sys.exit(0 if NodeServe.authorize(sys.stdin.read()) else 1)
            if args.node_command == "serve-job":
                sys.exit(NodeServe.serve(os.environ.get("SSH_ORIGINAL_COMMAND"), key_tag=args.key))
            print("usage: ling-admin node {enable,disable,status,list,add,remove,set,start,stop,sync-model,run,jobs,logs,cancel,fetch}")
            sys.exit(2)

        elif args.command == "host":
            from dreamference.vllm_server import HostSafetySetup
            if args.host_command == "check":
                sys.exit(0 if HostSafetySetup.check() else 1)
            if args.host_command == "setup":
                sys.exit(0 if HostSafetySetup.setup() else 1)
            print("usage: ling-admin host {check,setup}")
            sys.exit(2)

        elif args.command == "night":
            from dreamference.night_shift import NightShiftRunner, NightShiftScheduler, NightShiftSettings
            if args.night_command == "enable":
                sys.exit(0 if NightShiftScheduler.enable(args.window or NightShiftSettings().window) else 1)
            if args.night_command == "disable":
                sys.exit(0 if NightShiftScheduler.disable() else 1)
            if args.night_command == "status":
                print(NightShiftScheduler.status())
                sys.exit(0)
            if args.night_command == "run":
                sys.exit(NightShiftRunner.run(until=args.until, minutes=args.minutes,
                                              idle_minutes=args.idle_minutes,
                                              ignore_sessions=args.ignore_open_sessions))
            print("usage: ling-admin night {enable,disable,status,run}")
            sys.exit(2)

        elif args.command == "code":
            from dreamference.cli.code_index_setup import CodeIndexSetup
            if args.code_command == "setup":
                sys.exit(0 if CodeIndexSetup.install() else 1)
            print("usage: ling-admin code setup")
            sys.exit(2)

        elif args.command == "codex":
            import subprocess
            from dreamference.runner.codex_branded_builder import CodexBrandedBuilder
            if args.codex_command == "build":
                cls._refuse_during_night_run("codex build")
                # Only a node has Python, so building here marks the machine as one: the launcher
                # then uses the local model server and never browses for another.
                from dreamference.node import NodeIdentity
                NodeIdentity.ensure()
                # Asked before the build: afterwards a build that compiled and one that found
                # nothing to do both report success.
                was_current = CodexBrandedBuilder.is_current()
                built = CodexBrandedBuilder.build(force=args.force)
                if built and not args.no_audit and (args.force or not was_current):
                    # A new binary is when a new network channel would appear. The verdict is
                    # printed and recorded; it never changes the build's exit code.
                    from dreamference.audit import EgressAudit
                    EgressAudit.after_build()
                sys.exit(0 if built else 1)
            if args.codex_command == "test":
                from dreamference.runner.codex_test_runner import CodexTestRunner
                sys.exit(CodexTestRunner.run(user_filter=args.filter, test_threads=args.test_threads,
                                             jobs=args.jobs, memory_max=args.memory_max,
                                             accept_snapshots=args.accept_snapshots))
            # The branded build only -- never an upstream `codex` from PATH.
            if not CodexInstaller.install_if_missing():
                sys.exit(1)
            codex_bin = CodexInstaller.get_codex_executable()
            if args.codex_command == "start":
                print("🚀 Starting ling's app-server daemon...")
                subprocess.Popen(
                    [codex_bin, "app-server", "daemon", "start"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                print("✅ ling's app-server daemon started.")
            elif args.codex_command == "stop":
                print("🛑 Stopping ling's app-server daemon...")
                subprocess.call([codex_bin, "app-server", "daemon", "stop"])
                print("✅ ling's app-server daemon stopped.")

        elif args.command == "logs":
            cls.display_header()
            vllm_mgr = VLLMServerManager(host=f"http://localhost:{args.port}")
            try:
                vllm_mgr.show_request_logs(port=args.port)
            except KeyboardInterrupt:
                print("\nStopped tailing logs.")

        elif args.command == "benchmark_server":
            cls.display_header()
            import subprocess
            from dreamference.hardware import resolve_model_hf_repo
            hf_repo = resolve_model_hf_repo(args.model)
            container = f"dreamference-vllm-{args.port}"

            # The dataset used to be read from a path baked into one image's layout, which broke
            # the moment a model pinned a different image (the DFlash entry keeps it under
            # /vllm-workspace, the project image under /opt/vllm). Rather than guess where each
            # image hides it, fetch the canonical copy from vLLM upstream and hand it to whatever
            # container is running. One source of truth, no per-image knowledge.
            dataset_path = args.dataset_path
            if dataset_path is None:
                dataset_path = cls._provision_sonnet_dataset(container)
                if dataset_path is None:
                    sys.exit(1)

            # -t only when there is a terminal to attach to, so this stays runnable from a script.
            exec_flags = ["-i", "-t"] if sys.stdout.isatty() else ["-i"]

            cmd = [
                "docker", "exec", *exec_flags, container,
                "vllm", "bench", "serve",
                "--backend", "openai-chat",
                "--base-url", f"http://localhost:{args.port}",
                "--endpoint", "/v1/chat/completions",
                "--model", hf_repo,
                "--dataset-name", "sonnet",
                "--dataset-path", dataset_path,
                "--sonnet-input-len", "4000",
                "--sonnet-prefix-len", "2000",
                "--sonnet-output-len", "512",
                "--num-prompts", str(args.num_prompts),
                "--max-concurrency", str(args.max_concurrency),
                "--temperature", "0",
                "--ignore-eos"
            ]
            
            print(f"🚀 Running benchmark on {hf_repo} via {dataset_path}...")
            try:
                subprocess.run(cmd, check=True)
            except subprocess.CalledProcessError as e:
                print(f"❌ Benchmark failed: {e}")
            except KeyboardInterrupt:
                print("\n⏹️  Benchmark cancelled.")

        elif args.command == "index":
            cls._refuse_during_night_run("index")
            cls.display_header()
            target_dir = args.dir or os.getcwd()
            from dreamference.context_engine import ContextEngine
            ctx_engine = ContextEngine(workspace_root=target_dir)
            with console.status("[bold green]Indexing workspace AST & vectors...[/bold green]"):
                summary = ctx_engine.index_workspace(force_reindex=args.force)
            console.print(f"[bold green]✅ Workspace Indexed![/bold green]")
            console.print(f"   Files Indexed: {summary['total_indexed_files']}")
            console.print(f"   AST Symbols:   {summary['total_ast_symbols']}")

        elif args.command == "searxng":
            if args.searxng_command == "start":
                from dreamference.chat.searxng_sidecar import SEARXNG_HOST_PORT, SearxngSidecar
                if not SearxngSidecar.start():
                    print("❌ SearXNG did not start.")
                    sys.exit(1)
                print(f"✅ SearXNG is running on http://127.0.0.1:{SEARXNG_HOST_PORT}")
                # An advertised node offers it to clients from now on.
                from dreamference.node import NodeAdvertiser
                NodeAdvertiser.on_searxng_started()
                sys.exit(0)
            print("usage: ling-admin searxng {start}")
            sys.exit(2)

        elif args.command == "google":
            cls._run_google(args.google_command)

        elif args.command == "web":
            cls.display_header()
            # Lazy for the same reason as the context engine: web_canvas imports it, and
            # `ling-admin web` is the only subcommand that needs either.
            from dreamference.web_canvas import start_web_canvas_server

            start_web_canvas_server(port=args.port, daemon=False)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                console.print("\n[yellow]Stopping Web Canvas UI...[/yellow]")

def main(argv: Optional[List[str]] = None) -> None:
    """
    Standalone CLI main function.

    `ling` is not defined here: it is the Rust binary built from the codex submodule, with the
    session setup this package used to do compiled into it (see `ling-rs/`).

    Args:
        argv (Optional[List[str]]): Arguments to parse; None reads `sys.argv`.
    """
    try:
        DreamferenceCLIController.run_cli(argv)
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)
