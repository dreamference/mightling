"""Guards shared by every test: nothing may touch the machine's containers, services or the real
home folder.

Until Onyx's retirement the first guard here kept tests away from the live web chat: a test that
reached its `configure()` unmocked once rewrote the real Onyx `.env` and recreated the live nginx
container. Its successors are below: no real `docker` command that changes something, no sudo, no
systemd unit, and the one-time offer to remove Onyx's leftovers never made.
"""

import os
import sys
import threading
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
from dreamference.chat.google_service import GoogleService  # noqa: E402
from dreamference.chat.retired_web_chat import RetiredWebChat  # noqa: E402

# `server start` starts the Google service on a node, and `google start` creates its container.
REAL_GOOGLE_SERVICE_START = GoogleService.start
# Every interactive `ling-admin` run may offer to remove the retired web chat's containers; the
# fixture below never lets it, and its own tests restore this.
REAL_RETIRED_OFFER = RetiredWebChat.offer
# `node enable` sets up the node's forge (FORGEJO O1): docker, user units, accounts.
from dreamference.forge.forge_service import ForgeService  # noqa: E402

REAL_FORGE_SETUP = ForgeService.setup
from dreamference.chat.image_search_sidecar import ImageSearchSidecar  # noqa: E402
from dreamference.chat.speech_sidecar import SpeechSidecar  # noqa: E402

# `server start` on a node also starts image search and speech-to-text when absent (ASK §19.6).
REAL_IMAGE_SEARCH_START = ImageSearchSidecar.start
REAL_SPEECH_START = SpeechSidecar.start
from dreamference.node.node_advertiser import NodeAdvertiser as _NodeAdvertiser  # noqa: E402

# Runs a command through sudo; the fixture below replaces it, and a test of it restores it.
REAL_RUN_PRIVILEGED = _NodeAdvertiser.run_privileged
from dreamference.vllm_server.vllm_server_manager import VLLMServerManager  # noqa: E402

# `server start` stops the code index's systemd scopes; the fixture below replaces it.
REAL_STOP_INDEX_SCOPES = VLLMServerManager._stop_index_scopes
from dreamference.vllm_server.sandbox_prerequisite import SandboxPrerequisite  # noqa: E402

# Every `ling-admin` run checks bubblewrap through a transient unit of the user's systemd and may
# ask a question; the fixture below replaces the check, and its own tests restore this.
REAL_SANDBOX_GATE = SandboxPrerequisite.gate
from dreamference.vllm_server.model_gate import ModelGate  # noqa: E402

# Asks the model server's gate what it does: an HTTP request to the configured server, which in the
# suite would be this machine's live one. The fixture below answers "no gate"; its tests restore it.
REAL_GATE_PROBE = ModelGate.probe
# A build through the machine's real sccache would start its server, a daemon outside the test, and
# whether one is installed must not decide a test. The fixture below answers "no compile cache";
# its tests restore it.
from dreamference.runner.codex_branded_builder import CodexBrandedBuilder  # noqa: E402

REAL_COMPILER_CACHE_ENVIRONMENT = CodexBrandedBuilder.compiler_cache_environment
# A release-hosted model (the ScaleSearch drafter) is downloaded from GitHub's release assets,
# possibly gigabytes. `ensure` is stubbed to "complete" so `server start` proceeds, `download` and
# `_fetch` fail the test; a test of the downloader restores all three from these names.
from dreamference.hardware.release_asset_downloader import ReleaseAssetDownloader  # noqa: E402

REAL_RELEASE_ENSURE = ReleaseAssetDownloader.ensure
REAL_RELEASE_DOWNLOAD = ReleaseAssetDownloader.download
REAL_RELEASE_FETCH = ReleaseAssetDownloader._fetch
# The name MemoryPressureWatchdog gives its thread, and how long a stopped one may take to exit.
PSI_THREAD_NAME = "psi-watchdog"
LEAK_GRACE_S = 2.0


