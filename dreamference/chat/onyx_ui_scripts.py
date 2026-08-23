"""
Injected Scripts for the Onyx Web UI.

This module provides the OnyxUIScripts class, which appends Dreamference's own JavaScript to Onyx's
compiled bundles. It is the third kind of patch: `onyx_ui_overrides.py` appends stylesheets,
`onyx_ui_labels.py` rewrites strings, and this adds behaviour -- an element that is not in Onyx's
markup at all, and cannot be conjured by either of the other two.

Onyx has no sanctioned injection point in this edition. `custom_analytics_script` exists in the
route table but is an Enterprise feature and answers 404 here, so the script goes into the bundle
the same way the stylesheets go into the CSS.

**It is appended to the chunks that already mention `opal-sidebar-footer`, not to every chunk.**
That is the chunk which renders the element being attached to, so the script arrives exactly when
the thing it needs exists, and a page that never loads the sidebar never runs it. The whole block
is wrapped in a guard and a `try`: a throwing top-level statement in a bundle chunk would take the
application down with it, and no button is worth that.
"""

import json
import subprocess
from typing import Final, Optional

from dreamference.chat.onyx_brand_assets import WEB_BUILD_DIR, OnyxBrandAssets
from dreamference.chat.gmail_search_service import (
    CONNECT_PATH as SERVICE_CONNECT_PATH,
    HOST_ORIGIN,
)

# Opens the appended block; everything after it in a chunk is this module's, so a re-run cuts at the
# marker and rewrites rather than stacking copies.
#
# **The block is written on a line of its own, and that is not cosmetic.** Turbopack ends its
# chunks with a `//# debugId=…` line comment and no trailing newline, so a block appended directly
# onto the end lands *inside that comment* and never executes. The script was silently inert for
# exactly this reason -- no error, no attribute set, no button -- until an offscreen WebKitGTK probe
# showed `data-puffin-engine` unset on a page whose sidebar had clearly rendered. `append_scripts`
# therefore trims trailing newlines from what it keeps and writes exactly one, which also stops a
# blank line accumulating per install.
SCRIPT_MARKER: Final[str] = "/*dreamference-ui-scripts*/"

# Only chunks that render this class get the script.
#
# The class is the sidebar footer's, but what it actually identifies is the **app shell** -- the
# chunk that renders the sidebar, which every `/app/*` route mounts, verified by loading
# `/app/settings/connectors` and finding the footer present. It is a chunk selector, not the place
# anything is attached to any more; both scripts below find their own anchors at run time.
ANCHOR_CLASS: Final[str] = "opal-sidebar-footer"

# The element the button is given, so the stylesheet in `onyx_ui_overrides.py` can style it and
# the script can find its own work again after a re-render.
BUTTON_ID: Final[str] = "puffin-connect-google"

# Where the button lives: Onyx's own **Settings → Connectors** page, alongside the connectors it
# would be one of. The route is matched exactly, so the button exists on that page and nowhere
# else, and React removes it along with the page on navigation -- there is nothing to clean up.
CONNECT_PATH: Final[str] = "/app/settings/connectors"

# The section it is appended to, identified by its heading rather than by its classes. The section
# is `div.flex.flex-col…` holding `div.w-full` (the heading) and an `.opal-card` (the connector
# list, or the "No connectors set up" placeholder) -- all Tailwind utilities, none of them a name
# anyone chose. The heading text is the only stable handle on that page, and the `.opal-content-md`
# structure around it is Onyx's own component. Note the page carries *two* nodes reading
# "Connectors" -- this one and the settings nav link -- which is why the search is scoped to
# `.opal-content-md-title-row`.
SECTION_HEADING: Final[str] = "Connectors"

# How often placement is re-checked, as opposed to the status fetch below. Route changes in this
# app are client-side, so nothing loads and no event this script can see fires; a short interval is
# what makes the button appear promptly on arriving at the page. It stays cheap because the first
# thing it does is compare `location.pathname`, which is false on every other route in the app.
PLACE_INTERVAL_MS: Final[int] = 500

