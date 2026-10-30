"""
Model Matrix Registry for Dreamference.

This module provides the ModelMatrixRegistry class which acts as the single source of truth
for supported LLMs, speculative decoding draft models, and short alias resolution.
"""

import copy
from typing import Any, Dict, Optional, Final
from dreamference.hardware.model_spec import ModelSpec

# Quantization formats that a checkpoint declares in its own config.json. vLLM auto-detects these,
# and passing an explicit --quantization alongside them fights that detection.
#
# 'AUTOROUND-INT4' is spelled out rather than folded into a bare 'INT4' because the two mean
# different things in this table: several entries list 'INT4' to say "could be served in INT4",
# which declares nothing, while an AutoRound checkpoint ships
# quantization_config.quant_method='auto-round' in config.json and needs no flag.
SELF_DECLARING_PRECISIONS: Final[frozenset] = frozenset(
    {"NVFP4", "MXFP4", "AWQ", "GPTQ", "AUTOROUND-INT4"}
)

# Single source of truth for the model Dreamference serves when nothing else is specified. Imported by
# the config layer and the vLLM launcher so the two cannot drift apart.
#
# qwen3.8-27b-nvfp4-dflash2 since 2026-09-29, on SGLang: decode ties the 122B on prose and code
# (24.1 / 47.5 against 23.8 / 49.9 tok/s) and beats it on JSON (82.5 against 53.1), the live
# slash-command suite passes as it did, a real ling task finished in 10 s against 34, context is
# 262K against 32K, and ~29 GB more host memory stays free while serving. The 122B recipes on vLLM
# (hybrid-dflash, the default from 2026-08-23, and int4-dflash) and the 35B were removed on
# 2026-10-07: Mightling serves this one model.
DEFAULT_MODEL_ALIAS: Final[str] = "qwen3.8-27b-nvfp4-dflash2"

# Models Mightling served once and removed, keyed by every name an older configuration could hold for
# them (alias, HuggingFace repository, display name, all lowercase), each naming the release that
# removed it. Unknown keys are otherwise taken as raw HuggingFace repositories, so without this a
# 1.4.1 dreamference.toml naming a removed alias was accepted by `main-model set`, and `model
# download` and `server start` tried to fetch a repository of that name.
REMOVED_MODELS: Final[Dict[str, str]] = {
    name: "1.5.1"
    for name in (
        "qwen3.5-122b-a10b-hybrid-dflash",
        "qwen 3.5 122b-a10b (int4+fp8 hybrid + dflash + dense-bandwidth stack)",
        "qwen3.5-122b-a10b-int4-dflash",
        "qwen 3.5 122b-a10b (int4 autoround + dflash)",
        "intel/qwen3.5-122b-a10b-int4-autoround",
        "qwen3.5-122b-a10b-nvfp4",
        "qwen 3.5 122b-a10b (nvfp4)",
        "nvidia/qwen3.5-122b-a10b-nvfp4",
        "qwen3.6-35b-a3b-nvfp4",
        "qwen 3.6 35b-a3b (nvfp4)",
        "nvidia/qwen3.6-35b-a3b-nvfp4",
        "qwen3.5-122b-a10b-dflash-draft",
        "qwen 3.5 122b-a10b dflash drafter (draft model)",
        "z-lab/qwen3.5-122b-a10b-dflash",
    )
}

# The diffusion model served beside the main one. A separate default rather than a mode of the
# main model, because the two run in parallel: every configuration names both, and `ling-admin server
# start` launches both.
DEFAULT_DIFFUSION_MODEL_ALIAS: Final[str] = "tiny-a2d-coder-0.5b-diffusion"

# Whether Mightling uses a diffusion model at all. Off since 2026-10-03: the only one that fits beside
# the main model (Tiny-A2D 0.5B) was measured unusable in every role tried (FAST_TOOLS §1,
# COMPACTION §9.3), so it cost memory and a download for nothing. Off means it is never started
# or downloaded, a leftover container is removed, and nothing names it to the user: no
# `diffusion-model` command, no `server start` flags, no endpoint row, no entry in `model list`.
# The code stays, for a capable diffusion model later (FAST_TOOLS §3); setting this to True
# restores all of it.
DIFFUSION_ENABLED: Final[bool] = False

# The production recipe: the SGLang launch of qwen3.8-27b-nvfp4-dflash2. A module constant so an
# entry measured against it (night 2's all-NVFP4 checkpoint, specs/DREAMFERENCE_MODELS.md §2.2)
# can take it whole and change only the checkpoint, which is what makes that A/B fair.
# An adapter's model id is its base's plus this and its name (`RadixArk/Qwen3.8-27B-NVFP4+rust`).
ADAPTER_SEPARATOR: Final[str] = "+"

# The pinned SGLang image (v0.5.19 until 1.6.1, v0.5.21 since 2026-10-14) and its /v1/responses
# source, by the sha256 of the image's own copy. A runtime patch names all three, so a new image
# serves nothing patched until each patch has been checked against its file again.
QWEN38_SGLANG_IMAGE: Final[str] = (
    "lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469"
)
SGLANG_SERVING_RESPONSES: Final[str] = (
    "/sgl-workspace/sglang/python/sglang/srt/entrypoints/openai/serving_responses.py"
)
SGLANG_SERVING_RESPONSES_SHA256: Final[str] = (
    "ed490a69c960cafa7fc97129e585d176993fb63dbda66468ac9b86770813a00d"
)

# SGLang v0.5.21's /v1/responses tags the text a model writes before a tool call
# `phase: "commentary"`, and Codex sends that item back in every later request. Rebuilding the chat
# history, the endpoint merges consecutive assistant items only when their phases are equal; a
# function_call carries no phase, so the text and its call never merge and render as two assistant
# turns, the first one ending after the announcement. The model copies the pattern and ends its
# turn after "Let me look at X." (57 early stops on nights 12 and 15; v0.5.19 tagged no phase and
# nights 4-10 had none). The merge ignores the phase, as ling-engine's does. Mounted on every
# launch of the pinned image; a file or anchor that does not match stops the start.
SGLANG_PHASE_MERGE_PATCH: Final[dict[str, Any]] = {
    "path": SGLANG_SERVING_RESPONSES,
    "sha256": SGLANG_SERVING_RESPONSES_SHA256,
    "image": QWEN38_SGLANG_IMAGE,
    "substitutions": [
        [
            '                and merged[-1].get("role") == "assistant"\n'
            '                and merged[-1].get("phase") == msg.get("phase")\n'
            "            ):\n",
            '                and merged[-1].get("role") == "assistant"\n'
            "            ):\n",
        ],
    ],
}

# The anchor of SGLANG_STREAM_PHASE_PATCH: the body of v0.5.21's `responses_stream_generator_non_harmony`
# loop, which holds every event of a message, its text included, until a tool call opens or the
# response ends, to tag the message's phase on the events it sends.
_SGLANG_STREAM_PHASE_HOLD: Final[str] = (
    '            payload = orjson.loads(event.split("data: ", 1)[1])\n'
    '            event_type = payload["type"]\n'
    '            item_type = payload.get("item", {}).get("type")\n'
    '            opens_item = event_type == "response.output_item.added"\n'
    '            if pending or (opens_item and item_type == "message"):\n'
    "                pending.append(payload)\n"
    "                tool_follows = opens_item and item_type in (\n"
    '                    "function_call",\n'
    '                    "custom_tool_call",\n'
    "                )\n"
    "                terminal = event_type in (\n"
    '                    "response.completed",\n'
    '                    "response.incomplete",\n'
    '                    "response.failed",\n'
    "                )\n"
    "                if tool_follows or terminal:\n"
    '                    phase = "commentary" if tool_follows else "final_answer"\n'
    "                    for buffered in pending:\n"
    '                        if buffered.get("item", {}).get("type") == "message":\n'
    '                            buffered["item"]["phase"] = phase\n'
    "                        yield f\"event: {buffered['type']}\\ndata: {orjson.dumps(buffered).decode()}\\n\\n\"\n"
    "                    pending.clear()\n"
    "            else:\n"
    "                yield event\n"
)