@pytest.fixture(autouse=True)
def _no_compiler_cache(monkeypatch):
    monkeypatch.setattr(
        CodexBrandedBuilder, "compiler_cache_environment", classmethod(lambda cls, environment: {})
    )


@pytest.fixture(autouse=True)
def _isolate_services(monkeypatch):
    # `server start` stops the code index's scopes and starts the Google service, image search and
    # speech-to-text on a node; a
    # `ling-admin` run may offer to remove Onyx's leftovers. None of it may happen for real.
    monkeypatch.setattr(VLLMServerManager, "_stop_index_scopes", classmethod(lambda cls: None))
    monkeypatch.setattr(GoogleService, "start", classmethod(lambda cls: True))
    monkeypatch.setattr(RetiredWebChat, "offer", classmethod(lambda cls, command: None))
    monkeypatch.setattr(ImageSearchSidecar, "start", classmethod(lambda cls, *a, **k: True))
    monkeypatch.setattr(SpeechSidecar, "start", classmethod(lambda cls, *a, **k: True))
    monkeypatch.setattr(ForgeService, "setup", classmethod(lambda cls, show_password=True: True))


@pytest.fixture(autouse=True)
def _isolate_node_advert(tmp_path_factory, monkeypatch):
    # `server start` and `server stop` rewrite the node's Avahi service file when one exists
    # (specs/DREAMFERENCE_MIGHTLING_NODE.md §5.2). It lives under /etc, which the home isolation
    # below does not reach: without this, a server test on an advertised node would change what
    # the real node tells the network. sudo is never run from a test either.
    from dreamference.node import NodeAdvertiser, NodeServiceFile

    scratch = tmp_path_factory.mktemp("avahi") / "mightling-node.service"
    monkeypatch.setattr(NodeServiceFile, "service_path", scratch)
    monkeypatch.setattr(NodeAdvertiser, "run_privileged", classmethod(lambda cls, command, purpose, yes=False: False))
    # Whether this machine has Avahi must not decide a test (it does not on a CI runner).
    monkeypatch.setattr(NodeAdvertiser, "avahi_installed", classmethod(lambda cls: True))


@pytest.fixture(autouse=True)
def _no_browse_wait(monkeypatch):
    # Every connection to a paired node resolves it on the network first and, when no browse shows
    # it, keeps browsing for up to a minute (NodePairing.resolve). A test's browse is a stub, and
    # a test never waits for the network: the budget is zero, and what one test resolved is not
    # another test's answer. The browse itself is stubbed too: an unstubbed one is a real
    # `avahi-browse`, a multicast query on the user's network (ten of them were counted in the
    # suite on 2026-10-10); a test that wants nodes seen sets its own stub, which lands later and wins.
    from dreamference.node import NodeBrowser, NodePairing

    NodePairing.forget_resolved()
    monkeypatch.setattr(NodePairing, "resolve_wait_s", 0.0)
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: []))
    monkeypatch.setattr(NodeBrowser, "browse_service", classmethod(lambda cls, service_type, timeout=6: []))
    yield
    NodePairing.forget_resolved()


@pytest.fixture(autouse=True)
def _no_passwordless_sudo(monkeypatch):
    # Whether sudo needs a password on this machine must not decide a test, and asking it is a
    # real sudo; a test of the no-password path says so itself. The same for a node's root helper.
    from dreamference.vllm_server import HostSafetySetup

    monkeypatch.setattr(HostSafetySetup, "passwordless_sudo", classmethod(lambda cls: False))
    monkeypatch.setattr(HostSafetySetup, "helper_works", classmethod(lambda cls: False), raising=False)


@pytest.fixture(autouse=True)
def _no_wifi_reading(monkeypatch):
    # Whether this machine is on Wi-Fi with power saving on must not decide a test (`host check`,
    # `host setup`, `node enable` and `node prepare` all read it), and the reading is a real
    # nmcli/iw. A test of the Wi-Fi check sets its own `_read`, which lands later and wins.
    from dreamference.vllm_server import WifiPowerSave

    monkeypatch.setattr(WifiPowerSave, "_read", classmethod(lambda cls, argv: None))


