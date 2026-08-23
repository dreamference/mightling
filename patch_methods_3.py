with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    lines = f.readlines()

search_idx = -1
message_idx = -1
for i, line in enumerate(lines):
    if line.startswith('    def search(cls'):
        search_idx = i
    if line.startswith('    def message(cls'):
        message_idx = i

if search_idx != -1 and message_idx != -1:
    del lines[search_idx:message_idx]
    
    search_new = '''    def search(cls, query: str, limit: int) -> Dict[str, Any]:
        """
        Finds messages matching a Gmail search query.
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
        
        return {"messages": all_messages[:limit]}
    
    @classmethod
'''
    lines.insert(search_idx, search_new)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.writelines(lines)

