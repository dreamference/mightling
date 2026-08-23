import re

with open('dreamference/chat/onyx_ui_scripts.py', 'r') as f:
    content = f.read()

# I want: onclick='window.__puffinDisconnect(\\"" + emails[i] + "\\")'
# So in Python:
# 'onclick=\\\'window.__puffinDisconnect(\\"" + emails[i] + "\\")\\\''
content = content.replace(
    'onclick=\\\'window.__puffinDisconnect(\" + emails[i] + \")\\\'',
    'onclick=\\\'window.__puffinDisconnect(\\"" + emails[i] + "\\")\\\''
)

with open('dreamference/chat/onyx_ui_scripts.py', 'w') as f:
    f.write(content)

