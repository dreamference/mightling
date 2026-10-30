"""`ForgeRepositories`: which hooks each repository gets, and how a repository is made or filled.

Nothing here runs Forgejo, git against a forge or `gh`: the API, git and the hooks are stand-ins
that record what they were asked.
"""

import pytest

from dreamference.forge.forge_accounts import ForgeAccounts
from dreamference.forge.forge_anonymity import SCOPE_FULL, SCOPE_OFF, ForgeAnonymity
from dreamference.forge.forge_repositories import ForgeRepositories
from dreamference.forge.forge_settings import ForgeSettings

SHARED = {"full_name": "owner/tools", "name": "tools", "private": False}
PRIVATE = {"full_name": "owner/notes", "name": "notes", "private": True}
UNSET = {"full_name": "owner/unset", "name": "unset"}
NEVER = {"full_name": "owner/memos", "name": "memos", "private": False}
GITHUB = "https://github.com/example/tools"


@pytest.fixture
def forge(monkeypatch, tmp_path):
    """A forge whose data and share folders are the test's own, with an admin token."""
    monkeypatch.setattr(ForgeSettings, "data_dir", classmethod(lambda cls: tmp_path / "data"))
    monkeypatch.setattr(ForgeSettings, "share_dir", classmethod(lambda cls: tmp_path / "share"))
    monkeypatch.setattr(ForgeAccounts, "admin_token", classmethod(lambda cls: "admin-token"))
    return tmp_path


def make_bare(full_name):
    folder = ForgeRepositories.repository_dir(full_name)
    folder.mkdir(parents=True)
    return folder


# -- the hooks each repository gets


@pytest.fixture
def hooks(monkeypatch, forge):
    installed = {}
    monkeypatch.setattr(ForgeAnonymity, "write_patterns", classmethod(lambda cls, data: None))

    def install(cls, folder, scope, exempt, check_roots, trigger):
        installed[folder.parent.name + "/" + folder.name.removesuffix(".git")] = {
            "scope": scope,
            "exempt": exempt,
            "check_roots": check_roots,
            "trigger": trigger,
        }

    monkeypatch.setattr(ForgeAnonymity, "install", classmethod(install))
    return installed


def test_a_private_repository_gets_no_check_and_no_mirror_trigger_whatever_its_settings(
    monkeypatch, hooks
):
    repositories = [PRIVATE, UNSET, NEVER]
    monkeypatch.setattr(ForgeRepositories, "all", classmethod(lambda cls: repositories))
    for repository in repositories:
        make_bare(repository["full_name"])
        ForgeRepositories.set_settings(
            repository["full_name"], scope=SCOPE_FULL, exempt=["docs"], github=GITHUB
        )

    assert ForgeRepositories.install_hooks(mirror_trigger=True) == 3

    for repository in repositories:
        assert hooks[repository["full_name"]] == {
            "scope": SCOPE_OFF,
            "exempt": ["docs"],
            "check_roots": False,
            "trigger": False,
        }, repository["full_name"]


def test_a_shared_repository_gets_its_scope_and_the_trigger_only_with_a_destination(
    monkeypatch, hooks
):
    plain = {"full_name": "owner/plain", "name": "plain", "private": False}
    unchecked = {"full_name": "owner/loose", "name": "loose", "private": False}
    monkeypatch.setattr(
        ForgeRepositories, "all", classmethod(lambda cls: [SHARED, plain, unchecked])
    )
    for repository in (SHARED, plain, unchecked):
        make_bare(repository["full_name"])
    ForgeRepositories.set_settings(SHARED["full_name"], scope=SCOPE_FULL, github=GITHUB)
    ForgeRepositories.set_settings(plain["full_name"], scope=SCOPE_FULL)
    ForgeRepositories.set_settings(unchecked["full_name"], scope=SCOPE_OFF, github=GITHUB)

    ForgeRepositories.install_hooks(mirror_trigger=True)

    assert hooks[SHARED["full_name"]]["trigger"] and hooks[SHARED["full_name"]]["check_roots"]
    assert hooks[SHARED["full_name"]]["scope"] == SCOPE_FULL
    assert not hooks[plain["full_name"]]["trigger"], "no GitHub destination, no trigger"
    assert hooks[plain["full_name"]]["check_roots"]
    # A shared repository whose check is off scans nothing and takes any history.
    assert hooks[unchecked["full_name"]] == {
        "scope": SCOPE_OFF,
        "exempt": [],
        "check_roots": False,
        "trigger": True,
    }


def test_without_the_mirror_trigger_no_repository_gets_one_and_a_missing_folder_is_skipped(
    monkeypatch, hooks
):
    monkeypatch.setattr(ForgeRepositories, "all", classmethod(lambda cls: [SHARED, PRIVATE]))
    make_bare(SHARED["full_name"])
    ForgeRepositories.set_settings(SHARED["full_name"], scope=SCOPE_FULL, github=GITHUB)

    assert ForgeRepositories.install_hooks(mirror_trigger=False) == 1

    assert list(hooks) == [SHARED["full_name"]]
    assert hooks[SHARED["full_name"]]["trigger"] is False


def test_a_repository_folder_is_named_in_lower_case_under_the_data_folder(forge):
    assert ForgeRepositories.repository_dir("Owner/Tools.Repo") == (
        forge / "data" / "git" / "repositories" / "owner" / "tools.repo.git"
    )
