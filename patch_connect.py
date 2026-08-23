import re

with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

# We need to replace this exact block:
#                if parsed.path == CONNECT_PATH:
#                    if cls.status()["connected"]:
#                        self._page("Gmail is already connected.")
#                        return
#                    self._setup_page()
# With:
#                if parsed.path == CONNECT_PATH:
#                    self._setup_page()

old_block = '''                if parsed.path == CONNECT_PATH:
                    if cls.status()["connected"]:
                        self._page("Gmail is already connected.")
                        return
                    self._setup_page()'''

new_block = '''                if parsed.path == CONNECT_PATH:
                    self._setup_page()'''

content = content.replace(old_block, new_block)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)

