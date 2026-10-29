# ruff: noqa: B023, C901, RUF001
"""Seeded random inputs for the configuration readers, writers and tiers.

No `random` module and no extra dependency: a small xorshift generator, a fixed seed list, so a
failure repeats on every machine. `MIGHTLING_FUZZ_CASES` sets the cases per seed (default 600);
a long hunt is `MIGHTLING_FUZZ_CASES=200000`. The properties:

- a configuration file of any bytes is read without raising, and what a writer wrote reads back;
- every setting resolves argument, then environment, then file, then its default, and a value a
  tier holds that is not valid is skipped rather than ending the search;
- the config path resolves custom path, environment, local files, global file, in that order;
- the `NO_PROXY` merge keeps every entry once, in order, and is stable when applied twice.
"""

import json
import os

import pytest

import dreamference.config.config_path_resolver as path_resolver
from dreamference.config import ConfigPathResolver, DreamferenceConfig, ProxyBypass
from dreamference.config.config_file_storage_manager import ConfigFileStorageManager
from dreamference.config.dreamference_config import (
    DEFAULT_MIGHTLING_PROMPT,
    DEFAULT_MIGHTLING_REFINE_VERSION,
    DEFAULT_MODEL,
    DEFAULT_SPECULATIVE_TOKENS,
    DEFAULT_VLLM_HOST,
)

SEEDS = (1, 2, 3, 0x9E3779B97F4A7C15, 0xDEADBEEFCAFEF00D)
CASES = int(os.environ.get("MIGHTLING_FUZZ_CASES", "600"))
MASK = (1 << 64) - 1

# The bytes that decide how a reader splits or quotes: quotes, backslashes, `=`, `#`, `:`, line ends,
# a byte order mark, tabs, non-ASCII, and plain words.
PIECES = (
    '"', "'", "\\", "=", "#", ":", "\n", "\r\n", "\r", "﻿", "\t", " ", "  ", "é", "ß", "日本",
    "\U0001f600", "a", "b", "model", "-", "_", ".", "/", "[", "]", "{", "}", ",", "%", "0", "1",
    "on", "off", "true", "no", "~", " ", " ",
)  # fmt: skip


class XorShift64:
    """A tiny repeatable generator; the same seed gives the same cases everywhere."""

    def __init__(self, seed: int) -> None:
        self.state = seed & MASK or 1

    def next(self) -> int:
        x = self.state
        x ^= (x << 13) & MASK
        x ^= x >> 7
        x ^= (x << 17) & MASK
        self.state = x
        return x

    def below(self, bound: int) -> int:
        return self.next() % bound

    def chance(self, numerator: int, denominator: int) -> bool:
        return self.below(denominator) < numerator

    def pick(self, options):
        return options[self.below(len(options))]

    def text(self, longest: int = 12) -> str:
        return "".join(self.pick(PIECES) for _ in range(self.below(longest + 1)))

    def word(self, longest: int = 10) -> str:
        return "".join(self.pick("abcdefgh019-_.") for _ in range(1 + self.below(longest)))


def cases(rng: XorShift64):
    for _ in range(CASES):
        yield rng


