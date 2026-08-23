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
#
# "Gmail Accounts" rather than "Connectors" because `onyx_ui_labels.py` rewrites that label; this
# constant must match what the page *renders after* the rewrite, not what Onyx ships. The aliases
# exist because the heading text and this script travel in *different* chunks, and a browser can
# hold a fresh copy of one and a stale copy of the other -- seen in the field as a connect card
# that silently vanished mid-rename. Matching every name the heading has ever had makes a mixed
# cache degrade to old labels rather than to a missing feature.
SECTION_HEADING: Final[str] = "Gmail Accounts"
SECTION_HEADING_ALIASES: Final[tuple] = ("Gmail Accounts", "Google", "Connectors")

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
    f'var PATH="{CONNECT_PATH}",HEADS={json.dumps(list(SECTION_HEADING_ALIASES))};'
    "var last=null,lastSig=null;"
    
    "window.__puffinDisconnect = function(email) {"
    f"  fetch('{HOST_ORIGIN}/disconnect', {{"
    "    method: 'POST',"
    "    headers: {'Content-Type': 'application/json'},"
    "    body: JSON.stringify({email: email})"
    "  }).then(function(){ check(); });"
    "};"
    
    # In gmail mode the card lives in the Chat Preferences pane, found the way the synthetic-tab
    # CSS finds it: the settings nav's sibling that holds the Personal Preferences textarea.
    # `getAttribute` is feature-checked because the test harness stubs a minimal documentElement.
    "function panel(){"
    "var de=document.documentElement;"
    "var sec=de.getAttribute?de.getAttribute('data-puffin-section'):null;"
    "if(sec==='gmail'&&location.pathname==='/app/settings/chat-preferences'){"
    "var nav=document.querySelector('[data-testid=\"settings-left-tab-navigation\"]');"
    "if(!nav||!nav.parentElement)return null;"
    "var cs=nav.parentElement.children;"
    "for(var j=0;j<cs.length;j++){"
    "if(cs[j]!==nav&&cs[j].querySelector&&cs[j].querySelector('textarea'))return cs[j];}"
    "return null;}"
    "if(location.pathname!==PATH)return null;"
    "var h=document.querySelectorAll('.opal-content-md-title-row span');"
    "for(var i=0;i<h.length;i++){if(HEADS.indexOf(h[i].textContent.trim())>=0){"
    "var w=h[i].closest('div.w-full');return w&&w.parentElement}}return null;}"
    # Rebuild only when the state changed or React wiped the injected nodes -- the 500ms
    # re-apply otherwise destroys the card mid-click and the Disconnect button never fires.
    "function apply(){var p=panel();if(!p)return;"
    "if(location.pathname==='/app/settings/chat-preferences'&&!document.getElementById(ID+'-head')){"
    "var hd=document.createElement('div');hd.id=ID+'-head';hd.textContent='Gmail Accounts';"
    "hd.style.cssText='font-size:16px;font-weight:600;color:#111827;width:100%;';"
    "p.appendChild(hd);}"
    "var sig=JSON.stringify(last)+'|'+location.pathname;"
    "var e=document.getElementById(ID);"
    "var ec=document.getElementById(ID+'-card');"
    "var present=e&&(!(last&&last.connected)||ec);"
    "if(present&&sig===lastSig)return;"
    "lastSig=sig;"
    "if(e)e.remove();"
    "if(ec)ec.remove();"
    
    "var kids=p.children||[];for(var i=0;i<kids.length;i++){"
    "  if(kids[i].textContent&&kids[i].textContent.includes('No connectors')){"
    "    kids[i].style.display='none';"
    "  }"
    "}"
    
    "if(last&&last.connected){"
    "var div=document.createElement('div');div.id=ID+'-card';"
    "div.style.padding='4px 16px';div.style.background='#fff';div.style.width='100%';div.style.border='1px solid #e5e7eb';div.style.borderRadius='12px';var inPrefs=location.pathname==='/app/settings/chat-preferences';div.style.marginTop=inPrefs?'-20px':'16px';div.style.marginBottom=inPrefs?'0':'16px';"
    # One row per connected account, in the language of Onyx's own dialogs (the share sheet's
    # rows): stroke icon, semibold title, muted description, and a quietly bordered action.
    'var html="";'
    "var emails = (last.email || '').split(', ');"
    "for(var i=0;i<emails.length;i++){"
    '  if(emails[i]) html += "<div style=\'display:flex;align-items:center;gap:12px;padding:12px 0;" + (html?"border-top:1px solid #f3f4f6;":"") + "\'>"'
    '    + "<svg width=\'20\' height=\'20\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'#374151\' stroke-width=\'2\' stroke-linecap=\'round\' stroke-linejoin=\'round\' style=\'flex-shrink:0\'><rect x=\'2\' y=\'4\' width=\'20\' height=\'16\' rx=\'2\'/><path d=\'m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7\'/></svg>"'
    '    + "<div style=\'flex:1;min-width:0\'><div style=\'font-size:15px;font-weight:600;color:#111827;\'>" + emails[i] + "</div>"'
    '    + "<div style=\'font-size:13px;color:#6b7280;margin-top:2px;\'>Gmail search is active for this account.</div></div>"'
    '    + "<button onclick=\'window.__puffinDisconnect(\\"" + emails[i] + "\\")\' style=\'background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:8px 14px;font-size:13px;font-weight:500;color:#374151;cursor:pointer;flex-shrink:0;\'>Disconnect</button></div>";'
    "}"
    "div.innerHTML=html;"
    "p.appendChild(div);"
    "}"
    
    "var a=document.createElement('a');a.id=ID;a.href=U;"
    "a.target=ENG==='blink'?'_blank':'_self';a.rel='noopener';"
    "a.textContent=(last&&last.connected)?'Connect another Google account':'Connect to Google';"
    # On Blink the consent flow opens as a modal over the page -- an iframe of the service's
    # connect page, in the settings modal's visual idiom. The click on Authorize inside it still
    # window.open()s Google in a real tab, because Google refuses to be framed. WebKit keeps the
    # in-place navigation: `window.open` is inert there and a modal would dead-end at Authorize.
    "if(ENG==='blink'){a.addEventListener('click',function(e){"
    "e.preventDefault();e.stopPropagation();openConnect();},true);}"
    "p.appendChild(a);"
    "}"
    "function openConnect(){"
    "if(document.getElementById(ID+'-modal'))return;"
    "var o=document.createElement('div');o.id=ID+'-modal';"
    "o.style.cssText='position:fixed;inset:0;z-index:2100;background:rgba(17,24,39,.5);"
    "display:flex;align-items:center;justify-content:center;';"
    "var pn=document.createElement('div');"
    "pn.style.cssText='position:relative;width:min(560px,calc(100vw - 48px));"
    "height:min(540px,calc(100vh - 48px));background:#fff;border-radius:16px;"
    "box-shadow:0 25px 50px -12px rgba(0,0,0,.25);overflow:hidden;';"
    "var fr=document.createElement('iframe');fr.src=U;"
    "fr.style.cssText='width:100%;height:100%;border:0;display:block;';"
    "var x=document.createElement('button');x.setAttribute('aria-label','Close');"
    "x.textContent='\u00d7';"
    "x.style.cssText='position:absolute;top:10px;right:12px;width:32px;height:32px;border:none;"
    "background:transparent;color:#6b7280;font-size:22px;line-height:1;cursor:pointer;"
    "border-radius:8px;';"
    # Closing re-checks status immediately: the user most often closes this right after
    # completing consent in the Google tab, and the card behind should reflect it at once
    # rather than on the next 5s poll.
    "function cl(){o.remove();document.removeEventListener('keydown',esc2,true);check();}"
    "function esc2(e){if(e.key==='Escape'){e.stopPropagation();cl();}}"
    "x.addEventListener('click',cl);"
    "o.addEventListener('mousedown',function(e){if(e.target===o)cl();});"
    "document.addEventListener('keydown',esc2,true);"
    "pn.appendChild(fr);pn.appendChild(x);o.appendChild(pn);document.body.appendChild(o);}"
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

