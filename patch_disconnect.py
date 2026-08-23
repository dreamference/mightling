import re

with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

# 1. Add delete_token method
delete_token_meth = '''
    @classmethod
    def delete_token(cls, address: str, directory: Optional[str] = None) -> bool:
        path = os.path.join(directory or CONFIG_DIR, CREDENTIALS_NAME)
        stored = cls._raw(directory)
        accounts = stored.get("accounts", {})
        if address in accounts:
            del accounts[address]
            try:
                with open(path, "w") as handle:
                    json.dump({"accounts": accounts}, handle, indent=2)
                return True
            except OSError:
                return False
        return False
'''

# Insert right after save_token method
pat = r'(    def save_token\([^\)]*\) -> bool:.*?        return True\n)'
content = re.sub(pat, r'\1' + delete_token_meth, content, flags=re.DOTALL)

# 2. Add /disconnect to do_POST
disconnect_handler = '''
                if parsed.path == "/disconnect":
                    content_length = int(self.headers.get('Content-Length', 0))
                    post_data = self.rfile.read(content_length).decode('utf-8')
                    try:
                        data = json.loads(post_data)
                        email = data.get("email")
                        if email:
                            success = GmailSearchService.delete_token(email)
                            self._reply(200, {"status": "ok", "deleted": success})
                            return
                    except Exception as e:
                        self._reply(400, {"error": str(e)})
                        return
                    self._reply(400, {"error": "Invalid request"})
                    return
'''

content = re.sub(r'(                if parsed.path == "/api/google/oauth/complete":)', disconnect_handler + r'\n\1', content)

# Also handle preflight CORS for /disconnect if using fetch with JSON
# Wait, fetch with JSON triggers preflight OPTIONS.
# But previously do_OPTIONS wasn't added?
# Let's check if do_OPTIONS exists.