@pytest.fixture(autouse=True)
def _skip_sandbox_gate(monkeypatch):
    monkeypatch.setattr(SandboxPrerequisite, "gate", classmethod(lambda cls, command, subcommand: True))


@pytest.fixture(autouse=True)
def _no_model_gate_probe(monkeypatch):
    monkeypatch.setattr(ModelGate, "probe", classmethod(lambda cls, host, timeout=2.0: None))


# Python asks the built `ling` for the air-gap level (`AirgappedResolver`, AIRGAPPED §6.5), which a
# test may not run, and without an answer the level is `on`. This stand-in answers
# `ling airgapped --json` in the tiers' order for the plain forms a test writes (a session file,
# the variable, a `mightling_airgapped = "…"` line before any table, a seal by pid); how the crate
# reads anything less plain is pinned by its own cases (ling-rs/airgapped/cases.json `resolve`),
# not here. It writes its arguments to `$FAKE_LING_AIRGAPPED_LOG` when set.
FAKE_LING_AIRGAPPED = r"""#!/bin/sh
[ -n "$FAKE_LING_AIRGAPPED_LOG" ] && printf '%s\n' "$*" >> "$FAKE_LING_AIRGAPPED_LOG"
shift 2
thread= cwd=$PWD pid=
while [ $# -gt 0 ]; do
  case "$1" in --thread) thread=$2 ;; --cwd) cwd=$2 ;; --sealed-by) pid=$2 ;; esac
  shift 2
done
answer() { printf '{"level":"%s","tier":"%s","source":"stand-in","invalid":[]}\n' "$1" "$2"; exit 0; }
level_of() {
  case $(printf '%s' "$1" | tr -d ' \t\r\n' | tr 'A-Z' 'a-z') in off) echo off ;; *) echo on ;; esac
}
sealed() {
  [ -n "$pid" ] && for seal in "${XDG_RUNTIME_DIR:-/nonexistent}"/ling-airgapped/*; do
    [ -f "$seal" ] && [ "$(cat "$seal")" = "$pid" ] && return 0
  done
  return 1
}
decided() { if [ "$1" = off ] && sealed; then answer on sealed; fi; answer "$1" "$2"; }
session=${CODEX_HOME:-$HOME/.mightling}/airgapped/$thread
if [ -n "$thread" ] && [ -f "$session" ]; then decided "$(level_of "$(cat "$session")")" session; fi
if [ -n "$DREAMFERENCE_MIGHTLING_AIRGAPPED" ]; then
  decided "$(level_of "$DREAMFERENCE_MIGHTLING_AIRGAPPED")" environment
fi
level=
for file in "${DREAMFERENCE_CONFIG_PATH:-$cwd/dreamference.toml}" "$HOME/.config/dreamference/config.toml"; do
  [ -f "$file" ] || continue
  value=$(sed -n '/^[[:space:]]*\[/q; s/^[[:space:]]*mightling_airgapped[[:space:]]*=[[:space:]]*//p' "$file")
  value=$(printf '%s\n' "$value" | head -n 1)
  [ -n "$value" ] || continue
  value=$(printf '%s' "$value" | sed 's/[[:space:]]*#.*//; s/^"\(.*\)"$/\1/')
  [ "$(level_of "$value")" = on ] && level=on || level=${level:-off}
done
[ -n "$level" ] && decided "$level" config
decided off default
"""
from dreamference.config.airgapped_resolver import AirgappedResolver  # noqa: E402

REAL_AIRGAPPED_BINARY = AirgappedResolver.binary


@pytest.fixture(scope="session")
def fake_ling_airgapped(tmp_path_factory) -> str:
    script = tmp_path_factory.mktemp("fake-ling") / "ling"
    script.write_text(FAKE_LING_AIRGAPPED)
    script.chmod(0o755)
    return str(script)


@pytest.fixture(autouse=True)
def _airgapped_from_a_stand_in(monkeypatch, fake_ling_airgapped):
    monkeypatch.setattr(AirgappedResolver, "binary", classmethod(lambda cls: fake_ling_airgapped))


