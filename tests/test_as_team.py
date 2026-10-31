"""scripts/as_team.py: the team bots' manifests, code owners, message guard, commits and reviews.

specs/DREAMFERENCE_ORGANISATION.md §5. GitHub is never reached: every request goes to a fake
session that answers from a table and records what was sent. Git runs for real in a temporary
repository, and the JSON web token is signed and verified with a throwaway key by `openssl`.
"""

import base64
import importlib.util
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "as_team.py"
APPS = ROOT / ".github" / "apps"
API = "https://api.github.com"
REPO = "dreamference/mightling"
# A made-up name: the real denylist lives outside the repository, which never names what it forbids.
FORBIDDEN = "Zorblax"
PERSONAL = "Quillmere"
BASE_PERMISSIONS = {
    "contents": "write",
    "issues": "write",
    "metadata": "read",
    "pull_requests": "write",
}


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("as_team", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


as_team = load_script()


class FakeResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self.payload = payload
        self.content = b"" if payload is None else json.dumps(payload).encode()
        self.text = self.content.decode()

    def json(self) -> Any:
        return self.payload


class FakeSession:
    """Answers (method, path) from a table; a list answers once per call, in order."""

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def request(
        self, method: str, url: str, headers: dict[str, str], json: Any, timeout: int
    ) -> FakeResponse:
        path = url.removeprefix(API).split("?")[0]
        self.calls.append(
            {"method": method, "path": path, "auth": headers.get("Authorization"), "json": json}
        )
        answer = self.routes[(method, path)]
        if isinstance(answer, list):
            answer = answer.pop(0)
        return FakeResponse(*answer)

    def sent(self, method: str, path: str) -> list[Any]:
        return [
            call["json"] for call in self.calls if (call["method"], call["path"]) == (method, path)
        ]


class FakeSigner:
    def token(self, credentials: Any) -> str:
        return f"jwt-{credentials.app_id}"


@pytest.fixture
def keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A key folder with credentials for every team and a denylist naming FORBIDDEN."""
    folder = tmp_path / "team-bots"
    folder.mkdir(mode=0o700)
    for number, team in enumerate(json.loads((APPS / "teams.json").read_text())["teams"], 1):
        (folder / f"{team}.json").write_text(
            json.dumps({"app_id": number, "slug": f"dreamference-{team}"})
        )
        key = folder / f"{team}.pem"
        key.write_text("not a real key")
        key.chmod(0o600)
    (folder / "denylist.txt").write_text(f"# one entry per line\n{FORBIDDEN}\n")
    (tmp_path / "anonymity-denylist.txt").write_text(
        f"# personal names\n{PERSONAL}\nnode-[0-9]{{4}}\n"
    )
    monkeypatch.setenv("DREAMFERENCE_TEAM_BOTS_DIR", str(folder))
    return folder


REAL_LOOKUP_TOKEN = as_team.GitHub.lookup_token


@pytest.fixture(autouse=True)
def no_gh_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test off the real `gh`; the lookup tests restore REAL_LOOKUP_TOKEN."""
    monkeypatch.setattr(as_team.GitHub, "lookup_token", lambda self, team: None)


def github(routes: dict[tuple[str, str], Any]) -> tuple[Any, FakeSession]:
    session = FakeSession(routes)
    return as_team.GitHub(session=session, signer=FakeSigner()), session


def token_routes(app_id: int = 3, token: str = "agent-token") -> dict[tuple[str, str], Any]:
    return {
        ("GET", f"/repos/{REPO}/installation"): (200, {"id": 70 + app_id}),
        ("POST", f"/app/installations/{70 + app_id}/access_tokens"): (201, {"token": token}),
    }


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one base commit, `a.txt`."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "someone")
    git(path, "config", "user.email", "someone@example.com")
    (path / "a.txt").write_text("one\n")
    git(path, "add", "a.txt")
    git(path, "commit", "-q", "-m", "base")
    return path


# The registry, the manifests and the code owners.


def test_there_are_fifteen_teams_each_with_a_minimal_manifest():
    registry = as_team.TeamRegistry()
    assert len(registry.teams) == 15
    assert sorted(path.stem for path in APPS.glob("*.json") if path.stem != "teams") == sorted(
        registry.teams
    )
    for team in registry.teams:
        manifest = registry.manifest(team)
        extra = {
            "quality": {"statuses": "write"},
            "release": {"workflows": "write", "actions": "write"},
        }.get(team, {})
        assert manifest["name"] == f"dreamference-{team}"
        assert len(manifest["name"]) <= 34
        assert manifest["default_permissions"] == {**BASE_PERMISSIONS, **extra}
        assert manifest["public"] is False
        assert manifest["default_events"] == []
        assert "hook_attributes" not in manifest
        assert manifest["url"] == "https://mightling.dreamference.ai"
        assert "not a person" in manifest["description"]
        assert registry.teams[team]["email"] == f"{team}@dreamference.ai"
        assert registry.teams[team]["peer"] in registry.teams
        assert registry.teams[team]["peer"] != team


def test_code_owners_name_only_the_teams_and_every_team_owns_something():
    registry = as_team.TeamRegistry()
    named = {owner for _pattern, owners in registry.code_owners.rules for owner in owners}
    assert named == {f"@dreamference/{team}" for team in registry.teams}


@pytest.mark.parametrize(
    ("path", "teams"),
    [
        ("anything-new.txt", {"quality"}),
        ("ling-rs/src/lib.rs", {"agent"}),
        ("ling-rs/src/night.rs", {"night-shift"}),
        ("ling-rs/src/update.rs", {"release"}),
        ("ling-rs/web/policy.json", {"security", "web", "desktop"}),
        ("codex-patches/0019-airgapped.patch", {"security", "agent"}),
        ("codex-patches/0001-something.patch", {"agent"}),
        ("dreamference/chat/desktop_runner.py", {"desktop"}),
        ("dreamference/chat/gmail_client.py", {"web"}),
        ("dreamference/chat/matrix_homeserver.py", {"messengers"}),
        ("dreamference/mcp_server/web_tools.py", {"web"}),
        ("dreamference/mcp_server/mcp_server.py", {"code-index"}),
        ("dreamference/vllm_server/model_gate.py", {"nodes", "models"}),
        ("ling-engine", {"engine"}),
        ("paper/ling-engine.tex", {"engine", "docs"}),
        ("paper/ling.tex", {"docs"}),
        ("specs/DREAMFERENCE_RELEASE_1.7.0.md", {"release"}),
        ("specs/DREAMFERENCE_MODELS.md", {"docs"}),
        ("scripts/swe_bench_compare.py", {"benchmarks"}),
        ("scripts/release_benchmark.py", {"release"}),
        ("scripts/as_team.py", {"quality"}),
        (".github/apps/engine.json", {"quality"}),
        (".github/workflows/release.yml", {"release", "security"}),
        ("tests/test_node.py", {"quality"}),
    ],
)
def test_the_last_matching_code_owners_line_wins(path: str, teams: set[str]):
    assert as_team.TeamRegistry().owning_teams(path) == teams


def test_every_tracked_path_is_named_by_a_line_other_than_the_fallback():
    rules = as_team.TeamRegistry().code_owners.rules
    unnamed = [
        path
        for path in git(ROOT, "ls-files").splitlines()
        if not any(
            pattern != "*" and as_team.CodeOwners.matches(pattern, path) for pattern, _ in rules
        )
    ]
    assert unnamed == [], "give each a line in .github/apps/CODEOWNERS (its owning team)"


def test_reviewers_are_the_other_owning_teams_or_the_authors_peer():
    registry = as_team.TeamRegistry()
    assert registry.required_reviewers("agent", ["ling-rs/src/lib.rs"]) == {"security"}
    assert registry.required_reviewers("agent", ["ling-rs/src/lib.rs", "ling-rs/src/night.rs"]) == {
        "night-shift"
    }
    assert registry.required_reviewers(None, ["ling-rs/src/night.rs"]) == {"night-shift"}
    assert registry.required_reviewers("quality", ["tests/test_node.py"]) == {"docs"}


def test_an_unknown_team_is_refused():
    with pytest.raises(as_team.Refusal, match="not a team"):
        as_team.TeamRegistry().check("marketing")


# The guard.


def test_the_guard_strips_trailers_and_generated_lines(
    keys: Path, capsys: pytest.CaptureFixture[str]
):
    message = (
        "Fix the gate\n\nThe body stays.\n"
        "The reference generated by scripts/gen_admin_reference.py stays too.\n\n"
        "Co-Authored-By: Someone <someone@example.com>\n"
        "Some-Session: https://example.com/session\n"
        "Generated with a tool\n"
        "\U0001f916 Generated with a tool\n"
    )
    assert as_team.MessageGuard().clean(message) == (
        "Fix the gate\n\nThe body stays.\n"
        "The reference generated by scripts/gen_admin_reference.py stays too."
    )
    assert capsys.readouterr().err.count("stripped:") == 4


def test_the_guard_refuses_a_denylisted_word_in_any_case(keys: Path):
    guard = as_team.MessageGuard()
    with pytest.raises(as_team.Refusal, match=r"denylist\.txt line 2"):
        guard.clean(f"Built with {FORBIDDEN.lower()}")
    with pytest.raises(as_team.Refusal, match="reword"):
        guard.clean(f"title\n\nsee {FORBIDDEN.upper()}.")
    assert guard.clean(f"the {FORBIDDEN}ian calendar") == f"the {FORBIDDEN}ian calendar"


def test_the_anonymity_list_is_read_too_and_its_entries_are_never_echoed(keys: Path):
    guard = as_team.MessageGuard()
    with pytest.raises(as_team.Refusal) as refused:
        guard.clean(f"measured on {PERSONAL.lower()}'s laptop")
    assert "anonymity-denylist.txt line 2" in str(refused.value)
    assert PERSONAL.lower() not in str(refused.value).lower()
    with pytest.raises(as_team.Refusal, match="line 3"):
        guard.clean("ran on node-1234 overnight")
    assert guard.clean("ran on node-12345") == "ran on node-12345"


def test_an_entry_that_is_not_a_regular_expression_is_matched_literally(keys: Path):
    (keys.parent / "anonymity-denylist.txt").write_text("c++(\n")
    with pytest.raises(as_team.Refusal):
        as_team.MessageGuard().clean("written in c++( today")


@pytest.mark.parametrize("missing", ["team-bots/denylist.txt", "anonymity-denylist.txt"])
def test_the_guard_refuses_everything_without_both_lists(keys: Path, missing: str):
    (keys.parent / missing).unlink()
    with pytest.raises(as_team.Refusal, match="no denylist"):
        as_team.MessageGuard().clean("an innocent message")


def test_a_branch_name_is_checked_too(keys: Path):
    guard = as_team.MessageGuard()
    assert guard.name("docs/organisation") == "docs/organisation"
    with pytest.raises(as_team.Refusal, match="branch name"):
        guard.name(f"{FORBIDDEN.lower()}/organisation")


# Credentials and tokens.


def test_a_key_others_can_read_is_refused(keys: Path):
    (keys / "engine.pem").chmod(0o644)
    with pytest.raises(as_team.Refusal, match="chmod go="):
        as_team.AppCredentials.load("engine")


def test_registration_stores_the_key_private_and_never_overwrites(tmp_path: Path):
    folder = tmp_path / "fresh"
    conversion = {"id": 42, "slug": "dreamference-engine", "pem": "PEM"}
    stored = as_team.AppCredentials.store("engine", conversion, folder)
    assert stored.app_id == 42
    assert (folder / "engine.pem").stat().st_mode & 0o777 == 0o600
    assert folder.stat().st_mode & 0o777 == 0o700
    assert json.loads((folder / "engine.json").read_text()) == {
        "app_id": 42,
        "slug": "dreamference-engine",
    }
    with pytest.raises(as_team.Refusal, match="exists"):
        as_team.AppCredentials.store("engine", conversion, folder)


def test_the_token_is_minted_for_one_repository_and_cached(keys: Path):
    client, session = github(token_routes())
    assert client.installation_token("agent", REPO) == "agent-token"
    assert client.installation_token("agent", REPO) == "agent-token"
    assert len(session.calls) == 2
    assert session.calls[0]["auth"] == "Bearer jwt-3"
    assert session.sent("POST", "/app/installations/73/access_tokens") == [
        {"repositories": ["mightling"]}
    ]


def test_the_commit_email_uses_the_bot_users_id(keys: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(as_team.GitHub, "lookup_token", REAL_LOOKUP_TOKEN)
    monkeypatch.setattr(
        as_team.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="gh-token\n", stderr=""),
    )
    client, session = github({("GET", "/users/dreamference-engine%5Bbot%5D"): (200, {"id": 123})})
    expected = (
        "dreamference-engine[bot]",
        "123+dreamference-engine[bot]@users.noreply.github.com",
    )
    assert client.identity("engine") == expected
    assert session.calls[0]["auth"] == "Bearer gh-token"
    # The second lookup is answered from the cache: no network call, file is private.
    assert client.identity("engine") == expected
    assert len(session.calls) == 1
    assert (keys / "identities.json").stat().st_mode & 0o777 == 0o600


def test_the_identity_lookup_falls_back_to_the_app_jwt(keys: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(as_team.GitHub, "lookup_token", REAL_LOOKUP_TOKEN)

    def no_gh(*_a, **_k):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(as_team.subprocess, "run", no_gh)
    client, session = github({("GET", "/users/dreamference-engine%5Bbot%5D"): (200, {"id": 5})})
    client.identity("engine")
    assert session.calls[0]["auth"] is not None


def test_the_json_web_token_is_signed_with_the_apps_key(tmp_path: Path):
    key, public = tmp_path / "app.pem", tmp_path / "app.pub"
    subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(key),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(public)], check=True)
    credentials = as_team.AppCredentials(7, "dreamference-engine", key)
    token = as_team.JwtSigner(clock=lambda: 1_000_000).token(credentials)
    header, claims, signature = token.split(".")
    padded = claims + "=" * (-len(claims) % 4)
    assert json.loads(base64.urlsafe_b64decode(padded)) == {
        "iat": 999_940,
        "exp": 1_000_540,
        "iss": "7",
    }
    (tmp_path / "sig").write_bytes(
        base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    )
    verified = subprocess.run(
        ["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(tmp_path / "sig")],
        input=f"{header}.{claims}".encode(),
        capture_output=True,
    )
    assert verified.returncode == 0


# Publishing.


def test_publishing_replays_the_series_as_signed_bot_commits(keys: Path, repo: Path):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("two\n")
    (repo / "b.txt").write_text("new\n")
    git(repo, "add", "a.txt", "b.txt")
    git(
        repo,
        "commit",
        "-q",
        "-m",
        "Change a, add b\n\nWhy.\n\nCo-Authored-By: Someone <s@example.com>",
    )
    git(repo, "rm", "-q", "a.txt")
    git(repo, "commit", "-q", "-m", "Remove a")
    routes = {
        **token_routes(),
        ("GET", f"/repos/{REPO}/git/ref/heads/work"): (404, {"message": "Not Found"}),
        ("POST", f"/repos/{REPO}/git/refs"): (201, {"ref": "refs/heads/work"}),
        ("POST", "/graphql"): [
            (200, {"data": {"createCommitOnBranch": {"commit": {"oid": "c1", "url": ""}}}}),
            (200, {"data": {"createCommitOnBranch": {"commit": {"oid": "c2", "url": ""}}}}),
        ],
        ("GET", f"/repos/{REPO}/git/commits/c2"): (
            200,
            {"tree": {"sha": git(repo, "rev-parse", "HEAD^{tree}")}},
        ),
    }
    client, session = github(routes)
    publisher = as_team.Publisher(client, as_team.Git(repo), as_team.MessageGuard())
    assert publisher.publish_api("agent", REPO, "work", base) == "c2"
    assert session.sent("POST", f"/repos/{REPO}/git/refs") == [
        {"ref": "refs/heads/work", "sha": base}
    ]
    first, second = (call["variables"]["input"] for call in session.sent("POST", "/graphql"))
    assert first["message"] == {"headline": "Change a, add b", "body": "Why."}
    assert first["expectedHeadOid"] == base
    contents = {
        item["path"]: base64.b64decode(item["contents"])
        for item in first["fileChanges"]["additions"]
    }
    assert contents == {"a.txt": b"two\n", "b.txt": b"new\n"}
    assert second["fileChanges"] == {"additions": [], "deletions": [{"path": "a.txt"}]}
    assert second["expectedHeadOid"] == "c1"


def test_an_executable_is_refused_before_anything_is_sent(keys: Path, repo: Path):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "run.sh").write_text("#!/bin/sh\n")
    (repo / "run.sh").chmod(0o755)
    git(repo, "add", "run.sh")
    git(repo, "commit", "-q", "-m", "Add a script")
    client, session = github({})
    publisher = as_team.Publisher(client, as_team.Git(repo), as_team.MessageGuard())
    with pytest.raises(as_team.Refusal, match="--via git"):
        publisher.publish_api("agent", REPO, "work", base)
    assert session.calls == []


