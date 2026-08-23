with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

delete_token_meth = '''
    @classmethod
    def delete_token(cls, address: str, directory: Optional[str] = None) -> bool:
        import os, json
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
        
    @classmethod
    def save_token'''

content = content.replace('    @classmethod\n    def save_token', delete_token_meth, 1)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)
