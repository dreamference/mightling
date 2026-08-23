import re

with open('dreamference/chat/onyx_runner.py', 'r') as f:
    content = f.read()

pat = r'(    @classmethod\n    def install_gnome_token_timer\(cls\) -> bool:.*?)(?=    def _upsert_puffin_assistant)'
content = re.sub(pat, '', content, flags=re.DOTALL)

with open('dreamference/chat/onyx_runner.py', 'w') as f:
    f.write(content)

