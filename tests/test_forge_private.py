"""A private repository can never leave the forge (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §5.3, O6).

Two layers. The first runs in the suite with everything mocked: the GitHub mirror skips a private
or never-mirror repository by name and by visibility, and stops without sending anything when it
cannot read visibility. The second runs a throwaway Forgejo in Docker (marked `forge_container`,
run with MIGHTLING_FORGE_CONTAINER_TESTS=1, never the node's own forge): the API refuses a push
mirror on a private repository, and the mirror job, pointed at a local bare stand-in for GitHub,
sends a shared repository's branches and never the private one's.
"""

import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

from dreamference.forge import (
    ForgeAccounts,
    ForgeAppIni,
    ForgeContainer,
    ForgeGithubMirror,
    ForgeRepositories,
    ForgeSettings,
    forge_github_mirror,
)

PRIVATE = {
    "full_name": "dreamference/archive-pre-rewrite",
    "name": "archive-pre-rewrite",
    "private": True,
}
MEMOS = {"full_name": "dreamference/memos", "name": "memos", "private": False}
SHARED = {"full_name": "dreamference/mightling", "name": "mightling", "private": False}


def configure_mirror():
    ForgeSettings.update_state(role="record")
    ForgeGithubMirror.save_settings(True, "dgxcoder")
    for repo in (PRIVATE, MEMOS, SHARED):
        ForgeRepositories.set_settings(
            repo["full_name"], scope="full", github=f"https://github.com/{repo['full_name']}.git"
        )


def test_private_and_never_mirror_repositories_are_skipped(monkeypatch):
    configure_mirror()
    monkeypatch.setattr(
        ForgeRepositories, "all", classmethod(lambda cls, token=None: [PRIVATE, MEMOS, SHARED])
    )
    sent = []
    monkeypatch.setattr(
        ForgeGithubMirror,
        "mirror_one",
        classmethod(lambda cls, name, destination, token: sent.append(name) or {"ok": True}),
    )
    assert ForgeGithubMirror.run() == 0
    assert sent == ["dreamference/mightling"]
    repositories = ForgeGithubMirror.status()["repositories"]
    assert "skipped" in repositories["dreamference/archive-pre-rewrite"]["error"]
    assert "skipped" in repositories["dreamference/memos"]["error"]


def test_a_missing_visibility_counts_as_private():
    assert ForgeRepositories.never_mirrored({"name": "x"})
    assert ForgeRepositories.never_mirrored({"name": "x", "private": None})
    assert not ForgeRepositories.never_mirrored({"name": "x", "private": False})


def test_the_mirror_stops_when_it_cannot_read_visibility(monkeypatch):
    configure_mirror()

    def unreadable(cls, token=None):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(ForgeRepositories, "all", classmethod(unreadable))
    monkeypatch.setattr(
        ForgeGithubMirror,
        "mirror_one",
        classmethod(lambda *a: pytest.fail("sent without visibility")),
    )
    assert ForgeGithubMirror.run() == 2
    assert "visibility" in ForgeGithubMirror.status()["stopped"]


def test_no_node_allows_forgejos_own_push_mirrors(tmp_path):
    for role in ("record", "mirror"):
        ForgeAppIni.write(tmp_path / role, "gx10-1a2b", role)
        assert "DISABLE_NEW_PUSH = true" in ForgeAppIni.path(tmp_path / role).read_text()
    assert "[mirror]\nENABLED = false" in ForgeAppIni.path(tmp_path / "record").read_text()


def test_the_tick_removes_any_push_mirror_and_names_a_private_one(monkeypatch):
    monkeypatch.setattr(
        ForgeRepositories, "all", classmethod(lambda cls, token=None: [PRIVATE, SHARED])
    )
    deleted = []

    def api(cls, method, path, token, body=None, timeout=30):
        if method == "DELETE":
            deleted.append(path)
            return 204, None
        return 200, [{"remote_name": "remote_mirror_x"}] if "archive" in path else []

    monkeypatch.setattr(ForgeContainer, "api", classmethod(api))
    lines = ForgeRepositories.remove_push_mirrors()
    assert deleted == ["/repos/dreamference/archive-pre-rewrite/push_mirrors/remote_mirror_x"]
    assert "PRIVATE" in lines[0]