@pytest.fixture(autouse=True)
def _refuse_real_docker(monkeypatch, request):
    # A test that reached the retired web chat's Gmail start ran `docker rm -f dreamference-gmail`
    # and `docker run` for real, replacing the live Gmail sidecar with one mounting a pytest temp
    # folder and a test secret: Gmail search answered "unauthorised" until it was recreated
    # (2026-09-29). Every real docker command from a test now fails that test;
    # tests that exercise docker paths mock subprocess themselves, which replaces this guard.
    import subprocess

    def guarded(original):
        def run(*args, **kwargs):
            argv = args[0] if args else kwargs.get("args")
            program = argv[0] if isinstance(argv, (list, tuple)) and argv else str(argv).split(" ")[0]
            if os.path.basename(str(program)) == "docker" and _changes_something(argv) \
                    and not _throwaway_forge(request, argv):
                raise AssertionError(f"a test tried to run a real docker command: {argv!r}; mock it")
            # `server start` stops the code index's scopes before its host-safety pre-flight
            # (specs/DREAMFERENCE_MIGHTLING_CODE_INDEX.md §9.2): a test must never stop, freeze or
            # start a unit of the user's own systemd.
            if os.path.basename(str(program)) in ("systemctl", "systemd-run") and _changes_systemd(argv):
                raise AssertionError(f"a test tried to run a real systemd command: {argv!r}; mock it")
            # Provisioning (specs/DREAMFERENCE_MIGHTLING_FLEET.md §14): no test may reach another
            # machine or become root.
            # Wi-Fi power saving (vllm_server/wifi_power_save.py): a test may never change this
            # machine's network, which a benchmark night or an SSH session depends on.
            if os.path.basename(str(program)) in ("nmcli", "iw") and _changes_network(argv):
                raise AssertionError(f"a test tried to change the network: {argv!r}; mock it")
            if os.path.basename(str(program)) in ("ssh", "scp", "rsync", "sudo", "sg"):
                raise AssertionError(f"a test tried to run a real {os.path.basename(str(program))}: {argv!r}; mock it")
            # The suite is offline by construction (AGENTS.md): the installed `ling` and the other
            # binaries of the install would wait for, and reach, this machine's model server. A
            # test that needs an agent writes a stand-in under its tmp_path (test_night_shift.py).
            installed = _installed_binary(program)
            if installed and not request.node.get_closest_marker(INSTALLED_BINARY_MARK):
                raise AssertionError(f"a test tried to run the installed {installed}: {argv!r}; use a stand-in, "
                                     f"or mark the test `{INSTALLED_BINARY_MARK}` if it tests the built binary")
            return original(*args, **kwargs)
        return run

    for name in ("run", "call", "check_call", "check_output", "Popen"):
        monkeypatch.setattr(subprocess, name, guarded(getattr(subprocess, name)))


# dockerd's control socket, where a connect is a request to the real daemon: the PSI watchdog kills
# and updates containers over it without the `docker` CLI, so `_refuse_real_docker` cannot see it.
# Two watchdog tests sent `kill?signal=SIGKILL` and a restart-policy update for `test-container` to
# the real dockerd (2026-10-30).
def _real_docker_sockets() -> set:
    paths = ["/var/run/docker.sock", "/run/docker.sock"]
    if hasattr(os, "getuid"):
        paths.append(f"/run/user/{os.getuid()}/docker.sock")  # rootless dockerd
    docker_host = os.environ.get("DOCKER_HOST", "")
    if docker_host.startswith("unix://"):
        paths.append(docker_host[len("unix://"):])
    return {os.path.realpath(path) for path in paths}


REAL_DOCKER_SOCKETS = _real_docker_sockets()


