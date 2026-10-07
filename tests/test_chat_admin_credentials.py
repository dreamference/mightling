"""
The web chat's admin account: a generated password per install, stored privately, and the one-time
move of an install that still has the published default password. No test signs in to a real web
chat: conftest refuses an unmocked OnyxRunner._login, and these tests fake the HTTP layer.
"""

import ast
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from dreamference.chat import chat_admin_credentials
from dreamference.chat.chat_admin_credentials import ChatAdminCredentials
from dreamference.chat.onyx_runner import (
    DEFAULT_ONYX_EMAIL,
    LEGACY_ONYX_PASSWORD,
    ONYX_CHANGE_PASSWORD_PATH,
    OnyxRunner,
)

REPO = Path(__file__).resolve().parent.parent
API = "http://localhost:3000/api"


@pytest.fixture(autouse=True)
def credentials_file(tmp_path, monkeypatch):
    path = tmp_path / "config" / "dreamference" / "chat-admin.json"
    monkeypatch.setattr(chat_admin_credentials, "CHAT_ADMIN_PATH", str(path))
    return path


class FakeOnyx:
    """The three Onyx routes the account logic uses, with one account."""

    def __init__(self, password=None, change_status=None):
        self.password = password          # None: no account yet
        self.change_status = change_status
        self.calls = []

    def login(self, api, email, password):
        self.calls.append(("login", email, password))
        return f"session={password}" if self.password is not None and password == self.password else None

    def authenticate(self, api, email, password):
        self.calls.append(("authenticate", email, password))
        if self.password is None:
            self.password = password      # registration, then login
        return self.login(api, email, password)

    def request(self, url, payload, cookie, method="POST"):
        self.calls.append(("request", url, payload, cookie))
        if url.endswith(ONYX_CHANGE_PASSWORD_PATH):
            if self.change_status:
                return None, self.change_status
            if payload["old_password"] != self.password:
                return None, "HTTP 400: Invalid current password"
            self.password = payload["new_password"]
            return None, None
        raise AssertionError(f"unexpected request {url}")

    def patches(self):
        return (patch.object(OnyxRunner, "_login", self.login),
                patch.object(OnyxRunner, "_authenticate", self.authenticate),
                patch.object(OnyxRunner, "_request", self.request))


def session(onyx, **kwargs):
    login, authenticate, request = onyx.patches()
    with login, authenticate, request:
        return OnyxRunner()._admin_session(API, **kwargs)


# -- generation and storage --------------------------------------------------------------------------

def test_a_generated_password_is_long_random_and_passes_every_onyx_rule():
    passwords = {ChatAdminCredentials.generate_password() for _ in range(50)}
    assert len(passwords) == 50
    for password in passwords:
        assert 8 <= len(password) <= 64
        assert any(c.isupper() for c in password) and any(c.islower() for c in password)
        assert any(c.isdigit() for c in password)
        assert any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password)
        assert password != LEGACY_ONYX_PASSWORD


def test_credentials_are_stored_for_the_owner_only(credentials_file):
    path = ChatAdminCredentials.save("a@b.dev", "s3cret")
    assert path == credentials_file
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert ChatAdminCredentials.load() == ("a@b.dev", "s3cret")
    assert not list(path.parent.glob(".*.tmp"))


def test_a_missing_or_broken_file_reads_as_no_credentials(credentials_file):
    assert ChatAdminCredentials.load() is None
    credentials_file.parent.mkdir(parents=True)
    credentials_file.write_text("{not json")
    assert ChatAdminCredentials.load() is None
    credentials_file.write_text('{"email": "a@b.dev"}')
    assert ChatAdminCredentials.load() is None


# -- signing in ----------------------------------------------------------------------------------------

def test_a_fresh_deployment_registers_a_generated_password_and_stores_it():
    onyx = FakeOnyx(password=None)
    cookie = session(onyx)
    email, password = ChatAdminCredentials.load()
    assert email == DEFAULT_ONYX_EMAIL and password != LEGACY_ONYX_PASSWORD
    assert onyx.password == password and cookie == f"session={password}"


