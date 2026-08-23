import re

with open('dreamference/chat/onyx_ui_scripts.py', 'r') as f:
    content = f.read()

new_script = """CONNECT_GOOGLE_SCRIPT: Final[str] = (
    ";(function(){try{"
    "if(window.__puffinConnect)return;window.__puffinConnect=1;"
    'var ENG=/Chrome\\\\//.test(navigator.userAgent)?"blink":"webkit";'
    f'document.documentElement.setAttribute("{ENGINE_ATTRIBUTE}",ENG);'
    f'var S="{HOST_ORIGIN}/status",U="{HOST_ORIGIN}{SERVICE_CONNECT_PATH}",ID="{BUTTON_ID}";'
    f'var PATH="{CONNECT_PATH}",HEAD="{SECTION_HEADING}";'
    "var last=null;"
    
    "window.__puffinDisconnect = function(email) {"
    f"  fetch('{HOST_ORIGIN}/disconnect', {{"
    "    method: 'POST',"
    "    headers: {'Content-Type': 'application/json'},"
    "    body: JSON.stringify({email: email})"
    "  }}).then(function(){ check(); });"
    "};"
    
    "function panel(){if(location.pathname!==PATH)return null;"
    "var h=document.querySelectorAll('.opal-content-md-title-row span');"
    "for(var i=0;i<h.length;i++){if(h[i].textContent.trim()===HEAD){"
    "var w=h[i].closest('div.w-full');return w&&w.parentElement}}return null;}"
    "function apply(){var p=panel();if(!p)return;"
    "var e=document.getElementById(ID);"
    "if(e)e.remove();"
    "var ec=document.getElementById(ID+'-card');"
    "if(ec)ec.remove();"
    
    "for(var i=0;i<p.children.length;i++){"
    "  if(p.children[i].textContent.includes('No connectors')){"
    "    p.children[i].style.display='none';"
    "  }"
    "}"
    
    "if(last&&last.connected){"
    "var div=document.createElement('div');div.id=ID+'-card';"
    "div.style.padding='16px';div.style.background='#f0fdfa';div.style.border='1px solid #14b8a6';div.style.borderRadius='8px';div.style.marginBottom='16px';div.style.marginTop='16px';"
    "var html='<h3 style=\"margin:0 0 12px;font-size:16px;font-weight:bold;color:#0f766e;\">✅ Google Connected</h3>';"
    "var emails = (last.email || '').split(', ');"
    "for(var i=0;i<emails.length;i++){"
    "  if(emails[i]) html += '<div style=\"display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;\"><p style=\"margin:0;font-size:14px;color:#0f766e;\">Gmail search is active for <b>' + emails[i] + '</b>.</p><button onclick=\"window.__puffinDisconnect(\\'' + emails[i] + '\\')\" style=\"background:none;border:none;color:#ef4444;cursor:pointer;font-size:12px;font-weight:bold;\">Disconnect</button></div>';"
    "}"
    "div.innerHTML=html;"
    "p.appendChild(div);"
    "}"
    
    "var a=document.createElement('a');a.id=ID;a.href=U;"
    "a.target=ENG==='blink'?'_blank':'_self';a.rel='noopener';"
    "a.textContent=(last&&last.connected)?'Connect another Google account':'Connect to Google';"
    "p.appendChild(a);"
    "}"
    "function check(){fetch(S).then(function(r){return r.json()}).then(function(s){"
    "last=s;apply()}).catch(function(){last=null;apply()});}"
    f"function boot(){{check();setInterval(check,{POLL_INTERVAL_MS});"
    f"setInterval(apply,{PLACE_INTERVAL_MS});}}"
    "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',boot)}"
    "else{boot()}"
    "}catch(e){}})();"
)"""

pat = r'CONNECT_GOOGLE_SCRIPT: Final\[str\] = \(.*?\n\)\n'
content = re.sub(pat, new_script + '\n', content, flags=re.DOTALL)

with open('dreamference/chat/onyx_ui_scripts.py', 'w') as f:
    f.write(content)

