"""`ForgeMirrors`: where the mirror plan comes from, and what a sync does with none or a good one.

The forge, the pairing and the other nodes are stand-ins; the state file lives in the test's HOME.
"""

import json

import pytest

from dreamference.forge.forge_accounts import ForgeAccounts
from dreamference.forge.forge_container import ForgeContainer
from dreamference.forge.forge_mirrors import ForgeMirrors
from dreamference.forge.forge_repositories import ForgeRepositories
from dreamference.forge.forge_settings import ForgeSettings

PLAN = {"record_node": "rec-1", "record_name": "record", "token": "t", "repositories": ["o/r"]}


def never(*_, **__):
    pytest.fail("called")


def test_a_record_that_cannot_list_its_repositories_gives_no_plan(monkeypatch):
    def unreadable(cls, token=None):
        raise RuntimeError("the forge did not list /repos/search (HTTP 500)")

    ForgeSettings.update_state(role="record")
    monkeypatch.setattr(ForgeContainer, "running", classmethod(lambda cls: True))
    monkeypatch.setattr(ForgeAccounts, "mirror_token", classmethod(lambda cls: "t"))
    monkeypatch.setattr(ForgeRepositories, "all", classmethod(unreadable))
    assert ForgeMirrors.plan() is None


def test_serving_the_plan_prints_it_or_fails_with_the_reason(monkeypatch, capsys):
    monkeypatch.setattr(ForgeMirrors, "plan", classmethod(lambda cls: None))
    assert ForgeMirrors.serve_plan() == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "not a running forge of record" in captured.err

    monkeypatch.setattr(ForgeMirrors, "plan", classmethod(lambda cls: PLAN))
    assert ForgeMirrors.serve_plan() == 0
    assert json.loads(capsys.readouterr().out) == PLAN
