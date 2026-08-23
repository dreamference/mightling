import re

with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

# Fix status
status_old = '''    def status(cls) -> Dict[str, Any]:
        """
        Reports whether the mailbox is connected and under what email.
        """
        creds = cls.credentials()
        connected = creds is not None
        email = creds["email"] if creds else None
        return {"configured": connected, "connected": connected, "email": email}'''
status_new = '''    def status(cls) -> Dict[str, Any]:
        creds = cls.credentials()
        connected = len(creds) > 0
        email = ", ".join(c["email"] for c in creds) if connected else None
        return {"configured": connected, "connected": connected, "email": email}'''
content = content.replace(status_old, status_new)

# Fix open_mailbox
open_old = '''    def _open_mailbox(cls) -> Tuple[Optional[imaplib.IMAP4_SSL], Optional[str]]:
        """
        Connects, authenticates and selects All Mail read-only.

        Returns:
            Tuple[Optional[imaplib.IMAP4_SSL], Optional[str]]: The connection, or an error message.
        """
        stored = cls.credentials()
        if not stored:
            return None, NOT_CONNECTED_MESSAGE
        try:
            connection = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT_SECONDS)
            connection.authenticate("XOAUTH2", cls._xoauth2(stored["email"], stored["access_token"]))
            # Read-only, so that nothing this service does can change the mailbox even by accident.
            connection.select(f'"{cls._all_mail_folder(connection)}"', readonly=True)
        except imaplib.IMAP4.error as exc:
            return None, f"Gmail refused the connection: {exc}"
        except OSError as exc:
            return None, f"Could not reach {IMAP_HOST}: {exc}"
        return connection, None'''

# Rename it to _open_mailboxes which returns a list of connections
open_new = '''    def _open_mailboxes(cls) -> Tuple[List[imaplib.IMAP4_SSL], Optional[str]]:
        stored = cls.credentials()
        if not stored:
            return [], NOT_CONNECTED_MESSAGE
        connections = []
        for cred in stored:
            try:
                conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT_SECONDS)
                conn.authenticate("XOAUTH2", cls._xoauth2(cred["email"], cred["access_token"]))
                conn.select(f'"{cls._all_mail_folder(conn)}"', readonly=True)
                connections.append(conn)
            except Exception as e:
                # Silently skip failing accounts in multi-account context, 
                # or log them. For now, continue to next.
                continue
        if not connections:
            return [], "Could not connect to any Gmail accounts."
        return connections, None'''
content = content.replace(open_old, open_new)

# Fix search
search_old = '''    def search(cls, query: str, limit: int) -> Dict[str, Any]:
        """
        Finds messages matching a Gmail search query.

        Args:
            query (str): Gmail search syntax, e.g. `from:alice newer_than:7d`.
            limit (int): Maximum number of messages to return.

        Returns:
            Dict[str, Any]: `{"messages": [...]}`, each entry carrying the id, sender, subject and
                date -- enough to choose one to open, and no more.
        """
        connection, error = cls._open_mailbox()
        if not connection:
            return {"error": error}
        try:
            uids = cls._search_uids(connection, query)
            if not uids:
                return {"messages": []}
            # Newest first: IMAP returns UIDs ascending, and recency is what a mailbox question
            # almost always means.
            messages = cls._fetch_headers(connection, uids[-limit:][::-1])
            return {"messages": messages}
        except imaplib.IMAP4.error as exc:
            return {"error": f"Search failed: {exc}"}
        finally:
            try:
                connection.logout()
            except imaplib.IMAP4.error:
                pass'''
search_new = '''    def search(cls, query: str, limit: int) -> Dict[str, Any]:
        connections, error = cls._open_mailboxes()
        if not connections:
            return {"error": error}
        
        all_messages = []
        for connection in connections:
            try:
                uids = cls._search_uids(connection, query)
                if uids:
                    # We append a mailbox index to the id so we can route `message()` calls later!
                    messages = cls._fetch_headers(connection, uids[-limit:][::-1])
                    for m in messages:
                        # Append the account email to the UID to make it globally unique
                        # Wait! message() takes a message ID. How will message() know which mailbox?
                        # Actually, IMAP Message-ID is globally unique, but _fetch_headers returns IMAP UIDs!
                        # IMAP UIDs are folder-specific. We MUST prefix it with the email so message() can find it.
                        m["id"] = f"{connection.user}@|@{m['id']}" if hasattr(connection, 'user') else m['id']
                    all_messages.extend(messages)
            except Exception:
                pass
            finally:
                try:
                    connection.logout()
                except Exception:
                    pass
        
        # Sort combined results by date descending if we wanted, but _fetch_headers returns parsed dates.
        # We'll just return up to limit combined.
        # Wait, connection.user doesn't exist. How to get email? 
        pass'''

# We need to refine search_new
content = content.replace(search_old, search_new)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)

