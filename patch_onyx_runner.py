import re

with open('dreamference/chat/onyx_runner.py', 'r') as f:
    content = f.read()

# Replace connect_gmail
connect_gmail_regex = re.compile(r'(def connect_gmail.*?)(?:    @classmethod)', re.DOTALL)
def connect_gmail_repl(m):
    return """def connect_gmail(
        self,
        web_url: str = DEFAULT_ONYX_WEB_URL,
        email: str = DEFAULT_ONYX_EMAIL,
        password: str = DEFAULT_ONYX_PASSWORD,
    ) -> bool:
        \"\"\"Registers the Gmail search tool in Onyx.\"\"\"
        api = f"{web_url.rstrip('/')}/api"
        cookie = self._authenticate(api, email, password)
        if not cookie:
            return False
        if not self.enable_gmail_search(api, cookie):
            return False
        self._upsert_puffin_assistant(api, cookie)
        print("✅ Gmail search is available to the assistant.")
        return True

    @classmethod"""
content = connect_gmail_regex.sub(connect_gmail_repl, content)

# Remove _record_gnome_accounts, refresh_gnome_token, install_gnome_token_timer
# They are class methods.
remove_methods = [
    r'(    @classmethod\n    def _record_gnome_accounts\(cls\).*?)(?=    @classmethod\n    def refresh_gnome_token)',
    r'(    @classmethod\n    def refresh_gnome_token\(cls, announce: bool = False\) -> bool:.*?)(?=    @classmethod\n    def install_gnome_token_timer)',
    r'(    @classmethod\n    def install_gnome_token_timer\(cls\) -> bool:.*?)(?=    def _upsert_puffin_assistant)'
]

for pat in remove_methods:
    content = re.sub(pat, '', content, flags=re.DOTALL)

with open('dreamference/chat/onyx_runner.py', 'w') as f:
    f.write(content)

