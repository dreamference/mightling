"""Guards shared by every test: nothing may touch the real Onyx deployment or the real home folder.

`OnyxRunner.configure()` writes Onyx's `.env` and recreates containers. A test that reaches it
without mocking a step once rewrote the user's real `~/.config/onyx/deployment/.env` and recreated
the live nginx container. These fixtures point the env file at a scratch copy and make any
unmocked container recreate fail the test instead of running `docker compose`.
"""

import os
import sys
from pathlib import Path

import pytest

# Resolved before any test changes HOME. Every dreamference module is imported here, so the
# constants they compute from the home folder at import exist to be redirected below.
REAL_HOME = os.path.realpath(os.path.expanduser("~"))
# The checkout may itself live under the home folder; paths into it (the codex submodule, the
# patch series, the desktop project) are source the tests read, not state they could clobber.
CHECKOUT = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))
import dreamference.cli.dreamference_cli_controller  # noqa: E402,F401 - imports every subsystem
import dreamference.mcp_server  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _isolate_onyx_deployment(tmp_path_factory, monkeypatch):
    from dreamference.chat import onyx_runner

    # Already-configured by default, as a real deployment is after its first `configure()`, so the
    # telemetry and loopback steps are no-ops unless a test sets up its own file to exercise them.
    env = tmp_path_factory.mktemp("onyx") / ".env"  # not tmp_path: tests index that folder
    settings = {**onyx_runner.ONYX_PRIVACY_ENV, **onyx_runner.ONYX_LOOPBACK_ENV}
    env.write_text("".join(f'{key}="{value}"\n' for key, value in settings.items()))
    monkeypatch.setattr(onyx_runner, "ONYX_ENV_FILE", str(env))

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to recreate a real Onyx container; mock _recreate_service")

    monkeypatch.setattr(onyx_runner.OnyxRunner, "_recreate_service", classmethod(refuse))


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory, monkeypatch):
    # Runners write their agents' configs under `~`: `~/.continue/config.json`, Goose's config,
    # and more. Tests used to run against the real home folder, and every run rewrote the user's
    # Continue config with a test model the machine does not serve. Each test gets its own home.
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))

    # HOME alone does not reach paths a module resolved at import, like VLLM_CACHE_HOME: a test
    # that ran start_server stamped the real ~/.cache/dreamference/vllm/.compile_signature with a
    # test model's signature, so the next real `server start` saw a mismatch and discarded a
    # 467 MB torch.compile cache (an 8-12 minute recompile). Every string or Path attribute of a
    # dreamference module that lies under the real home is re-pointed at the same place under the
    # test's home -- including copies another module took with `from ... import`.
    for module_name, module in list(sys.modules.items()):
        if module is None or not (module_name == "dreamference" or module_name.startswith("dreamference.")):
            continue
        for attribute, value in list(vars(module).items()):
            if attribute.startswith("__"):
                continue
            if isinstance(value, (str, Path)) and _under_real_home(value):
                relocated = home / os.path.relpath(str(value), REAL_HOME)
                monkeypatch.setattr(module, attribute, type(value)(relocated) if isinstance(value, Path) else str(relocated))


def _under_real_home(value) -> bool:
    text = str(value)
    if text == CHECKOUT or text.startswith(CHECKOUT + os.sep):
        return False
    return text == REAL_HOME or text.startswith(REAL_HOME + os.sep)