# Its replacement: the message's added event and every text delta pass at once; only the three events
# that close a message (output_text.done, content_part.done, output_item.done) wait, for the one event
# after them, which the inner generator yields in the same step: a tool call's output_item.added
# (phase commentary) or anything else (final_answer). Nothing the model wrote is held.
_SGLANG_STREAM_PHASE_STREAM: Final[str] = (
    "            # Mightling: hold only a message's closing events, for one event of lookahead.\n"
    '            payload = orjson.loads(event.split("data: ", 1)[1])\n'
    '            event_type = payload["type"]\n'
    '            item_type = payload.get("item", {}).get("type")\n'
    "            if event_type in (\n"
    '                "response.output_text.done",\n'
    '                "response.content_part.done",\n'
    '            ) or (event_type == "response.output_item.done" and item_type == "message"):\n'
    "                pending.append(payload)\n"
    "                continue\n"
    "            if pending:\n"
    '                tool_follows = event_type == "response.output_item.added" and item_type in (\n'
    '                    "function_call",\n'
    '                    "custom_tool_call",\n'
    "                )\n"
    '                phase = "commentary" if tool_follows else "final_answer"\n'
    "                for buffered in pending:\n"
    '                    if buffered.get("item", {}).get("type") == "message":\n'
    '                        buffered["item"]["phase"] = phase\n'
    "                    yield f\"event: {buffered['type']}\\ndata: {orjson.dumps(buffered).decode()}\\n\\n\"\n"
    "                pending.clear()\n"
    "            yield event\n"
    "        for buffered in pending:\n"
    '            if buffered.get("item", {}).get("type") == "message":\n'
    '                buffered["item"]["phase"] = "final_answer"\n'
    "            yield f\"event: {buffered['type']}\\ndata: {orjson.dumps(buffered).decode()}\\n\\n\"\n"
)

# SGLang v0.5.21's /v1/responses sends no text of a request that offers tools until the turn ends
# (found 2026-10-27 by the messenger live test: 188 text pieces, all at 27.4 s; reasoning streamed).
# The stream wrapper tags each message `commentary` when a tool call follows it and `final_answer`
# otherwise, and to put that tag on every event of the message it held them all, text deltas
# included, until a tool call opened or the response ended. Every `ling` request offers tools, so
# the TUI, `ling web`, the app and the phone bridges showed an answer only when it was complete.
# The patch streams the text and holds only the message's closing events for the next event, which
# decides the phase: Codex reads it from the completed item (output_item.done), so what it records
# and sends back is unchanged. One difference from the image: a message followed by reasoning and
# then a tool call is closed as `final_answer` (the image waited for the call); the history merge
# ignores the phase (SGLANG_PHASE_MERGE_PATCH), so the history the model sees is the same.
SGLANG_STREAM_PHASE_PATCH: Final[dict[str, Any]] = {
    "path": SGLANG_SERVING_RESPONSES,
    "sha256": SGLANG_SERVING_RESPONSES_SHA256,
    "image": QWEN38_SGLANG_IMAGE,
    "substitutions": [[_SGLANG_STREAM_PHASE_HOLD, _SGLANG_STREAM_PHASE_STREAM]],
}

# Model files Dreamference hosts itself, as assets of one release of github.com/dreamference/mightling (a
# pre-release tagged `models-<date>`, never the latest: `releases/latest` stays the newest Mightling), each
# pinned by SHA-256. The download (ReleaseAssetDownloader) writes them into the Hugging Face hub cache as a
# download from the Hub would, the blob named by its SHA-256 and linked from `snapshots/<tag>/`, so the
# launch builders, the memory gates and `node sync-model` (which checks every blob against its name) treat
# them as any pinned checkpoint. The `models--dreamference--...` folder is a name in the cache, not a
# Hugging Face repository.
MODELS_RELEASE_TAG: Final[str] = "models-2026-10-27"
MODELS_RELEASE_URL: Final[str] = (
    f"https://github.com/dreamference/mightling/releases/download/{MODELS_RELEASE_TAG}"
)

# The drafter production speculates against since 1.7.2: the DFlash2 drafter (incoai/Qwen3.8-27B-DFlash2, Apache-2.0)
# in the RTN-calibrated NVFP4 layout of maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal (Apache-2.0) with its 16-value
# blocks' scales searched against the BF16 original (ling-engine BACKLOG 22, bench/quant/scalesearch.py: 27% less
# weight error, accepted per pass x1.055 on SGLang on the replay set, paired by request, the step unchanged), hosted
# as release assets with its model card's attribution. Its revision is the release's tag, the name of its snapshot.
QWEN38_DRAFTER_REPO: Final[str] = "dreamference/qwen3.8-27b-dflash2-nvfp4-scalesearch"
QWEN38_DRAFTER_REVISION: Final[str] = MODELS_RELEASE_TAG

# ling-engine's sparse drafter head (BACKLOG 19): the target LM head's rank-256 factors and the 32K list, built from
# RadixArk/Qwen3.8-27B-NVFP4's head (Apache-2.0), hosted as a release asset beside the drafter. An engine asset:
# fetched, synced and passed (`flag`) only when the served engine is `engine` and its recipe lists `feature` in
# `engine_features` (ling-engine 7417523 and later), so a build without it, or SGLang, never downloads it.
# The first ling-engine commit that reads the sparse head (BACKLOG 19, merged 2026-10-26). A ling-engine recipe lists
# the asset's feature in `engine_features` exactly when its `engine_commit` descends from it; an install has no
# engine history to ask, so tests/test_hardware.py checks that rule against the ling-engine submodule.
LING_ENGINE_SPARSE_HEAD_SINCE: Final[str] = "ec9dce800f986c6fff546fb728b87ebbe16eaa37"

QWEN38_SPARSE_HEAD_ASSET: Final[dict[str, Any]] = {
    "repo": "dreamference/qwen3.8-27b-sparse-head",
    "revision": MODELS_RELEASE_TAG,
    "engine": "ling",
    "feature": "sparse-head",
    "flag": "--sparse-head-dir",
}

# What each release-hosted model's snapshot holds: the file's name in the snapshot, the release asset it is
# fetched from, and the asset's SHA-256 (the release's SHA256SUMS lists the same).
RELEASE_HOSTED_MODELS: Final[dict[str, dict[str, Any]]] = {
    QWEN38_DRAFTER_REPO: {
        "revision": QWEN38_DRAFTER_REVISION,
        "files": {
            "model.safetensors": {
                "url": f"{MODELS_RELEASE_URL}/qwen3.8-27b-dflash2-nvfp4-scalesearch.safetensors",
                "sha256": "5ec78f26b5254d46bd376b2127ce87d6dfb0b6653e01d6f36983cfd3fdc35f4b",
            },
            "config.json": {
                "url": f"{MODELS_RELEASE_URL}/qwen3.8-27b-dflash2-nvfp4-scalesearch.config.json",
                "sha256": "6bf5936c68d432db3c8408d5836dc32e05dc3c4be538eb3e576439186262b186",
            },
            "hf_quant_config.json": {
                "url": f"{MODELS_RELEASE_URL}/qwen3.8-27b-dflash2-nvfp4-scalesearch.hf_quant_config.json",
                "sha256": "7aa6b31ef6cde1cf7c5489f3a4920be2f5ff1cd2a56e9c5ae1096ae7c94a58bb",
            },
        },
    },
    QWEN38_SPARSE_HEAD_ASSET["repo"]: {
        "revision": QWEN38_SPARSE_HEAD_ASSET["revision"],
        "files": {
            "model.safetensors": {
                "url": f"{MODELS_RELEASE_URL}/qwen3.8-27b-sparse-head.safetensors",
                "sha256": "4dda4169bfbefc62082bd46ba21fa90579dd6bd763b92570a52b016b92d1e83a",
            },
        },
    },
}

