"""
Main Dreamference Configuration Class.

This module provides the DreamferenceConfig class which merges CLI parameters, environment
variables, `.dreamference/config.yaml`, and system defaults into a unified settings object.
"""

import os
import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Final

from dreamference.hardware import MODEL_MATRIX, check_model_compatibility
from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS, DEFAULT_DIFFUSION_MODEL_ALIAS
from dreamference.config.config_path_resolver import ConfigPathResolver
from dreamference.config.config_file_storage_manager import ConfigFileStorageManager

# System defaults
DEFAULT_VLLM_HOST: Final[str] = "http://localhost:8000"
DEFAULT_MODEL: Final[str] = DEFAULT_MODEL_ALIAS
DEFAULT_DIFFUSION_MODEL: Final[str] = DEFAULT_DIFFUSION_MODEL_ALIAS
DEFAULT_SPECULATIVE_TOKENS: Final[int] = 8
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

DEFAULT_GUIDED_DECODING_BACKEND: Final[str] = "xgrammar"
DEFAULT_CAVE_MODE: Final[bool] = False
DEFAULT_USE_TENSORIZER: Final[bool] = False
# Whether the Mightling agent's prompt advertises `ling-admin gmail` when an account is connected.
# Read by the Rust launcher too (DREAMFERENCE_MIGHTLING_GMAIL, then `mightling_gmail` in the TOML file).
DEFAULT_MIGHTLING_GMAIL: Final[bool] = True
# Whether the Mightling agent is handed a rule-built ledger (files, failed commands, last test result)
# after each compaction (specs/DREAMFERENCE_MIGHTLING_COMPACTION.md §10.1). Read by the Rust launcher
# (DREAMFERENCE_MIGHTLING_COMPACTION_LEDGER, then `mightling_compaction_ledger` in the TOML file); a test
# keeps this default equal to LEDGER_DEFAULT in ling-rs/src/compaction.rs.
DEFAULT_MIGHTLING_COMPACTION_LEDGER: Final[bool] = True
# How tersely the Mightling agent answers (`/cavemode`, specs/DREAMFERENCE_MIGHTLING_CAVE_MODE.md). Read by
# the Rust launcher too (DREAMFERENCE_MIGHTLING_CAVE_MODE, then `mightling_cave_mode` in the TOML file);
# a test keeps this default equal to DEFAULT_MIGHTLING_CAVE_MODE in ling-rs/src/cave.rs.
DEFAULT_MIGHTLING_CAVE_MODE: Final[str] = "ultra"
MIGHTLING_CAVE_MODE_LEVELS: Final[tuple] = ("off", "lite", "full", "ultra")
# How much of the internet a Mightling session may use (`/airgapped`,
# specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md): everything or nothing. The Rust
# side (ling-rs/airgapped) resolves it for the agent's commands; a test keeps this default equal
# to its DEFAULT_MIGHTLING_AIRGAPPED. Listed loosest first.
DEFAULT_MIGHTLING_AIRGAPPED: Final[str] = "off"
MIGHTLING_AIRGAPPED_LEVELS: Final[tuple] = ("off", "on")
# The system prompt new ling sessions start with (`ling prompt`, specs/DREAMFERENCE_MIGHTLING_PROMPT.md):
# `default` (Codex's own), `high-swe`, or a custom prompt in `$CODEX_HOME/system-prompts/<name>.md`.
# The Rust launcher reads it too (DREAMFERENCE_MIGHTLING_PROMPT, then `mightling_prompt` in the TOML file)
# and decides whether a name is installed; a test keeps this default equal to DEFAULT_PROMPT in
# ling-rs/src/prompt.rs.
DEFAULT_MIGHTLING_PROMPT: Final[str] = "default"
MIGHTLING_PROMPT_NAME: Final[re.Pattern] = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