# Settings as a modal over the chat rather than a full-page navigation.
#
# The user-menu entry is a real anchor (`href="/app/settings"`, unlike the tabs inside the
# settings page, which are divs), so a capture-phase click listener on `document` sees it before
# React's root-level handlers and can swallow the navigation: `preventDefault` stops the anchor,
# `stopPropagation` at document capture stops the router. The route then loads in an iframe
# inside a fixed overlay -- same-origin, and `frame-ancestors 'self'` permits it.
#
# The settings route renders the whole app shell, sidebar and all, which inside a modal would
# look like a miniature app. The script cannot restyle the framed document from outside, but it
# also runs *inside* the iframe (the chunks are the same), where `window.top !== window.self` is
# the discriminator: a framed document marks its own <html> with `FRAMED_ATTRIBUTE` and returns
# before installing the interceptor, and `SETTINGS_MODAL_CSS` in `onyx_ui_overrides.py` keys the
# sidebar removal on that attribute. Escape and a backdrop click both close; the synthetic
# Escape dispatched before opening asks the radix popover holding the menu to fold itself.
FRAMED_ATTRIBUTE: Final[str] = "data-puffin-framed"
SETTINGS_MODAL_ID: Final[str] = "puffin-settings-modal"
SETTINGS_MODAL_SCRIPT: Final[str] = (
    ";(function(){try{"
    "if(window.__puffinSettingsModal)return;window.__puffinSettingsModal=1;"
    f'var OID="{SETTINGS_MODAL_ID}";'
    "if(window.top!==window.self){"
    f'document.documentElement.setAttribute("{FRAMED_ATTRIBUTE}","1");return}}'
    "function close(){var o=document.getElementById(OID);"
    "if(o){o.remove();document.removeEventListener('keydown',esc,true);"
    "if(window.removeEventListener&&window.__puffinModalMsg){"
    "window.removeEventListener('message',window.__puffinModalMsg);window.__puffinModalMsg=null;}}}"
    "function esc(e){if(e.key==='Escape'){e.stopPropagation();close();}}"
    "function open(href){close();"
    "var o=document.createElement('div');o.id=OID;"
    "o.style.cssText='position:fixed;inset:0;z-index:2000;background:rgba(17,24,39,.5);"
    "display:flex;align-items:center;justify-content:center;';"
    "var p=document.createElement('div');"
    "p.style.cssText='position:relative;width:min(960px,calc(100vw - 48px));"
    "height:min(680px,calc(100vh - 48px));background:#fff;border-radius:16px;"
    "box-shadow:0 25px 50px -12px rgba(0,0,0,.25);overflow:hidden;';"
    # One iframe per settings route, all loaded up front; the one matching the clicked href is
    # shown. `__puffinTabs` is published by the synthetic-tabs script, which runs after this one
    # but long before any click. Swapping frames on request is what makes cross-route tab
    # switches instant -- reloading a single iframe was a visible white flash.
    "var tabs=window.__puffinTabs||[];var routes=[];"
    "for(var i=0;i<tabs.length;i++){if(routes.indexOf(tabs[i].p[0])<0)routes.push(tabs[i].p[0]);}"
    "var cur=href.split('#')[0];if(routes.indexOf(cur)<0)routes.push(cur);"
    "var frames={};"
    "routes.forEach(function(rt){"
    "var fr=document.createElement('iframe');fr.src=rt===cur?href:rt;"
    "fr.style.cssText='width:100%;height:100%;border:0;display:'+(rt===cur?'block':'none')+';';"
    "frames[rt]=fr;});"
    "function onmsg(ev){"
    "if(ev.origin!==location.origin)return;"
    "var d=ev.data;if(!d||!d.puffinSettingsNav)return;"
    "var s=d.puffinSettingsNav,tb=null,i;"
    "var tl=window.__puffinTabs||[];"
    "for(i=0;i<tl.length;i++){if(tl[i].s===s)tb=tl[i];}"
    "if(!tb||!frames[tb.p[0]])return;"
    "for(var rt in frames){frames[rt].style.display=rt===tb.p[0]?'block':'none';}"
    # Same-document hash write: replace() with only the fragment differing does not reload.
    "try{frames[tb.p[0]].contentWindow.location.replace(tb.p[0]+'#'+s);}"
    "catch(e){frames[tb.p[0]].src=tb.p[0]+'#'+s;}}"
    "if(window.addEventListener){window.__puffinModalMsg=onmsg;window.addEventListener('message',onmsg);}"
    "var x=document.createElement('button');x.setAttribute('aria-label','Close settings');"
    "x.textContent='\u00d7';"
    "x.style.cssText='position:absolute;top:10px;right:12px;width:32px;height:32px;border:none;"
    "background:transparent;color:#6b7280;font-size:22px;line-height:1;cursor:pointer;"
    "border-radius:8px;';"
    "x.addEventListener('click',close);"
    "routes.forEach(function(rt){p.appendChild(frames[rt]);});p.appendChild(x);o.appendChild(p);"
    "o.addEventListener('mousedown',function(e){if(e.target===o)close();});"
    "document.addEventListener('keydown',esc,true);"
    "document.body.appendChild(o);}"
    "document.addEventListener('click',function(e){"
    "var n=e.target;"
    "while(n&&n.getAttribute){"
    "if(n.tagName==='A'){var h=n.getAttribute('href')||'';"
    "if(h==='/app/settings'||h.indexOf('/app/settings/')===0){"
    "e.preventDefault();e.stopPropagation();"
    "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));"
    "open(h);return;}}"
    "n=n.parentNode;}"
    "},true);"
    "}catch(e){}})();"
)