QWEN38_SGLANG_RECIPE: Final[Dict[str, Any]] = {
    "engine": "sglang",
    "docker_image": QWEN38_SGLANG_IMAGE,
    # Patches of the image's own Python, mounted over it on every launch (SGLangRuntimePatcher).
    "runtime_patches": [SGLANG_PHASE_MERGE_PATCH, SGLANG_STREAM_PHASE_PATCH],
    "revision": "52d1adc5f38aa5ebf099c29ed7025ba34cfbb854",
    "max_model_len": 262144,
    # SGLang's --mem-fraction-static: weights plus KV pool. What lies outside it (CUDA
    # graphs, torch.compile, activations) is why the container needs more headroom
    # than a vLLM arena of the same fraction.
    "gpu_memory_utilization": 0.50,
    "container_headroom_gb": 24.0,
    "tool_call_parser": "qwen3_coder",
    "reasoning_parser": "qwen3",
    "speculative_config": {
        "method": "DFLASH",
        "model": QWEN38_DRAFTER_REPO,
        "revision": QWEN38_DRAFTER_REVISION,
        "num_speculative_tokens": 12,
        "quantization": "modelopt_fp4",
    },
    # The checkpoint's own chat template refused two things our clients send, and
    # reasoned at its most expensive level by default. Applied to a copy at launch
    # (ChatTemplatePatcher); each anchor must match exactly once or the start stops.
    "chat_template_patches": [
        # Codex offers `minimal`/`high` (and `max` exists elsewhere); the template knew
        # only xhigh/medium/low and answered HTTP 400. The default drops from xhigh to
        # medium: hasso5703 measured xhigh at 3.19x medium's thinking tokens and a
        # lower HumanEval (93.9% against 98.2%), five failures being budget
        # truncations. ling itself sends `none` and is unaffected.
        [
            "    {%- set resolved_reasoning_effort = reasoning_effort|default('xhigh') %}\n"
            "    {%- if resolved_reasoning_effort not in ('xhigh', 'medium', 'low') %}",
            "    {%- set resolved_reasoning_effort = reasoning_effort|default('medium') %}\n"
            "    {%- if resolved_reasoning_effort in ('max', 'high') %}\n"
            "        {%- set resolved_reasoning_effort = 'xhigh' %}\n"
            "    {%- elif resolved_reasoning_effort == 'minimal' %}\n"
            "        {%- set resolved_reasoning_effort = 'low' %}\n"
            "    {%- endif %}\n"
            "    {%- if resolved_reasoning_effort not in ('xhigh', 'medium', 'low') %}",
        ],
        # A system message after the first one raised "System message must be at the
        # beginning" on the chat-completions path; it becomes a reminder in the turn.
        [
            "        {%- if not loop.first %}\n"
            "            {{- raise_exception('System message must be at the beginning.') }}\n"
            "        {%- endif %}",
            "        {%- if not loop.first %}\n"
            "            {{- '<|im_start|>user\\n<system-reminder>\\n' + content + "
            "'\\n</system-reminder><|im_end|>\\n' }}\n"
            "        {%- endif %}",
        ],
    ],
    "extra_args": [
        "--attention-backend", "flashinfer",
        # FlashInfer's plain sampling kernel, used only when a request asks for no
        # truncation at all (top_p 1 and no top_k: what the completions endpoint does
        # by default), returned token 0 ('!') for 16 of 16 sampled requests on this
        # GB10; any top_p < 1 or any top_k was clean. PyTorch's sampler: 0 of 16, and
        # no measurable speed cost (greedy 25.5 / 50.3 / 87.0 tok/s prose/code/JSON).
        "--sampling-backend", "pytorch",
        "--chunked-prefill-size", "8192",
        "--disable-prefill-cuda-graph",
        # v0.5.21 split --cuda-graph-max-bs into -decode and -prefill, and rejects the old
        # name as ambiguous (the first start on it restart-looped, 2026-10-14). Prefill
        # graphs are off above, so the decode one is the same setting as before.
        "--cuda-graph-max-bs-decode", "8",
        "--disable-flashinfer-autotune",
        "--mamba-radix-cache-strategy", "extra_buffer",
        "--mamba-ssm-dtype", "bfloat16",
        "--max-mamba-cache-size", "96",
        "--max-running-requests", "8",
        "--enable-torch-compile",
        "--torch-compile-max-bs", "4",
        "--num-continuous-decode-steps", "2",
        # Stops the scheduler busy-polling a CPU core while no request is running,
        # which on a box that is always on is most of the time.
        "--sleep-on-idle",
        "--enable-metrics",
    ],
}

# LoRA adapters on the production model (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §8.2), as the
# probe of 2026-10-19 measured them on the pinned image with DFlash2 (docs/dev, branch
# research/lora-serving-probe): adapters on the FP8 layers, the 16 full-attention q/k/v/o and the
# 48 Gated DeltaNet projections, serve with the production launch unchanged (54-56 against 57.7
# tok/s with an adapter active; a request without one is token for token production's output).
# Adapters on the NVFP4 MLP crash the start under torch.compile and the fused SiLU+FP4 path, so
# they are not served. A key of the default entry only, never of QWEN38_SGLANG_RECIPE: Minima
# spreads that constant, and every one of its layers, the GDN projections included, is NVFP4.
QWEN38_LORA_RECIPE: Final[dict[str, Any]] = {
    # The modules an adapter's adapter_config.json may name, in SGLang's names: the GDN input
    # projection is `in_proj_qkvz` (SGLang stacks q, k, v and z into it and has no rule for the
    # checkpoint's own `in_proj_qkv` + `in_proj_z`).
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "in_proj_qkvz", "out_proj"],
    # The module names its tensors may carry: the GDN input projection split as in_proj_q/k/v/z.
    "weight_modules": [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "in_proj_q", "in_proj_k", "in_proj_v", "in_proj_z", "out_proj",
    ],
    # The pool is preallocated at start for every slot at this rank: about 0.17 GiB per slot on
    # these layers at rank 16, measured. The first adapters are rank 16; a larger one needs a
    # larger ceiling and a restart.
    "max_lora_rank": 16,
    # One slot is the base's: a batch holds the base and three adapters.
    "max_loras_per_batch": 4,
    # Adapters the engine holds loaded at once; the gate unloads the least recently used past it.
    "max_loaded_loras": 8,
    # SGLang v0.5.21's /v1/responses, the only endpoint ling speaks, builds its request without the
    # adapter: it accepted `<base>:<adapter>` and ran the base (the probe). Both places that build
    # one pass `lora_path` as /v1/chat/completions does. Applied to a copy of the image's file and
    # mounted over it together with the recipe's own patch of the same file (SGLangRuntimePatcher);
    # the sha256 is the image's own file, so a new image serves no adapters until the patch is
    # checked against it.
    "runtime_patches": [
        {
            "path": SGLANG_SERVING_RESPONSES,
            "sha256": SGLANG_SERVING_RESPONSES_SHA256,
            "image": QWEN38_SGLANG_IMAGE,
            "substitutions": [
                [
                    "                        require_reasoning=require_reasoning,\n"
                    "                    )\n"
                    "\n"
                    "                    generator = self._generate_with_builtin_tools(",
                    "                        require_reasoning=require_reasoning,\n"
                    "                        lora_path=self._resolve_lora_path(request.model, None),\n"
                    "                    )\n"
                    "\n"
                    "                    generator = self._generate_with_builtin_tools(",
                ],
                [
                    "                require_reasoning=adapted_request.require_reasoning,\n"
                    "            )\n",
                    "                require_reasoning=adapted_request.require_reasoning,\n"
                    "                lora_path=adapted_request.lora_path,\n"
                    "            )\n",
                ],
            ],
        },
    ],
}


# The engines a recipe may name in `engine` (vLLM when it names none).
ENGINE_VLLM: Final[str] = "vllm"
ENGINE_SGLANG: Final[str] = "sglang"
ENGINE_LING: Final[str] = "ling"

# ling-engine (github.com/dreamference/ling-engine), the server written for this one model
# (ling-engine specs/DREAMFERENCE_LING_ENGINE_INTEGRATION.md §12): opt-in for
# qwen3.8-27b-nvfp4-dflash2 in 1.7.2 (`server start --engine ling`, or `server_engine = "ling"` in
# the config), the default since 1.8.0 with four sessions at the full context (the owner's decisions
# of 2026-10-26, 2026-10-28 and 2026-10-29). Image input and LoRA adapters follow in 1.8.1; until
# then they stay on SGLang (`--engine sglang`, which an adapter start picks by itself). Its quality was measured on
# SWE-bench nights 12 and 15 against SGLang and night 17 against SGLang with the phase-merge fix;
# speculation and tree verification are exact, so the drafter moves speed alone.
#
# The commit the image's ling-serve was built from (the full hash, never abbreviated), recorded in
# the image's labels (`org.opencontainers.image.revision`, `ai.dreamference.ling-engine.commit`).
# 1.8.0: 4853a93, ling-engine main after fair pairing (51b7372, `--fair-pairing 1|0`, default 0) and the
# head-major page pool (4853a93, used only when the page pool is on) were merged onto P4d (02704d4: paged
# KV sizing in `--plan-only` and the auto-size; the page pool stays off unless `--kv-pages` is given). The
# recipe passes neither `--fair-pairing` nor `--kv-pages`, so both stay off; the image's binary was built
# from that commit under neutral paths (scripts/build_ling_serve_image.sh), and its greedy output on the
# four flat sessions is identical to 02704d4's image (sha256:f241d647…), which 1.8.0 pinned first.
# Before them, 1.7.2 pinned 8d112d0 (bdda3a2 plus the tokenizer's BPE cache under a shared mutex).
QWEN38_LING_ENGINE_COMMIT: Final[str] = "4853a93a4996339ec1867c68704c761d83d75dd2"

