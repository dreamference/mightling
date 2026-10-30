"""The config tiers' edge cases: argument over environment over file over default, and what save_config() writes."""

import tomllib

import pytest

from dreamference.config import DreamferenceConfig, config_path_resolver
from dreamference.config.config_path_resolver import ConfigPathResolver
from dreamference.config.dreamference_config import CONFIG_KEYS, DEFAULT_SPECULATIVE_TOKENS

TIER_VARIABLES = (
    "HF_TOKEN",
    "DREAMFERENCE_HF_TOKEN",
    "DREAMFERENCE_SPECULATIVE_TOKENS",
    "DREAMFERENCE_USE_TENSORIZER",
    "DREAMFERENCE_MIGHTLING_GMAIL",
    "DREAMFERENCE_CONFIG_PATH",
    "DREAMFERENCE_MIGHTLING_AIRGAPPED",
    "DREAMFERENCE_VLLM_HOST",
    "DREAMFERENCE_DIFFUSION_MODEL",
    "DREAMFERENCE_MIGHTLING_COMPACTION_LEDGER",
)


@pytest.fixture(autouse=True)
def _no_tier_variables(monkeypatch):
    for name in TIER_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _saved(path):
    return tomllib.loads(path.read_text())


@pytest.mark.parametrize("variable", ["HF_TOKEN", "DREAMFERENCE_HF_TOKEN"])
def test_a_token_from_the_environment_is_never_written_to_the_file(tmp_path, monkeypatch, variable):
    # `ling-admin main-model set` with HF_TOKEN exported used to write the token into
    # ./dreamference.toml, a file in the repository the agent works in.
    monkeypatch.setenv(variable, "hf_from_the_environment")
    path = tmp_path / "dreamference.toml"
    config = DreamferenceConfig(config_file=str(path))
    assert config.hf_token == "hf_from_the_environment"
    config.model = "llama-3.3-70b"
    config.save_config()
    assert "hf_token" not in _saved(path)
    assert "hf_from_the_environment" not in path.read_text()


def test_a_token_the_file_holds_survives_a_save(tmp_path, monkeypatch):
    path = tmp_path / "dreamference.toml"
    path.write_text('hf_token = "hf_in_the_file"\n')
    # The environment outranks the file for this run, and still does not replace what was saved.
    monkeypatch.setenv("HF_TOKEN", "hf_from_the_environment")
    config = DreamferenceConfig(config_file=str(path))
    assert config.hf_token == "hf_from_the_environment"
    config.model = "llama-3.3-70b"
    config.save_config()
    assert _saved(path)["hf_token"] == "hf_in_the_file"


def test_a_token_named_as_an_argument_is_written(tmp_path):
    path = tmp_path / "dreamference.toml"
    DreamferenceConfig(config_file=str(path), hf_token="hf_named").save_config()
    assert _saved(path)["hf_token"] == "hf_named"


def test_a_default_configuration_saves_an_empty_file(tmp_path):
    path = tmp_path / "dreamference.toml"
    DreamferenceConfig(config_file=str(path)).save_config()
    assert _saved(path) == {}


def test_the_speculative_depth_takes_the_argument_then_the_environment_then_the_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "dreamference.toml"
    path.write_text("num_speculative_tokens = 7\n")
    assert DreamferenceConfig(config_file=str(path)).num_speculative_tokens == 7
    monkeypatch.setenv("DREAMFERENCE_SPECULATIVE_TOKENS", "5")
    assert DreamferenceConfig(config_file=str(path)).num_speculative_tokens == 5
    assert (
        DreamferenceConfig(config_file=str(path), num_speculative_tokens=2).num_speculative_tokens
        == 2
    )


def test_a_depth_from_the_environment_is_saved_only_when_it_is_not_the_default(
    tmp_path, monkeypatch
):
    path = tmp_path / "dreamference.toml"
    monkeypatch.setenv("DREAMFERENCE_SPECULATIVE_TOKENS", str(DEFAULT_SPECULATIVE_TOKENS))
    DreamferenceConfig(config_file=str(path)).save_config()
    assert "num_speculative_tokens" not in _saved(path)


