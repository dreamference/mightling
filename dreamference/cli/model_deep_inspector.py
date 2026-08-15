"""
Deep model inspection for Dreamference.

`dream main-model inspect` answers "is the server up and behaving"; this answers "what is actually
running, and are its parameters the right ones". The two are separated because everything here
costs real time — the workload profile sends live requests, and the rest reads the checkpoint and
the container's startup log.

Each probe exists because a specific thing went wrong or stayed invisible on this hardware:

* Acceptance is reported per workload because one blended figure is actively misleading on a
  speculative server. The same box measured 86% on code and 26% across a prose benchmark, and
  `num_speculative_tokens` is tuned against whichever number you happen to look at.
* The quantization map exists because "INT4 checkpoint" is not what this checkpoint is.
* KV geometry exists because the hybrid attention/linear-attention split is what makes the
  drafter's pages disagree with the target's, which is the failure that blocked the first two
  load attempts.
* Sampling provenance exists because the values that reach the model come from four places and
  only one of them is visible in the launch command.
* Graph coverage exists because a cold compile is 8-12 minutes and the captured batch sizes bound
  any concurrency change.
"""

import json
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

# Bytes per element for the KV cache dtypes this project serves.
KV_DTYPE_BYTES: Dict[str, int] = {"auto": 2, "bfloat16": 2, "float16": 2, "fp8": 1, "fp8_e4m3": 1}

# Probe suite for per-workload acceptance. Deliberately small and fixed: the point is a comparable
# reading per class, not a benchmark. Categories match the split upstream measured DFlash against.
WORKLOAD_PROBES: Tuple[Tuple[str, str], ...] = (
    ("code", "Write a Python function that merges two sorted lists. Code only."),
    ("code", "Write a Python binary search over a sorted list. Code only."),
    ("structured", "Return a JSON array of 4 users with name, age and email. JSON only."),
    ("prose", "Write a paragraph about the history of the bicycle."),
    ("repetitive", "Count from 1 to 60, separated by commas."),
)

# Acceptance below this at a given draft slot means the slot is costing more verification than it
# returns. Used only to suggest a window size, never to change one.
SLOT_USEFUL_THRESHOLD: float = 0.5