# What the engine at that commit has that an asset or a launch may depend on, so the runtime asks
# a list rather than git: "sparse-head" (the drafter's sparse head, read from a file beside the
# drafter) from ling-engine ec9dce8 on. A checkout-only test keeps the list true to the commit's
# ancestry. Empty at 1.7.2's 8d112d0; "kv-pages" (f180b44) and "sparse-head" (ec9dce8) are both ancestors of 4853a93.
QWEN38_LING_ENGINE_FEATURES: Final[tuple[str, ...]] = ("kv-pages", "sparse-head", "paging-switch", "fair-pairing")

# The last commit of ling-engine's P4b (BATCHING §5.3: `--kv-pages` with the KV page pool, admission by
# pages, reclaim of idle sessions, the last resort), which is what lets four sessions at 262,144 tokens
# fit where the flat KV cannot (~19 GB each). A ling-engine recipe lists "kv-pages" in `engine_features`
# exactly when its `engine_commit` descends from it (tests/test_hardware.py checks that against the
# submodule), and only then does the launch pass `--sessions`/`--batch`, with `--kv-pages` when the
# recipe's `kv_pages` is on (an engine before it exits 2 on `--kv-pages`, and its auto-size is not the
# plan these flags were measured against). P4d's merge keeps the flat slots the default and takes
# `--kv-pages` (and `--no-kv-pages`), so this commit stays the boundary for the flags.
LING_ENGINE_KV_PAGES_SINCE: Final[str] = "f180b44a40369d7dd03f9ae97e7fa8be4ece1380"

# The first ling-engine commits with `--no-kv-pages` (P4d, the pool becoming the engine's default) and with
# `--fair-pairing` (the scheduler's pairing switch). A recipe lists "paging-switch" / "fair-pairing" in
# `engine_features` exactly when its `engine_commit` descends from the one; an engine before either exits 2
# on the flag, so the launch passes it only then.
LING_ENGINE_PAGING_SWITCH_SINCE: Final[str] = "c818a28de1f2e8a444ba6a6654d9a984e1c99903"
LING_ENGINE_FAIR_PAIRING_SINCE: Final[str] = "51b7372fbd69b43e83d980ea5e1047bcb86e57bd"

# The sessions and the batch the recipe asks of an engine with the "kv-pages" feature: four
# conversations at once (the owner's bar of 2026-10-28; BATCHING §5.3 P4d), flat at 1.8.0 and paged
# from 1.8.1. `--sessions` and `--batch` are parsed by every ling-serve since 8d112d0, but without the
# feature they stay the engine's own (auto-sized from MemAvailable, at most four).
QWEN38_LING_SESSIONS: Final[int] = 4
# Whether the recipe turns the page pool on. Off at 1.8.0 (see the recipe); the pinned engine
# (P4d merged) is flat by default and takes `--kv-pages` only when asked.
QWEN38_LING_KV_PAGES: Final[bool] = False
QWEN38_LING_BATCH: Final[int] = 4
# Each flat session's context when the pool is off (the coordinator's decision of 2026-10-29: 1.8.0
# serves four flat sessions, paging off): the 786,432 positions of three flat sessions at 262,144,
# shared by four (a multiple of 2,048). Its plan, 4 x 13.4 GB beside 27.8 GB of weights and scratch,
# is 81.2 GB, below the 82.4 GB of the three at 262,144 the engine chose by itself (`--plan-only`
# in the pinned image, 2026-10-29). Given both `--sessions` and `--max-context`, the engine sizes
# nothing: a host that cannot hold the plan refuses the start (exit 3) rather than serve fewer.
QWEN38_LING_FLAT_CONTEXT: Final[int] = 196608

# The engine `server start` uses when neither `--engine` nor `server_engine` says. THE 1.8.0 SWITCH,
# made: ENGINE_LING makes ling-serve the default and SGLang the `--engine sglang` fallback; nothing
# else changes (`ModelMatrixRegistry.engine_recipes_for`). The checklist is
# docs/dev/release-1.8.0.md; setting ENGINE_SGLANG back is the whole rollback.
QWEN38_DEFAULT_ENGINE: Final[str] = ENGINE_LING

# ling-serve's image (docker/ling-serve/Dockerfile, built by scripts/build_ling_serve_image.sh on an
# aarch64 machine from QWEN38_LING_ENGINE_COMMIT), pinned by the digest GHCR gave the push
# (ghcr.io/dreamference/ling-serve:4853a93a4996, pushed 2026-10-30; linux/arm64, 688 MB). 1.7.2's was
# sha256:7bff4e37… (8d112d0d2539) and 1.8.0's first sha256:f241d647… (02704d4a0040). 1.7.2's replaced
# two images whose binaries carried the build machine's paths, deleted from the registry: the first
# 8d112d0 build (sha256:9428996a…) and bdda3a2's (sha256:87807c50…).
QWEN38_LING_IMAGE: Final[str] = (
    "ghcr.io/dreamference/ling-serve@sha256:311325fe9f57768ad3ed920cba1580ef359a73c4c9246b308bcde2f506f27338"
)

# The DFlash2 drafters ling-serve can run, pinned. Speculation is exact, so the drafter moves
# acceptance and speed, never the output. The ScaleSearch re-quantization of the RTN-calibrated one
# (ling-engine BACKLOG §19 item 22), what both arms of SWE-bench nights 12, 15 and 17 ran, is served
# since 1.7.2, as on SGLang (QWEN38_DRAFTER_REPO, from the release assets). The RTN-calibrated one,
# served until then, stays named here for an A/B; it has no matrix entry any more (the memory gates
# size a drafter by its entry before the snapshot is on disk), so serving it again means adding one.
QWEN38_RTN_DRAFTER: Final[dict[str, Any]] = {
    "model": "maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal",
    "revision": "bd7a934213c47a9e7ef69eef36bb3325f47fd1f1",
}
QWEN38_SCALESEARCH_DRAFTER: Final[dict[str, Any]] = {
    "model": QWEN38_DRAFTER_REPO,
    "revision": QWEN38_DRAFTER_REVISION,
}
QWEN38_LING_DRAFTER: Final[dict[str, Any]] = QWEN38_SCALESEARCH_DRAFTER

