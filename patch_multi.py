import re
import os

with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

# 1. Update _raw to return the loaded JSON
# (already does that, but let's make sure we handle the accounts structure)

raw_old = '''    def _raw(cls, directory: Optional[str] = None) -> Dict[str, Any]:
        """
        Reads the credentials file as stored, without deciding whether it is usable.

        Args:
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            Dict[str, Any]: The stored fields, empty if there are none.
        """
        try:
            with open(os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)) as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return {}
        return stored if isinstance(stored, dict) else {}'''

raw_new = '''    def _raw(cls, directory: Optional[str] = None) -> Dict[str, Any]:
        """Reads credentials file. Migrates old single-account format if needed."""
        try:
            with open(os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)) as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return {"accounts": {}}
        if not isinstance(stored, dict):
            return {"accounts": {}}
        if "accounts" not in stored:
            # migrate legacy
            if "email" in stored and "access_token" in stored:
                return {"accounts": {stored["email"]: stored}}
            return {"accounts": {}}
        return stored'''

content = content.replace(raw_old, raw_new)

# 2. Update save_token to use the dictionary

save_old = '''    def save_token(
        cls, address: str, token: str, lifetime: int, directory: Optional[str] = None, refresh_token: Optional[str] = None
    ) -> bool:
        """
        Stores a GNOME Online Accounts access token for the container to use.

        Written by the host, because GOA lives on the session bus and the service does not. The
        expiry is recorded rather than the lifetime so that a stale file is recognisable as stale
        without knowing when it was written.

        Args:
            address (str): The Google address the token authenticates as.
            token (str): The access token.
            lifetime (int): Seconds until it expires, as GOA reported it.
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            bool: True if the file was written.
        """
        path = os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)
        try:
            with open(path, "w") as handle:
                json.dump({
                    "email": address,
                    "access_token": cls._seal(token, directory),
                    # A minute of slack, so a token about to expire is not handed to a connection
                    # that will take a moment to open.
                    "expires_at": time.time() + max(0, lifetime - 60),
                }, handle, indent=2)
            os.chmod(path, 0o600)
        except OSError:
            return False
        return True'''

save_new = '''    def save_token(
        cls, address: str, token: str, lifetime: int, directory: Optional[str] = None, refresh_token: Optional[str] = None
    ) -> bool:
        path = os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)
        stored = cls._raw(directory)
        accounts = stored.get("accounts", {})
        
        acc = accounts.get(address, {"email": address})
        acc["access_token"] = cls._seal(token, directory)
        acc["expires_at"] = time.time() + max(0, lifetime - 60)
        if refresh_token:
            acc["refresh_token"] = cls._seal(refresh_token, directory)
        accounts[address] = acc
        
        try:
            with open(path, "w") as handle:
                json.dump({"accounts": accounts}, handle, indent=2)
            os.chmod(path, 0o600)
        except OSError:
            return False
        return True'''
        
content = content.replace(save_old, save_new)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)