# Marks the rendering engine on `<html>` so stylesheets can tell WebKitGTK from Blink.
#
# The desktop app renders through WebKitGTK and the browser through Blink, against the same server
# and therefore the same stylesheets. Mostly that is invisible, but not always: WebKitGTK draws a
# trough hairline down a scroll container even when `scrollbar-color` paints both parts
# transparent, so the sidebar had a grey line in the app and none in the browser. CSS has no way to
# ask which engine it is running in, and there is no honest `@supports` discriminator between the
# two, so the script sets an attribute and the stylesheet keys off it.
ENGINE_ATTRIBUTE: Final[str] = "data-puffin-engine"

# How often to re-check. The status can change without a reload -- the user consents in another
# tab and comes back -- and React re-renders the footer, which would otherwise drop the button.
POLL_INTERVAL_MS: Final[int] = 5000

CONNECT_GOOGLE_SCRIPT: Final[str] = (
    ";(function(){try{"
    "if(window.__puffinConnect)return;window.__puffinConnect=1;"
    'var ENG=/Chrome\//.test(navigator.userAgent)?"blink":"webkit";'
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
    "var ec=document.getElementById(ID+'-card');"
    "if(ec)ec.remove();"
    
    # Hide the "No connectors" card by finding it among p's children
    "for(var i=0;i<p.children.length;i++){"
    "  if(p.children[i].textContent.includes('No connectors')){"
    "    p.children[i].style.display='none';"
    "  }"
    "}"
    
    # Render connected accounts
    "if(last&&last.connected){"
    "var div=document.createElement('div');div.id=ID+'-card';"
    "div.style.padding='16px';div.style.background='#f0fdfa';div.style.border='1px solid #14b8a6';div.style.borderRadius='8px';div.style.marginBottom='16px';div.style.marginTop='16px';"
    'var html="<h3 style=\'margin:0 0 12px;font-size:16px;font-weight:bold;color:#0f766e;\'>✅ Google Connected</h3>";'
    "var emails = (last.email || '').split(', ');"
    "for(var i=0;i<emails.length;i++){"
    '  if(emails[i]) html += "<p style=\'margin:0 0 8px;font-size:14px;color:#0f766e;\'>Gmail search is active for <b>" + emails[i] + "</b>.</p>";'
    "}"
    "div.innerHTML=html;"
    "p.appendChild(div);"
    "}"
    
    # Render the button
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
)

# A drawn scrollbar for the desktop app.
#
# WebKitGTK paints its native scrollbar as engine chrome, with a trough line no CSS colour can
# reach -- the stylesheet therefore sets `scrollbar-width:none` there, which removes the line and
# the bar together. This script puts a bar back by drawing one: a single fixed-position thumb,
# positioned from the scroll container's own geometry, so there is no engine trough to leak.
#
# It runs only when the engine marker says webkit; Blink keeps its native hover-revealed
# scrollbar, which has no such problem. The thumb is re-derived from a fresh `querySelector` on
# every update because React recreates the chat list wholesale -- holding a reference would mean
# tracking a corpse after the next re-render. `scroll` is listened for in the capture phase, since
# scroll events do not bubble. The interval is the fallback for what no event announces: chat
# titles arriving and changing the scrollable height.
SCROLLBAR_ID: Final[str] = "puffin-scrollbar"
SCROLLBAR_MIN_THUMB_PX: Final[int] = 30
SCROLLBAR_SCRIPT: Final[str] = (
    ";(function(){try{"
    "if(window.__puffinScrollbar)return;window.__puffinScrollbar=1;"
    f'var ID="{SCROLLBAR_ID}",MIN={SCROLLBAR_MIN_THUMB_PX};'
    "function boot(){"
    "if(document.documentElement.getAttribute('data-puffin-engine')!=='webkit')return;"
    "var t=document.createElement('div');t.id=ID;document.body.appendChild(t);"
    "var drag=null;"
    "function sc(){return document.querySelector('.opal-sidebar-body__scroll');}"
    "function geo(s,r){var h=Math.max(MIN,r.height*s.clientHeight/s.scrollHeight);"
    "return{h:h,top:r.top+(r.height-h)*(s.scrollTop/(s.scrollHeight-s.clientHeight))};}"
    "function upd(){var s=sc();"
    "if(!s||s.scrollHeight<=s.clientHeight+1){t.style.display='none';return}"
    "var r=s.getBoundingClientRect(),g=geo(s,r);"
    "t.style.display='block';t.style.height=g.h+'px';t.style.top=g.top+'px';"
    "t.style.left=(r.right-8)+'px';}"
    "document.addEventListener('scroll',upd,true);"
    "window.addEventListener('resize',upd);"
    "setInterval(upd,1000);"
    "t.addEventListener('mousedown',function(e){var s=sc();if(!s)return;"
    "drag={y:e.clientY,top:s.scrollTop};e.preventDefault();});"
    "document.addEventListener('mousemove',function(e){var s=drag&&sc();if(!s)return;"
    "var r=s.getBoundingClientRect(),g=geo(s,r);"
    "s.scrollTop=drag.top+(e.clientY-drag.y)*"
    "((s.scrollHeight-s.clientHeight)/(r.height-g.h));});"
    "document.addEventListener('mouseup',function(){drag=null});"
    "upd();}"
    "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',boot)}"
    "else{boot()}"
    "}catch(e){}})();"
)