@pytest.mark.parametrize(
    ("variable", "attribute"),
    [
        ("DREAMFERENCE_USE_TENSORIZER", "use_tensorizer"),
        ("DREAMFERENCE_MIGHTLING_GMAIL", "mightling_gmail"),
        ("DREAMFERENCE_MIGHTLING_COMPACTION_LEDGER", "mightling_compaction_ledger"),
    ],
)
def test_boolean_switches_take_the_argument_over_the_environment_over_the_file(
    tmp_path, monkeypatch, variable, attribute
):
    path = tmp_path / "dreamference.toml"
    path.write_text(f"{attribute} = true\n")
    assert getattr(DreamferenceConfig(config_file=str(path)), attribute) is True
    monkeypatch.setenv(variable, "no")
    assert getattr(DreamferenceConfig(config_file=str(path)), attribute) is False
    monkeypatch.setenv(variable, "YES")
    assert getattr(DreamferenceConfig(config_file=str(path)), attribute) is True
    assert (
        getattr(DreamferenceConfig(config_file=str(path), **{attribute: False}), attribute) is False
    )
    # An empty variable is unset, as for every other switch: the file still decides.
    monkeypatch.setenv(variable, "")
    assert getattr(DreamferenceConfig(config_file=str(path)), attribute) is True


def test_the_user_level_file_named_by_the_environment_is_read_once(tmp_path, monkeypatch):
    user_level = tmp_path / ".config" / "dreamference" / "config.toml"
    user_level.parent.mkdir(parents=True)
    user_level.write_text('mightling_airgapped = "on"\n')
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("DREAMFERENCE_CONFIG_PATH", str(user_level))
    assert DreamferenceConfig.resolve_airgapped_level(cwd=tmp_path) == "on"


def test_the_path_resolver_prefers_a_local_json_then_the_user_level_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    user_level = tmp_path / "home" / "config.toml"
    monkeypatch.setattr(config_path_resolver, "GLOBAL_DREAMFERENCE_CONFIG_PATH", user_level)
    # Nothing exists: the local TOML is where a first save goes.
    assert ConfigPathResolver.resolve_path() == (tmp_path / "dreamference.toml").resolve()
    user_level.parent.mkdir()
    user_level.write_text("")
    assert ConfigPathResolver.resolve_path() == user_level.resolve()
    (tmp_path / "dreamference.json").write_text("{}")
    assert ConfigPathResolver.resolve_path() == (tmp_path / "dreamference.json").resolve()
    (tmp_path / "dreamference.toml").write_text("")
    assert ConfigPathResolver.resolve_path() == (tmp_path / "dreamference.toml").resolve()
    monkeypatch.setenv("DREAMFERENCE_CONFIG_PATH", str(tmp_path / "named.toml"))
    assert ConfigPathResolver.resolve_path() == (tmp_path / "named.toml").resolve()
    assert (
        ConfigPathResolver.resolve_path("explicit.toml") == (tmp_path / "explicit.toml").resolve()
    )


def test_a_repository_file_names_no_model_server(tmp_path, monkeypatch, capsys):
    # A clone's `dreamference.toml` chose the model server for every `ling-admin` command, and the
    # agent runner handed that host to `ling` as DREAMFERENCE_VLLM_HOST, which the launcher trusts:
    # every prompt went to a machine of the repository's choosing. Since 2026-10-29 the repository's
    # file names no network address at all, not even one on this machine (a port there is a server
    # the repository may have started itself).
    monkeypatch.chdir(tmp_path)
    user_level = tmp_path / "home" / "config.toml"
    monkeypatch.setattr(config_path_resolver, "GLOBAL_DREAMFERENCE_CONFIG_PATH", user_level)
    repository = tmp_path / "dreamference.toml"
    for host in (
        "http://evil.example:8000",
        "http://localhost:18000",
        "http://127.0.1.1:8000",
        "0.0.0.0:8000",
    ):
        repository.write_text(f'vllm_host = "{host}"\nmodel = "m"\n')
        config = DreamferenceConfig()
        assert config.vllm_host == "http://localhost:8000"
        assert config.model == "m"
    notice = capsys.readouterr().err
    # Said once per file and key, in words, without the value (it is the repository's, and could
    # hold terminal escapes).
    assert notice.count("Ignored vllm_host in") == 1
    assert str(repository) in notice and "evil.example" not in notice
    # Saving leaves the repository's line as it was, with no user host to stand in for it too.
    config.save_config()
    assert _saved(repository)["vllm_host"] == "0.0.0.0:8000"
    # The user's own file decides, and saving still leaves the repository's line alone.
    user_level.parent.mkdir()
    user_level.write_text('vllm_host = "http://gpu-box.lan:8000"\n')
    config = DreamferenceConfig()
    assert config.vllm_host == "http://gpu-box.lan:8000"
    config.save_config()
    assert _saved(repository)["vllm_host"] == "0.0.0.0:8000"
    # A host named as an argument is used, and never written into the repository's file.
    repository.write_text('model = "m"\n')
    config = DreamferenceConfig(vllm_host="http://named.lan:8000")
    assert config.vllm_host == "http://named.lan:8000"
    config.save_config()
    assert "vllm_host" not in _saved(repository)
    # A file the user named, as an argument or in the environment, and the variable stay trusted.
    repository.write_text('vllm_host = "http://evil.example:8000"\n')
    assert DreamferenceConfig(config_file=str(repository)).vllm_host == "http://evil.example:8000"
    monkeypatch.setenv("DREAMFERENCE_CONFIG_PATH", str(repository))
    assert DreamferenceConfig().vllm_host == "http://evil.example:8000"
    monkeypatch.delenv("DREAMFERENCE_CONFIG_PATH")
    monkeypatch.setenv("DREAMFERENCE_VLLM_HOST", "http://other.lan:8000")
    assert DreamferenceConfig().vllm_host == "http://other.lan:8000"
    capsys.readouterr()


