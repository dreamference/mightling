"""`FleetModelPlan`: the folders a node needs, and which of them this machine can copy.

The hub cache is a folder in the test's own tmp directory; the matrix answers are stand-ins.
"""

import pytest

import dreamference.hardware
from dreamference.context_engine.embedding_calculator import EMBEDDING_MODEL
from dreamference.node.fleet_model_plan import FleetModelPlan
from dreamference.node.node_model_sync import NodeModelSync

EMBEDDING = NodeModelSync.folder_name(EMBEDDING_MODEL)


@pytest.fixture
def hub(monkeypatch, tmp_path):
    monkeypatch.setattr(NodeModelSync, "hub", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def matrix(monkeypatch):
    """The model's repositories, set per test, and its launch overrides (none)."""
    repos = []
    monkeypatch.setattr(NodeModelSync, "repos", classmethod(lambda cls, key: list(repos)))
    monkeypatch.setattr(dreamference.hardware, "get_model_launch_overrides", lambda key: {})
    return repos


@pytest.mark.parametrize(
    ("repos", "here", "folders"),
    [
        (["org/model"], True, ["models--org--model", EMBEDDING]),
        (["org/model", EMBEDDING_MODEL], True, ["models--org--model", EMBEDDING]),
        (["org/model"], False, ["models--org--model"]),
    ],
)
def test_the_embedding_model_is_added_once_and_only_when_this_machine_has_it(
    hub, matrix, repos, here, folders
):
    matrix.extend(repos)
    if here:
        (hub / EMBEDDING).mkdir()
    assert FleetModelPlan.plan("the-model")["folders"] == folders