def test_a_denylisted_message_is_refused_before_anything_is_sent(keys: Path, repo: Path):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("two\n")
    git(repo, "commit", "-q", "-am", f"Thanks to {FORBIDDEN}")
    client, session = github({})
    publisher = as_team.Publisher(client, as_team.Git(repo), as_team.MessageGuard())
    with pytest.raises(as_team.Refusal, match="reword"):
        publisher.publish_api("agent", REPO, "work", base)
    assert session.calls == []


def test_a_branch_whose_tree_differs_from_the_base_is_not_extended(keys: Path, repo: Path):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("two\n")
    git(repo, "commit", "-q", "-am", "Change a")
    routes = {
        **token_routes(),
        ("GET", f"/repos/{REPO}/git/ref/heads/work"): (200, {"object": {"sha": "remote"}}),
        ("GET", f"/repos/{REPO}/git/commits/remote"): (200, {"tree": {"sha": "some-other-tree"}}),
    }
    client, session = github(routes)
    publisher = as_team.Publisher(client, as_team.Git(repo), as_team.MessageGuard())
    with pytest.raises(as_team.Refusal, match="rebase"):
        publisher.publish_api("agent", REPO, "work", base)
    assert session.sent("POST", "/graphql") == []


def test_a_bot_commit_carries_the_bots_identity_in_utc(keys: Path, repo: Path):
    (repo / "a.txt").write_text("two\n")
    git(repo, "add", "a.txt")
    client, _session = github({("GET", "/users/dreamference-engine%5Bbot%5D"): (200, {"id": 9})})
    publisher = as_team.Publisher(client, as_team.Git(repo), as_team.MessageGuard())
    when = datetime(2026, 10, 20, 9, 30, tzinfo=UTC)
    publisher.commit("engine", "Tune\n\nCo-Authored-By: X <x@example.com>\n", now=when)
    shown = git(repo, "log", "-1", "--format=%an|%ae|%cn|%ce|%ad|%cd|%B", "--date=iso")
    email = "9+dreamference-engine[bot]@users.noreply.github.com"
    stamp = "2026-10-20 09:30:00 +0000"
    assert (
        shown
        == f"dreamference-engine[bot]|{email}|dreamference-engine[bot]|{email}|{stamp}|{stamp}|Tune"
    )