@pytest.fixture(autouse=True)
def _refuse_real_dockerd_socket(monkeypatch):
    # Refused with the error a stopped dockerd gives, so the code takes its own fallback, and failed
    # after the test: the watchdog's thread swallows every exception, so raising alone would let
    # the test pass.
    import socket

    reached = []
    connect = socket.socket.connect

    def guarded(self, address):
        if self.family == getattr(socket, "AF_UNIX", None) and isinstance(address, (str, bytes)) \
                and os.path.realpath(os.fsdecode(address)) in REAL_DOCKER_SOCKETS:
            reached.append(os.fsdecode(address))
            raise ConnectionRefusedError(111, "a test may not reach the real dockerd")
        return connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield
    if reached:
        pytest.fail(f"the test connected to the real dockerd at {reached[0]}; stub the socket")


# The throwaway Forgejo of tests/test_forge_private.py (FORGEJO §5.3): a test marked
# `forge_container`, run only when MIGHTLING_FORGE_CONTAINER_TESTS=1, may run docker commands that
# name its own container, network or image pin, never the node's forge (`mightling-forge`).
FORGE_CONTAINER_MARK: str = "forge_container"
THROWAWAY_FORGE_PREFIX: str = "mightling-forge-test-"


def _throwaway_forge(request, argv) -> bool:
    if not request.node.get_closest_marker(FORGE_CONTAINER_MARK) or not isinstance(argv, (list, tuple)):
        return False
    words = [str(w) for w in argv]
    if any(w == "mightling-forge" or w.startswith("mightling-forge:") for w in words):
        return False
    return any(THROWAWAY_FORGE_PREFIX in w for w in words) or words[1:2] == ["pull"]


# The one opt-out of the offline rule: a test of the built `ling` itself (test_mightling_slash_commands.py)
# may run the installed binary and, when the model server answers, reach it. Registered in pyproject.toml.
INSTALLED_BINARY_MARK: str = "installed_binary"

# Where a release install and `ling-admin codex build` put the binaries, and the links to them.
INSTALLED_BINARY_DIRS: tuple = tuple(os.path.join(REAL_HOME, d) for d in
                                     (".local/bin", ".local/share/dreamference", ".cache/dreamference/puffin-codex"))


def _installed_binary(program) -> str:
    """The resolved path when `program` is a binary of the install (or a link to one), else ''."""
    import shutil
    text = str(program)
    if not text:
        return ""
    located = text if os.sep in text else (shutil.which(text) or "")
    if not located:
        return ""
    # `~` means the real home here, whatever HOME a test has set.
    if located == "~" or located.startswith("~" + os.sep):
        located = REAL_HOME + located[1:]
    for candidate in (os.path.abspath(located), os.path.realpath(located)):
        if any(candidate == d or candidate.startswith(d + os.sep) for d in INSTALLED_BINARY_DIRS):
            return candidate
    return ""


# The model server's public port and the engine's behind the gate (vllm_server/model_gate.py).
MODEL_SERVER_PORTS: tuple = (8000, 18000)


@pytest.fixture(autouse=True)
def _no_release_download(monkeypatch):
    def refused(cls, *args, **kwargs):
        raise AssertionError("a test tried to download release assets from the network; stub it "
                             "or restore the REAL_RELEASE_* names for a test of the downloader")

    monkeypatch.setattr(ReleaseAssetDownloader, "ensure", classmethod(lambda cls, model_key: True))
    monkeypatch.setattr(ReleaseAssetDownloader, "download", classmethod(refused))
    monkeypatch.setattr(ReleaseAssetDownloader, "_fetch", classmethod(refused))


