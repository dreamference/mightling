"""
Mailbox Credentials for Gmail Search.

This module names the host folder that holds the token the Gmail search service runs on. The
sealing format and the file layout live in `GmailSearchService` alone -- that module is *copied*
into a container with nothing installed into it and has to be self-contained, and reimplementing
any of it here would give the two sides two chances to disagree about the same file.

There is no `connect()` here, and that is the point of the current design: the credential is a
Google account the user has added to their desktop, not something Dreamference collects. The host
reads the tokens created by the Mightling OAuth flow, which the service itself stores in the
mounted folder when a connection completes in the browser.
"""

import os
from typing import Final

# Where the credentials live on the host. The directory is bind-mounted into the search service's
# container, so this path is also `/config` as far as that container is concerned.
CREDENTIALS_DIR: Final[str] = os.path.expanduser("~/.config/dreamference/gmail")
