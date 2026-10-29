"""The session of one web tool call."""

from typing import Final

import requests
import requests.utils

from dreamference.mcp_server.deadline_adapter import DeadlineAdapter

# The header a login travels in.
AUTHORIZATION: Final[str] = "Authorization"


class WebSession(requests.Session):
    """One call's session, which obeys a RequestDeadline and never sends `~/.netrc` logins.

    requests adds the .netrc login for the request's host and again for every redirect target,
    so a page could send the agent to a host the user keeps a password for (over plain http
    too) and have it read what that login sees. `ling-fetch` never reads .netrc. `trust_env`
    stays on: it also brings the proxy and CA bundle settings, which both copies follow.
    """

    def __init__(self) -> None:
        """A session whose http and https requests go through a DeadlineAdapter."""
        super().__init__()
        adapter = DeadlineAdapter()
        self.mount("http://", adapter)
        self.mount("https://", adapter)

    def prepare_request(self, request: requests.Request) -> requests.PreparedRequest:
        """Prepares `request` as requests does, with an auth set so that the .netrc lookup is skipped.

        A login written in the URL itself is the caller's and is still sent; otherwise the auth is
        one that changes nothing.

        Args:
            request: The request to prepare.

        Returns:
            requests.PreparedRequest: The prepared request.
        """
        if request.auth is None:
            login = requests.utils.get_auth_from_url(str(request.url or ""))
            request.auth = login if any(login) else (lambda prepared: prepared)
        return super().prepare_request(request)

    def rebuild_auth(
        self, prepared_request: requests.PreparedRequest, response: requests.Response
    ) -> None:
        """Strips the login on a change of host as requests does, without its .netrc lookup."""
        if AUTHORIZATION in prepared_request.headers and self.should_strip_auth(
            response.request.url or "", prepared_request.url or ""
        ):
            del prepared_request.headers[AUTHORIZATION]