def test_an_install_on_the_published_default_is_moved_to_a_generated_password():
    onyx = FakeOnyx(password=LEGACY_ONYX_PASSWORD)
    cookie = session(onyx)
    email, password = ChatAdminCredentials.load()
    assert (email, onyx.password) == (DEFAULT_ONYX_EMAIL, password)
    assert password != LEGACY_ONYX_PASSWORD
    assert cookie == f"session={password}"               # signed in with the new password
    changes = [c for c in onyx.calls if c[0] == "request"]
    assert changes[0][2] == {"old_password": LEGACY_ONYX_PASSWORD, "new_password": password}


def test_the_move_happens_once_and_then_the_stored_password_is_used():
    onyx = FakeOnyx(password=LEGACY_ONYX_PASSWORD)
    session(onyx)
    stored = ChatAdminCredentials.load()
    onyx.calls.clear()
    assert session(onyx) == f"session={stored[1]}"
    assert [c[0] for c in onyx.calls] == ["login"]        # no legacy attempt, no change
    assert ChatAdminCredentials.load() == stored


def test_a_refused_change_keeps_the_old_password_and_stores_nothing():
    onyx = FakeOnyx(password=LEGACY_ONYX_PASSWORD, change_status="HTTP 500: boom")
    cookie = session(onyx)
    assert cookie == f"session={LEGACY_ONYX_PASSWORD}"     # this run still works
    assert onyx.password == LEGACY_ONYX_PASSWORD
    assert ChatAdminCredentials.load() is None


def test_a_password_the_user_chose_is_left_alone(capsys):
    # Not the default, no stored file: registration is refused because the account exists.
    onyx = FakeOnyx(password="mine")

    def refuse_registration(runner, api, email, password):
        onyx.calls.append(("authenticate", email, password))
        return None

    login, _, request = onyx.patches()
    with login, request, patch.object(OnyxRunner, "_authenticate", refuse_registration):
        assert OnyxRunner()._admin_session(API) is None
    assert onyx.password == "mine" and ChatAdminCredentials.load() is None
    assert not [c for c in onyx.calls if c[0] == "request"]


def test_a_stored_password_that_no_longer_works_changes_nothing(capsys):
    ChatAdminCredentials.save(DEFAULT_ONYX_EMAIL, "stale")
    onyx = FakeOnyx(password="changed-in-the-ui")
    assert session(onyx) is None
    assert [c[0] for c in onyx.calls] == ["login"]
    assert "chat configure --email" in capsys.readouterr().out
    assert ChatAdminCredentials.load() == (DEFAULT_ONYX_EMAIL, "stale")


def test_credentials_the_user_passes_are_used_and_stored():
    onyx = FakeOnyx(password=None)
    assert session(onyx, email="me@example.com", password="Own-pass1!") == "session=Own-pass1!"
    assert ChatAdminCredentials.load() == ("me@example.com", "Own-pass1!")


def test_half_a_credential_pair_is_refused():
    onyx = FakeOnyx(password=None)
    assert session(onyx, email="me@example.com") is None
    assert onyx.calls == [] and ChatAdminCredentials.load() is None


def test_ling_admin_chat_password_shows_the_stored_account(capsys):
    assert OnyxRunner().show_admin_credentials() == 1
    ChatAdminCredentials.save("a@b.dev", "pw")
    assert OnyxRunner().show_admin_credentials() == 0
    out = capsys.readouterr().out
    assert "a@b.dev" in out and "pw" in out


# -- the published default is read in one place only ---------------------------------------------

def test_only_the_migration_reads_the_legacy_password():
    users = []
    for path in (REPO / "dreamference").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Name) and node.id in ("LEGACY_ONYX_PASSWORD", "DEFAULT_ONYX_PASSWORD"):
                users.append((path.name, node.id))
    # Assigned once, then read twice: the legacy sign-in and the change request's old password.
    assert users == [("onyx_runner.py", "LEGACY_ONYX_PASSWORD")] * 3
