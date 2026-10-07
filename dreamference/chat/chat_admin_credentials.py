"""
The web chat's administrator account: a random password per install, kept in one private file.

Until 2026-10-07 every install created the same account, `admin@dreamference.dev` with the
password `dreamference`, both published in this repository. On a node that publishes the web UI
on the local network (`ling-admin node enable`) anyone on that network could sign in as the
administrator, read every chat and point the model provider at their own server; on any machine,
other local users and commands run with network access could do the same on loopback.

Now `configure` generates a password the first time it registers the account and keeps it in
`~/.config/dreamference/chat-admin.json`, readable by the owner only. An install made with the old
default is moved over once: `configure` signs in with the default, changes the password through
Onyx's own `POST /api/password/change-password`, and stores the new one. Everything that signs in
-- `configure`, `chat gmail`, the desktop app's automatic sign-in -- reads this file. On a client
machine the file does not exist (the account belongs to the node), so the desktop app shows the
ordinary login page; `ling-admin chat password` on the node prints what to type.
"""

import json
import os
import secrets
from pathlib import Path
from typing import Final, Optional, Tuple

# Where the account is kept. Module-level, so tests (and conftest's HOME isolation) can repoint it.
CHAT_ADMIN_PATH: Final[str] = os.path.expanduser("~/.config/dreamference/chat-admin.json")

# Onyx validates passwords with configurable rules (PASSWORD_MIN_LENGTH 8, MAX_LENGTH 64, and
# optional upper/lower/digit/special requirements, all off by default). A generated password
# satisfies every rule at once, so a deployment that switches them on still accepts it.
GENERATED_RANDOM_BYTES: Final[int] = 30
GENERATED_SUFFIX: Final[str] = "Ml7!"


class ChatAdminCredentials:
    """Generates, stores and reads the web chat's administrator credentials."""

    @classmethod
    def path(cls) -> Path:
        """
        The file the credentials live in.

        Returns:
            Path: `~/.config/dreamference/chat-admin.json`, or wherever CHAT_ADMIN_PATH points.
        """
        return Path(CHAT_ADMIN_PATH)

    @classmethod
    def generate_password(cls) -> str:
        """
        A new random password that passes every password rule Onyx can be configured with.

        Returns:
            str: 44 characters: 40 URL-safe random ones, then one upper-case letter, one
                lower-case letter, one digit and one special character.
        """
        return secrets.token_urlsafe(GENERATED_RANDOM_BYTES) + GENERATED_SUFFIX

    @classmethod
    def load(cls) -> Optional[Tuple[str, str]]:
        """
        The stored e-mail and password.

        Returns:
            Optional[Tuple[str, str]]: (email, password), or None when the file is missing or
                unreadable.
        """
        try:
            data = json.loads(cls.path().read_text())
        except (OSError, ValueError):
            return None
        email, password = data.get("email"), data.get("password")
        if not isinstance(email, str) or not isinstance(password, str) or not email or not password:
            return None
        return email, password

    @classmethod
    def save(cls, email: str, password: str) -> Path:
        """
        Stores the credentials, readable and writable by the owner only (0600, directory 0700).

        The file is written beside its final name and renamed into place, so a reader never sees
        half a file, and it is created with its final mode, so it is never briefly readable by
        others.

        Args:
            email (str): Account e-mail.
            password (str): Account password.

        Returns:
            Path: The file written.
        """
        path = cls.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump({"email": email, "password": password}, handle)
                handle.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return path

    @classmethod
    def forget(cls) -> None:
        """Removes the stored credentials, if any."""
        cls.path().unlink(missing_ok=True)
