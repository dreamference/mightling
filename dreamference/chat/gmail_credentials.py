"""
Mailbox Credentials for Gmail Search.

This module provides the GmailCredentials class, the host-side view of the credentials the Gmail
search service runs on. It exists so that `dream onyx gmail` can connect a mailbox from the
terminal, the same connection the Connect to Google button makes from the UI.

**It is a thin front on `GmailSearchService`, deliberately.** The sealing format, the file layout
and the IMAP login check all live in the service module, because that module is *copied* into a
container with nothing installed into it and has to be self-contained. Reimplementing any of it
here would give the two sides two chances to disagree about the same file.

The transport is IMAP with a Google app password, which is why this module no longer opens a
browser, listens on a loopback redirect, or knows anything about OAuth. What it costs is the
read-only guarantee the old `gmail.readonly` scope gave: an app password is the whole account, and
IMAP has no scopes. The service narrows that as far as the protocol allows -- it issues only
`LOGIN`, `LIST`, `SEARCH` and `FETCH`, selects the mailbox read-only, and peeks rather than reads
so that nothing it does marks a message as seen.
"""

import os
import re
from typing import Dict, Final, Optional

from dreamference.chat.gmail_search_service import IMAP_HOST, GmailSearchService

# Where the credentials live on the host. The directory is bind-mounted into the search service's
# container, so this path is also `/config` as far as that container is concerned.
CREDENTIALS_DIR: Final[str] = os.path.expanduser("~/.config/dreamference/gmail")
CREDENTIALS_FILE: Final[str] = os.path.join(CREDENTIALS_DIR, "credentials.json")

# Where the user creates the app password, printed when a login is refused -- which is almost
# always either a mistyped code or 2-Step Verification not being on.
APP_PASSWORDS_URL: Final[str] = "https://myaccount.google.com/apppasswords"


class GmailCredentials:
    """
    Stores and checks the Gmail app password the search service authenticates with.
    """

    @classmethod
    def _ensure_directory(cls) -> bool:
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
        Reads the stored credentials.

        Returns:
            Optional[Dict[str, str]]: The address and app password, or None if Gmail has not been
                connected yet.
        """
        return GmailSearchService.credentials(CREDENTIALS_DIR)

    @classmethod
    def save(cls, address: str, app_password: str) -> bool:
        """
        Stores the credentials, sealed and readable only by this user.

        Args:
            address (str): The Gmail address.
            app_password (str): The 16-character app password.

        Returns:
            bool: True if the file was written.
        """
        if not cls._ensure_directory():
            return False
        if not GmailSearchService.save_credentials(address, app_password, CREDENTIALS_DIR):
            print(f"❌ Could not store the Gmail credentials in {CREDENTIALS_FILE}.")
            return False
        return True

    @classmethod
    def connect(cls, address: str, app_password: str) -> bool:
        """
        Checks the credentials against Gmail and stores them if they work.

        Args:
            address (str): The Gmail address.
            app_password (str): The app password, with or without the spaces Google displays.

        Returns:
            bool: True if the mailbox is connected afterwards.
        """
        # Google shows the code as `xxxx xxxx xxxx xxxx`, and it gets pasted that way far more
        # often than not.
        cleaned = re.sub(r"\s+", "", app_password)
        print(f"🔐 Checking {address} against {IMAP_HOST}…")
        failure = GmailSearchService.verify(address, cleaned)
        if failure:
            print(f"❌ {failure}")
            print(f"💡 Create an app password at {APP_PASSWORDS_URL} — 2-Step Verification has to "
                  "be on first.")
            return False
        if not cls.save(address, cleaned):
            return False
        print("✅ Gmail connected.")
        return True

    @classmethod
    def forget(cls) -> bool:
        """
        Removes the stored credentials.

        Returns:
            bool: True if nothing is stored afterwards.
        """
        return GmailSearchService.forget(CREDENTIALS_DIR)