# The launch nights 12, 15 and 17 measured (Mightling's scripts/engine_ab_remote.sh), on Mightling's
# pinned checkpoint and drafter. What ling-serve's command line has no flag for is not here: it
# renders the chat template itself (the patched template's behaviour, checked against it by the
# engine's bench/template_test.py), serves only the qwen3_coder tool format and the qwen3 reasoning
# split, idles without spinning (no --sleep-on-idle), and takes no API key and no memory fraction.
# Its memory is what --max-context and its sessions allocate (each session's KV and state at the
# full context, allocated at start): 60 GB at 262,144, measured. Those are device allocations, which
# the container's cgroup cap does not count (it peaked at 0.4 GB; SGLang's container has about 8 GB
# charged of its own ~60 GB, so this was already so), and what bounds them is
# start_server's pre-load check against the 0.50 arena (~61 GB) and the PSI watchdog;
# `container_headroom_gb` sizes the cap as for SGLang.
QWEN38_LING_RECIPE: Final[dict[str, Any]] = {
    "engine": ENGINE_LING,
    "docker_image": QWEN38_LING_IMAGE,
    "engine_commit": QWEN38_LING_ENGINE_COMMIT,
    "engine_features": list(QWEN38_LING_ENGINE_FEATURES),
    # Files a particular engine reads beside the checkpoint and the drafter (get_engine_assets):
    # the sparse head, from an engine whose engine_features names "sparse-head".
    "engine_assets": [QWEN38_SPARSE_HEAD_ASSET],
    "revision": QWEN38_SGLANG_RECIPE["revision"],
    # ling-serve's --max-context: the KV and state of each resident session are allocated at this
    # size at start. SGLang's 262,144 with the page pool on (the owner's decision of 2026-10-26);
    # flat, QWEN38_LING_FLAT_CONTEXT, so that four sessions fit (1.8.0). The nights served 65,536, so
    # the long-context checks of the live test (start within the container's cap, a long needle,
    # decode speed, ling-run --spec-check at this length) prove it before release.
    "max_model_len": 262144 if QWEN38_LING_KV_PAGES else QWEN38_LING_FLAT_CONTEXT,
    "gpu_memory_utilization": 0.50,
    "container_headroom_gb": 24.0,
    # The formats ling-serve parses, compiled in (no flags): named for `status` and the start
    # checks' messages, as SGLang's recipe names its parsers.
    "tool_call_parser": "qwen3_coder",
    "reasoning_parser": "qwen3",
    # QWEN38_LING_DRAFTER, at the same 12 tokens a step as SGLang (--draft-block).
    "speculative_config": {
        "method": "DFLASH",
        **QWEN38_LING_DRAFTER,
        "num_speculative_tokens": QWEN38_SGLANG_RECIPE["speculative_config"]["num_speculative_tokens"],
    },
    # The nights' flags beyond the defaults: each speculative step as captured CUDA graphs, and
    # programmatic dependent launch. Tree verification, the megakernel stages, two sessions and a
    # batch of two are the engine's defaults at QWEN38_LING_ENGINE_COMMIT.
    "extra_args": ["--graphs", "--pdl"],
    # Four conversations at once (QWEN38_LING_SESSIONS/BATCH), on an engine that has the KV page pool
    # ("kv-pages" in `engine_features`). `kv_pages` is OFF at 1.8.0 (the owner's decision of
    # 2026-10-29: paged passes about 5% slower, ranges overlapping): the launch gives `--sessions 4
    # --batch 4` and no `--kv-pages`, so four flat slots at QWEN38_LING_FLAT_CONTEXT serve (the
    # coordinator's decision of 2026-10-29). 1.8.1 turns the pool on at 262,144
    # (specs/DREAMFERENCE_RELEASE_1.8.1.md).
    "sessions": QWEN38_LING_SESSIONS,
    "batch": QWEN38_LING_BATCH,
    "kv_pages": QWEN38_LING_KV_PAGES,
    # Image input on ling-serve, 1.8.1's (the owner's decision of 2026-10-29; 1.8.0 said False):
    # ling-serve starts with its vision tower on (`--vision on`, on an engine with the "kv-pages"
    # feature), so /model_info says image understanding and the launcher declares image input. The
    # tower's weights are in the checkpoint's bytes, and its scratch at the largest image (1,100 MB)
    # and the embedding cache (512 MB) are in ling-serve's plan: flat, the plan grows by about 1.6 GB
    # beside the four sessions' fixed KV; paged, the pool is sized after them, about 1.6 GB smaller.
    # False turns the tower off again (1.8.0's state, where `server start` names `--engine sglang`).
    "vision": True,
    # 1.8.1's per-launch switches (LingServeLaunchBuilder.switch): the KV page pool and fair pairing,
    # each off until the 10-31 night decides its default; a night arm overrides either with
    # DREAMFERENCE_LING_PAGING / DREAMFERENCE_LING_FAIR_PAIRING (0 or 1). Both act only on an engine
    # whose `engine_features` has "kv-pages" (and "paging-switch" for `--no-kv-pages`, "fair-pairing"
    # for `--fair-pairing`), so they take effect when the pin moves to the candidate engine.
    "fair_pairing": False,
}


