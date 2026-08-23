import re

with open('dreamference/chat/onyx_ui_scripts.py', 'r') as f:
    content = f.read()

# We need to add window.__puffinDisconnect function
new_disconnect_fn = '''
    "window.__puffinDisconnect = function(email) {"
    f"  fetch('{HOST_ORIGIN}/disconnect', {{"
    "    method: 'POST',"
    "    headers: {'Content-Type': 'application/json'},"
    "    body: JSON.stringify({email: email})"
    "  }).then(function(){ check(); });"
    "};"
'''

# We need to replace the html generation loop
old_loop = '\'var emails = (last.email || "").split(", ");\'\n    \'for(var i=0;i<emails.length;i++){"\'\n    \'"  if(emails[i]) html += "<p style=\\\'margin:0 0 8px;font-size:14px;color:#0f766e;\\\'>Gmail search is active for <b>" + emails[i] + "</b>.</p>";"\'\n    \'"}"\''
# Actually, the string replacement is tricky due to quotes. Let's just use regex to replace everything between `var html=` and `div.innerHTML=html;`

def replace_html(match):
    return (
        "\"var emails = (last.email || '').split(', ');\"\n"
        "    \"for(var i=0;i<emails.length;i++){\"\n"
        "    \"  if(emails[i]) html += '<div style=\\\"display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;\\\"><p style=\\\"margin:0;font-size:14px;color:#0f766e;\\\">Gmail search is active for <b>' + emails[i] + '</b>.</p><button onclick=\\\"window.__puffinDisconnect(\\'' + emails[i] + '\\')\\\" style=\\\"background:none;border:none;color:#ef4444;cursor:pointer;font-size:12px;font-weight:bold;\\\">Disconnect</button></div>';\"\n"
        "    \"}\"\n"
        "    \"div.innerHTML=html;\""
    )

content = re.sub(r'\'var emails = \(last\.email \|\| ""\)\.split\(\\", \\"\);.*?"div\.innerHTML=html;"\'', replace_html, content, flags=re.DOTALL)
# Wait, the current file contents uses a mix of single and double quotes. I will just rewrite the whole CONNECT_GOOGLE_SCRIPT again.