class ModelDeepInspector:
    """
    Reports how the served model is actually implemented and parameterised.

    A pure classmethod namespace, like the other inspection helpers: there is no state worth
    keeping between probes, and every method is independently useful.
    """

    @classmethod
    def _snapshot_config(cls, model_alias: str) -> Optional[Dict[str, Any]]:
        """
        Loads the served checkpoint's config.json from the local HuggingFace snapshot.

        Args:
            model_alias (str): Model alias or repo ID.

        Returns:
            Optional[Dict[str, Any]]: Parsed config, or None if the model is not downloaded.
        """
        from dreamference.hardware import ModelDownloader

        try:
            snapshot = ModelDownloader.get_model_snapshot_dir(model_alias)
        except Exception:
            return None
        if not snapshot:
            return None
        try:
            with open(snapshot / "config.json") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    @classmethod
    def _snapshot_file(cls, model_alias: str, filename: str) -> Optional[Dict[str, Any]]:
        """
        Loads an arbitrary JSON file from a model's local snapshot.

        Args:
            model_alias (str): Model alias or repo ID.
            filename (str): File to read, e.g. 'generation_config.json'.

        Returns:
            Optional[Dict[str, Any]]: Parsed JSON, or None if absent.
        """
        from dreamference.hardware import ModelDownloader

        try:
            snapshot = ModelDownloader.get_model_snapshot_dir(model_alias)
            if not snapshot:
                return None
            with open(snapshot / filename) as handle:
                return json.load(handle)
        except (OSError, ValueError, Exception):
            return None

    @classmethod
    def _container_log(cls, container: str) -> str:
        """
        Returns the container's full log, which is where vLLM records its startup decisions.

        Args:
            container (str): Container name.

        Returns:
            str: Combined stdout and stderr, empty if unavailable.
        """
        try:
            result = subprocess.run(
                ["docker", "logs", container], capture_output=True, text=True, timeout=60
            )
        except (subprocess.SubprocessError, OSError):
            return ""
        return (result.stdout or "") + (result.stderr or "")

    @classmethod
    def _spec_counters(cls, vllm_host: str) -> Optional[Dict[str, Any]]:
        """
        Snapshots the speculative-decoding counters, including the per-slot breakdown.

        Args:
            vllm_host (str): Base URL of the running server.

        Returns:
            Optional[Dict[str, Any]]: {'drafts', 'drafted', 'accepted', 'per_pos'}, or None.
        """
        try:
            import requests

            response = requests.get(f"{vllm_host}/metrics", timeout=5)
            if response.status_code != 200:
                return None
            text = response.text
        except Exception:
            return None

        def counter(name: str, label: str = "") -> Optional[float]:
            match = re.search(
                rf'^vllm:{name}\{{[^}}]*{label}[^}}]*\}}\s+([0-9.e+]+)$', text, re.M
            )
            return float(match.group(1)) if match else None

        drafts = counter("spec_decode_num_drafts_total")
        drafted = counter("spec_decode_num_draft_tokens_total")
        accepted = counter("spec_decode_num_accepted_tokens_total")
        if drafts is None or drafted is None or accepted is None:
            return None

        per_pos: List[float] = []
        for position in range(64):
            value = counter(
                "spec_decode_num_accepted_tokens_per_pos_total", f'position="{position}"'
            )
            if value is None:
                break
            per_pos.append(value)

        return {"drafts": drafts, "drafted": drafted, "accepted": accepted, "per_pos": per_pos}

    @classmethod
    def profile_acceptance_by_workload(
        cls, vllm_host: str, api_base: str, model_name: str
    ) -> List[Dict[str, Any]]:
        """
        Measures speculative acceptance separately for each class of work.

        vLLM's counters are global and cumulative, so a per-workload figure has to be taken as a
        delta around a controlled request. That is the whole method: snapshot, send one prompt,
        snapshot again, attribute the difference.

        The suggested window size follows from the per-slot acceptance inside that delta. A slot
        accepted less than half the time is costing more verification than it returns, so the
        suggestion is the number of slots that clear that bar — which is why the answer differs by
        workload, and why a single global `num_speculative_tokens` cannot be right for all of them.

        Args:
            vllm_host (str): Base URL, used for /metrics.
            api_base (str): Chat-completions endpoint.
            model_name (str): Model ID as advertised by the server.

        Returns:
            List[Dict[str, Any]]: One row per probe with category, acceptance, tau and suggestion.
        """
        import requests

        rows: List[Dict[str, Any]] = []
        for category, prompt in WORKLOAD_PROBES:
            before = cls._spec_counters(vllm_host)
            if before is None:
                return []
            try:
                requests.post(
                    api_base,
                    json={
                        "model": model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 200,
                        "temperature": 0,
                    },
                    timeout=120,
                )
            except Exception:
                continue
            after = cls._spec_counters(vllm_host)
            if after is None:
                continue

            drafts = after["drafts"] - before["drafts"]
            drafted = after["drafted"] - before["drafted"]
            accepted = after["accepted"] - before["accepted"]
            if drafts <= 0 or drafted <= 0:
                continue

            slots = [
                (a - b) / drafts
                for a, b in zip(after["per_pos"], before["per_pos"])
            ]
            useful = sum(1 for rate in slots if rate >= SLOT_USEFUL_THRESHOLD)
            rows.append({
                "category": category,
                "acceptance": 100 * accepted / drafted,
                "tau": 1 + accepted / drafts,
                "steps": int(drafts),
                "slots": slots,
                "suggested_n": max(1, useful),
            })
        return rows

    @classmethod
    def quantization_map(cls, model_alias: str, drafter_repo: Optional[str]) -> Dict[str, str]:
        """
        Reports which parts of the checkpoint are actually quantized, and to what.

        "INT4 checkpoint" understates the situation: AutoRound writes an `extra_config` naming the
        modules it deliberately left alone, and in this checkpoint that is every shared expert in
        every layer, held at 16 bits. The routed experts carry the quantization; the shared path
        does not. That is worth seeing, because it explains both where the memory went and why the
        thread's FP8 shared-expert variant moved the needle so little.

        Args:
            model_alias (str): Target model alias or repo ID.
            drafter_repo (Optional[str]): Speculative drafter repo, if the recipe names one.

        Returns:
            Dict[str, str]: Display-ready rows.
        """
        config = cls._snapshot_config(model_alias)
        if not config:
            return {"Quantization map": "Checkpoint not downloaded"}

        rows: Dict[str, str] = {}
        quant = config.get("quantization_config", {}) or {}
        text = config.get("text_config", {}) or {}

        rows["Method"] = (
            f"{quant.get('quant_method', 'none')} "
            f"({quant.get('bits', '?')}-bit, group_size={quant.get('group_size', '?')}, "
            f"packing={quant.get('packing_format', '?')})"
        )

        extra = quant.get("extra_config", {}) or {}
        by_module: Dict[str, Dict[int, int]] = {}
        for name, spec in extra.items():
            module = name.split(".")[-1]
            bits = spec.get("bits", quant.get("bits"))
            by_module.setdefault(module, {}).setdefault(bits, 0)
            by_module[module][bits] += 1
        if by_module:
            held_back = ", ".join(
                f"{module} x{sum(counts.values())} @ {'/'.join(str(b) for b in counts)}-bit"
                for module, counts in sorted(by_module.items())
            )
            rows["Held at higher precision"] = held_back
            rows["Implication"] = (
                f"{quant.get('bits', '?')}-bit applies to the routed experts only; "
                f"the shared-expert path stays 16-bit across all "
                f"{text.get('num_hidden_layers', '?')} layers"
            )
        else:
            rows["Held at higher precision"] = "None — uniform quantization"

        experts = text.get("num_experts")
        active = text.get("num_experts_per_tok")
        if experts and active:
            rows["MoE"] = (
                f"{experts} experts, {active} active/token "
                f"(intermediate {text.get('moe_intermediate_size', '?')}, "
                f"shared {text.get('shared_expert_intermediate_size', '?')})"
            )

        if drafter_repo:
            drafter = cls._snapshot_config(drafter_repo)
            if drafter:
                dflash = drafter.get("dflash_config", {}) or {}
                rows["Drafter"] = (
                    f"{drafter.get('num_hidden_layers', '?')} layers, "
                    f"hidden {drafter.get('hidden_size', '?')}, "
                    f"block_size {dflash.get('block_size', '?')}"
                )
                layer_ids = dflash.get("target_layer_ids")
                if layer_ids:
                    rows["Drafter taps"] = (
                        f"reads hidden states from target layers {layer_ids} — acceptance depends "
                        f"on how predictable those layers make the next block"
                    )
        return rows

    @classmethod
    def kv_geometry(cls, model_alias: str, container: str, kv_dtype: str) -> Dict[str, str]:
        """
        Breaks the KV cache down by layer type and checks it against what the engine built.

        This model interleaves full attention with linear (GDN) attention, and only the full
        attention layers hold a per-token KV cache. That split is not cosmetic — it is the reason
        the drafter's attention pages and the target's hybrid pages disagree, which is the assert
        that blocked the first two load attempts on this machine.

        Args:
            model_alias (str): Target model alias or repo ID.
            container (str): Running container, whose log carries the realised pool size.
            kv_dtype (str): KV cache dtype the server was launched with.

        Returns:
            Dict[str, str]: Display-ready rows.
        """
        config = cls._snapshot_config(model_alias)
        if not config:
            return {}

        text = config.get("text_config", {}) or {}
        layer_types = text.get("layer_types") or []
        if not layer_types:
            return {}

        full = sum(1 for t in layer_types if t == "full_attention")
        linear = len(layer_types) - full
        kv_heads = text.get("num_key_value_heads") or 0
        head_dim = text.get("head_dim") or 0
        element = KV_DTYPE_BYTES.get(kv_dtype, 2)

        rows: Dict[str, str] = {
            "Layer mix": (
                f"{full} full-attention + {linear} linear-attention (GDN) of "
                f"{len(layer_types)} total"
            )
        }

        if kv_heads and head_dim:
            # K and V, per full-attention layer. Linear-attention layers carry a fixed recurrent
            # state instead, so they contribute nothing that scales with context.
            per_token = kv_heads * head_dim * 2 * element * full
            rows["KV per token"] = (
                f"{per_token / 1024:.1f} KiB  "
                f"({kv_heads} kv-heads x {head_dim} head-dim x 2 x {element}B x {full} layers)"
            )

            log = cls._container_log(container)
            pool = re.search(r"GPU KV cache size:\s*([\d,]+)\s*tokens", log)
            if pool:
                tokens = int(pool.group(1).replace(",", ""))
                rows["Realised pool"] = (
                    f"{tokens:,} tokens = {tokens * per_token / (1024 ** 3):.2f} GiB of KV"
                )
        return rows

    @classmethod
    def sampling_provenance(
        cls, model_alias: str, container: str, running_cmd: List[str]
    ) -> Dict[str, str]:
        """
        Traces the sampling and chat-template values from checkpoint to rendered prompt.

        Four layers can set these — the checkpoint's generation_config, server flags, the chat
        template, and the client's own request — and only the middle one is visible in the launch
        command. The interesting case is silence: a client that sends no temperature inherits the
        checkpoint's, which for this model is 0.6 with top_p 0.95, not the deterministic sampling
        the published throughput numbers were taken under.

        The template claim is checked rather than asserted. `--enable-log-requests` puts the
        rendered prompt in the log, so whether `enable_thinking=false` actually reached the model
        is a matter of looking.

        Args:
            model_alias (str): Target model alias or repo ID.
            container (str): Running container name.
            running_cmd (List[str]): The container's argv.

        Returns:
            Dict[str, str]: Display-ready rows.
        """
        rows: Dict[str, str] = {}

        generation = cls._snapshot_file(model_alias, "generation_config.json") or {}
        if generation:
            sampled = {
                k: generation[k]
                for k in ("do_sample", "temperature", "top_p", "top_k", "repetition_penalty")
                if k in generation
            }
            rows["Checkpoint defaults"] = ", ".join(f"{k}={v}" for k, v in sampled.items())
            if generation.get("temperature") not in (None, 0):
                rows["Inherited if unset"] = (
                    f"a request that omits temperature samples at {generation.get('temperature')} "
                    f"— set it explicitly for reproducible output"
                )

        overrides = [
            arg for arg in running_cmd
            if arg.startswith("--override-generation-config") or arg.startswith("--temperature")
        ]
        rows["Server overrides"] = ", ".join(overrides) if overrides else "None (client decides)"

        template_kwargs = None
        if "--default-chat-template-kwargs" in running_cmd:
            index = running_cmd.index("--default-chat-template-kwargs")
            if index + 1 < len(running_cmd):
                template_kwargs = running_cmd[index + 1]
        rows["Template kwargs"] = template_kwargs or "None"

        log = cls._container_log(container)
        if log:
            if re.search(r"<\|im_start\|>assistant\\n<think>\\n\\n</think>", log):
                rows["Rendered prompt"] = (
                    "[green]verified[/green] — assistant turns open with an empty "
                    "<think></think>, so thinking is genuinely disabled"
                )
            elif re.search(r"<\|im_start\|>assistant\\n<think>", log):
                rows["Rendered prompt"] = (
                    "[yellow]thinking is ON[/yellow] — assistant turns open with an unclosed "
                    "<think>, despite any template kwargs above"
                )
        return rows

    @classmethod
    def graph_coverage(cls, container: str, max_num_seqs: Optional[str]) -> Dict[str, str]:
        """
        Reports what torch.compile and CUDA graph capture actually produced.

        Worth knowing for two reasons: the captured batch sizes bound how far concurrency can rise
        before falling back to eager execution, and the cache directory hash is what decides
        whether the next start pays another 8-12 minute compile.

        Args:
            container (str): Running container name.
            max_num_seqs (Optional[str]): Configured concurrency, to compare against capture sizes.

        Returns:
            Dict[str, str]: Display-ready rows.
        """
        log = cls._container_log(container)
        if not log:
            return {}

        rows: Dict[str, str] = {}

        sizes = re.search(r"'cudagraph_capture_sizes':\s*\[([^\]]*)\]", log)
        if sizes:
            captured = [int(x) for x in re.findall(r"\d+", sizes.group(1))]
            rows["CUDA graphs"] = f"captured for batch sizes {captured}"
            if max_num_seqs and max_num_seqs.isdigit():
                configured = int(max_num_seqs)
                headroom = [s for s in captured if s >= configured]
                rows["Concurrency headroom"] = (
                    f"max-num-seqs={configured}; graphs cover up to {max(captured)} "
                    f"({len(headroom)} capture point(s) at or above the current setting)"
                )

        caches = sorted(set(re.findall(r"torch_compile_cache/([0-9a-f]+)/rank_\d+_\d+/(\w+)", log)))
        if caches:
            digest = caches[0][0]
            graphs = ", ".join(sorted({name for _, name in caches}))
            rows["Compiled graphs"] = f"{graphs} (cache {digest})"
            rows["Cache key"] = (
                "hashes engine config, traced sources and compiler — but not "
                "num_speculative_tokens, which _reset_stale_compile_cache covers separately"
            )

        compile_time = re.search(r"torch\.compile takes ([\d.]+) s", log)
        if compile_time:
            rows["Compile time"] = f"{float(compile_time.group(1)):.1f}s on the last cold start"
        return rows
