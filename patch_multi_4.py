import re

with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

# Fix open_mailboxes
open_old = '''    def _open_mailboxes(cls) -> Tuple[List[imaplib.IMAP4_SSL], Optional[str]]:
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
open_new = '''    def _open_mailboxes(cls) -> Tuple[Dict[str, imaplib.IMAP4_SSL], Optional[str]]:
        stored = cls.credentials()
        if not stored:
            return {}, NOT_CONNECTED_MESSAGE
        connections = {}
        for cred in stored:
            try:
                conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT_SECONDS)
                conn.authenticate("XOAUTH2", cls._xoauth2(cred["email"], cred["access_token"]))
                conn.select(f'"{cls._all_mail_folder(conn)}"', readonly=True)
                connections[cred["email"]] = conn
            except Exception:
                continue
        if not connections:
            return {}, "Could not connect to any Gmail accounts."
        return connections, None'''
content = content.replace(open_old, open_new)

# Fix search
search_old = '''    def search(cls, query: str, limit: int) -> Dict[str, Any]:
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

search_new = '''    def search(cls, query: str, limit: int) -> Dict[str, Any]:
        connections, error = cls._open_mailboxes()
        if not connections:
            return {"error": error}
        
        all_messages = []
        for email, connection in connections.items():
            try:
                uids = cls._search_uids(connection, query)
                if uids:
                    messages = cls._fetch_headers(connection, uids[-limit:][::-1])
                    for m in messages:
                        m["id"] = f"{email}|{m['id']}"
                    all_messages.extend(messages)
            except Exception:
                pass
            finally:
                try:
                    connection.logout()
                except Exception:
                    pass
        
        return {"messages": all_messages[:limit]}'''
content = content.replace(search_old, search_new)

# Fix message
msg_old = '''    def message(cls, uid: str) -> Dict[str, Any]:
        """
        Reads one message body.

        Args:
            uid (str): The IMAP sequence ID from `search`.

        Returns:
            Dict[str, Any]: The message content, or an error.
        """
        connection, error = cls._open_mailbox()
        if not connection:
            return {"error": error}
        try:
            return cls._fetch_body(connection, uid)
        except imaplib.IMAP4.error as exc:
            return {"error": f"Read failed: {exc}"}
        finally:
            try:
                connection.logout()
            except imaplib.IMAP4.error:
                pass'''
msg_new = '''    def message(cls, uid: str) -> Dict[str, Any]:
        if "|" in uid:
            email, real_uid = uid.split("|", 1)
        else:
            email, real_uid = None, uid
            
        connections, error = cls._open_mailboxes()
        if not connections:
            return {"error": error}
            
        # If email specified, only use that connection
        if email and email in connections:
            conn = connections[email]
            try:
                return cls._fetch_body(conn, real_uid)
            except Exception as e:
                return {"error": str(e)}
            finally:
                for c in connections.values():
                    try:
                        c.logout()
                    except:
                        pass
                        
        # Fallback if no email prefix (legacy)
        for conn in connections.values():
            try:
                res = cls._fetch_body(conn, real_uid)
                if res and "error" not in res:
                    return res
            except:
                pass
            finally:
                try:
                    conn.logout()
                except:
                    pass
        return {"error": "Message not found in any mailbox"}'''
content = content.replace(msg_old, msg_new)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)

