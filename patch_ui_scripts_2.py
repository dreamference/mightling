import re

with open('dreamference/chat/onyx_ui_scripts.py', 'r') as f:
    content = f.read()

# Fix the quotes!
content = content.replace(
    '"div.innerHTML=\'<h3 style="margin:0 0 8px;font-weight:bold;color:#0f766e;">✅ Google Connected</h3><p style="margin:0;color:#0f766e;">Gmail search is active for <b>\' + (last.email || \'your account\') + \'</b>.</p>\';"',
    '\'div.innerHTML="<h3 style=\\\'margin:0 0 8px;font-weight:bold;color:#0f766e;\\\'>✅ Google Connected</h3><p style=\\\'margin:0;color:#0f766e;\\\'>Gmail search is active for <b>" + (last.email || "your account") + "</b>.</p>";\''
)

with open('dreamference/chat/onyx_ui_scripts.py', 'w') as f:
    f.write(content)