def test_a_push_mirror_forgejo_would_not_remove_is_not_reported_removed(monkeypatch):
    monkeypatch.setattr(ForgeRepositories, "all", classmethod(lambda cls, token=None: [PRIVATE]))

    def api(cls, method, path, token, body=None, timeout=30):
        return (500, None) if method == "DELETE" else (200, [{"remote_name": "remote_mirror_x"}])

    monkeypatch.setattr(ForgeContainer, "api", classmethod(api))
    lines = ForgeRepositories.remove_push_mirrors()
    assert len(lines) == 1 and "Could not remove" in lines[0] and "PRIVATE" in lines[0]
    assert "(500)" in lines[0]


def test_an_archive_forgejo_would_not_make_private_is_not_reported_private(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(ForgeRepositories, "create", classmethod(lambda cls, *a, **k: True))
    monkeypatch.setattr(ForgeAccounts, "admin_token", classmethod(lambda cls: "t"))
    monkeypatch.setattr(
        ForgeRepositories,
        "git",
        classmethod(lambda cls, *a, **k: subprocess.CompletedProcess(a, 0, "", "")),
    )
    monkeypatch.setattr(ForgeRepositories, "set_settings", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(
        ForgeContainer, "api", classmethod(lambda cls, method, *a, **k: (500, None))
    )
    bundle = tmp_path / "old.bundle"
    assert ForgeRepositories.import_bundles([bundle], "owner/archive-pre-rewrite") is False
    out = capsys.readouterr().out
    assert "private, archived" not in out and "(500)" in out


# -- a throwaway Forgejo -----------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def throwaway_forge(tmp_path, monkeypatch):
    """A Forgejo of the pinned image on free loopback ports, removed afterwards."""
    if os.environ.get("MIGHTLING_FORGE_CONTAINER_TESTS") != "1":
        pytest.skip("set MIGHTLING_FORGE_CONTAINER_TESTS=1 to run the throwaway Forgejo")
    name = f"mightling-forge-test-{os.getpid()}"
    monkeypatch.setattr(ForgeContainer, "name", name)
    monkeypatch.setattr(ForgeContainer, "network", name)
    monkeypatch.setattr(ForgeContainer, "http_port", free_port())
    monkeypatch.setattr(ForgeContainer, "ssh_port", free_port())
    data = tmp_path / "data"
    ForgeAppIni.write(data, "forge-test", "record")
    assert ForgeContainer.ensure_image() and ForgeContainer.ensure_network()
    argv = ForgeContainer.run_arguments(data, [])
    argv.insert(2, "-d")
    try:
        assert subprocess.run(argv, capture_output=True, text=True, check=False).returncode == 0
        assert ForgeContainer.wait_until_answering(120)
        ready, _ = ForgeAccounts.ensure()
        assert ready
        yield data
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["docker", "network", "rm", name], capture_output=True, check=False)
        # The container wrote as the image's user; clear what it left before pytest's cleanup.
        subprocess.run(["chmod", "-R", "u+rwX", str(data)], capture_output=True, check=False)


@pytest.mark.forge_container
def test_a_private_repository_never_reaches_github(throwaway_forge, tmp_path, monkeypatch):
    token = ForgeAccounts.admin_token()
    assert ForgeRepositories.ensure_organisation("dreamference")
    assert ForgeRepositories.create("dreamference", "archive-pre-rewrite", private=True)
    assert ForgeRepositories.create("dreamference", "mightling", private=False)

    # The API refuses a push mirror on the private repository (on any, as configured).
    status, _ = ForgeContainer.api(
        "POST",
        "/repos/dreamference/archive-pre-rewrite/push_mirrors",
        token,
        {
            "remote_address": "https://github.com/example/leak.git",
            "interval": "8h",
            "sync_on_commit": True,
        },
    )
    assert status in (400, 403, 404, 422), status

    # Content in both, pushed as forge-admin.
    work = tmp_path / "w"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(work),
            "-c",
            "user.name=d",
            "-c",
            "user.email=d@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "one",
        ],
        check=True,
    )
    environment = ForgeRepositories.git_environment(token)
    for repo in ("archive-pre-rewrite", "mightling"):
        pushed = subprocess.run(
            [
                "git",
                "-C",
                str(work),
                *ForgeRepositories.forge_credential_arguments(),
                "push",
                "-q",
                ForgeRepositories.forge_url(f"dreamference/{repo}"),
                "main",
            ],
            env=environment,
            capture_output=True,
            text=True,
        )
        assert pushed.returncode == 0, pushed.stderr

    # Local bare stand-ins for GitHub, configured as each repository's destination.
    stand_ins = {}
    for repo in ("archive-pre-rewrite", "mightling"):
        stand_ins[repo] = tmp_path / "github" / f"{repo}.git"
        subprocess.run(["git", "init", "-q", "--bare", str(stand_ins[repo])], check=True)
        ForgeRepositories.set_settings(
            f"dreamference/{repo}", scope="off", github=str(stand_ins[repo])
        )
    monkeypatch.setattr(forge_github_mirror, "GITHUB_PREFIX", str(tmp_path / "github"))
    ForgeSettings.update_state(role="record")
    ForgeGithubMirror.save_settings(True, "")
    ForgeGithubMirror.run()

    def heads(path: Path) -> str:
        return subprocess.run(
            ["git", "-C", str(path), "for-each-ref", "refs/heads"], capture_output=True, text=True
        ).stdout

    assert "refs/heads/main" in heads(stand_ins["mightling"]), ForgeGithubMirror.status()
    assert heads(stand_ins["archive-pre-rewrite"]) == "", "the private repository's refs arrived"

    # Visibility unreadable: the job stops instead of pushing anything.
    work_commit = subprocess.run(
        [
            "git",
            "-C",
            str(work),
            "-c",
            "user.name=d",
            "-c",
            "user.email=d@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "two",
        ],
        check=True,
    )
    assert work_commit.returncode == 0
    subprocess.run(
        [
            "git",
            "-C",
            str(work),
            *ForgeRepositories.forge_credential_arguments(),
            "push",
            "-q",
            ForgeRepositories.forge_url("dreamference/mightling"),
            "main",
        ],
        env=environment,
        check=True,
    )
    before = heads(stand_ins["mightling"])

    def unreadable(cls, token=None):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(ForgeRepositories, "all", classmethod(unreadable))
    assert ForgeGithubMirror.run() == 2
    time.sleep(0.1)
    assert heads(stand_ins["mightling"]) == before


def test_an_archive_is_private_before_its_history_is_pushed(tmp_path, monkeypatch):
    # The repository may already exist and be public: the pre-rewrite history must not land there.
    calls = []
    monkeypatch.setattr(ForgeRepositories, "create", classmethod(lambda cls, *a, **k: True))
    monkeypatch.setattr(ForgeAccounts, "admin_token", classmethod(lambda cls: "t"))

    def git(cls, work, arguments, environment=None, timeout=0):
        calls.append("push" if "push" in arguments else "fetch")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def api(cls, method, path, token, body=None, timeout=30):
        calls.append(f"{method} {sorted(body or {})}")
        return 200, {}

    monkeypatch.setattr(ForgeRepositories, "git", classmethod(git))
    monkeypatch.setattr(ForgeRepositories, "set_settings", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(ForgeContainer, "api", classmethod(api))
    assert ForgeRepositories.import_bundles([tmp_path / "old.bundle"], "owner/archive-pre-rewrite")
    assert "PATCH ['private']" in calls[: calls.index("push")], calls
