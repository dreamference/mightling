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
from dreamference.chat.gmail_search_service import HOST_ORIGIN

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
ANCHOR_CLASS: Final[str] = "opal-sidebar-footer"

# The element the button is given, so the stylesheet in `onyx_ui_overrides.py` can style it and
# the script can find its own work again after a re-render.
BUTTON_ID: Final[str] = "puffin-connect-google"

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
    # Blink's user agent contains "Chrome/"; WebKitGTK's does not. Set before anything else, so a
    # stylesheet depending on it applies as early as this script runs.
    f'document.documentElement.setAttribute("{ENGINE_ATTRIBUTE}",'
    '/Chrome\\//.test(navigator.userAgent)?"blink":"webkit");' 
    f'var S="{HOST_ORIGIN}/status",U="{HOST_ORIGIN}/oauth/start",ID="{BUTTON_ID}";'
    # Placed at the top of the footer, above the account row, so it reads as a prompt rather than
    # as one more menu entry.
    "function place(){var f=document.querySelector('.%s');" % ANCHOR_CLASS +
    "if(!f)return;if(document.getElementById(ID))return;"
    "var a=document.createElement('a');a.id=ID;a.href=U;a.target='_blank';"
    "a.rel='noopener';a.textContent='Connect to Google';f.prepend(a);}"
    "function drop(){var e=document.getElementById(ID);if(e)e.remove();}"
    # `configured && !connected` is the only state this button is for: with no Google client there
    # is nothing to connect to, and once connected there is nothing to ask for.
    "function check(){fetch(S).then(function(r){return r.json()}).then(function(s){"
    "if(s&&s.configured&&!s.connected){place()}else{drop()}}).catch(drop);}"
    f"function boot(){{check();setInterval(check,{POLL_INTERVAL_MS});}}"
    "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',boot)}"
    "else{boot()}"
    "}catch(e){}})();"
)

# Everything this module injects.
UI_SCRIPTS: Final[str] = SCRIPT_MARKER + CONNECT_GOOGLE_SCRIPT


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
