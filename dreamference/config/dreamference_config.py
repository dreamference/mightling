"""
Main Dreamference Configuration Class.

This module provides the DreamferenceConfig class which merges CLI parameters, environment
variables, `.dreamference/config.yaml`, and system defaults into a unified settings object.
It also manages Goose AI Agent configuration files (~/.config/goose/config.yaml).
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Final

from dreamference.hardware import MODEL_MATRIX, check_model_compatibility
from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS, DEFAULT_DIFFUSION_MODEL_ALIAS
from dreamference.config.config_path_resolver import ConfigPathResolver
from dreamference.config.config_file_storage_manager import ConfigFileStorageManager, yaml

# System defaults
DEFAULT_VLLM_HOST: Final[str] = "http://localhost:8000"
DEFAULT_MODEL: Final[str] = DEFAULT_MODEL_ALIAS
DEFAULT_DIFFUSION_MODEL: Final[str] = DEFAULT_DIFFUSION_MODEL_ALIAS
DEFAULT_SPECULATIVE_TOKENS: Final[int] = 8
DEFAULT_SANDBOX: Final[str] = "none"
DEFAULT_AGENT_RUNNER: Final[str] = "codex"
DEFAULT_PREFIX_CACHING: Final[bool] = True
DEFAULT_CHUNKED_PREFILL: Final[bool] = True
DEFAULT_SCHEDULER_STEPS: Final[int] = 8
DEFAULT_ATTENTION_BACKEND: Final[str] = "auto"
# None, not a dtype: this value is handed to the launcher as an explicit caller argument, so any
# concrete default here overrides every model's own kv_cache_dtype recipe. The choice belongs to
# the model — fp8 halves KV but is not accepted by every attention backend — so the config layer
# stays silent unless the operator says otherwise.
DEFAULT_KV_CACHE_DTYPE: Final[Optional[str]] = None
DEFAULT_ENABLE_AUTO_TOOL_CHOICE: Final[bool] = True
DEFAULT_TOOL_CALL_PARSER: Final[str] = "hermes"
DEFAULT_MAX_NUM_BATCHED_TOKENS: Final[int] = 8192

HERMES_TOOL_CALL_PROMPT: Final[str] = (
    "When you need to execute a tool, format your tool call strictly using <tool_call> tags as follows:\n"
    "<tool_call>\n"
    "{\"name\": \"function_name\", \"arguments\": {\"arg\": \"val\"}}\n"
    "</tool_call>"
)


DEFAULT_GUIDED_DECODING_BACKEND: Final[str] = "xgrammar"
DEFAULT_CAVE_MODE: Final[bool] = False
DEFAULT_USE_TENSORIZER: Final[bool] = False

CAVE_MODE_PROMPT: Final[str] = (
    "You are in Cave Mode. You are a senior Staff Engineer. "
    "Do not explain your reasoning. Do not use pleasantries, greetings, or conclusions. "
    "Do not apologize. Output only the exact shell commands, tool calls, or code modifications "
    "required to complete the user's objective. If asked a question, answer in 15 words or less."
)

GOOSE_CONFIG_PATH: Final[Path] = Path.home() / ".config" / "goose" / "config.yaml"

class DreamferenceConfig:
    """
    Unified configuration manager implementing the 4-tier precedence hierarchy:
    CLI Parameters > Environment Variables > `.dreamference/config.yaml` > System Defaults.
    """

    def __init__(
        self,
        config_file: Optional[str] = None,
        vllm_host: Optional[str] = None,
        model: Optional[str] = None,
        diffusion_model: Optional[str] = None,
        draft_model: Optional[str] = None,
        num_speculative_tokens: Optional[int] = None,
        sandbox: Optional[str] = None,
        agent_runner: Optional[str] = None,
        hf_token: Optional[str] = None,
        enable_prefix_caching: Optional[bool] = None,
        enable_chunked_prefill: Optional[bool] = None,
        num_scheduler_steps: Optional[int] = None,
        attention_backend: Optional[str] = None,
        kv_cache_dtype: Optional[str] = None,
        cave_mode: Optional[bool] = None,
        use_tensorizer: Optional[bool] = None,
        guided_decoding_backend: Optional[str] = None,
    ):
        """
        Initializes DreamferenceConfig by loading file defaults and overriding with environment variables and parameters.
        """
        self.config_file_path: Path = ConfigPathResolver.resolve_path(config_file)
        self.file_data: Dict[str, Any] = ConfigFileStorageManager.load_config_dict(self.config_file_path)

        # 1. Target vLLM server endpoint host URL
        self.vllm_host: str = (
            vllm_host
            if vllm_host is not None
            else os.getenv(
                "DREAMFERENCE_VLLM_HOST",
                str(self.file_data.get("vllm_host", DEFAULT_VLLM_HOST))
            )
        )

        # 2. Main target LLM model name served on vLLM.
        #
        # `_model_pinned` records whether this value was *chosen* or merely defaulted to, which is
        # not the same question as whether it currently differs from the default. save_config()
        # needs the distinction: a model that happens to equal today's DEFAULT_MODEL is still an
        # explicit choice, and dropping it from the file on that basis silently re-points the
        # workspace the next time the default moves. Anything that arrived from a constructor
        # argument, the environment, or the config file counts as chosen; only falling all the way
        # through to DEFAULT_MODEL does not.
        self._model_pinned: bool = (
            model is not None
            or os.getenv("DREAMFERENCE_MODEL") is not None
            or "model" in self.file_data
        )
        # Assigned to the backing field rather than through the property: resolution is not a
        # choice, and going through the setter here would mark every config as pinned.
        self._model: str = (
            model
            if model is not None
            else os.getenv(
                "DREAMFERENCE_MODEL",
                str(self.file_data.get("model", DEFAULT_MODEL))
            )
        )

        # 2b. Diffusion model served in parallel with the main model. Every configuration names
        # one, exactly as it names a main model, and the pinning rules are the same for the same
        # reason: a diffusion model that happens to equal today's default is still a choice, and
        # dropping it from the file re-points the workspace when the default moves.
        self._diffusion_model_pinned: bool = (
            diffusion_model is not None
            or os.getenv("DREAMFERENCE_DIFFUSION_MODEL") is not None
            or "diffusion_model" in self.file_data
        )
        self._diffusion_model: str = (
            diffusion_model
            if diffusion_model is not None
            else os.getenv(
                "DREAMFERENCE_DIFFUSION_MODEL",
                str(self.file_data.get("diffusion_model", DEFAULT_DIFFUSION_MODEL))
            )
        )

        # 3. Optional speculative decoding draft model name
        self.draft_model: Optional[str] = (
            draft_model
            if draft_model is not None
            else os.getenv(
                "DREAMFERENCE_DRAFT_MODEL",
                self.file_data.get("draft_model", None)
            )
        )

        # 4. Number of speculative draft tokens to propose per step
        env_spec_tokens = os.getenv("DREAMFERENCE_SPECULATIVE_TOKENS")
        if num_speculative_tokens is not None:
            self.num_speculative_tokens: int = num_speculative_tokens
        elif env_spec_tokens is not None:
            self.num_speculative_tokens = int(env_spec_tokens)
        else:
            self.num_speculative_tokens = int(
                self.file_data.get("num_speculative_tokens", DEFAULT_SPECULATIVE_TOKENS)
            )

        # 5. Container sandbox engine ('none', 'apptainer', 'podman', 'docker')
        self.sandbox: str = (
            sandbox
            if sandbox is not None
            else os.getenv(
                "DREAMFERENCE_SANDBOX",
                str(self.file_data.get("sandbox", DEFAULT_SANDBOX))
            )
        ).lower()

        # 6. Primary AI agent runner ('goose' [default] or 'cline')
        self.agent_runner: str = (
            agent_runner
            if agent_runner is not None
            else os.getenv(
                "DREAMFERENCE_AGENT",
                os.getenv(
                    "DREAMFERENCE_RUNNER",
                    str(self.file_data.get("agent_runner", DEFAULT_AGENT_RUNNER))
                )
            )
        ).lower()

        # 7. HuggingFace access token
        self.hf_token: Optional[str] = (
            hf_token
            if hf_token is not None
            else os.getenv(
                "HF_TOKEN",
                os.getenv(
                    "DREAMFERENCE_HF_TOKEN",
                    self.file_data.get("hf_token", None)
                )
            )
        )

        # 8. Blackwell GB10 Performance Tuning Flags
        self.enable_prefix_caching: bool = bool(
            enable_prefix_caching
            if enable_prefix_caching is not None
            else self.file_data.get("enable_prefix_caching", DEFAULT_PREFIX_CACHING)
        )

        self.enable_chunked_prefill: bool = bool(
            enable_chunked_prefill
            if enable_chunked_prefill is not None
            else self.file_data.get("enable_chunked_prefill", DEFAULT_CHUNKED_PREFILL)
        )

        self.num_scheduler_steps: int = int(
            num_scheduler_steps
            if num_scheduler_steps is not None
            else self.file_data.get("num_scheduler_steps", DEFAULT_SCHEDULER_STEPS)
        )

        self.attention_backend: str = str(
            attention_backend
            if attention_backend is not None
            else self.file_data.get("attention_backend", DEFAULT_ATTENTION_BACKEND)
        )

        # None means "unset — whatever the model's recipe asks for", and the str() coercion that
        # used to wrap this had to go with it, since it turned None into the string "None".
        #
        # This field is passed straight into start_server, where a non-None value counts as an
        # explicit caller choice and outranks the registry recipe. Defaulting it to a concrete
        # 'fp8' therefore made every model's kv_cache_dtype recipe entry unreachable through the
        # CLI — silently, because the two NVFP4 recipes ask for fp8 anyway. The first model that
        # wanted something else got fp8 regardless and failed to load: FlashAttention rejects an
        # fp8 KV cache outright ("Selected backend FLASH_ATTN is not valid for this configuration.
        # Reason: ['kv_cache_dtype not supported']").
        self.kv_cache_dtype: Optional[str] = (
            kv_cache_dtype
            if kv_cache_dtype is not None
            else self.file_data.get("kv_cache_dtype", DEFAULT_KV_CACHE_DTYPE)
        )

        self.cave_mode: bool = bool(
            cave_mode
            if cave_mode is not None
            else self.file_data.get("cave_mode", DEFAULT_CAVE_MODE)
        )

        self.guided_decoding_backend: str = str(
            guided_decoding_backend
            if guided_decoding_backend is not None
            else self.file_data.get("guided_decoding_backend", DEFAULT_GUIDED_DECODING_BACKEND)
        )

        env_tensorizer = os.getenv("DREAMFERENCE_USE_TENSORIZER")
        if use_tensorizer is not None:
            self.use_tensorizer: bool = use_tensorizer
        elif env_tensorizer is not None:
            self.use_tensorizer = env_tensorizer.lower() in ("1", "true", "yes")
        else:
            self.use_tensorizer = bool(self.file_data.get("use_tensorizer", DEFAULT_USE_TENSORIZER))

        # Path to official Goose config file
        self.config_path: Path = GOOSE_CONFIG_PATH

    @property
    def model(self) -> str:
        """
        The main target LLM served on vLLM.

        Returns:
            str: Model alias, HuggingFace repo ID, or display name.
        """
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        """
        Sets the model and records that it was chosen deliberately.

        Assigning a model is what `puffin-admin main-model set` does, and it is a pin by definition — the
        caller named this model. That has to be remembered separately from the value itself, or
        save_config() cannot tell a deliberate choice from a value that merely matches today's
        default, and would drop the former on the floor.

        Args:
            value (str): Model alias, HuggingFace repo ID, or display name.
        """
        self._model = value
        self._model_pinned = True

    @property
    def diffusion_model(self) -> str:
        """
        The diffusion model served beside the main one.

        Returns:
            str: Model alias, HuggingFace repo ID, or display name.
        """
        return self._diffusion_model

    @diffusion_model.setter
    def diffusion_model(self, value: str) -> None:
        """
        Sets the diffusion model and records that it was chosen deliberately.

        Assigning through this property is what `puffin-admin diffusion-model set` does, and it is a pin
        by definition -- same contract as the main model's setter above.

        Args:
            value (str): Model alias, HuggingFace repo ID, or display name.
        """
        self._diffusion_model = value
        self._diffusion_model_pinned = True

    def save_config(self, target_path: Optional[Path] = None) -> Path:
        """
        Saves current active configuration parameters to YAML or JSON config file.

        Args:
            target_path (Optional[Path]): Target output file path. Defaults to self.config_file_path.

        Returns:
            Path: Written config file path.
        """
        out_path = target_path or self.config_file_path
        data: Dict[str, Any] = {
            "vllm_host": self.vllm_host,
            "agent_runner": self.agent_runner,
        }
        # Written whenever the model was chosen, not merely whenever it differs from the default.
        # The two came apart the moment DEFAULT_MODEL changed: a workspace pinned to what was then
        # the default had written no `model` key at all, so it silently followed the default to a
        # different checkpoint. Every other field below can be re-derived from its default; this
        # one carries intent.
        if self.model and (self._model_pinned or self.model != DEFAULT_MODEL):
            data["model"] = self.model
        # Same intent-carrying rule as `model` above, for the same reason.
        if self.diffusion_model and (self._diffusion_model_pinned or self.diffusion_model != DEFAULT_DIFFUSION_MODEL):
            data["diffusion_model"] = self.diffusion_model
        if self.draft_model is not None: data["draft_model"] = self.draft_model
        if self.num_speculative_tokens != DEFAULT_SPECULATIVE_TOKENS: data["num_speculative_tokens"] = self.num_speculative_tokens
        if self.sandbox != DEFAULT_SANDBOX: data["sandbox"] = self.sandbox
        if self.hf_token is not None: data["hf_token"] = self.hf_token
        if self.enable_prefix_caching != DEFAULT_PREFIX_CACHING: data["enable_prefix_caching"] = self.enable_prefix_caching
        if self.enable_chunked_prefill != DEFAULT_CHUNKED_PREFILL: data["enable_chunked_prefill"] = self.enable_chunked_prefill
        if self.num_scheduler_steps != DEFAULT_SCHEDULER_STEPS: data["num_scheduler_steps"] = self.num_scheduler_steps
        if self.attention_backend != DEFAULT_ATTENTION_BACKEND: data["attention_backend"] = self.attention_backend
        if self.kv_cache_dtype != DEFAULT_KV_CACHE_DTYPE: data["kv_cache_dtype"] = self.kv_cache_dtype
        if self.cave_mode != DEFAULT_CAVE_MODE: data["cave_mode"] = self.cave_mode
        if self.guided_decoding_backend != DEFAULT_GUIDED_DECODING_BACKEND: data["guided_decoding_backend"] = self.guided_decoding_backend
        if self.use_tensorizer != DEFAULT_USE_TENSORIZER: data["use_tensorizer"] = self.use_tensorizer

        return ConfigFileStorageManager.save_config_dict(out_path, data)

    def resolve_tool_call_parser(self) -> str:
        """
        Returns the vLLM tool-call parser that matches the configured model.

        Returns:
            str: Parser name (e.g. 'hermes', 'qwen3_xml').
        """
        from dreamference.vllm_server.vllm_server_manager import VLLMServerManager
        return VLLMServerManager().resolve_tool_call_parser(self.model)

    def build_instructions(self) -> str:
        """
        Builds the Goose `instructions` block for the configured model.

        The Hermes prompt teaches the model to wrap tool calls in `<tool_call>` tags, which is only
        correct when vLLM is running the hermes parser. Models served through a different parser
        (Qwen 3.6 emits XML) already produce the format their parser expects, and instructing them
        to emit Hermes tags instead yields tool calls that the server cannot parse.

        Returns:
            str: Instructions text, possibly empty when no prompt is warranted.
        """
        parts = []
        if self.resolve_tool_call_parser() == "hermes":
            parts.append(HERMES_TOOL_CALL_PROMPT)
        if self.cave_mode:
            parts.append(CAVE_MODE_PROMPT)
        return "\n\n".join(parts)

    def validate_model(self) -> Tuple[bool, str]:
        """
        Validates selected main and draft models against GB10 hardware memory specs.

        Returns:
            Tuple[bool, str]: Tuple of (is_valid, validation_message).
        """
        if self.draft_model:
            from dreamference.hardware import check_speculative_compatibility
            return check_speculative_compatibility(self.model, self.draft_model)
        return check_model_compatibility(self.model)

    def get_env_vars(self) -> Dict[str, str]:
        """
        Generates environment variables required for Goose agent processes.
        Includes GOOSE_ALLOW_SHELL=1 and GOOSE_ALLOW_READ=1 so the built-in
        developer extension can execute real OS shell commands (/bin/bash -c ...)
        instead of emitting simulated JSON tool calls.

        Returns:
            Dict[str, str]: Environment variables dictionary (GOOSE_PROVIDER, OPENAI_BASE_URL, HF_TOKEN, etc.).
        """
        from dreamference.hardware import resolve_model_hf_repo
        resolved_model = resolve_model_hf_repo(self.model)
        base_url = self.vllm_host.rstrip("/") + "/v1"
        env: Dict[str, str] = {
            "GOOSE_PROVIDER": "openai",
            "OPENAI_BASE_URL": base_url,
            "OPENAI_API_KEY": "gb10-local-token",
            "GOOSE_MODEL": resolved_model,
            "GOOSE_ALLOW_SHELL": "1",
            "GOOSE_ALLOW_READ": "1",
            "GOOSE_TELEMETRY_OFF": "1",
        }
        if self.hf_token:
            env["HF_TOKEN"] = self.hf_token
            env["HUGGING_FACE_HUB_TOKEN"] = self.hf_token
        return env

    def ensure_goose_config(self, extra_mcp_servers: Optional[Dict[str, Any]] = None) -> None:
        """
        Ensures ~/.config/goose/config.yaml is updated with local vLLM OpenAI endpoint settings,
        the built-in "developer" extension (real OS shell execution via /bin/bash with allow_shell), and the stdio MCP extension.
        When cave_mode=True, injects the strict Cave Mode system prompt via the "instructions" key.

        Args:
            extra_mcp_servers (Optional[Dict[str, Any]]): Optional additional MCP extensions to merge.
        """
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        from dreamference.hardware import resolve_model_hf_repo
        resolved_model = resolve_model_hf_repo(self.model)
        base_url = self.vllm_host.rstrip("/") + "/v1"
        config_data: Dict[str, Any] = {
            "provider": "openai",
            "openai": {
                "base_url": base_url,
                "api_key": "gb10-local-token",
                "model": resolved_model
            },
            "extensions": {
                "developer": {
                    "enabled": True,
                    "type": "builtin",
                    "allow_shell": True
                },
                "jetbrains_mcp": {
                    "enabled": True,
                    "type": "stdio",
                    "cmd": "dreamference",
                    "args": ["mcp"]
                }
            }
        }
        config_data["instructions"] = self.build_instructions()

        if extra_mcp_servers:
            config_data["extensions"].update(extra_mcp_servers)

        if yaml:
            if self.config_path.exists():
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        existing = yaml.safe_load(f) or {}
                    if isinstance(existing, dict):
                        # Deep-merge extensions so developer + jetbrains_mcp are never lost
                        if "extensions" in existing and isinstance(existing["extensions"], dict):
                            existing["extensions"].update(config_data.get("extensions", {}))
                            config_data["extensions"] = existing["extensions"]
                        existing.update(config_data)
                        config_data = existing
                        # Force developer extension (with allow_shell) so real /bin/bash is always used
                        if "extensions" not in config_data or not isinstance(config_data["extensions"], dict):
                            config_data["extensions"] = {}
                        config_data["extensions"]["developer"] = {
                            "enabled": True,
                            "type": "builtin",
                            "allow_shell": True
                        }
                        # Re-assert our instructions: the merge above lets a stale user-authored
                        # block (or a previous model's tool-call format) win otherwise.
                        config_data["instructions"] = self.build_instructions()
                except Exception:
                    pass

            print(f"[dreamference] Writing Goose config to {self.config_path}")
            print(f"[dreamference] Final developer extension: {config_data.get('extensions', {}).get('developer')}")
            if self.cave_mode:
                print(f"[dreamference] Final instructions (cave): {config_data.get('instructions', '')[:80]}...")
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False)
        else:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)

    def write_temporary_goose_config(self, extra_mcp_servers: Optional[Dict[str, Any]] = None) -> "Path":
        """
        Creates a fresh Goose config file in /tmp/dreamference with a random name.
        The file contains the local vLLM endpoint, developer extension (with allow_shell),
        jetbrains_mcp, and cave instructions when enabled.
        The caller is responsible for deleting the file after use.

        Returns:
            Path: Absolute path to the generated temporary config file.
        """
        import uuid
        from pathlib import Path as _Path

        tmp_dir = _Path("/tmp/dreamference")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = tmp_dir / f"goose_{uuid.uuid4().hex}.yaml"

        from dreamference.hardware import resolve_model_hf_repo
        resolved_model = resolve_model_hf_repo(self.model)
        base_url = self.vllm_host.rstrip("/") + "/v1"
        config_data: Dict[str, Any] = {
            "provider": "openai",
            "openai": {
                "base_url": base_url,
                "api_key": "gb10-local-token",
                "model": resolved_model
            },
            "extensions": {
                "developer": {
                    "enabled": True,
                    "type": "builtin",
                    "allow_shell": True
                },
                "jetbrains_mcp": {
                    "enabled": True,
                    "type": "stdio",
                    "cmd": "dreamference",
                    "args": ["mcp"]
                }
            }
        }
        config_data["instructions"] = self.build_instructions()

        if extra_mcp_servers:
            config_data["extensions"].update(extra_mcp_servers)

        if yaml:
            with open(temp_path, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False)
        else:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)

        return temp_path