def split_engine_recipes(
    default_engine: str, recipes: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Splits a model's recipes into the default one and the others, for `ModelSpec`.

    Args:
        default_engine: The engine that is the default (a key of `recipes`).
        recipes: Every engine's recipe, by engine name.

    Returns:
        tuple: The default engine's recipe (`launch_overrides`), then the rest (`engine_recipes`).
    """
    return recipes[default_engine], {name: r for name, r in recipes.items() if name != default_engine}


_QWEN38_LAUNCH_OVERRIDES, _QWEN38_ENGINE_RECIPES = split_engine_recipes(
    QWEN38_DEFAULT_ENGINE,
    {
        ENGINE_SGLANG: {**QWEN38_SGLANG_RECIPE, "lora": QWEN38_LORA_RECIPE},
        ENGINE_LING: QWEN38_LING_RECIPE,
    },
)


class ModelMatrixRegistry:
    """
    Registry holding qualified models for NVIDIA GB10 hardware and short alias resolution logic.
    """

    # Static registry of supported target models and speculative draft models
    MATRIX: Final[Dict[str, ModelSpec]] = {
        "qwen3.8-27b-nvfp4-dflash2": ModelSpec(
            name="Qwen 3.8 27B (NVFP4 + DFlash2, ling-serve)",
            params_b=27.0,
            supported_precisions=["NVFP4"],
            min_memory_gb=20.0,
            max_memory_gb=70.0,
            compatible_gb10=True,
            notes=(
                "Served by ling-engine's ling-serve since 1.8.0 (QWEN38_LING_RECIPE, four sessions "
                "at the full context), with SGLang one flag away (`server start --engine sglang`), "
                "the engine for image input and LoRA adapters until 1.8.1. Not vLLM, because "
                "the speed is in the drafter: DFlash2 is a block-diffusion drafter vLLM supports "
                "only through an unmerged pull request. The measurements below are SGLang's.\n\n"
                "Measured here (2026-09-29, single stream, temperature 0, thinking off, median of "
                "three after a warm-up, decode net of time to first token): prose 25.5, code 50.3, "
                "JSON 87.0 tok/s with the PyTorch sampler (24.1 / 47.5 / 82.5 before it); TTFT 0.22 s; "
                "prefill ~1,700 tok/s on a 13,333-token prompt the prefix cache could not help, "
                "~1,000 tok/s on 115,628 tokens (needle found). Image input works; four ling "
                "tasks at once finished in 23 s with >= 39.8 GB still available. First boot 7.5 min (torch.compile), with ~38.7 GB of "
                "host memory still available while serving, against ~10 GB beside the 122B. The "
                "same `ling exec` coding task took 10 s here and 34 s on the 122B.\n\n"
                "Published single-Spark numbers: vLLM with the checkpoint's own MTP managed 24.0 "
                "tok/s on a code prompt (26.0 with no speculation), SGLang with DFlash2 50.9 on "
                "code, 25.4 on long prose and 66.6 on short chat (MiaAI-Lab), 71.4 greedy median "
                "(hasso5703). Our 122B does 49.9 / 23.8 on code / prose, so decode is a tie there "
                "and structured output is faster; the larger gains are quality (Terminal-Bench 2.1 "
                "73.0), context (262K against 32K) and memory (~20 GB of weights against ~71).\n\n"
                "Recipe: github.com/hasso5703/dgx-spark-qwen38 (v1.18, measured 2026-09-17): the "
                "pinned lmsysorg/sglang image (v0.5.19 until 1.6.1; v0.5.21 since 2026-10-14, "
                "which carries the DFlash checkpoint-crossing fix, after the wrong-answer probe of "
                "docs/dev/sglang-probe-2026-10-14.md), RadixArk's NVFP4 conversion and the "
                "NVFP4 DFlash2 drafter at the revisions pinned below (the RTN-calibrated one until "
                "1.7.2, the searched-scale one Dreamference hosts since), 12 draft "
                "tokens (the recipe says 16; 12 measured +7% on the replay set, 48.3 against "
                "45.1 tok/s single stream, speculation still exact, applied 2026-10-09), "
                "memory fraction 0.50, flashinfer attention, mamba radix cache in "
                "extra_buffer mode, torch.compile up to batch 4. Not copied: its API key and "
                "keepalive proxy (Mightling reaches the server the way it reaches vLLM) and its "
                "reasoning-effort default."
            ),
            hf_repo_id="RadixArk/Qwen3.8-27B-NVFP4",
            # The default engine's recipe and the other's (QWEN38_DEFAULT_ENGINE): ling-serve by
            # default since 1.8.0, with four sessions at the full context (the owner's decisions of
            # 2026-10-26 and 2026-10-28), and SGLang the rollback and, until 1.8.1, the engine for
            # image input and LoRA adapters (opt-in ling-serve in 1.7.2).
            launch_overrides=_QWEN38_LAUNCH_OVERRIDES,
            engine_recipes=_QWEN38_ENGINE_RECIPES,
            supports_vision=True,
        ),
        # Night 2's candidate (specs/DREAMFERENCE_MODELS.md §2.2), not a default: production
        # switches to it only if a SWE-bench A/B on this machine holds quality and it is faster.
        "qwen3.8-27b-minima-nvfp4-dflash2": ModelSpec(
            name="Qwen 3.8 27B Minima (all-NVFP4 + DFlash2, SGLang)",
            params_b=27.0,
            supported_precisions=["NVFP4"],
            min_memory_gb=19.0,
            max_memory_gb=70.0,
            compatible_gb10=True,
            notes=(
                "Minima (arXiv 2609.04098): Qwen3.8-27B with all 496 linear layers in NVFP4 W4A4, "
                "the Gated DeltaNet projections and gates included, by post-training quantization "
                "with llm-compressor (compressed-tensors `nvfp4-pack-quantized`), plus static FP8 "
                "KV-cache scales; embeddings, lm_head, conv1d and norms stay BF16. 18.8 GB on disk "
                "against the production checkpoint's FP8/NVFP4 mix. A text-only "
                "Qwen3_5ForCausalLM extraction: no vision tower, so no image input. Its own paper "
                "reports BF16 quality within seed noise and decode at 47 against 51 tok/s for the "
                "production recipe at concurrency 1 (RTX PRO 6000, vLLM 0.27.1).\n\n"
                "Served by the production recipe unchanged (QWEN38_SGLANG_RECIPE: the same image, "
                "flags, chat-template patches and DFlash2 drafter at 12 tokens), so a comparison "
                "differs in the target's weights alone. Two departures, both for that reason: "
                "the checkpoint's own revision, and `--kv-cache-dtype bfloat16`, because SGLang "
                "turns a compressed-tensors kv_cache_scheme into an FP8 KV pool under `auto`, "
                "while production's pool is BF16. The drafter fits: it carries no embeddings or "
                "lm_head of its own, and reads hidden states of width 5,120 at layers 5, 19, 33, "
                "47 and 61 of 64, which this checkpoint has; its acceptance is measured, not "
                "assumed, because it was trained against other hidden states.\n\n"
                "SGLang v0.5.19 and v0.5.21 serve the architecture (models/qwen3_5_text.py, with DFlash "
                "capture and the extra_buffer mamba cache) and the format "
                "(CompressedTensorsW4A4Fp4). That scheme takes the largest global scale of a fused "
                "group (qkv+z, b+a, q/k/v, gate/up) without rescaling, which the paper shows "
                "silently mis-scales the DeltaNet gates; this checkpoint ships its fused groups "
                "harmonized to one global scale each, checked on the download "
                "(specs/DREAMFERENCE_MODELS.md §2.2, scripts/check_nvfp4_fused_scales.py)."
            ),
            hf_repo_id="minima-ai/mnma_qwen3.8_27b_nvfp4",
            launch_overrides={
                **QWEN38_SGLANG_RECIPE,
                "revision": "16e768e7d0461b0b86e565ecedd08a24eca53e9a",
                "extra_args": [*QWEN38_SGLANG_RECIPE["extra_args"], "--kv-cache-dtype", "bfloat16"],
            },
            supports_vision=False,
        ),
        # A trial candidate (approved 2026-10-19; docs/dev/flash-next-feasibility-2026-10-19.md),
        # not a default: smoke-booted by day first (docs/dev/flash-next-smoke-boot.md), then a
        # SWE-bench A/B night against production.
        "qwen3.8-flash-next-nvfp4-mtp": ModelSpec(
            name="Qwen 3.8 Flash-Next (QAD NVFP4 + MTP, vLLM)",
            # 125B (6B active) plus the 51B n-gram table and the 4B MTP head.
            params_b=180.0,
            # ModelOpt MIXED_PRECISION, declared in its own config.json: no --quantization.
            supported_precisions=["NVFP4"],
            # The snapshot's size (106.4 GB = 99.1 GiB): before the download this is the
            # weights figure the start gate uses, so it must not exceed the arena below.
            min_memory_gb=100.0,
            max_memory_gb=115.0,
            compatible_gb10=True,
            notes=(
                "Trial candidate, not yet booted here. Qwen3.8-Flash-Next is about 125B parameters "
                "with 6B active (48 layers, 12 x (3 Gated DeltaNet + 1 Qwen Sparse Attention), 512 "
                "experts of which 10 routed and 1 shared are active), plus a 51B n-gram (PLE) "
                "embedding table, a 4B MTP head and a vision tower. This is local-inference-lab's "
                "quantization-aware-distilled export: experts and the n-gram table in NVFP4 (the "
                "table 26.8 GiB instead of 47.7 in FP8), attention and shared experts in MXFP8. It "
                "loads at about 97.5 GiB (vLLM #56273) and is the only export that both fits "
                "resident on one GB10 and loads on upstream vLLM.\n\n"
                "vLLM, not SGLang: no SGLang release loads a packed NVFP4 n-gram table (#41624 "
                "open), and the vLLM nightly pinned below is the first image with #56273 (not in "
                "v0.32.0). MTP at 3 draft tokens: MiaAI-Lab measured about 2.8-3.0 accepted per "
                "step and 48.7 tok/s on prose on a patched vLLM; upstream with this checkpoint and "
                "MTP on GB10 is unmeasured. Expected 45-55 tok/s on prose against production's "
                "25.5.\n\n"
                "Memory is tight: about 15 GiB stays free while serving, so the global load-transient "
                "estimate (0.15 x weights) refuses the load and SWE-bench runs one instance at a "
                "time (see load_transient_gb and swe_bench_max_parallel). Known risks on SM121: "
                "vLLM #54521 (greedy output nondeterministic and corrupted past the 2,048-token "
                "indexer budget) and silent long-context corruption seen in a community build; the "
                "smoke boot checks both. Licence: Qwen Community License 1.0, to check before "
                "shipping."
            ),
            hf_repo_id="local-inference-lab/Qwen3.8-Flash-Next-NVFP4",
            supports_vision=True,
            # Each SWE-bench task container may take up to task_memory (8G) beside a model that
            # leaves about 15 GiB free, with the 8 GiB reserve: one at a time until the smoke
            # boot measures more room (two would need task_memory lowered as well).
            swe_bench_max_parallel=1,
            launch_overrides={
                "engine": "vllm",
                # vLLM nightly e68efc685ab1 (2026-10-19), the multi-arch index digest; the first
                # image with #56273 (packed NVFP4 n-gram tables).
                "docker_image": (
                    "vllm/vllm-openai@sha256:"
                    "1ff6716fe240a922d0d518b8884261e25b0c02eda8e2ce90888e364978249cad"
                ),
                "revision": "6c01ac378d22f6652c9f392c87700496eca8e5dc",
                # Covers SWE-bench's 49,152-token tasks and the smoke boot's 120K needle. The KV
                # pool is whatever the arena leaves, not sized by this; 262,144 (native) once the
                # smoke boot's log shows a pool that holds it.
                "max_model_len": 131072,
                # Not the 0.90 of the feasibility sketch: the container's cgroup cap is the arena
                # plus container_headroom_gb, clamped to total - HOST_MEMORY_RESERVE_GB (109.6 GiB
                # here), so a 109.4 GiB arena would have left the process 0.2 GiB outside it. At
                # 0.86 the arena is 104.6 GiB (weights 97.5, about 7 for KV, mamba state and
                # graphs) and the process keeps 5 GiB under the cap.
                "gpu_memory_utilization": 0.86,
                # Measured-figure override of the start gate's load transient (owner's approval,
                # 2026-10-19). 0.15 x 99.1 GiB = 14.9 GiB would need 119.5 GiB free, which no GB10
                # has. PROVISIONAL until the smoke boot measures the real peak: the 0.15
                # calibration came from fastsafetensors staging a checkpoint twice, and this load
                # mmaps (vLLM's default loader) after its page cache is dropped. Replace with the
                # measured transient plus 2 GiB (docs/dev/flash-next-smoke-boot.md §3).
                "load_transient_gb": 8.0,
                # The vLLM recipe's choice; NVIDIA's FP4-Local card uses qwen3_xml.
                "tool_call_parser": "qwen3_coder",
                "reasoning_parser": "qwen3",
                # Self-speculation: the MTP head ships in the checkpoint, so no drafter to fetch.
                "speculative_config": {"method": "mtp", "num_speculative_tokens": 3},
                # Qwen's template, byte-identical to the base model's: both anchors occur in it
                # exactly once, so production's patches carry over unchanged.
                "chat_template_patches": QWEN38_SGLANG_RECIPE["chat_template_patches"],
                "kv_cache_dtype": "fp8",
                "max_num_batched_tokens": 4096,
                "extra_args": [
                    # Prefix caching on a hybrid GDN model needs the mamba states aligned to it.
                    "--mamba-cache-mode", "align",
                    "--max-num-seqs", "4",
                ],
            },
        ),
        "qwen3.8-27b-dflash2-draft": ModelSpec(
            name="Qwen 3.8 27B DFlash2 Drafter (NVFP4, Draft Model)",
            params_b=1.0,
            supported_precisions=["NVFP4"],
            min_memory_gb=1.0,
            max_memory_gb=2.0,
            compatible_gb10=True,
            notes=(
                "Drafter for qwen3.8-27b-nvfp4-dflash2, named by its speculative_config and listed "
                "so the memory gates have a size before the snapshot is on disk. Since 1.7.2 the "
                "searched-scale NVFP4 quantization of incoai/Qwen3.8-27B-DFlash2 that Dreamference "
                "hosts (until then maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal, round to nearest)."
            ),
            hf_repo_id=QWEN38_DRAFTER_REPO,
        ),
        "tiny-a2d-coder-0.5b-diffusion": ModelSpec(
            name="Tiny-A2D Qwen2.5-Coder 0.5B (bd3lm diffusion)",
            params_b=0.6,
            supported_precisions=["BF16"],
            # ~1.2 GiB of BF16 shards plus activations. Small enough that the diffusion sidecar
            # runs it under a fixed container memory cap instead of the PSI watchdog.
            min_memory_gb=1.5,
            max_memory_gb=3.0,
            compatible_gb10=True,
            notes=(
                "Default diffusion model. A Qwen2.5-Coder 0.5B converted to a block-diffusion "
                "(bd3lm) language model by the dLLM project's Tiny-A2D recipe: generation "
                "denoises 32-token blocks over up to 128 steps instead of decoding "
                "autoregressively. NOT vLLM-servable -- the checkpoint loads through "
                "transformers as AutoModelForMaskedLM with trust_remote_code and generates via "
                "its own remote-code sampler -- which is what is_diffusion records and why "
                "DiffusionServerManager serves it rather than the vLLM launcher. The model "
                "card's sampler settings (steps=128, block_size=32, temperature=0.0, "
                "cfg_scale=0.0, remasking='low_confidence') are applied by the sidecar's "
                "serving script, which falls back to bare max_new_tokens if the remote code's "
                "generate signature differs. Apache 2.0."
            ),
            hf_repo_id="dllm-collection/Qwen2.5-Coder-0.5B-Instruct-diffusion-bd3lm-v0.1",
            is_diffusion=True,
        ),
    }

    @classmethod
    def key_for_served_id(cls, served_id: str, preferred: Optional[str] = None) -> Optional[str]:
        """
        Finds the registry entry for a model id a running server reports in /v1/models.

        The server names its model by checkpoint, and several entries can share one (recipes
        differing only in launch flags), so a preferred key wins when it matches.

        Args:
            served_id (str): The id from the server's /v1/models.
            preferred (Optional[str]): Key to return if it serves that checkpoint.

        Returns:
            Optional[str]: The matching key, or None when no entry serves that checkpoint. An
            adapter's id, `<base>+<adapter>`, names its base's entry.
        """
        served_id = served_id.split(ADAPTER_SEPARATOR, 1)[0]
        if preferred and cls.resolve_hf_repo(preferred) == served_id:
            return preferred
        for key in cls.MATRIX:
            if cls.resolve_hf_repo(key) == served_id:
                return key
        return None

    @classmethod
    def get_lora_recipe(cls, model_key: str, engine: str | None = None) -> dict[str, Any] | None:
        """The LoRA settings a model is served with, if it serves adapters at all.

        Args:
            model_key (str): Model alias, HF repo ID, or display name.
            engine (str | None): The engine whose recipe is asked; None asks every engine the
                model has, default first (an adapter fits the checkpoint whichever engine is
                chosen; only the one that serves adapters names how).

        Returns:
            dict[str, Any] | None: A copy of the recipe's `lora` key, or None.
        """
        engines = [engine] if engine else cls.engines(model_key)
        for name in engines:
            lora = cls.get_launch_overrides(model_key, name).get("lora")
            if isinstance(lora, dict):
                return dict(lora)
        return None

    @classmethod
    def resolve_hf_repo(cls, model_key: str) -> str:
        """
        Resolves model alias, repo string, or display name to official HuggingFace repository ID.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            str: Official HuggingFace repository identifier (or original string if unmapped).
        """
        if not model_key:
            return model_key
        spec = cls.get_spec(model_key)
        return spec.hf_repo_id if spec else model_key

    @classmethod
    def get_spec(cls, model_key: str) -> Optional[ModelSpec]:
        """
        Retrieves ModelSpec for given short alias, HuggingFace repo ID, or display name.

        Args:
            model_key (str): Short model name key, HF repo ID, or display name.

        Returns:
            Optional[ModelSpec]: ModelSpec object or None if key is unrecognized.
        """
        if not model_key:
            return None
        key = model_key.strip().lower()
        if key in cls.MATRIX:
            return cls.MATRIX[key]
        for spec in cls.MATRIX.values():
            if spec.hf_repo_id.lower() == key or spec.name.lower() == key:
                return spec
        norm_key = key.replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
        for alias, spec in cls.MATRIX.items():
            norm_alias = alias.replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
            norm_hf = spec.hf_repo_id.lower().replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
            norm_name = spec.name.lower().replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
            if norm_key in (norm_alias, norm_hf, norm_name):
                return spec
        return None

    @classmethod
    def get_launch_overrides(cls, model_key: str, engine: str | None = None) -> dict[str, Any]:
        """
        Retrieves the per-model launch recipe for the given alias, repo ID, or display name.

        Args:
            model_key (str): Model alias, HF repo ID, or display name. Unrecognized keys
                yield an empty recipe, leaving the caller on global defaults.
            engine (str | None): The engine to serve it with (`vllm`, `sglang`, `ling`); None,
                or the default recipe's own engine, is the default recipe (`launch_overrides`),
                another one the model's recipe for that engine (`engine_recipes`).

        Returns:
            Dict[str, Any]: Copy of the recipe, safe for the caller to mutate.

        Raises:
            ValueError: When the model has no recipe for that engine.
        """
        if not model_key:
            return {}
        spec = cls.get_spec(model_key)
        if not spec:
            return {}
        if not engine or engine.lower() == cls.engine_of(spec.launch_overrides):
            return copy.deepcopy(dict(spec.launch_overrides))
        recipe = spec.engine_recipes.get(engine.lower())
        if recipe is None:
            raise ValueError(
                f"{model_key} has no recipe for the engine '{engine}'; its engines: "
                f"{', '.join(cls.engines(model_key))}."
            )
        return copy.deepcopy(dict(recipe))

    @classmethod
    def engine_of(cls, recipe: dict[str, Any]) -> str:
        """Names the engine a recipe runs on.

        Args:
            recipe (dict[str, Any]): A launch recipe.

        Returns:
            str: `launch_overrides['engine']`, lower case; `vllm` when it names none.
        """
        return str(recipe.get("engine") or ENGINE_VLLM).lower()

    @classmethod
    def engines(cls, model_key: str) -> list[str]:
        """Lists the engines a model can be served with.

        Args:
            model_key (str): Model alias, HF repo ID, or display name.

        Returns:
            list[str]: The default recipe's engine first, then the others'; empty for an unknown model.
        """
        spec = cls.get_spec(model_key) if model_key else None
        if not spec:
            return []
        default = cls.engine_of(spec.launch_overrides)
        return [default, *(name for name in spec.engine_recipes if name != default)]

    @classmethod
    def get_speculative_draft_repo(cls, model_key: str) -> Optional[str]:
        """
        Retrieves the separate draft checkpoint a model's recipe speculates against, if any.

        Two kinds of speculation live in `launch_overrides['speculative_config']` and only one of
        them names a second checkpoint. Self-speculation (MTP, Eagle heads) ships inside the target
        and has no 'model' key; a drafter like DFlash is its own repository that has to be fetched
        and counted against memory alongside the target. Callers that pre-download weights or size
        the pre-flight memory gates need to see the second kind, and it is invisible to them
        otherwise — the `draft_model` argument on the launcher only covers drafters named by the
        caller, not ones the recipe brings with it.

        Args:
            model_key (str): Model alias, HF repo ID, or display name.

        Returns:
            Optional[str]: HuggingFace repo ID of the recipe's draft model, or None when the model
                is unknown, speculates against itself, or does not speculate at all.
        """
        spec_config = cls.get_launch_overrides(model_key).get("speculative_config") or {}
        return spec_config.get("model") or None

    @classmethod
    def pinned_revision(cls, model_key: str | None) -> str | None:
        """The commit a model's checkpoint is fetched at: the one its recipes were measured on.

        A checkpoint's own recipe pins it (`revision`); a drafter is pinned by the recipes that
        name it (`speculative_config['revision']`), since its matrix entry, if it has one, carries
        no recipe. Every fetch (`model download`, `init`, the pre-download of `server start`,
        tensorization) and `node sync-model` ask here, so none of them takes the Hub's default
        branch, which moves under a pin (319f741 instead of the pinned 52d1adc on 2026-10-27).

        Args:
            model_key (str | None): Model alias, HF repo ID, or display name.

        Returns:
            str | None: The pinned commit; None for a model no recipe pins.
        """
        if not model_key:
            return None
        spec = cls.get_spec(model_key)
        if spec is not None:
            recipes = [spec.launch_overrides, *spec.engine_recipes.values()]
            for recipe in recipes:
                if recipe.get("revision"):
                    return str(recipe["revision"])
        repo = cls.resolve_hf_repo(model_key).lower()
        for entry in cls.MATRIX.values():
            for recipe in (entry.launch_overrides, *entry.engine_recipes.values()):
                spec_config = recipe.get("speculative_config") or {}
                drafter = str(spec_config.get("model") or "").lower()
                if drafter == repo and spec_config.get("revision"):
                    return str(spec_config["revision"])
        return None

    @classmethod
    def swe_bench_max_parallel(cls, model_key: str | None) -> int | None:
        """The most SWE-bench instances that may run at once beside a model, by the model's own entry.

        Args:
            model_key (str | None): Model alias, HF repo ID (what a server reports), or display name.

        Returns:
            int | None: The cap (at least 1), or None when the model is unknown or has none.
        """
        spec = cls.get_spec(model_key) if model_key else None
        if not spec or spec.swe_bench_max_parallel is None:
            return None
        return max(1, int(spec.swe_bench_max_parallel))

    @classmethod
    def get_engine_assets(cls, model_key: str, engine: str | None = None) -> list[dict[str, Any]]:
        """Lists the files the model's serving engine reads besides the checkpoint and the drafter.

        An asset belongs to one engine and one feature of it: it applies only when the recipe's
        `engine` is the asset's and the recipe's `engine_features` names the asset's feature, so
        an engine build that does not read it (or another engine) never downloads or syncs it.

        Args:
            model_key (str): Model alias, HF repo ID, or display name.
            engine (str | None): The engine whose recipe is asked; None for the model's default.

        Returns:
            list[dict[str, Any]]: The applicable assets (`repo`, `revision`, `flag`), possibly empty.
        """
        overrides = cls.get_launch_overrides(model_key, engine)
        engine = cls.engine_of(overrides)
        features = set(overrides.get("engine_features") or [])
        return [
            dict(asset)
            for asset in overrides.get("engine_assets") or []
            if asset.get("engine") == engine and asset.get("feature") in features
        ]

    @classmethod
    def get_release_source(cls, model_key: str | None) -> dict[str, Any] | None:
        """The release assets a model is fetched from instead of the Hugging Face Hub, if it is hosted so.

        Args:
            model_key (str | None): Model alias, repository-style name (`dreamference/...`), or display name.

        Returns:
            dict[str, Any] | None: `revision` (the snapshot's name) and `files` (each snapshot file's `url` and
            `sha256`), or None for a model the Hub serves.
        """
        if not model_key:
            return None
        repo = (cls.resolve_hf_repo(model_key) or model_key).lower()
        for name, source in RELEASE_HOSTED_MODELS.items():
            if name.lower() == repo:
                return {"revision": source["revision"], "files": {k: dict(v) for k, v in source["files"].items()}}
        return None

    @classmethod
    def supports_vision(cls, model_key: str) -> bool:
        """
        Reports whether the model accepts image input alongside text.

        vLLM needs no telling -- it reads the modality off the checkpoint -- but API clients do.
        The retired Onyx web chat refused an upload unless its model entry advertised the
        capability; Ask sends an image as `localImage` and relies on the served model having
        vision, which this records (specs/DREAMFERENCE_MIGHTLING_ASK.md §1).

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            bool: True when the checkpoint is vision-capable.
        """
        spec = cls.get_spec(model_key) if model_key else None
        return bool(spec and spec.supports_vision)

    @classmethod
    def is_diffusion(cls, model_key: str) -> bool:
        """
        Reports whether the model is a diffusion language model.

        The distinction routes serving: an autoregressive checkpoint goes to the vLLM launcher, a
        diffusion one to the transformers-based sidecar, and pointing either at the other kind
        fails only at load time with an unhelpful error. Unknown keys report False, which sends
        them down the vLLM path -- the path with pre-flight gates that can explain a bad load.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            bool: True when the checkpoint generates by diffusion rather than autoregression.
        """
        spec = cls.get_spec(model_key) if model_key else None
        return bool(spec and spec.is_diffusion)

    @classmethod
    def diffusion_enabled(cls) -> bool:
        """
        Reports whether Mightling serves, downloads and shows diffusion models at all.

        Read through this method rather than the constant, at call time, so every caller sees
        the same switch and a test can turn it on in one place.

        Returns:
            bool: The value of DIFFUSION_ENABLED.
        """
        return DIFFUSION_ENABLED

    @classmethod
    def is_offered(cls, model_key: str) -> bool:
        """
        Reports whether a model is offered to the user: listed, downloadable, selectable.

        Every model is, except a diffusion model while diffusion is switched off. Unknown keys
        are offered, because a raw HuggingFace repository may still be downloaded by name.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            bool: False for a removed model, and for a diffusion model with diffusion switched off.
        """
        if cls.removed_in(model_key):
            return False
        return cls.diffusion_enabled() or not cls.is_diffusion(model_key)

    @classmethod
    def removed_in(cls, model_key: Optional[str]) -> Optional[str]:
        """
        Names the release that removed a model, if `model_key` names one Mightling no longer serves.

        Args:
            model_key (Optional[str]): Short model alias, HF repo ID, or display name.

        Returns:
            Optional[str]: The release that removed it, or None for any other key.
        """
        if not model_key:
            return None
        return REMOVED_MODELS.get(model_key.strip().lower())

    @classmethod
    def removed_message(cls, model_key: str, configured: bool = False) -> str:
        """
        What to tell a user whose command or configuration names a removed model.

        Args:
            model_key (str): The removed model's name, as the user gave it.
            configured (bool): True when it came from the configuration rather than the command
                line, so the message says where it is set.

        Returns:
            str: Two lines: what happened, and the command that fixes it.
        """
        where = " (it is the model in your Mightling configuration)" if configured else ""
        return (f"'{model_key}' was removed in Mightling {cls.removed_in(model_key)}{where}: Mightling "
                f"serves one model, {DEFAULT_MODEL_ALIAS}.\n"
                f"   Switch to it: ling-admin main-model set {DEFAULT_MODEL_ALIAS}")

    @classmethod
    def declares_own_quantization(cls, model_key: str) -> bool:
        """
        Reports whether the checkpoint carries its quantization format in its own config.

        Callers use this to suppress an inferred `--quantization` flag: an NVFP4 or AWQ checkpoint
        already tells vLLM what it is, and overriding that with a guess derived from the model name
        misconfigures the load.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            bool: True when the model's precisions are all self-declaring.
        """
        spec = cls.get_spec(model_key) if model_key else None
        if not spec or not spec.supported_precisions:
            return False
        return all(p.upper() in SELF_DECLARING_PRECISIONS for p in spec.supported_precisions)
