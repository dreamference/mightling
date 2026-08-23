import re

with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

# Replace search
search_new = '''    def search(cls, query: str, limit: int) -> Dict[str, Any]:
        """
        Finds messages matching a Gmail search query.

        Args:
            query (str): Gmail search syntax, e.g. `from:alice newer_than:7d`.
            limit (int): Maximum number of messages to return.

        Returns:
            Dict[str, Any]: `{"messages": [...]}`, each entry carrying the id, sender, subject and
                date -- enough to choose one to open, and no more.
        """
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

content = re.sub(r'    def search\(cls, query: str, limit: int\) -> Dict\[str, Any\]:.*?(?=    @classmethod\n    def open)', search_new + '\n\n', content, flags=re.DOTALL)


# Replace open (message)
# Wait, the method that opens the message is called `open` or `message`?
# In the previous trace: `627:        connection, error = cls._open_mailbox()` in `def open(cls, message_id: str)`
msg_new = '''    def open(cls, message_id: str) -> Dict[str, Any]:
        """
        Reads one message body.

        Args:
            message_id (str): The X-GM-MSGID to fetch.

        Returns:
            Dict[str, Any]: The headers and the plain-text body.
        """
        if "|" in message_id:
            email, real_id = message_id.split("|", 1)
        else:
            email, real_id = None, message_id
            
        connections, error = cls._open_mailboxes()
        if not connections:
            return {"error": error}
            
        if email and email in connections:
            conn = connections[email]
            try:
                status, data = conn.uid("SEARCH", None, "X-GM-MSGID", real_id)
                if status == "OK" and data[0]:
                    uid = data[0].split()[0].decode("ascii")
                    return cls._fetch_body(conn, uid)
            except Exception as e:
                return {"error": str(e)}
            finally:
                for c in connections.values():
                    try:
                        c.logout()
                    except:
                        pass
                return {"error": "Message not found"}

        for conn in connections.values():
            try:
                status, data = conn.uid("SEARCH", None, "X-GM-MSGID", real_id)
                if status == "OK" and data[0]:
                    uid = data[0].split()[0].decode("ascii")
                    res = cls._fetch_body(conn, uid)
                    if res and "error" not in res:
                        return res
            except Exception:
                pass
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass
        return {"error": "Message not found"}'''

content = re.sub(r'    def open\(cls, message_id: str\) -> Dict\[str, Any\]:.*?(?=    @classmethod\n    def _search_uids)', msg_new + '\n\n', content, flags=re.DOTALL)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)

