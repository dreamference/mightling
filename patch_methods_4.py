with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    lines = f.readlines()

message_idx = -1
for i, line in enumerate(lines):
    if line.startswith('    def message(cls'):
        message_idx = i

if message_idx != -1:
    # Find the next @classmethod or class end
    next_idx = len(lines)
    for i in range(message_idx + 1, len(lines)):
        if lines[i].startswith('    @classmethod'):
            next_idx = i
            break
            
    del lines[message_idx:next_idx]
    
    msg_new = '''    def message(cls, message_id: str) -> Dict[str, Any]:
        """Reads one message body."""
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
        return {"error": "Message not found"}
'''
    lines.insert(message_idx, msg_new)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.writelines(lines)

