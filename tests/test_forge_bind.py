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
