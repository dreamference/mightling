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
from dreamference.chat.onyx_runner import OnyxRunner  # noqa: E402

from dreamference.chat.onyx_brand_assets import OnyxBrandAssets  # noqa: E402
from dreamference.chat.onyx_ui_fonts import OnyxUIFonts  # noqa: E402
from dreamference.chat.onyx_ui_labels import OnyxUILabels  # noqa: E402
from dreamference.chat.onyx_ui_overrides import OnyxUIOverrides  # noqa: E402
from dreamference.chat.onyx_ui_scripts import OnyxUIScripts  # noqa: E402

# Kept for the tests that exercise the real methods (the fixture below replaces them).
REAL_SERVED_MODEL_KEY = OnyxRunner.served_model_key
REAL_START_GMAIL_SERVICE = OnyxRunner._start_gmail_service
REAL_ATTACH_SEARXNG = OnyxRunner._attach_searxng
REAL_BRAND_INSTALL = OnyxBrandAssets.install
REAL_FONTS_INSTALL = OnyxUIFonts.install
REAL_START_STT_SERVER = OnyxRunner._start_stt_server
REAL_ALLOW_LOCAL_VOICE_ENDPOINT = OnyxRunner._allow_local_voice_endpoint
# The UI patchers write into the live web-server container (`docker cp`, `docker exec node`).
UI_PATCHERS = (OnyxBrandAssets, OnyxUIFonts, OnyxUILabels, OnyxUIOverrides, OnyxUIScripts)


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
    # configure() asks the live server which model it serves; tests use the configured one, so
    # their result does not depend on what happens to be running on this machine.
    monkeypatch.setattr(onyx_runner.OnyxRunner, "served_model_key", lambda self: self.config.model)
    # configure() also starts sidecars and writes into live containers. Until 2026-09-29 every
    # offline test run did so for real: it recreated the Gmail sidecar (with a pytest temp folder
    # and secret once HOME was isolated, which broke Gmail search until the next configure),
    # joined SearXNG and the speech-to-text sidecar to Onyx's network, copied test logos into the
    # web server and rewrote its bundle through `docker exec`. A test of one of these methods
    # restores it from the REAL_* names above.
    monkeypatch.setattr(onyx_runner.OnyxRunner, "_start_gmail_service", lambda self, secret: True)
    monkeypatch.setattr(onyx_runner.OnyxRunner, "_attach_searxng", lambda self: True)
    monkeypatch.setattr(onyx_runner.OnyxRunner, "_start_stt_server", lambda self: True)
    monkeypatch.setattr(onyx_runner.OnyxRunner, "_allow_local_voice_endpoint", lambda self, *a, **k: True)
    for patcher in UI_PATCHERS:
        monkeypatch.setattr(patcher, "install", classmethod(lambda cls, container=None: True))


@pytest.fixture(autouse=True)
def _refuse_real_docker(monkeypatch):
    # A test that reached OnyxRunner._start_gmail_service ran `docker rm -f dreamference-gmail` and
    # `docker run` for real, replacing the live Gmail sidecar with one mounting a pytest temp
    # folder and a test secret: Gmail search in the web chat answered "unauthorised" until the
    # next `configure` (2026-09-29). Every real docker command from a test now fails that test;
    # tests that exercise docker paths mock subprocess themselves, which replaces this guard.
    import subprocess

    def guarded(original):
        def run(*args, **kwargs):
            argv = args[0] if args else kwargs.get("args")
            program = argv[0] if isinstance(argv, (list, tuple)) and argv else str(argv).split(" ")[0]
            if os.path.basename(str(program)) == "docker" and _changes_something(argv):
                raise AssertionError(f"a test tried to run a real docker command: {argv!r}; mock it")
            return original(*args, **kwargs)
        return run

    for name in ("run", "call", "check_call", "check_output", "Popen"):
        monkeypatch.setattr(subprocess, name, guarded(getattr(subprocess, name)))


# Docker subcommands that only read. Launch-command tests ask `docker info` and `docker image
# inspect`, which change nothing; anything else touches the machine's containers.
READ_ONLY_DOCKER: tuple = ("info", "version", "inspect", "ps", "images", "port", "logs", "stats")


def _changes_something(argv) -> bool:
    words = [str(w) for w in argv[1:]] if isinstance(argv, (list, tuple)) else str(argv).split()[1:]
    words = [w for w in words if not w.startswith("-")]
    if not words:
        return False
    if words[0] in READ_ONLY_DOCKER:
        return False
    if words[0] in ("image", "container", "network", "volume") and len(words) > 1:
        return words[1] not in ("inspect", "ls")
    return True


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
