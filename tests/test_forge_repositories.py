"""`ForgeRepositories`: which hooks each repository gets, and how a repository is made or filled.

Nothing here runs Forgejo, git against a forge or `gh`: the API, git and the hooks are stand-ins
that record what they were asked.
"""

import subprocess
from pathlib import Path

import pytest

from dreamference.forge.forge_accounts import ForgeAccounts
from dreamference.forge.forge_anonymity import SCOPE_FULL, SCOPE_OFF, ForgeAnonymity
from dreamference.forge.forge_container import ForgeContainer
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


# -- creating a repository and filling it


class FakeApi:
    """Answers each (method, path) from a table and records every call."""

    def __init__(self, answers):
        self.answers = answers
        self.refused = []
        self.calls = []

    def __call__(self, cls, method, path, token, body=None, timeout=0):
        self.calls.append((method, path, body))
        for refused_body, answer in self.refused:
            if body == refused_body:
                return answer
        return self.answers.get((method, path), (404, {"message": "not found"}))


@pytest.fixture
def api(monkeypatch, forge):
    fake = FakeApi({})
    monkeypatch.setattr(ForgeContainer, "api", classmethod(fake))
    return fake


@pytest.fixture
def git(monkeypatch):
    """Git in a work clone: each call recorded, answering from `codes` by its subcommand."""
    state = {"calls": [], "codes": {}}

    def run(cls, repository, arguments, environment=None, timeout=0):
        verb = next(a for a in arguments if a in ("fetch", "push"))
        state["calls"].append((verb, arguments, environment))
        code = state["codes"].get(verb, 0)
        return subprocess.CompletedProcess(arguments, code, "", f"{verb} broke" if code else "")

    monkeypatch.setattr(ForgeRepositories, "git", classmethod(run))
    return state


def init_bare_only(monkeypatch):
    """`git init --bare` of the work clone, the one real command, is recorded instead."""
    made = []
    monkeypatch.setattr(
        "dreamference.forge.forge_repositories.subprocess.run",
        lambda argv, **_: made.append(argv) or Path(argv[-1]).mkdir(parents=True),
    )
    return made


def test_an_existing_repository_is_not_created_again(api):
    api.answers[("GET", "/repos/owner/tools")] = (200, {})
    assert ForgeRepositories.create("owner", "tools", private=False) is True
    assert [call[0] for call in api.calls] == ["GET"]


def test_a_repository_is_created_in_an_organisation_or_for_a_user(api):
    api.answers[("GET", "/orgs/team")] = (200, {})
    api.answers[("POST", "/orgs/team/repos")] = (201, {})
    api.answers[("POST", "/admin/users/someone/repos")] = (201, {})

    assert ForgeRepositories.create("team", "tools", private=True)
    assert ForgeRepositories.create("someone", "tools", private=False)

    posts = [(path, body) for method, path, body in api.calls if method == "POST"]
    assert posts[0][0] == "/orgs/team/repos" and posts[0][1]["private"] is True
    assert posts[1][0] == "/admin/users/someone/repos" and posts[1][1]["private"] is False


def test_a_repository_the_forge_does_not_create_reads_as_missing(api, capsys):
    api.answers[("POST", "/admin/users/someone/repos")] = (409, {"message": "taken"})
    assert ForgeRepositories.create("someone", "tools", private=False) is False
    out = capsys.readouterr().out
    assert "HTTP 409" in out and "taken" in out


def test_nothing_is_pushed_into_a_repository_the_forge_did_not_make_private(api, git, tmp_path):
    api.answers[("PATCH", "/repos/owner/archive")] = (500, None)
    assert ForgeRepositories.push_private(tmp_path, "owner/archive") is False
    assert git["calls"] == []


def test_a_private_push_that_git_refuses_fails(api, git, tmp_path, capsys):
    api.answers[("PATCH", "/repos/owner/archive")] = (200, {})
    git["codes"]["push"] = 1
    assert ForgeRepositories.push_private(tmp_path, "owner/archive") is False
    assert api.calls == [("PATCH", "/repos/owner/archive", {"private": True})]
    assert "push broke" in capsys.readouterr().out
