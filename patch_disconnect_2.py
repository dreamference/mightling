with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

disconnect_handler = '''
                if parsed.path == "/disconnect":
                    content_length = int(self.headers.get('Content-Length', 0))
                    post_data = self.rfile.read(content_length).decode('utf-8')
                    try:
                        import json
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
                if parsed.path == "/api/google/oauth/complete":'''

content = content.replace('                if parsed.path == "/api/google/oauth/complete":', disconnect_handler, 1)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)
