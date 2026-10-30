"""`ForgeBind`: reading the interfaces and Avahi's rules when either cannot be read.

`ip` never runs: `subprocess.run` is a stand-in. The addresses are labels, not real addresses.
"""

import subprocess
from types import SimpleNamespace

import pytest

from dreamference.forge import forge_bind
from dreamference.forge.forge_bind import ForgeBind


def answering(stdout):
    return lambda *_, **__: SimpleNamespace(stdout=stdout)


def raising(error):
    def run(*_, **__):
        raise error

    return run


@pytest.mark.parametrize(
    "run",
    [
        raising(FileNotFoundError("ip")),
        raising(subprocess.TimeoutExpired(["ip"], 10)),
        answering("not json"),
        answering('{"ifname": "eth0"}'),
        answering(""),
    ],
)
def test_interfaces_that_cannot_be_listed_are_none(monkeypatch, run):
    monkeypatch.setattr(forge_bind.subprocess, "run", run)
    assert ForgeBind.interfaces() == []


def test_the_interfaces_are_ip_s_list(monkeypatch):
    monkeypatch.setattr(forge_bind.subprocess, "run", answering('[{"ifname": "eth0"}]'))
    assert ForgeBind.interfaces() == [{"ifname": "eth0"}]


def test_an_unreadable_avahi_conf_allows_and_denies_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(forge_bind, "AVAHI_CONF", tmp_path / "missing.conf")
    assert ForgeBind.avahi_rules() == ([], [])

    conf = tmp_path / "avahi-daemon.conf"
    conf.write_text("[server]\nallow-interfaces=eth0, wlan0\ndeny-interfaces=docker0\n")
    monkeypatch.setattr(forge_bind, "AVAHI_CONF", conf)
    assert ForgeBind.avahi_rules() == (["eth0", "wlan0"], ["docker0"])


def test_only_each_ipv4_address_is_kept_once(monkeypatch):
    interfaces = [
        {
            "ifname": "eth0",
            "addr_info": [
                {"family": "inet6", "local": "lan-v6"},
                {"family": "inet"},
                {"local": "lan-a"},
                {"family": "inet", "local": "lan-a"},
                {"family": "inet", "local": "lan-b"},
            ],
        },
        {"ifname": "eth1", "addr_info": None},
    ]
    assert ForgeBind.lan_addresses(interfaces, avahi_text="") == ["lan-a", "lan-b"]
