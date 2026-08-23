import re

with open('dreamference/chat/onyx_ui_scripts.py', 'r') as f:
    content = f.read()

# Fix 1: the extra } in fetch
content = content.replace("  }}).then(function(){ check(); });", "  }).then(function(){ check(); });")

# Fix 2: the onclick quotes
# Currently it is: onclick=\"window.__puffinDisconnect(\\\'\" + emails[i] + \"\\\')\"
# But it is inside a string delimited by double quotes!
# So we need it to be: onclick=\\\'window.__puffinDisconnect(\" + emails[i] + \")\\\'
content = content.replace(
    'onclick=\\\"window.__puffinDisconnect(\\\'\" + emails[i] + \"\\\')\\\"',
    'onclick=\\\'window.__puffinDisconnect(\" + emails[i] + \")\\\''
)

with open('dreamference/chat/onyx_ui_scripts.py', 'w') as f:
    f.write(content)

