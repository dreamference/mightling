import re

with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

status_old = '''    def status(cls) -> Dict[str, Any]:
        """
        Reports whether the mailbox is connected.

        Both flags mean the same thing -- a usable token exists -- and the pair is kept because the
        injected UI script reads it as one object.

        Returns:
            Dict[str, Any]: `configured` and `connected`.
        """
        connected = cls.credentials() is not None
        return {"configured": connected, "connected": connected}'''
status_new = '''    def status(cls) -> Dict[str, Any]:
        """
        Reports whether the mailbox is connected and under what email.
        """
        creds = cls.credentials()
        connected = creds is not None
        email = creds["email"] if creds else None
        return {"configured": connected, "connected": connected, "email": email}'''

content = content.replace(status_old, status_new)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)