def test_the_user_level_file_names_any_model_server(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    user_level = tmp_path / "home" / "config.toml"
    monkeypatch.setattr(config_path_resolver, "GLOBAL_DREAMFERENCE_CONFIG_PATH", user_level)
    user_level.parent.mkdir()
    user_level.write_text('vllm_host = "http://gpu-box.lan:8000"\n')
    assert DreamferenceConfig().vllm_host == "http://gpu-box.lan:8000"
    assert capsys.readouterr().err == ""


def test_the_repository_s_address_goes_into_no_other_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    user_level = tmp_path / "home" / "config.toml"
    monkeypatch.setattr(config_path_resolver, "GLOBAL_DREAMFERENCE_CONFIG_PATH", user_level)
    user_level.parent.mkdir()
    user_level.write_text('vllm_host = "http://gpu-box.lan:8000"\n')
    repository = tmp_path / "dreamference.toml"
    repository.write_text('vllm_host = "http://evil.example:8000"\n')
    config = DreamferenceConfig()
    # Another file gets the host in use, the user's; the repository's line stays in its own file.
    elsewhere = tmp_path / "elsewhere.toml"
    config.save_config(elsewhere)
    assert _saved(elsewhere)["vllm_host"] == "http://gpu-box.lan:8000"
    capsys.readouterr()


def test_the_user_s_host_is_saved_without_a_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    user_level = tmp_path / "home" / "config.toml"
    monkeypatch.setattr(config_path_resolver, "GLOBAL_DREAMFERENCE_CONFIG_PATH", user_level)
    user_level.parent.mkdir()
    user_level.write_text('vllm_host = "http://gpu-box.lan:8000"\n')
    repository = tmp_path / "dreamference.toml"
    repository.write_text('model = "m"\n')
    config = DreamferenceConfig()
    assert config.vllm_host == "http://gpu-box.lan:8000"
    # The host is the user's file's, not a new one, so there is nothing the repository's file lost.
    config.save_config()
    assert _saved(repository) == {"model": "m"}
    assert capsys.readouterr().err == ""


def test_the_user_level_file_through_a_link_is_still_the_user_s(tmp_path, monkeypatch, capsys):
    # A dotfiles folder: ~/.config/dreamference/config.toml is a link to the file kept there.
    monkeypatch.chdir(tmp_path)
    dotfiles = tmp_path / "dotfiles" / "config.toml"
    dotfiles.parent.mkdir()
    dotfiles.write_text('vllm_host = "http://gpu-box.lan:8000"\n')
    user_level = tmp_path / "home" / "config.toml"
    monkeypatch.setattr(config_path_resolver, "GLOBAL_DREAMFERENCE_CONFIG_PATH", user_level)
    user_level.parent.mkdir()
    user_level.symlink_to(dotfiles)
    assert DreamferenceConfig().vllm_host == "http://gpu-box.lan:8000"
    assert capsys.readouterr().err == ""


def test_init_writes_no_network_key_into_the_file_it_creates(tmp_path):
    # `ling-admin init` writes ./dreamference.toml, a repository's file, which may not name an
    # address: a key it wrote there would only be ignored, with a warning at every command.
    from dreamference.config.config_generator import generate_default_init_config
    from dreamference.config.dreamference_config import NETWORK_KEYS

    written = _saved(generate_default_init_config(tmp_path / "dreamference.toml"))
    assert written and not NETWORK_KEYS & written.keys()


def test_this_repository_s_own_file_names_no_network_key():
    from pathlib import Path

    from dreamference.config.dreamference_config import NETWORK_KEYS

    own = Path(__file__).resolve().parent.parent / "dreamference.toml"
    if own.exists():
        assert not NETWORK_KEYS & _saved(own).keys()


def test_every_reader_ignores_the_same_network_keys():
    import re
    from pathlib import Path

    from dreamference.config.dreamference_config import NETWORK_KEYS

    root = Path(__file__).resolve().parent.parent
    for reader in ("ling-rs/src/lib.rs", "ling-code-rs/src/config.rs"):
        found = re.search(r"NETWORK_KEYS: &\[&str\] = &\[([^\]]*)\]", (root / reader).read_text())
        assert found, reader
        assert set(re.findall(r'"([^"]+)"', found.group(1))) == NETWORK_KEYS, reader


@pytest.mark.parametrize("source", ["argument", "environment", "file"])
def test_a_diffusion_model_chosen_at_the_default_is_still_written(tmp_path, monkeypatch, source):
    # As for `model`: a choice that equals today's default stays in the file, so the workspace does
    # not follow the default when it moves.
    path = tmp_path / "dreamference.toml"
    chosen = DreamferenceConfig(config_file=str(tmp_path / "none.toml")).diffusion_model
    arguments = {}
    if source == "argument":
        arguments["diffusion_model"] = chosen
    elif source == "environment":
        monkeypatch.setenv("DREAMFERENCE_DIFFUSION_MODEL", chosen)
    else:
        path.write_text(f'diffusion_model = "{chosen}"\n')
    DreamferenceConfig(config_file=str(path), **arguments).save_config()
    assert _saved(path)["diffusion_model"] == chosen


def test_the_server_tuning_keys_are_read_from_the_file_and_saved_back(tmp_path):
    path = tmp_path / "dreamference.toml"
    tuning = {
        "enable_prefix_caching": False,
        "enable_chunked_prefill": False,
        "num_scheduler_steps": 3,
        "attention_backend": "flashinfer",
        "kv_cache_dtype": "fp8",
        "guided_decoding_backend": "outlines",
    }
    path.write_text(
        "enable_prefix_caching = false\n"
        "enable_chunked_prefill = false\n"
        "num_scheduler_steps = 3\n"
        'attention_backend = "flashinfer"\n'
        'kv_cache_dtype = "fp8"\n'
        'guided_decoding_backend = "outlines"\n'
    )
    config = DreamferenceConfig(config_file=str(path))
    assert {key: getattr(config, key) for key in tuning} == tuning
    config.save_config()
    assert _saved(path) == tuning


def test_the_compaction_ledger_also_takes_on_from_the_environment(tmp_path, monkeypatch):
    path = tmp_path / "dreamference.toml"
    path.write_text("mightling_compaction_ledger = false\n")
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_COMPACTION_LEDGER", "On")
    assert DreamferenceConfig(config_file=str(path)).mightling_compaction_ledger is True


def test_an_air_gap_level_set_after_loading_is_saved(tmp_path, monkeypatch):
    path = tmp_path / "dreamference.toml"
    path.write_text('model = "llama-3.3-70b"\n')
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "off")
    config = DreamferenceConfig(config_file=str(path))
    # The environment gave `off`; a level chosen since is the user's choice, not the environment's.
    config.mightling_airgapped = "on"
    config.save_config()
    assert _saved(path)["mightling_airgapped"] == "on"


