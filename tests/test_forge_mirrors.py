"""`ForgeMirrors`: where the mirror plan comes from, and what a sync does with none or a good one.

The forge, the pairing and the other nodes are stand-ins; the state file lives in the test's HOME.
"""

import json
import subprocess

import pytest

from dreamference.forge.forge_accounts import ForgeAccounts
from dreamference.forge.forge_container import ForgeContainer
from dreamference.forge.forge_mirrors import ForgeMirrors
from dreamference.forge.forge_repositories import ForgeRepositories
from dreamference.forge.forge_settings import ForgeSettings
from dreamference.node.node_pairing import NodePairing

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


# Fetching the plan


def test_the_record_fetches_its_own_plan_and_asks_no_node(monkeypatch):
    ForgeSettings.update_state(role="record")
    monkeypatch.setattr(ForgeMirrors, "plan", classmethod(lambda cls: PLAN))
    monkeypatch.setattr(NodePairing, "find", classmethod(never))
    assert ForgeMirrors.fetch_plan() == PLAN


def test_a_node_that_knows_no_record_asks_no_one(monkeypatch):
    ForgeSettings.update_state(role="mirror")
    monkeypatch.setattr(NodePairing, "find", classmethod(never))
    assert ForgeMirrors.fetch_plan() is None


def test_a_record_this_node_is_not_paired_with_is_not_asked(monkeypatch):
    ForgeSettings.update_state(role="mirror", record_node="rec-1")
    monkeypatch.setattr(NodePairing, "find", classmethod(lambda cls, name, browse=True: None))
    monkeypatch.setattr(NodePairing, "run", classmethod(never))
    assert ForgeMirrors.fetch_plan() is None


def test_an_answer_that_is_not_json_is_no_plan(monkeypatch):
    ForgeSettings.update_state(role="mirror", record_node="rec-1")
    monkeypatch.setattr(
        NodePairing, "find", classmethod(lambda cls, name, browse=True: {"node": name})
    )
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(lambda cls, *a, **k: subprocess.CompletedProcess([], 0, "{not json", "")),
    )
    assert ForgeMirrors.fetch_plan() is None
