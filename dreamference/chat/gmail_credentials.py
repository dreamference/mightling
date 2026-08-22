"""
Mailbox Credentials for Gmail Search.

This module provides the GmailCredentials class, the host-side view of the token the Gmail search
service runs on. It is a thin front on `GmailSearchService` so that the sealing format and the file
layout live in one place -- that module is *copied* into a container with nothing installed into
it and has to be self-contained, and reimplementing any of it here would give the two sides two
chances to disagree about the same file.

There is no `connect()` here, and that is the point of the current design: the credential is a
Google account the user has added to their desktop, not something Dreamference collects. The host
reads a short-lived access token out of GNOME Online Accounts (`goa_accounts.py`) and writes it
where the container can find it; `OnyxRunner.refresh_gnome_token` is what does that, on a timer.
"""

import os
from typing import Dict, Final, Optional

from dreamference.chat.gmail_search_service import GmailSearchService

# Where the credentials live on the host. The directory is bind-mounted into the search service's
# container, so this path is also `/config` as far as that container is concerned.
CREDENTIALS_DIR: Final[str] = os.path.expanduser("~/.config/dreamference/gmail")
CREDENTIALS_FILE: Final[str] = os.path.join(CREDENTIALS_DIR, "credentials.json")


class GmailCredentials:
    """
    Reads and clears the Google access token the search service authenticates with.
    """

    @classmethod
    def ensure_directory(cls) -> bool:
        """
        Creates the credentials directory, readable only by this user.

        Returns:
            bool: True if the directory exists afterwards.
        """
        try:
            os.makedirs(CREDENTIALS_DIR, mode=0o700, exist_ok=True)
        except OSError as exc:
            print(f"❌ Could not create {CREDENTIALS_DIR}: {exc}")
            return False
        return True

    @classmethod
    def load(cls) -> Optional[Dict[str, str]]:
        """
        Reads the stored token.

        Returns:
            Optional[Dict[str, str]]: The address and access token, or None if Gmail is not
                connected or the stored token has expired.
        """
        return GmailSearchService.credentials(CREDENTIALS_DIR)

    @classmethod
    def forget(cls) -> bool:
        """
        Removes the stored token.

        Returns:
            bool: True if nothing is stored afterwards.
        """
        return GmailSearchService.forget(CREDENTIALS_DIR)