def test_an_air_gap_level_from_the_environment_is_not_written(tmp_path, monkeypatch):
    path = tmp_path / "dreamference.toml"
    path.write_text('model = "llama-3.3-70b"\n')
    monkeypatch.setenv("DREAMFERENCE_MIGHTLING_AIRGAPPED", "on")
    config = DreamferenceConfig(config_file=str(path))
    assert config.mightling_airgapped == "on"
    config.save_config()
    assert _saved(path) == {"model": "llama-3.3-70b"}


def test_every_key_save_config_writes_is_one_it_removes_again(tmp_path):
    path = tmp_path / "dreamference.toml"
    DreamferenceConfig(
        config_file=str(path),
        vllm_host="http://gpu-box.lan:8000",
        agent_runner="claude",
        model="llama-3.3-70b",
        diffusion_model="diffusion-model",
        draft_model="draft-model",
        num_speculative_tokens=DEFAULT_SPECULATIVE_TOKENS + 1,
        hf_token="hf_named",
        enable_prefix_caching=False,
        enable_chunked_prefill=False,
        num_scheduler_steps=3,
        attention_backend="flashinfer",
        kv_cache_dtype="fp8",
        guided_decoding_backend="outlines",
        use_tensorizer=True,
        server_engine="sglang",
        mightling_gmail=False,
        mightling_compaction_ledger=False,
        mightling_airgapped="on",
        mightling_prompt="terse",
        mightling_refine=True,
        mightling_refine_version="v2",
    ).save_config()
    assert set(_saved(path)) == CONFIG_KEYS
    # A default configuration saved over it leaves only the keys another reader owns.
    with path.open("a") as file:
        file.write("other_reader = 1\n")
    DreamferenceConfig(config_file=str(tmp_path / "none.toml")).save_config(path)
    assert _saved(path) == {"other_reader": 1}


