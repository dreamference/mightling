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
    "function panel(){if(location.pathname!==PATH)return null;"
    "var h=document.querySelectorAll('.opal-content-md-title-row span');"
    "for(var i=0;i<h.length;i++){if(h[i].textContent.trim()===HEAD){"
    "var w=h[i].closest('div.w-full');return w&&w.parentElement}}return null;}"
    "function apply(){var p=panel();if(!p)return;"
    "var e=document.getElementById(ID);"
    "if(e)e.remove();"
    
    "var div=document.createElement('div');div.id=ID;"
    "div.style.marginTop='16px';"
    
    "if(last&&last.connected){"
    "div.innerHTML='<div style=\"padding:16px;background:#f0fdfa;border:1px solid #14b8a6;border-radius:8px;margin-bottom:16px;\"><h3 style=\"margin:0 0 8px;font-weight:bold;color:#0f766e;\">✅ Google Connected</h3><p style=\"margin:0;color:#0f766e;\">Gmail search is active for <b>' + (last.email || 'your account') + '</b>.</p></div>';"
    "var cards=p.querySelectorAll('.text-sm.text-gray-500');"
    "for(var i=0;i<cards.length;i++){if(cards[i].textContent.includes('No connectors'))cards[i].style.display='none';}"
    "}"
    
    "var a=document.createElement('a');a.href=U;"
    "a.target=ENG==='blink'?'_blank':'_self';a.rel='noopener';"
    "a.textContent=(last&&last.connected)?'Connect another Google account':'Connect to Google';"
    "div.appendChild(a);"
    "p.appendChild(div);"
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