CAVE_MODE_PROMPT: Final[str] = (
    "You are in Cave Mode. You are a senior Staff Engineer. "
    "Do not explain your reasoning. Do not use pleasantries, greetings, or conclusions. "
    "Do not apologize. Output only the exact shell commands, tool calls, or code modifications "
    "required to complete the user's objective. If asked a question, answer in 15 words or less."
)

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
        mightling_gmail: Optional[bool] = None,
        mightling_compaction_ledger: Optional[bool] = None,
        mightling_cave_mode: Optional[str] = None,
        mightling_airgapped: Optional[str] = None,
        mightling_prompt: Optional[str] = None,
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

        # 6. Agent runner ('codex' [default], 'cline', 'continue' or 'openhands')
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

        env_mightling_gmail = os.getenv("DREAMFERENCE_MIGHTLING_GMAIL")
        if mightling_gmail is not None:
            self.mightling_gmail: bool = mightling_gmail
        elif env_mightling_gmail is not None:
            self.mightling_gmail = env_mightling_gmail.lower() in ("1", "true", "yes")
        else:
            self.mightling_gmail = bool(self.file_data.get("mightling_gmail", DEFAULT_MIGHTLING_GMAIL))

        env_ledger = os.getenv("DREAMFERENCE_MIGHTLING_COMPACTION_LEDGER")
        if mightling_compaction_ledger is not None:
            self.mightling_compaction_ledger: bool = mightling_compaction_ledger
        elif env_ledger:
            self.mightling_compaction_ledger = env_ledger.lower() in ("1", "true", "yes", "on")
        else:
            self.mightling_compaction_ledger = bool(
                self.file_data.get("mightling_compaction_ledger", DEFAULT_MIGHTLING_COMPACTION_LEDGER))

        # The same tiers the launcher reads, first valid value wins; an invalid one is skipped there
        # too, so a typo in one tier falls through rather than switching cave mode off.
        self.mightling_cave_mode: str = DEFAULT_MIGHTLING_CAVE_MODE
        for candidate in (mightling_cave_mode, os.getenv("DREAMFERENCE_MIGHTLING_CAVE_MODE"), self.file_data.get("mightling_cave_mode")):
            if isinstance(candidate, str) and candidate.strip().lower() in MIGHTLING_CAVE_MODE_LEVELS:
                self.mightling_cave_mode = candidate.strip().lower()
                break

        # The air-gap level, through the same tiers; an invalid value is skipped. This reads the one
        # configuration file this object resolved: the launcher also reads the user-level file and
        # takes the stricter of the two, because the agent can write the repository's.
        self.mightling_airgapped: str = DEFAULT_MIGHTLING_AIRGAPPED
        for candidate in (mightling_airgapped, os.getenv("DREAMFERENCE_MIGHTLING_AIRGAPPED"), self.file_data.get("mightling_airgapped")):
            level = self.parse_airgapped_level(candidate)
            if level is not None:
                self.mightling_airgapped = level
                break

        # The prompt's name, through the same tiers. Only its form is checked here: which prompts
        # are installed is known to the launcher, which skips a name it does not have.
        self.mightling_prompt: str = DEFAULT_MIGHTLING_PROMPT
        for candidate in (mightling_prompt, os.getenv("DREAMFERENCE_MIGHTLING_PROMPT"), self.file_data.get("mightling_prompt")):
            name = self.parse_prompt_name(candidate)
            if name is not None:
                self.mightling_prompt = name
                break

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

        Assigning a model is what `ling-admin main-model set` does, and it is a pin by definition — the
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

        Assigning through this property is what `ling-admin diffusion-model set` does, and it is a pin
        by definition -- same contract as the main model's setter above.

        Args:
            value (str): Model alias, HuggingFace repo ID, or display name.
        """
        self._diffusion_model = value
        self._diffusion_model_pinned = True

    @classmethod
    def parse_airgapped_level(cls, value: Any) -> Optional[str]:
        """
        Reads an air-gap level as the launcher does: a level name in any case. `duckduckgo` was a
        level until 2026-10-03 and is now an unknown name, like any other.

        A YAML file turns a bare `on` or `off` into a boolean, so those are accepted too.

        Args:
            value: What a tier holds.

        Returns:
            Optional[str]: `off` or `on`; None for anything else.
        """
        if isinstance(value, bool):
            return "on" if value else "off"
        if not isinstance(value, str):
            return None
        name = value.strip().lower()
        return name if name in MIGHTLING_AIRGAPPED_LEVELS else None

    @classmethod
    def parse_prompt_name(cls, value: Any) -> Optional[str]:
        """
        Reads a prompt's name as the launcher does: lowercase letters, digits and hyphens.

        Args:
            value: What a tier holds.

        Returns:
            Optional[str]: The name, stripped; None when it is not one a prompt can have.
        """
        if not isinstance(value, str):
            return None
        name = value.strip()
        return name if MIGHTLING_PROMPT_NAME.fullmatch(name) else None

    @classmethod
    def resolve_airgapped_level(cls, cwd: Optional[Path] = None) -> str:
        """
        Resolves the configured air-gap level the way the Rust side does for a command with no
        session (`ling-rs/airgapped`, tiers 2 to 4): `DREAMFERENCE_MIGHTLING_AIRGAPPED`, then the
        **stricter** of the two configuration files, then the default.

        The two files are the one `DREAMFERENCE_CONFIG_PATH` names (or `<cwd>/dreamference.toml`)
        and the user-level `~/.config/dreamference/config.toml`. The stricter wins because an agent
        can write the repository's file: it may tighten the user's level, never loosen it. An
        instance's `mightling_airgapped` reads one file only, so anything that *acts* on the level
        (the MCP server's web tools) asks here.

        Args:
            cwd: The directory whose `dreamference.toml` counts; defaults to the current one.

        Returns:
            str: `off` or `on`.
        """
        level = cls.parse_airgapped_level(os.getenv("DREAMFERENCE_MIGHTLING_AIRGAPPED"))
        if level is not None:
            return level
        custom = os.getenv("DREAMFERENCE_CONFIG_PATH")
        files = [Path(custom)] if custom else [Path(cwd or Path.cwd()) / "dreamference.toml"]
        user_level = Path.home() / ".config" / "dreamference" / "config.toml"
        if user_level not in files:
            files.append(user_level)
        strictest: Optional[str] = None
        for path in files:
            if not path.is_file():
                continue
            level = cls.parse_airgapped_level(
                ConfigFileStorageManager.load_config_dict(path).get("mightling_airgapped"))
            if level is not None and (strictest is None or MIGHTLING_AIRGAPPED_LEVELS.index(level)
                                      > MIGHTLING_AIRGAPPED_LEVELS.index(strictest)):
                strictest = level
        return strictest or DEFAULT_MIGHTLING_AIRGAPPED

    def save_config(self, target_path: Optional[Path] = None) -> Path:
        """
        Saves current active configuration parameters to YAML or JSON config file.

        Args:
            target_path (Optional[Path]): Target output file path. Defaults to self.config_file_path.

        Returns:
            Path: Written config file path.
        """
        out_path = target_path or self.config_file_path
        data: Dict[str, Any] = {}
        # Until 2026-10-02 these two were written whatever their value, the only fields that broke
        # the rule the rest of this method follows: a default written to the file stops following
        # the default, so a later change of DEFAULT_VLLM_HOST would have skipped every saved config.
        if self.vllm_host != DEFAULT_VLLM_HOST: data["vllm_host"] = self.vllm_host
        if self.agent_runner != DEFAULT_AGENT_RUNNER: data["agent_runner"] = self.agent_runner
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
        if self.hf_token is not None: data["hf_token"] = self.hf_token
        if self.enable_prefix_caching != DEFAULT_PREFIX_CACHING: data["enable_prefix_caching"] = self.enable_prefix_caching
        if self.enable_chunked_prefill != DEFAULT_CHUNKED_PREFILL: data["enable_chunked_prefill"] = self.enable_chunked_prefill
        if self.num_scheduler_steps != DEFAULT_SCHEDULER_STEPS: data["num_scheduler_steps"] = self.num_scheduler_steps
        if self.attention_backend != DEFAULT_ATTENTION_BACKEND: data["attention_backend"] = self.attention_backend
        if self.kv_cache_dtype != DEFAULT_KV_CACHE_DTYPE: data["kv_cache_dtype"] = self.kv_cache_dtype
        if self.cave_mode != DEFAULT_CAVE_MODE: data["cave_mode"] = self.cave_mode
        if self.guided_decoding_backend != DEFAULT_GUIDED_DECODING_BACKEND: data["guided_decoding_backend"] = self.guided_decoding_backend
        if self.use_tensorizer != DEFAULT_USE_TENSORIZER: data["use_tensorizer"] = self.use_tensorizer
        if self.mightling_gmail != DEFAULT_MIGHTLING_GMAIL: data["mightling_gmail"] = self.mightling_gmail
        if self.mightling_compaction_ledger != DEFAULT_MIGHTLING_COMPACTION_LEDGER:
            data["mightling_compaction_ledger"] = self.mightling_compaction_ledger
        if self.mightling_cave_mode != DEFAULT_MIGHTLING_CAVE_MODE: data["mightling_cave_mode"] = self.mightling_cave_mode
        if self.mightling_airgapped != DEFAULT_MIGHTLING_AIRGAPPED: data["mightling_airgapped"] = self.mightling_airgapped
        if self.mightling_prompt != DEFAULT_MIGHTLING_PROMPT: data["mightling_prompt"] = self.mightling_prompt

        return ConfigFileStorageManager.save_config_dict(out_path, data)

    def resolve_tool_call_parser(self) -> str:
        """
        Returns the vLLM tool-call parser that matches the configured model.

        Returns:
            str: Parser name (e.g. 'hermes', 'qwen3_xml').
        """
        from dreamference.vllm_server.vllm_server_manager import VLLMServerManager
        return VLLMServerManager().resolve_tool_call_parser(self.model)

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