def test_an_empty_yaml_file_is_saved_over(tmp_path):
    yaml = pytest.importorskip("yaml")
    path = tmp_path / "dreamference.yaml"
    path.write_text("")
    DreamferenceConfig(config_file=str(path), model="llama-3.3-70b").save_config()
    assert yaml.safe_load(path.read_text()) == {"model": "llama-3.3-70b"}


def test_resetting_the_host_to_the_default_over_a_repository_file_warns(
    tmp_path, monkeypatch, capsys
):
    # The user's own file names a host and a repository file is in use. Setting the host back to the
    # default makes save_config() write nothing, and the next load takes the user's host again, so the
    # change is lost; it says so, as a changed host does.
    monkeypatch.chdir(tmp_path)
    user_level = tmp_path / "home" / "config.toml"
    user_level.parent.mkdir()
    user_level.write_text('vllm_host = "http://gpu-box.lan:8000"\n')
    monkeypatch.setattr(config_path_resolver, "GLOBAL_DREAMFERENCE_CONFIG_PATH", user_level)
    repository = tmp_path / "dreamference.toml"
    repository.write_text('model = "m"\n')
    config = DreamferenceConfig()
    assert config.vllm_host == "http://gpu-box.lan:8000"
    capsys.readouterr()
    config.save_config()
    assert capsys.readouterr().err == ""
    config.vllm_host = "http://localhost:8000"
    config.save_config()
    assert "vllm_host not saved in" in capsys.readouterr().err
    assert "vllm_host" not in _saved(repository)
    assert DreamferenceConfig().vllm_host == "http://gpu-box.lan:8000"


def _gmail_files(tmp_path, monkeypatch, user: str | None, repository: str):
    monkeypatch.chdir(tmp_path)
    user_level = tmp_path / "home" / "config.toml"
    monkeypatch.setattr(config_path_resolver, "GLOBAL_DREAMFERENCE_CONFIG_PATH", user_level)
    user_level.parent.mkdir()
    if user is not None:
        user_level.write_text(user)
    (tmp_path / "dreamference.toml").write_text(repository)
    monkeypatch.delenv("DREAMFERENCE_MIGHTLING_GMAIL", raising=False)
    return DreamferenceConfig()


def test_a_repository_cannot_turn_gmail_back_on(tmp_path, monkeypatch):
    config = _gmail_files(
        tmp_path, monkeypatch, "mightling_gmail = false\n", "mightling_gmail = true\n"
    )
    assert config.mightling_gmail is False


def test_a_repository_can_turn_gmail_off(tmp_path, monkeypatch):
    config = _gmail_files(
        tmp_path, monkeypatch, "mightling_gmail = true\n", "mightling_gmail = false\n"
    )
    assert config.mightling_gmail is False


def test_a_repository_that_is_silent_leaves_gmail_to_the_user_file(tmp_path, monkeypatch):
    assert (
        _gmail_files(
            tmp_path, monkeypatch, "mightling_gmail = false\n", 'model = "m"\n'
        ).mightling_gmail
        is False
    )


def test_a_repository_true_with_no_user_choice_is_the_default(tmp_path, monkeypatch):
    assert (
        _gmail_files(tmp_path, monkeypatch, None, "mightling_gmail = true\n").mightling_gmail
        is True
    )