# -- files of any bytes, and what the writers write ---------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_reading_a_file_of_any_bytes_never_raises(seed, tmp_path):
    rng = XorShift64(seed)
    for _ in range(max(1, CASES // 10)):
        suffix = rng.pick((".toml", ".json", ".yaml", ".yml", ".conf"))
        body = rng.text(40).encode("utf-8", "surrogatepass")
        if rng.chance(1, 5):
            body = bytes(rng.below(256) for _ in range(rng.below(30)))
        if rng.chance(1, 4):
            body = b"\xef\xbb\xbf" + body
        path = tmp_path / f"c{suffix}"
        path.write_bytes(body)
        assert isinstance(ConfigFileStorageManager.load_config_dict(path), dict), body


def flat_value(rng: XorShift64):
    kind = rng.below(4)
    if kind == 0:
        return rng.below(1000)
    if kind == 1:
        return rng.chance(1, 2)
    # TOML and YAML readers differ on control characters; the values a configuration holds are
    # names, URLs and tokens, so the text here keeps to printable characters.
    text = "".join(c for c in rng.text(10) if c.isprintable() or c in "\t")
    # The `toml` package reads a value made of nothing but double quotes back as an empty string;
    # the package is not ours to fix, and no setting is ever just quotes.
    return text + "x" if text and set(text) == {'"'} else text


@pytest.mark.parametrize("seed", SEEDS)
def test_what_a_writer_wrote_reads_back_in_every_format(seed, tmp_path):
    rng = XorShift64(seed)
    for index in range(max(1, CASES // 5)):
        suffix = rng.pick((".toml", ".json", ".yaml"))
        data = {f"key_{rng.word()}": flat_value(rng) for _ in range(1 + rng.below(5))}
        if rng.chance(1, 3):
            data["night"] = {"hours": rng.below(24), "name": flat_value(rng)}
        path = tmp_path / f"{index % 7}{suffix}"
        path.unlink(missing_ok=True)
        ConfigFileStorageManager.save_config_dict(path, data)
        assert ConfigFileStorageManager.load_config_dict(path) == data, (suffix, data)
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("suffix", (".toml", ".json", ".yaml"))
def test_a_file_saved_with_a_byte_order_mark_reads_like_one_without(suffix, tmp_path):
    texts = {
        ".toml": 'model = "m"\nmightling_airgapped = "on"\n',
        ".json": '{"model": "m", "mightling_airgapped": "on"}',
        ".yaml": "model: m\nmightling_airgapped: 'on'\n",
    }
    plain, marked = tmp_path / f"plain{suffix}", tmp_path / f"marked{suffix}"
    plain.write_bytes(texts[suffix].encode())
    marked.write_bytes(b"\xef\xbb\xbf" + texts[suffix].encode())
    assert ConfigFileStorageManager.load_config_dict(marked) == {
        "model": "m",
        "mightling_airgapped": "on",
    }
    assert ConfigFileStorageManager.load_config_dict(
        plain
    ) == ConfigFileStorageManager.load_config_dict(marked)


# -- the tiers ----------------------------------------------------------------------------------


ENV_NAMES = (
    "DREAMFERENCE_VLLM_HOST",
    "DREAMFERENCE_MODEL",
    "DREAMFERENCE_DRAFT_MODEL",
    "DREAMFERENCE_SPECULATIVE_TOKENS",
    "DREAMFERENCE_AGENT",
    "DREAMFERENCE_RUNNER",
    "DREAMFERENCE_SERVER_ENGINE",
    "DREAMFERENCE_MIGHTLING_AIRGAPPED",
    "DREAMFERENCE_MIGHTLING_PROMPT",
    "DREAMFERENCE_MIGHTLING_REFINE_VERSION",
    "DREAMFERENCE_CONFIG_PATH",
)

ENGINES = ("ling", "sglang", "vllm")
LEVELS = ("off", "on")
VERSIONS = ("v1", "v2")


def first_valid(tiers, valid, default):
    """The first tier whose value is valid; an absent or invalid tier is skipped."""
    for value in tiers:
        got = valid(value)
        if got is not None:
            return got
    return default


def valid_engine(value):
    return (
        value.strip().lower()
        if isinstance(value, str) and value.strip().lower() in ENGINES
        else None
    )


def valid_level(value):
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, str) and value.strip().lower() in LEVELS:
        return value.strip().lower()
    return None


def valid_prompt(value):
    if not isinstance(value, str):
        return None
    name = value.strip()
    ok = 1 <= len(name) <= 64 and all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name)
    return name if ok and name[0] != "-" else None


def valid_version(value):
    if isinstance(value, str) and value.strip().lower() in VERSIONS:
        return value.strip().lower()
    return None


def maybe(rng: XorShift64, options):
    """One of the options, or the sentinel for 'this tier holds nothing'."""
    return None if rng.chance(2, 5) else rng.pick(options)


STRING_VALUES = ("alpha", "beta", "http://10.0.0.5:8000", "x y", "é", "", "Gamma")
ENGINE_VALUES = ("ling", " SGLang ", "vllm", "VLLM", "tgi", "", "ling\n", 7, True, "sg lang")
LEVEL_VALUES = ("on", "off", " ON ", "Off", "duckduckgo", "", True, False, 3, "yes")
PROMPT_VALUES = ("default", " plain ", "a-b", "Upper", "-x", "a_b", "x" * 64, "x" * 65, "", 5, "é")
VERSION_VALUES = ("v1", "V2", " v2 ", "v3", "", "1", 2)


def clear_environment(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("DREAMFERENCE_") or name in ("HF_TOKEN",):
            monkeypatch.delenv(name)


@pytest.mark.parametrize("seed", SEEDS)
def test_each_setting_resolves_argument_then_environment_then_file_then_default(
    seed, tmp_path, monkeypatch
):
    rng = XorShift64(seed)
    clear_environment(monkeypatch)
    path = tmp_path / "dreamference.json"
    for _ in range(CASES):
        for name in ENV_NAMES:
            monkeypatch.delenv(name, raising=False)
        arg, env, file = {}, {}, {}

        def tier(key, arg_name, env_name, options):
            if (value := maybe(rng, options)) is not None:
                arg[arg_name] = value
            if env_name and (value := maybe(rng, options)) is not None and isinstance(value, str):
                env[env_name] = value
            if (value := maybe(rng, options)) is not None:
                file[key] = value

        tier("vllm_host", "vllm_host", "DREAMFERENCE_VLLM_HOST", STRING_VALUES)
        tier("model", "model", "DREAMFERENCE_MODEL", STRING_VALUES)
        tier("draft_model", "draft_model", "DREAMFERENCE_DRAFT_MODEL", STRING_VALUES)
        tier("server_engine", "server_engine", "DREAMFERENCE_SERVER_ENGINE", ENGINE_VALUES)
        tier(
            "mightling_airgapped",
            "mightling_airgapped",
            "DREAMFERENCE_MIGHTLING_AIRGAPPED",
            LEVEL_VALUES,
        )
        tier("mightling_prompt", "mightling_prompt", "DREAMFERENCE_MIGHTLING_PROMPT", PROMPT_VALUES)
        tier(
            "mightling_refine_version",
            "mightling_refine_version",
            "DREAMFERENCE_MIGHTLING_REFINE_VERSION",
            VERSION_VALUES,
        )
        if rng.chance(1, 2):
            env["DREAMFERENCE_AGENT" if rng.chance(1, 2) else "DREAMFERENCE_RUNNER"] = rng.pick(
                ("Cline", "continue", "OPENHANDS")
            )
        if (value := maybe(rng, ("Cline", "Codex"))) is not None:
            arg["agent_runner"] = value
        if (value := maybe(rng, ("continue", "Cline"))) is not None:
            file["agent_runner"] = value
        if rng.chance(1, 2):
            env["DREAMFERENCE_SPECULATIVE_TOKENS"] = str(rng.below(50))
        if rng.chance(1, 2):
            arg["num_speculative_tokens"] = rng.below(50)
        if rng.chance(1, 2):
            file["num_speculative_tokens"] = rng.below(50)

        path.write_text(json.dumps(file))
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        config = DreamferenceConfig(config_file=str(path), **arg)

        def chosen(arg_name, env_name, file_name, default):
            if arg_name in arg:
                return arg[arg_name]
            if env_name in env:
                return env[env_name]
            return file.get(file_name, default)

        assert config.vllm_host == chosen(
            "vllm_host", "DREAMFERENCE_VLLM_HOST", "vllm_host", DEFAULT_VLLM_HOST
        )
        assert config.model == chosen("model", "DREAMFERENCE_MODEL", "model", DEFAULT_MODEL)
        assert config.draft_model == chosen(
            "draft_model", "DREAMFERENCE_DRAFT_MODEL", "draft_model", None
        )
        assert config._model_pinned == (
            "model" in arg or "DREAMFERENCE_MODEL" in env or "model" in file
        )
        assert config.num_speculative_tokens == int(
            arg.get(
                "num_speculative_tokens",
                env.get(
                    "DREAMFERENCE_SPECULATIVE_TOKENS",
                    file.get("num_speculative_tokens", DEFAULT_SPECULATIVE_TOKENS),
                ),
            )
        )
        runner = (
            arg.get("agent_runner")
            or env.get("DREAMFERENCE_AGENT")
            or env.get("DREAMFERENCE_RUNNER")
            or file.get("agent_runner")
            or "codex"
        )
        assert config.agent_runner == runner.lower()
        assert config.server_engine == first_valid(
            (
                arg.get("server_engine"),
                env.get("DREAMFERENCE_SERVER_ENGINE"),
                file.get("server_engine"),
            ),
            valid_engine,
            None,
        )
        assert config.mightling_airgapped == first_valid(
            (
                arg.get("mightling_airgapped"),
                env.get("DREAMFERENCE_MIGHTLING_AIRGAPPED"),
                file.get("mightling_airgapped"),
            ),
            valid_level,
            "off",
        )
        assert config.mightling_prompt == first_valid(
            (
                arg.get("mightling_prompt"),
                env.get("DREAMFERENCE_MIGHTLING_PROMPT"),
                file.get("mightling_prompt"),
            ),
            valid_prompt,
            DEFAULT_MIGHTLING_PROMPT,
        )
        assert config.mightling_refine_version == first_valid(
            (
                arg.get("mightling_refine_version"),
                env.get("DREAMFERENCE_MIGHTLING_REFINE_VERSION"),
                file.get("mightling_refine_version"),
            ),
            valid_version,
            DEFAULT_MIGHTLING_REFINE_VERSION,
        )


@pytest.mark.parametrize("seed", SEEDS)
def test_a_saved_configuration_loads_back_to_the_same_settings(seed, tmp_path, monkeypatch):
    rng = XorShift64(seed)
    clear_environment(monkeypatch)
    for index in range(max(1, CASES // 5)):
        suffix = rng.pick((".json", ".yaml", ".toml"))
        path = tmp_path / f"saved{index % 5}{suffix}"
        path.unlink(missing_ok=True)
        # Only a token named as an argument is saved; one set later, like one from the environment, is not.
        token = rng.pick((None, "tok", "t=k", 't"k', "t#k"))
        config = DreamferenceConfig(config_file=str(path), hf_token=token)
        config.vllm_host = rng.pick(STRING_VALUES[:4]) or "http://h:1"
        config.model = rng.pick(("alpha", "a=b", 'q"uote', "back\\slash", "h#sh", "col:on"))
        config.draft_model = rng.pick((None, "d", "d=1", "e#f"))
        config.num_speculative_tokens = rng.below(40)
        config.mightling_airgapped = rng.pick(LEVELS)
        config.mightling_refine = rng.chance(1, 2)
        config.server_engine = rng.pick((None, *ENGINES))
        saved = config.save_config()
        again = DreamferenceConfig(config_file=str(saved))
        for field in (
            "vllm_host",
            "model",
            "draft_model",
            "hf_token",
            "num_speculative_tokens",
            "mightling_airgapped",
            "mightling_refine",
            "server_engine",
        ):
            assert getattr(again, field) == getattr(config, field), (suffix, field)


# -- where the file is --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_the_config_path_resolves_in_tier_order(seed, tmp_path, monkeypatch):
    rng = XorShift64(seed)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    global_path = tmp_path / "home" / "config.toml"
    global_path.parent.mkdir()
    monkeypatch.setattr(path_resolver, "GLOBAL_DREAMFERENCE_CONFIG_PATH", global_path)
    custom_file, env_file = tmp_path / "custom.toml", tmp_path / "env.toml"
    for _ in range(max(1, CASES // 4)):
        present = {
            "toml": rng.chance(1, 2),
            "json": rng.chance(1, 2),
            "global": rng.chance(1, 2),
        }
        for name, path in (
            ("toml", work / "dreamference.toml"),
            ("json", work / "dreamference.json"),
            ("global", global_path),
        ):
            path.unlink(missing_ok=True)
            if present[name]:
                path.write_text("")
        custom = rng.pick((None, "", str(custom_file)))
        env = rng.pick((None, "", str(env_file)))
        monkeypatch.delenv("DREAMFERENCE_CONFIG_PATH", raising=False)
        if env is not None:
            monkeypatch.setenv("DREAMFERENCE_CONFIG_PATH", env)
        if custom:
            expected = custom_file
        elif env:
            expected = env_file
        elif present["toml"]:
            expected = work / "dreamference.toml"
        elif present["json"]:
            expected = work / "dreamference.json"
        elif present["global"]:
            expected = global_path
        else:
            expected = work / "dreamference.toml"
        assert ConfigPathResolver.resolve_path(custom) == expected.resolve()


# -- NO_PROXY -----------------------------------------------------------------------------------


def entry_list(rng: XorShift64) -> str | None:
    if rng.chance(1, 5):
        return None
    pool = (
        "localhost",
        "Example.COM",
        ".internal",
        "10.0.0.1",
        " ",
        "",
        "::1",
        "a b",
        "é.example",
        "*",
    )
    return ",".join(rng.pick(pool) for _ in range(rng.below(6)))


@pytest.mark.parametrize("seed", SEEDS)
def test_the_no_proxy_merge_keeps_every_entry_once_and_in_order(seed):
    rng = XorShift64(seed)
    for _ in range(CASES):
        upper, lower = entry_list(rng), entry_list(rng)
        urls = tuple(
            rng.pick(
                (
                    None,
                    "",
                    "http://10.1.2.3:8000/v1",
                    "gb10:8000",
                    "http://[::1",
                    "[",
                    rng.text(8),
                    "HTTP://Host.Local",
                )
            )
            for _ in range(rng.below(4))
        )
        merged = ProxyBypass.merged(upper, lower, urls)
        entries = merged.split(",") if merged else []
        keys = [entry.lower() for entry in entries]
        assert len(keys) == len(set(keys)), merged
        assert all(entry == entry.strip() and entry for entry in entries), merged
        existing = [e.strip() for v in (upper, lower) if v for e in v.split(",") if e.strip()]
        first_seen = list(dict.fromkeys(e.lower() for e in existing))
        assert keys[: len(first_seen)] == first_seen, (merged, upper, lower)
        assert {"localhost", "127.0.0.1", "::1"} <= set(keys)
        assert ProxyBypass.merged(merged, merged, urls) == merged
        env = {"NO_PROXY": upper or "", "no_proxy": lower or ""}
        ProxyBypass.apply(env, *urls)
        assert env["NO_PROXY"] == env["no_proxy"]
        assert ProxyBypass.apply(dict(env), *urls) == env


@pytest.mark.parametrize("seed", SEEDS)
def test_a_host_is_read_from_any_text_without_raising(seed):
    rng = XorShift64(seed)
    for _ in range(CASES):
        text = rng.text(14)
        if rng.chance(1, 3):
            text = rng.pick(("http://", "https://", "", "x:")) + text
        host = ProxyBypass.host_name(text)
        assert host is None or (isinstance(host, str) and host)
