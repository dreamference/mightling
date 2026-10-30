"""The Google Workspace reader asks Google nothing for an app no connected account has granted."""

import pytest

from dreamference.chat.gmail_search_service import GMAIL_SCOPE
from dreamference.chat.google_workspace_reader import DRIVE_SCOPE, GoogleWorkspaceReader


def test_calendar_events_without_a_calendar_grant_are_refused_before_any_request(monkeypatch):
    monkeypatch.setattr(
        GoogleWorkspaceReader,
        "_get",
        classmethod(lambda cls, *a, **k: pytest.fail("no account may be asked")),
    )
    accounts = [
        {"email": "mail@x.com", "access_token": "t1", "scopes": [GMAIL_SCOPE]},
        {"email": "drive@x.com", "access_token": "t2", "scopes": [DRIVE_SCOPE]},
        {"email": "old@x.com", "access_token": "t3", "scopes": None},
    ]
    for calendar in ("", "drive@x.com|primary"):
        answer = GoogleWorkspaceReader.calendar_events(
            accounts, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", calendar=calendar
        )
        assert answer == {
            "error": "No connected account has Google Calendar access. Connect it with /apps."
        }
