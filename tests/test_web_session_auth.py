"""A web tool's session sends the login its caller set, and only that one."""

import base64
import os
from pathlib import Path

import requests

from dreamference.mcp_server.web_session import WebSession


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def test_a_login_the_caller_sets_is_sent_and_not_replaced_by_the_url_or_netrc():
    netrc = Path(os.environ["HOME"]) / ".netrc"
    netrc.write_text("machine example.com login netrc-user password netrc-secret\n")
    netrc.chmod(0o600)
    session = WebSession()

    explicit = session.prepare_request(
        requests.Request("GET", "http://url-user:url-pass@example.com/", auth=("caller", "pw"))
    )
    assert explicit.headers["Authorization"] == _basic("caller", "pw")

    # Without one, only the URL's own login goes out, and with none there is no header at all.
    from_url = session.prepare_request(
        requests.Request("GET", "http://url-user:url-pass@example.com/")
    )
    assert from_url.headers["Authorization"] == _basic("url-user", "url-pass")
    bare = session.prepare_request(requests.Request("GET", "http://example.com/"))
    assert "Authorization" not in bare.headers