# Sections of Chat Preferences promoted to settings tabs of their own.
#
# Onyx renders Voice and Prompt Shortcuts as sections of Chat Preferences, and the tab nav comes
# out of a compiled route table -- there is no route to add. Each tab is therefore synthetic: a
# clone of a real tab row (cloning keeps Onyx's classes, so hover and the selected pill are
# Onyx's own, and a clone carries none of React's handlers, so clicking it does only what is
# wired here). A promoted section lives at `chat-preferences#<slug>` -- the same page, with the
# hash as the mode switch -- and the active slug is mirrored onto <html> as
# `SYNTHETIC_SECTION_ATTRIBUTE`, because CSS cannot read a URL: `onyx_ui_overrides.py` keys on it
# to show only that section, restyle the real Chat Preferences pill back to rest, and light the
# synthetic tab. React recreates the nav wholesale on navigation, so a 500ms interval re-injects
# missing tabs -- the same posture the scrollbar script takes -- and unlike the modal script this
# one must also run inside the settings modal, so it does not bail when framed.
#
# Which pane child each slug shows is `onyx_ui_overrides.py`'s side of the contract, pinned by
# position from the end of the pane; adding an entry here means adding its section rule there.
SYNTHETIC_SECTION_ATTRIBUTE: Final[str] = "data-puffin-section"
# (slug, label, routes). A tab's mode is its slug in the hash on one of its routes; the first
# tab listed for a route is that route's default mode when the hash is empty or unknown, which
# is what lights "General" on a plain /app/settings landing. All real route tabs are hidden by
# `onyx_ui_overrides.py`, so this table *is* the settings nav.
SYNTHETIC_TABS: Final[tuple] = (
    ("general", "General", ("/app/settings/general", "/app/settings")),
    ("chat", "Chat Preferences", ("/app/settings/chat-preferences",)),
    ("gmail", "Gmail Accounts", ("/app/settings/chat-preferences",)),
    ("shortcuts", "Prompt Shortcuts", ("/app/settings/chat-preferences",)),
    ("voice", "Voice", ("/app/settings/chat-preferences",)),
)
SETTINGS_TABS_SCRIPT: Final[str] = (
    ";(function(){try{"
    "if(window.__puffinSynthTabs)return;window.__puffinSynthTabs=1;"
    f'var ATTR="{SYNTHETIC_SECTION_ATTRIBUTE}";'
    f"var TABS={json.dumps([{'s': s, 'l': l, 'p': list(r)} for s, l, r in SYNTHETIC_TABS])};"
    'var NAV=\'[data-testid="settings-left-tab-navigation"]\';'
    "function cur(){var h=location.hash.slice(1),def=null,i;"
    "for(i=0;i<TABS.length;i++){var tb=TABS[i];"
    "if(tb.p.indexOf(location.pathname)<0)continue;"
    "if(tb.s===h)return h;"
    "if(def===null)def=tb.s;}"
    "return def;}"
    # Cross-route switches cannot ride React's router from out here: the rows expose no handler
    # props (verified by walking `__reactProps$*` up the whole chain), dispatched events -- even
    # faithful PointerEvents -- move nothing, and native pushState+popstate changes the URL
    # shallowly without rendering the route. Inside the settings modal the answer is
    # architectural: the modal preloads one iframe per settings route, and a framed document
    # just asks it to swap -- a postMessage and a hash write, no reload anywhere. At the top
    # level a cross-route switch is an ordinary page navigation, where a load is normal.
    "window.__puffinTabs=TABS;"
    "function go(s){var tb=null,i;"
    "for(i=0;i<TABS.length;i++){if(TABS[i].s===s)tb=TABS[i];}"
    "if(!tb)return;"
    "if(tb.p.indexOf(location.pathname)>=0){location.hash=s;sync();return}"
    "if(window.top!==window.self&&window.parent&&window.parent.postMessage){"
    "try{window.parent.postMessage({puffinSettingsNav:s},location.origin);return}catch(e){}}"
    "location.href=tb.p[0]+'#'+s;}"
    "function make(nav,def){"
    "var src=null,rows=nav.children,i;"
    "for(i=0;i<rows.length;i++){"
    "if(rows[i].id&&rows[i].id.indexOf('puffin-tab-')===0)continue;"
    "if(rows[i].querySelector&&rows[i].querySelector('span[title]')){src=rows[i];break}}"
    "if(!src)return null;"
    "var tab=src.cloneNode(true);tab.id='puffin-tab-'+def.s;"
    "var sp=tab.querySelector('span[title]');"
    "sp.textContent=def.l;sp.setAttribute('title',def.l);"
    "tab.addEventListener('click',function(e){"
    "e.preventDefault();e.stopPropagation();go(def.s);},true);"
    "nav.appendChild(tab);return tab;}"
    "function sync(){var c=cur();"
    "if(c){document.documentElement.setAttribute(ATTR,c);}"
    "else{document.documentElement.removeAttribute(ATTR);}"
    "var nav=document.querySelector(NAV);if(!nav)return;"
    "for(var i=0;i<TABS.length;i++){"
    "var tab=document.getElementById('puffin-tab-'+TABS[i].s);"
    "if(!tab)tab=make(nav,TABS[i]);if(!tab)continue;"
    "var ic=tab.querySelector('[data-interactive-state]');"
    "if(ic)ic.setAttribute('data-interactive-state',c===TABS[i].s?'selected':'empty');}}"
    # Leaving a synthetic tab by clicking a real one: Chat Preferences is the same route, so
    # React's navigation is a no-op that leaves the hash in the URL and the mode stuck on. A
    # capture click on any real nav row strips the hash first and lets React proceed; for other
    # tabs the strip is harmless, the route change ends the mode anyway.
    "document.addEventListener('click',function(e){"
    "if(!cur())return;"
    "var nav=document.querySelector(NAV);if(!nav)return;"
    "var n=e.target,inNav=false,ours=false;"
    "while(n&&n!==document){"
    "if(n.id&&n.id.indexOf('puffin-tab-')===0)ours=true;"
    "if(n===nav){inNav=true;break}n=n.parentNode}"
    "if(inNav&&!ours){history.replaceState(null,'',location.pathname);sync();}"
    "},true);"
    "window.addEventListener('hashchange',sync);"
    "setInterval(sync,500);"
    "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',sync)}"
    "else{sync()}"
    "}catch(e){}})();"
)

# Everything this module injects.
UI_SCRIPTS: Final[str] = (
    SCRIPT_MARKER + CONNECT_GOOGLE_SCRIPT + SCROLLBAR_SCRIPT + SETTINGS_MODAL_SCRIPT
    + SETTINGS_TABS_SCRIPT
)


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
