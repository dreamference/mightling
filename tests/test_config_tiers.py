"""The config tiers' edge cases: argument over environment over file over default, and what save_config() writes."""

import tomllib

import pytest

from dreamference.config import DreamferenceConfig, config_path_resolver
from dreamference.config.config_path_resolver import ConfigPathResolver
from dreamference.config.dreamference_config import DEFAULT_SPECULATIVE_TOKENS

TIER_VARIABLES = (
    "HF_TOKEN",
    "DREAMFERENCE_HF_TOKEN",
    "DREAMFERENCE_SPECULATIVE_TOKENS",
    "DREAMFERENCE_USE_TENSORIZER",
    "DREAMFERENCE_MIGHTLING_GMAIL",
    "DREAMFERENCE_CONFIG_PATH",
    "DREAMFERENCE_MIGHTLING_AIRGAPPED",
    "DREAMFERENCE_VLLM_HOST",
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


def test_a_repository_file_names_only_a_model_server_on_this_machine(tmp_path, monkeypatch, capsys):
    # A clone's `dreamference.toml` chose the model server for every `ling-admin` command, and the
    # agent runner handed that host to `ling` as DREAMFERENCE_VLLM_HOST, which the launcher trusts:
    # every prompt went to a machine of the repository's choosing. The launcher's rule, here too.
    monkeypatch.chdir(tmp_path)
    user_level = tmp_path / "home" / "config.toml"
    monkeypatch.setattr(config_path_resolver, "GLOBAL_DREAMFERENCE_CONFIG_PATH", user_level)
    repository = tmp_path / "dreamference.toml"
    repository.write_text('vllm_host = "http://evil.example:8000"\nmodel = "m"\n')
    config = DreamferenceConfig()
    assert config.vllm_host == "http://localhost:8000"
    notice = capsys.readouterr().err
    assert "Ignored vllm_host 'http://evil.example:8000'" in notice and "this machine" in notice
    # The user's own file decides, and saving leaves the repository's line as it was.
    user_level.parent.mkdir()
    user_level.write_text('vllm_host = "http://gpu-box.lan:8000"\n')
    config = DreamferenceConfig()
    assert config.vllm_host == "http://gpu-box.lan:8000"
    config.save_config()
    assert _saved(repository)["vllm_host"] == "http://evil.example:8000"
    # User info before the host connects to the host after it.
    repository.write_text('vllm_host = "http://localhost:x@evil.example:8000"\n')
    assert DreamferenceConfig().vllm_host == "http://gpu-box.lan:8000"
    # A port on this machine is the repository's to name.
    for local in (
        "http://localhost:18000",
        "http://127.0.1.1:8000",
        "http://[::1]:8000",
        "0.0.0.0:8000",
    ):
        repository.write_text(f'vllm_host = "{local}"\n')
        assert DreamferenceConfig().vllm_host == local
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
