"""Local traffic never goes through a leftover proxy (specs/DREAMFERENCE_MIGHTLING_EGRESS.md §11)."""

import os
import urllib.request

import pytest
import requests

from dreamference.config import ProxyBypass


def test_hosts_come_out_of_urls():
    assert ProxyBypass.host_name("http://192.168.1.20:8000/v1") == "192.168.1.20"
    assert ProxyBypass.host_name("http://Spark.local:8000") == "spark.local"
    assert ProxyBypass.host_name("https://[fe80::1]:8000/") == "fe80::1"
    assert ProxyBypass.host_name("gb10:8000") == "gb10"
    assert ProxyBypass.host_name("") is None and ProxyBypass.host_name(None) is None


def test_existing_entries_stay_first_and_local_hosts_are_added_once():
    merged = ProxyBypass.merged("corp.example, .internal", "other.example,corp.example",
                                ("http://192.168.1.20:8000", "http://localhost:8000", None))
    assert merged == "corp.example,.internal,other.example,localhost,127.0.0.1,::1,192.168.1.20"
    assert ProxyBypass.merged(None, None) == "localhost,127.0.0.1,::1"


def test_both_spellings_are_set_and_a_second_apply_changes_nothing():
    env = {"no_proxy": "corp.example"}
    ProxyBypass.apply(env, "http://10.1.2.3:8000")
    assert env["NO_PROXY"] == env["no_proxy"] == "corp.example,localhost,127.0.0.1,::1,10.1.2.3"
    assert ProxyBypass.apply(dict(env), "http://10.1.2.3:8000") == env


def test_the_python_clients_then_bypass_a_set_proxy(monkeypatch):
    # requests and urllib, the two clients ling-admin uses, both honour the value.
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("http_proxy", "http://proxy.example:3128")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    url = "http://10.1.2.3:8000/v1/models"
    assert not requests.utils.should_bypass_proxies(url, no_proxy=None)
    ProxyBypass.apply(os.environ, "http://10.1.2.3:8000")
    for local in (url, "http://127.0.0.1:8888/search", "http://localhost:8767/status"):
        assert requests.utils.should_bypass_proxies(local, no_proxy=None)
        assert urllib.request.proxy_bypass(local.split("/")[2])
    assert not requests.utils.should_bypass_proxies("http://example.org/", no_proxy=None)


def test_every_ling_admin_command_starts_with_local_traffic_exempt(monkeypatch):
    # The earliest exit, `mcp`, already has it: the configured host and loopback.
    from dreamference.cli import dreamference_cli_controller as controller
    from dreamference.vllm_server import SandboxPrerequisite
    monkeypatch.setenv("NO_PROXY", "corp.example")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("DREAMFERENCE_VLLM_HOST", "http://10.4.5.6:8000")
    monkeypatch.setattr(SandboxPrerequisite, "gate", classmethod(lambda cls, *a: True))
    monkeypatch.setattr(controller, "run_mcp_server", lambda: None)
    with pytest.raises(SystemExit):
        controller.DreamferenceCLIController.run_cli(["mcp"])
    assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == "corp.example,localhost,127.0.0.1,::1,10.4.5.6"


def test_a_host_with_a_comma_or_a_space_is_no_entry():
    # `http://10.0.0.1,evil:8000` gave the host `10.0.0.1,evil`, which NO_PROXY readers split in two.
    assert ProxyBypass.host_name("http://10.0.0.1,evil:8000") is None
    assert ProxyBypass.host_name("http://a b:8000") is None
    merged = ProxyBypass.merged(None, None, ("http://10.0.0.1,evil:8000",))
    assert merged == "localhost,127.0.0.1,::1"


def test_the_configured_host_is_exempt_once_the_configuration_is_read(monkeypatch):
    # The second apply: a host from the configuration, not from the environment, joins the list
    # before any runner or command starts.
    from types import SimpleNamespace

    from dreamference.cli import dreamference_cli_controller as controller
    from dreamference.vllm_server import SandboxPrerequisite

    class RunnerStartedError(Exception):
        pass

    def runner(config):
        raise RunnerStartedError

    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.delenv("DREAMFERENCE_VLLM_HOST", raising=False)
    monkeypatch.setattr(SandboxPrerequisite, "gate", classmethod(lambda cls, *a: True))
    monkeypatch.setattr(controller, "DreamferenceConfig",
                        lambda **kwargs: SimpleNamespace(vllm_host="http://10.7.8.9:8000", agent_runner="codex"))
    monkeypatch.setattr(controller, "CodexRunner", runner)
    with pytest.raises(RunnerStartedError):
        controller.DreamferenceCLIController.run_cli(["audit", "egress"])
    assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == "localhost,127.0.0.1,::1,10.7.8.9"