def test_a_push_refuses_a_commit_the_bot_did_not_make(keys: Path, repo: Path):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("two\n")
    git(repo, "commit", "-q", "-am", "By a person")
    client, _session = github({("GET", "/users/dreamference-engine%5Bbot%5D"): (200, {"id": 9})})
    publisher = as_team.Publisher(client, as_team.Git(repo), as_team.MessageGuard())
    with pytest.raises(as_team.Refusal, match="is not dreamference-engine"):
        publisher.publish_git("engine", REPO, "work", base)


def test_a_push_passes_the_token_in_the_environment_only(
    keys: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("two\n")
    git(repo, "add", "a.txt")
    routes = {
        ("GET", "/users/dreamference-engine%5Bbot%5D"): (200, {"id": 9}),
        **token_routes(1, "secret-token"),
    }
    client, _session = github(routes)
    publisher = as_team.Publisher(client, as_team.Git(repo), as_team.MessageGuard())
    publisher.commit("engine", "Tune")
    pushes: list[tuple[tuple[str, ...], dict[str, str]]] = []
    real_run = as_team.Git.run

    def run(
        self: Any, *args: str, env: dict[str, str] | None = None, stdin: bytes | None = None
    ) -> bytes:
        if args[0] == "push":
            pushes.append((args, env or {}))
            return b""
        return real_run(self, *args, env=env, stdin=stdin)

    monkeypatch.setattr(as_team.Git, "run", run)
    publisher.publish_git("engine", REPO, "work", base)
    [(args, env)] = pushes
    assert args == ("push", f"https://github.com/{REPO}.git", "HEAD:refs/heads/work")
    assert not any("secret-token" in arg for arg in args)
    expected = base64.b64encode(b"x-access-token:secret-token").decode()
    assert env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {expected}"


# Pull requests, reviews, the gate.


def pull_routes(
    author: str, files: list[str], reviews: list[dict[str, Any]]
) -> dict[tuple[str, str], Any]:
    return {
        ("GET", f"/repos/{REPO}/pulls/5"): (
            200,
            {"user": {"login": author}, "head": {"sha": "head"}, "title": "T", "body": "B"},
        ),
        ("GET", f"/repos/{REPO}/pulls/5/files"): (200, [{"filename": name} for name in files]),
        ("GET", f"/repos/{REPO}/pulls/5/reviews"): (200, reviews),
    }


def test_a_pull_request_is_opened_with_a_guarded_body(keys: Path):
    routes = {
        **token_routes(),
        ("POST", f"/repos/{REPO}/pulls"): (201, {"number": 5, "html_url": "u"}),
    }
    client, session = github(routes)
    pulls = as_team.PullRequests(client, as_team.TeamRegistry(), as_team.MessageGuard())
    pulls.open("agent", REPO, "work", "main", "Title", "Body\n\nCo-Authored-By: X <x@example.com>")
    assert session.sent("POST", f"/repos/{REPO}/pulls") == [
        {"title": "Title", "head": "work", "base": "main", "body": "Body"}
    ]
    with pytest.raises(as_team.Refusal, match="reword"):
        pulls.open("agent", REPO, "work", "main", f"{FORBIDDEN} title", "")


def test_a_team_does_not_review_its_own_pull_request(keys: Path):
    routes = {
        **token_routes(),
        **pull_routes("dreamference-agent[bot]", ["ling-rs/src/lib.rs"], []),
    }
    client, _session = github(routes)
    pulls = as_team.PullRequests(client, as_team.TeamRegistry(), as_team.MessageGuard())
    with pytest.raises(as_team.Refusal, match="own pull request"):
        pulls.review("agent", REPO, 5, "APPROVE", "")


def test_a_review_approves_the_current_head(keys: Path):
    routes = {
        **token_routes(11, "security-token"),
        **pull_routes("dreamference-agent[bot]", ["ling-rs/src/lib.rs"], []),
        ("POST", f"/repos/{REPO}/pulls/5/reviews"): (200, {"state": "APPROVED"}),
    }
    client, session = github(routes)
    pulls = as_team.PullRequests(client, as_team.TeamRegistry(), as_team.MessageGuard())
    pulls.review("security", REPO, 5, "APPROVE", "")
    assert session.sent("POST", f"/repos/{REPO}/pulls/5/reviews") == [
        {"commit_id": "head", "event": "APPROVE"}
    ]


def gate(reviews: list[dict[str, Any]]) -> tuple[tuple[str, str], FakeSession]:
    files = ["ling-rs/src/lib.rs", "ling-rs/src/night.rs"]
    routes = {
        **token_routes(15, "quality-token"),
        **pull_routes("dreamference-agent[bot]", files, reviews),
        ("POST", f"/repos/{REPO}/statuses/head"): (201, {}),
    }
    client, session = github(routes)
    pulls = as_team.PullRequests(client, as_team.TeamRegistry(), as_team.MessageGuard())
    return pulls.gate(REPO, 5), session


def review_by(team: str, state: str, commit: str = "head") -> dict[str, Any]:
    return {"user": {"login": f"dreamference-{team}[bot]"}, "state": state, "commit_id": commit}


def test_the_gate_passes_only_when_every_required_team_approved_the_head(keys: Path):
    (state, description), session = gate([review_by("night-shift", "APPROVED", "older")])
    assert (state, description) == ("pending", "waiting for: night-shift")
    (state, _description), session = gate([review_by("night-shift", "APPROVED")])
    assert state == "success"
    status = session.sent("POST", f"/repos/{REPO}/statuses/head")
    assert status == [
        {"state": "success", "context": "team-review", "description": "approved by: night-shift"}
    ]
    assert session.calls[-1]["auth"] == "Bearer quality-token"


def test_the_gate_fails_while_a_team_requests_changes(keys: Path):
    (state, description), _session = gate(
        [review_by("night-shift", "APPROVED"), review_by("security", "CHANGES_REQUESTED")]
    )
    assert (state, description) == ("failure", "changes requested by: security")


def test_a_later_approval_replaces_a_request_for_changes(keys: Path):
    (state, _description), _session = gate(
        [review_by("night-shift", "CHANGES_REQUESTED"), review_by("night-shift", "APPROVED")]
    )
    assert state == "success"


def test_a_merge_squashes_with_the_guarded_title(keys: Path):
    routes = {
        **token_routes(),
        **pull_routes("dreamference-agent[bot]", [], []),
        ("PUT", f"/repos/{REPO}/pulls/5/merge"): (200, {"sha": "merged"}),
    }
    client, session = github(routes)
    pulls = as_team.PullRequests(client, as_team.TeamRegistry(), as_team.MessageGuard())
    assert pulls.merge("agent", REPO, 5) == {"sha": "merged"}
    assert session.sent("PUT", f"/repos/{REPO}/pulls/5/merge") == [
        {"merge_method": "squash", "sha": "head", "commit_title": "T (#5)", "commit_message": "B"}
    ]


# Registration and the command line.


def test_the_registration_form_posts_the_manifest_to_the_organisation(keys: Path):
    client, _session = github({})
    form = as_team.ManifestRegistration(client, as_team.TeamRegistry()).form(
        "engine", "http://node-a.local:5000/t/callback", "engine.s1"
    )
    assert (
        'action="https://github.com/organizations/dreamference/settings/apps/new?state=engine.s1"'
        in form
    )
    assert "&quot;redirect_url&quot;: &quot;http://node-a.local:5000/t/callback&quot;" in form
    assert "&quot;name&quot;: &quot;dreamference-engine&quot;" in form


NAMES = ["node-a.local:5000", "192.0.2.7:5000"]


def site(
    tmp_path: Path, routes: dict[tuple[str, str], Any] | None = None
) -> tuple[Any, FakeSession]:
    client, session = github(routes or {})
    registration = as_team.ManifestRegistration(client, as_team.TeamRegistry())
    return as_team.RegistrationSite(
        registration, ["quality", "docs"], NAMES, "tok", tmp_path / "k"
    ), session


def test_the_phone_page_answers_only_under_its_token_and_its_two_names(tmp_path: Path):
    page, _session = site(tmp_path)
    assert page.handle("/tok/", "node-a.local:5000")[0] == 200
    assert page.handle("/tok/", "192.0.2.7:5000")[0] == 200
    assert page.handle("/tok/", "evil.example:5000")[0] == 404
    assert page.handle("/", "node-a.local:5000")[0] == 404
    assert page.handle("/other/", "node-a.local:5000")[0] == 404


def test_the_phone_page_sends_github_back_by_the_name_the_phone_used(tmp_path: Path):
    page, _session = site(tmp_path)
    by_name = page.handle("/tok/", "node-a.local:5000")[2]
    by_address = page.handle("/tok/", "192.0.2.7:5000")[2]
    assert "http://node-a.local:5000/tok/callback" in by_name
    assert "http://192.0.2.7:5000/tok/callback" in by_address
    assert 'name="viewport"' in by_name
    assert by_name.count("<form") == 2
    assert "http://node-a.local:5000/tok/" in by_name and "http://192.0.2.7:5000/tok/" in by_name


def test_the_callback_stores_the_app_and_spends_its_state(tmp_path: Path):
    answer = {"id": 41, "slug": "dreamference-quality", "pem": "PEM"}
    page, _session = site(tmp_path, {("POST", "/app-manifests/c1/conversions"): (201, answer)})
    page.handle("/tok/", "node-a.local:5000")
    state = page.state_for("quality")
    status, headers, _body = page.handle(
        f"/tok/callback?code=c1&state={state}", "node-a.local:5000"
    )
    assert (status, headers) == (303, {"Location": "/tok/"})
    assert (tmp_path / "k" / "quality.pem").stat().st_mode & 0o777 == 0o600
    assert "✅ app 41" in page.handle("/tok/", "node-a.local:5000")[2]
    assert not page.finished
    page.handle(f"/tok/callback?code=c1&state={state}", "node-a.local:5000")
    assert "spent" in page.problem


def test_the_page_lists_a_registered_team_as_done(tmp_path: Path):
    as_team.AppCredentials.store(
        "docs", {"id": 5, "slug": "dreamference-docs", "pem": "P"}, tmp_path / "k"
    )
    page, _session = site(tmp_path)
    assert "docs" in page.done and "quality" not in page.done
    assert page.handle("/tok/", "node-a.local:5000")[2].count("<form") == 1


def test_the_lan_address_is_the_routes_source_and_sends_nothing(monkeypatch: pytest.MonkeyPatch):
    seen: list[Any] = []

    class Probe:
        def __init__(self, family: int, kind: int) -> None:
            seen.append(kind)

        def connect(self, peer: tuple[str, int]) -> None:
            seen.append(peer)

        def getsockname(self) -> tuple[str, int]:
            return ("192.0.2.7", 40000)

        def close(self) -> None:
            seen.append("closed")

    monkeypatch.setattr(as_team.socket, "socket", Probe)
    assert as_team.lan_address() == "192.0.2.7"
    assert seen == [as_team.socket.SOCK_DGRAM, ("192.0.2.1", 9), "closed"]


def test_the_flows_code_is_exchanged_and_the_key_stored(tmp_path: Path):
    answer = {"id": 99, "slug": "dreamference-engine", "pem": "PEM"}
    client, session = github({("POST", "/app-manifests/abc/conversions"): (201, answer)})
    stored = as_team.ManifestRegistration(client, as_team.TeamRegistry()).convert(
        "engine", "abc", tmp_path / "k"
    )
    assert stored.app_id == 99
    assert session.calls[0]["auth"] is None
    assert (tmp_path / "k" / "engine.pem").read_text() == "PEM"


def test_the_command_line_prints_owners_and_refuses_with_status_one(
    keys: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    client, _session = github({})
    assert as_team.main(["owners", "ling-rs/src/night.rs"], github=client) == 0
    assert capsys.readouterr().out == "ling-rs/src/night.rs: night-shift\n"
    message = tmp_path / "message.txt"
    message.write_text(f"About {FORBIDDEN}\n")
    assert as_team.main(["check-message", str(message)], github=client) == 1
    assert "reword" in capsys.readouterr().err
    assert as_team.main(["whoami", "marketing"], github=client) == 1


def test_no_token_or_key_material_is_ever_printed(keys: Path, capsys: pytest.CaptureFixture[str]):
    client, _session = github(token_routes())
    assert as_team.main(["status"], github=client) == 0
    output = capsys.readouterr().out
    assert "not a real key" not in output
    assert output.count("✅") == 15
    assert os.environ["DREAMFERENCE_TEAM_BOTS_DIR"] == str(keys)


# Publishing hours and the queue.

AT_NINE = datetime(2026, 10, 20, 9, 7, tzinfo=UTC)
AT_NOON = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def test_the_publishing_hours_are_nine_and_twenty_one_utc():
    assert as_team.TeamRegistry().publish_hours == [9, 21]


def test_a_write_outside_the_hours_is_refused_unless_now(
    keys: Path, capsys: pytest.CaptureFixture[str]
):
    client, session = github({})
    argv = ["gate", "5"]
    assert as_team.main(argv, github=client, clock=lambda: AT_NOON) == 1
    assert "09:00, 21:00 UTC" in capsys.readouterr().err
    assert session.calls == []


def test_a_held_publish_is_pinned_to_commits_and_runs_at_the_hour(
    keys: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("two\n")
    git(repo, "commit", "-q", "-am", "Change a")
    head = git(repo, "rev-parse", "HEAD")
    body = repo / "body.md"
    body.write_text("Why.")
    client, _session = github({})
    held = ["publish", "agent", "--branch", "work", "--base", "HEAD~1"]
    assert as_team.main(["queue", *held], github=client, cwd=repo, clock=lambda: AT_NOON) == 0
    pr = ["pr", "agent", "--branch", "work", "--title", "Change a", "--body-file", str(body)]
    assert as_team.main(["queue", *pr], github=client, cwd=repo, clock=lambda: AT_NOON) == 0
    body.write_text("changed after queueing")
    (repo / "a.txt").write_text("three\n")
    git(repo, "commit", "-q", "-am", "Later work")
    ran: list[tuple[str, Any]] = []
    monkeypatch.setitem(as_team.COMMANDS, "publish", lambda run: ran.append(("publish", run.args)))
    monkeypatch.setitem(as_team.COMMANDS, "pr", lambda run: ran.append(("pr", run.body())))
    assert as_team.main(["flush"], github=client, cwd=repo, clock=lambda: AT_NOON) == 0
    assert ran == []
    assert as_team.main(["flush"], github=client, cwd=repo, clock=lambda: AT_NINE) == 0
    (first, publish_args), second = ran
    assert first == "publish"
    assert (publish_args.base, publish_args.head, publish_args.now) == (base, head, True)
    assert second == ("pr", "Why.")
    assert as_team.PublishQueue().entries() == []


def test_a_failing_held_command_stops_the_flush_and_stays_held(
    keys: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    client, _session = github({})
    for number in ("5", "6"):
        assert (
            as_team.main(["queue", "gate", number], github=client, cwd=repo, clock=lambda: AT_NOON)
            == 0
        )

    def refuse(run: Any) -> None:
        raise as_team.Refusal("GitHub answered 502")

    monkeypatch.setitem(as_team.COMMANDS, "gate", refuse)
    assert as_team.main(["flush"], github=client, cwd=repo, clock=lambda: AT_NINE) == 1
    assert "stopped the flush" in capsys.readouterr().err
    assert len(as_team.PublishQueue().entries()) == 2


def test_only_writes_are_held(keys: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    client, _session = github({})
    assert as_team.main(["queue", "status"], github=client, cwd=repo) == 1
    assert "only publish, pr, review, gate, merge" in capsys.readouterr().err


def test_the_timer_runs_flush_at_the_publishing_hours(
    keys: Path, capsys: pytest.CaptureFixture[str]
):
    assert as_team.main(["timer"], github=github({})[0]) == 0
    units = capsys.readouterr().out
    assert "OnCalendar=*-*-* 09,21:05:00 UTC" in units
    assert "as_team.py flush" in units
    assert "Environment=TZ=UTC" in units