@pytest.fixture(autouse=True)
def _no_model_server(monkeypatch, request):
    # Nothing in the suite may reach the model server: a test that did would hang behind the gate
    # while a benchmark holds it, or spend the model's time. Connections to its ports fail the
    # test by name; a test that needs a server starts its own on a free port (http.server, a
    # socket it bound). asyncio's sock_connect and http.client both go through socket.connect.
    import socket
    if request.node.get_closest_marker(INSTALLED_BINARY_MARK):
        return

    def refused(address):
        port = address[1] if isinstance(address, tuple) and len(address) >= 2 else None
        if port in MODEL_SERVER_PORTS:
            raise AssertionError(f"a test tried to connect to the model server at {address!r}; "
                                 "stub the request or serve a stand-in on a free port")
        # No outbound traffic either: only loopback (and the unspecified address) is reachable.
        if isinstance(address, tuple) and address and isinstance(address[0], str):
            import ipaddress
            try:
                ip = ipaddress.ip_address(address[0].split("%")[0])
            except ValueError:
                return  # a name: socket.connect gets resolved addresses, so this is not a connect
            if not (ip.is_loopback or ip.is_unspecified):
                raise AssertionError(f"a test tried to connect to {address!r}, off this machine; "
                                     "stub the request or serve a stand-in on loopback")

    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def connect(self, address):
        refused(address)
        return original_connect(self, address)

    def connect_ex(self, address):
        refused(address)
        return original_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


# Docker subcommands that only read. Launch-command tests ask `docker info` and `docker image
# inspect`, which change nothing; anything else touches the machine's containers.
READ_ONLY_DOCKER: tuple = ("info", "version", "inspect", "ps", "images", "port", "logs", "stats")


# systemctl verbs that only read.
READ_ONLY_SYSTEMCTL: tuple = ("show", "status", "is-active", "is-enabled", "is-failed", "list-units", "show-environment", "cat")


def _changes_systemd(argv) -> bool:
    words = [str(w) for w in argv[1:]] if isinstance(argv, (list, tuple)) else str(argv).split()[1:]
    if os.path.basename(str(argv[0] if isinstance(argv, (list, tuple)) else str(argv).split()[0])) == "systemd-run":
        return True
    verbs = [w for w in words if not w.startswith("-")]
    return bool(verbs) and verbs[0] not in READ_ONLY_SYSTEMCTL


# nmcli and iw words that change something; a reading (`device status`, `connection show`,
# `get power_save`) has none of them.
NETWORK_CHANGING_WORDS: tuple = ("modify", "set", "up", "down", "reapply", "connect", "disconnect",
                                 "delete", "add", "edit", "reload", "radio", "networking")


def _changes_network(argv) -> bool:
    words = [str(w) for w in argv[1:]] if isinstance(argv, (list, tuple)) else str(argv).split()[1:]
    return any(word in NETWORK_CHANGING_WORDS for word in words)


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
def _no_leaked_watchdog():
    # A background `start_server` leaves its PSI watchdog running. One test did so against the
    # real /proc/pressure/memory, and the thread's `docker inspect` retries were counted by a
    # later test's fake `subprocess.run`, which then failed only when run after it (2026-10-03).
    # A test that starts a watchdog must stop it, or keep it from starting.
    before = {thread.ident for thread in threading.enumerate() if thread.name == PSI_THREAD_NAME}
    yield
    leaked = []
    for thread in threading.enumerate():
        if thread.name != PSI_THREAD_NAME or thread.ident in before:
            continue
        thread.join(timeout=LEAK_GRACE_S)  # a stopped watchdog may still be finishing a sample
        if thread.is_alive():
            leaked.append(thread)
    if leaked:
        pytest.fail(f"the test left {len(leaked)} PSI watchdog thread(s) running; stop them")


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory, monkeypatch):
    # Runners write their agents' configs under `~`: `~/.continue/config.json`, Codex's home,
    # and more. Tests used to run against the real home folder, and every run rewrote the user's
    # Continue config with a test model the machine does not serve. Each test gets its own home.
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    # The HuggingFace cache follows these before HOME; a user's own setting would lead a test
    # that builds a docker command into creating folders in the real cache.
    for name in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    # `ling` exports CODEX_HOME to every command it runs, so a suite started by the agent, or by
    # Night Shift's sandboxed test run, resolved `$CODEX_HOME/night` and the rest to the real
    # ~/.mightling whatever HOME said: outside a sandbox it wrote the real `night/runner.lock`, and
    # inside one that write failed a test (2026-10-02). A test that needs the variable sets it.
    monkeypatch.delenv("CODEX_HOME", raising=False)
    # The rename migration's switch (legacy_name_migration.py): a developer's shell setting must not
    # decide whether a test migrates. A test that means to migrate sets it.
    monkeypatch.delenv("MIGHTLING_LEGACY_MIGRATION", raising=False)
    # `ling-admin` extends NO_PROXY in its own environment (ProxyBypass), so a test that runs the
    # CLI would leave the value behind for the rest of the suite. Set first, so that it is restored
    # (deleted) even when the developer's shell had none; a test that needs one sets it.
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)

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


