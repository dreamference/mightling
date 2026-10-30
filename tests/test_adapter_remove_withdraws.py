"""`ling-admin adapter remove`: a removed adapter is taken off the serving gate, a missing one is not.

The gate's list is what refuses a session still using the adapter at its next request. Neither
the installer nor the gate is real here.
"""

import argparse

import pytest

from dreamference.cli.adapter_command import AdapterCommand
from dreamference.hardware.adapter_installer import AdapterInstaller
from dreamference.vllm_server.adapter_serving import AdapterServing


@pytest.mark.parametrize("installed, code", [(True, 0), (False, 1)])
def test_only_a_removed_adapter_is_withdrawn_from_serving(monkeypatch, installed, code):
    withdrawn = []
    monkeypatch.setattr(AdapterInstaller, "remove", staticmethod(lambda name: installed))
    monkeypatch.setattr(AdapterServing, "withdraw", classmethod(lambda cls, n: withdrawn.append(n)))
    assert AdapterCommand._remove(argparse.Namespace(name="rust"), None) == code
    assert withdrawn == (["rust"] if installed else [])
