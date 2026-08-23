import re

with open('dreamference/chat/onyx_ui_scripts.py', 'r') as f:
    content = f.read()

content = content.replace(
    '"var html=\'<h3 style="margin:0 0 12px;font-size:16px;font-weight:bold;color:#0f766e;">✅ Google Connected</h3>\';"',
    '\'var html="<h3 style=\\\'margin:0 0 12px;font-size:16px;font-weight:bold;color:#0f766e;\\\'>✅ Google Connected</h3>";\''
)
content = content.replace(
    '"  if(emails[i]) html += \'<p style="margin:0 0 8px;font-size:14px;color:#0f766e;">Gmail search is active for <b>\' + emails[i] + \'</b>.</p>\';"',
    '\'  if(emails[i]) html += "<p style=\\\'margin:0 0 8px;font-size:14px;color:#0f766e;\\\'>Gmail search is active for <b>" + emails[i] + "</b>.</p>";\''
)

with open('dreamference/chat/onyx_ui_scripts.py', 'w') as f:
    f.write(content)