# Test-only vLLM recipes. The registry serves one model, on SGLang, so the vLLM launcher's generic
# machinery (a recipe's speculative config, a pinned image, the KV dtype, self-speculation, env
# vars across the container boundary) is exercised against these instead. Their shapes are those
# of the Qwen 3.5 122B and Qwen 3.6 35B recipes the registry carried until 2026-10-07.
VLLM_TEST_RECIPES = {
    "test-vllm-dflash": dict(
        name="Test vLLM recipe (INT4 AutoRound + external drafter)", params_b=122.0,
        supported_precisions=["AUTOROUND-INT4"], min_memory_gb=71.5, max_memory_gb=120.0,
        compatible_gb10=True, notes="Test only.", hf_repo_id="example/vllm-dflash-int4-AutoRound",
        supports_vision=True,
        launch_overrides={
            "attention_backend": "flash_attn",
            "docker_image": "example-vllm-dflash:1",
            "enable_prefix_caching": True,
            "env": {"VLLM_MARLIN_USE_ATOMIC_ADD": "1"},
            "extra_args": ["--max-num-seqs", "8", "--tensor-parallel-size", "1", "--dtype", "auto",
                           "--default-chat-template-kwargs", '{"enable_thinking": false}'],
            "gpu_memory_utilization": 0.7,
            "kv_cache_dtype": "auto",
            "max_model_len": 32768,
            "max_num_batched_tokens": 9048,
            "reasoning_parser": "qwen3",
            "speculative_config": {"attention_backend": "FLASH_ATTN", "method": "dflash",
                                   "model": "example/vllm-dflash-drafter", "num_speculative_tokens": 12},
            "tool_call_parser": "qwen3_xml",
        },
    ),
    "test-vllm-nvfp4": dict(
        name="Test vLLM recipe (NVFP4 + self-speculation)", params_b=35.0,
        supported_precisions=["NVFP4"], min_memory_gb=25.0, max_memory_gb=60.0,
        compatible_gb10=True, notes="Test only.", hf_repo_id="example/vllm-moe-NVFP4",
        launch_overrides={
            "attention_backend": "flashinfer",
            "env": {"VLLM_MARLIN_USE_ATOMIC_ADD": "1"},
            "extra_args": ["--max-num-seqs", "4", "--tensor-parallel-size", "1", "--dtype", "auto"],
            "gpu_memory_utilization": 0.3,
            "kv_cache_dtype": "fp8",
            "max_model_len": 32768,
            "max_num_batched_tokens": 8192,
            "reasoning_parser": "qwen3",
            "speculative_config": {"method": "mtp", "moe_backend": "triton", "num_speculative_tokens": 3},
            "tool_call_parser": "qwen3_xml",
        },
    ),
    "test-vllm-dflash-draft": dict(
        name="Test drafter", params_b=0.8, supported_precisions=["BF16"], min_memory_gb=1.5,
        max_memory_gb=2.5, compatible_gb10=True, notes="Test only.",
        hf_repo_id="example/vllm-dflash-drafter",
    ),
}


@pytest.fixture
def vllm_recipes(monkeypatch):
    """Adds the test-only vLLM recipes to the model registry for one test."""
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
    from dreamference.hardware.model_spec import ModelSpec

    for key, fields in VLLM_TEST_RECIPES.items():
        monkeypatch.setitem(ModelMatrixRegistry.MATRIX, key, ModelSpec(**fields))
    return VLLM_TEST_RECIPES
