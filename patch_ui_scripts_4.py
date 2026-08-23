import re

with open('dreamference/chat/onyx_ui_scripts.py', 'r') as f:
    content = f.read()

content = content.replace(
    '"div.innerHTML=\'<div style="padding:16px;background:#f0fdfa;border:1px solid #14b8a6;border-radius:8px;margin-bottom:16px;"><h3 style="margin:0 0 8px;font-weight:bold;color:#0f766e;">✅ Google Connected</h3><p style="margin:0;color:#0f766e;">Gmail search is active for <b>\' + (last.email || \'your account\') + \'</b>.</p></div>\';"',
    '\'div.innerHTML="<div style=\\\'padding:16px;background:#f0fdfa;border:1px solid #14b8a6;border-radius:8px;margin-bottom:16px;\\\'><h3 style=\\\'margin:0 0 8px;font-weight:bold;color:#0f766e;\\\'>✅ Google Connected</h3><p style=\\\'margin:0;color:#0f766e;\\\'>Gmail search is active for <b>" + (last.email || "your account") + "</b>.</p></div>";\''
)

with open('dreamference/chat/onyx_ui_scripts.py', 'w') as f:
    f.write(content)