# Everything this module injects.
UI_SCRIPTS: Final[str] = SCRIPT_MARKER + CONNECT_GOOGLE_SCRIPT + SCROLLBAR_SCRIPT


class OnyxUIScripts:
    """
    Appends Dreamference's scripts to a running Onyx web server container.
    """

    @classmethod
    def install(cls, container: Optional[str] = None) -> bool:
        """
        Adds the scripts to the compiled bundles.

        Args:
            container (Optional[str]): Onyx web server container name; discovered if omitted.

        Returns:
            bool: True if at least one bundle carries them afterwards.
        """
        target = container or OnyxBrandAssets.find_web_container()
        if not target:
            return False
        return cls.append_scripts(target) > 0

    @classmethod
    def append_scripts(cls, container: str) -> int:
        """
        Writes the script block into every bundle that renders the sidebar footer.

        Any previous block is cut at the marker and replaced, so this both installs and updates.
        Counts bundles carrying the block afterwards rather than ones it just wrote, so a re-run
        reports the same number instead of zero.

        Args:
            container (str): Onyx web server container name.

        Returns:
            int: Number of bundles carrying the scripts.
        """
        script = (
            "const fs=require('fs'),path=require('path');"
            f"const JS={json.dumps(UI_SCRIPTS)},MARK={json.dumps(SCRIPT_MARKER)},"
            f"ANCHOR={json.dumps(ANCHOR_CLASS)};"
            "let carrying=0;"
            "const walk=(d,dep)=>{if(dep>8)return;let e=[];"
            "try{e=fs.readdirSync(d,{withFileTypes:true})}catch(x){return}"
            "for(const f of e){const p=path.join(d,f.name);"
            "if(f.isDirectory()){if(!/node_modules|cache/.test(p))walk(p,dep+1);continue}"
            "if(!/\\.(js|mjs)$/.test(f.name))continue;"
            "let s='';try{s=fs.readFileSync(p,'utf8')}catch(x){continue}"
            "const i=s.indexOf(MARK);"
            # Trim the trailing newlines as well as the old block, so re-installing neither
            # stacks blank lines nor leaves the previous version's separator behind.
            "const base=(i<0?s:s.slice(0,i)).replace(/\\n+$/,'');"
            "if(!base.includes(ANCHOR))continue;"
            "const next=base+'\\n'+JS;"
            "if(next!==s){try{fs.writeFileSync(p,next)}catch(x){continue}}"
            "carrying++;}};"
            f"walk({json.dumps(WEB_BUILD_DIR)},0);console.log(carrying);"
        )
        try:
            result = subprocess.run(
                ["docker", "exec", container, "node", "-e", script],
                capture_output=True, text=True, timeout=180, check=False,
            )
            return int(result.stdout.strip().splitlines()[-1]) if result.returncode == 0 else 0
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return 0
