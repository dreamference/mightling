import re

with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

credentials_old = '''    def credentials(cls, directory: Optional[str] = None) -> Optional[Dict[str, str]]:
        """
        Reads the token the mailbox can actually be opened with.

        An expired token is not a failure to report, it is a stale file -- the timer that should
        have replaced it did not run. Reporting "not connected" is the honest state and brings the
        Connect button back.

        Args:
            directory (Optional[str]): Credentials directory; the mounted one by default.

        Returns:
            Optional[Dict[str, str]]: `email` and `access_token`, or None.
        """
        stored = cls._raw(directory)
        address = stored.get("email")
        sealed = stored.get("access_token")
        if not address or not sealed:
            return None
        if float(stored.get("expires_at", 0)) - time.time() < 60:
            sealed_refresh = stored.get("refresh_token")
            if sealed_refresh:
                refresh = cls._unseal(sealed_refresh, directory)
                if refresh:
                    import json
                    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=urllib.parse.urlencode({
                        "client_id": GOOGLE_OAUTH_CLIENT_ID,
                        "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                        "refresh_token": refresh,
                        "grant_type": "refresh_token"
                    }).encode("utf-8"), headers={"Content-Type": "application/x-www-form-urlencoded"})
                    try:
                        with urllib.request.urlopen(req, timeout=10) as res:
                            data = json.load(res)
                            if "access_token" in data:
                                cls.save_token(address, data["access_token"], data.get("expires_in", 3599), directory)
                                return {"email": address, "access_token": data["access_token"]}
                    except Exception as e:
                        pass
            return None
        token = cls._unseal(sealed, directory)
        return {"email": address, "access_token": token} if token else None'''

credentials_new = '''    def credentials(cls, directory: Optional[str] = None) -> List[Dict[str, str]]:
        """Returns all valid valid accounts."""
        import json, time, urllib.request, urllib.parse
        stored = cls._raw(directory)
        valid = []
        for address, acc in stored.get("accounts", {}).items():
            sealed = acc.get("access_token")
            if not sealed: continue
            
            if float(acc.get("expires_at", 0)) - time.time() < 60:
                sealed_refresh = acc.get("refresh_token")
                if not sealed_refresh: continue
                refresh = cls._unseal(sealed_refresh, directory)
                if not refresh: continue
                
                req = urllib.request.Request("https://oauth2.googleapis.com/token", data=urllib.parse.urlencode({
                    "client_id": GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                    "refresh_token": refresh,
                    "grant_type": "refresh_token"
                }).encode("utf-8"), headers={"Content-Type": "application/x-www-form-urlencoded"})
                try:
                    with urllib.request.urlopen(req, timeout=10) as res:
                        data = json.load(res)
                        if "access_token" in data:
                            cls.save_token(address, data["access_token"], data.get("expires_in", 3599), directory, refresh_token=refresh)
                            valid.append({"email": address, "access_token": data["access_token"]})
                except Exception:
                    pass
            else:
                token = cls._unseal(sealed, directory)
                if token:
                    valid.append({"email": address, "access_token": token})
        return valid'''
content = content.replace(credentials_old, credentials_new)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)

