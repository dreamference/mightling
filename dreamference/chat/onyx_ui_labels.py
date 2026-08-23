"""
Sidebar Label Rewrites for the Onyx Web UI.

This module provides the OnyxUILabels class, which shortens the sidebar's navigation labels in
Onyx's compiled JavaScript.

It is a third kind of patch, alongside the two CSS ones: `onyx_ui_fonts.py` substitutes rules Onyx
ships, `onyx_ui_overrides.py` appends rules it does not have, and this rewrites strings in the
bundle -- because a label is text in a JSX call, and no stylesheet can reach it. (`content` on a
pseudo-element can cover text up, but the original stays in the accessibility tree and in the
find-in-page results, which is worse than not doing it.)

Every rewrite matches a *quoted* JS string literal, and where the wording is a common one it also
carries the React prop it is passed as -- `children:"New Session"` rather than `New Session`. The
bare words appear all over the bundle, in analytics event names and aria labels and search
placeholders, and replacing them there renames things that are not this sidebar. A phrase
distinctive enough to have exactly one meaning ("How can I help you today?") is matched on the
quoted string alone, which is what lets one rule cover both the visible placeholder and the
`aria-placeholder` beside it.
"""

import json
import subprocess
from typing import Dict, Final, Optional

from dreamference.chat.onyx_brand_assets import WEB_BUILD_DIR, OnyxBrandAssets

# Sidebar tab labels, keyed by the JSX prop so the match cannot stray into unrelated strings.
#
# `New Session` is rewritten in two places: the sidebar tab and the command palette's matching
# action. That is deliberate -- they are the same command, and leaving the palette reading
# "New Session" next to a sidebar reading "New" would look like one of them had been missed.
LABEL_SUBSTITUTIONS: Final[Dict[str, str]] = {
    'children:"New Session"': 'children:"New"',
    'children:"Search Chats"': 'children:"Search"',
    # The composer placeholder is not passed as `children`, and it appears twice -- once as the
    # visible placeholder and once as `aria-placeholder`. Matching the quoted phrase catches both,
    # and the phrase is distinctive enough that nothing else in the bundle is a false positive.
    '"How can I help you today?"': '"Message"',
    # The search palette's placeholder. Projects are hidden, so offering to search them was a
    # promise the UI no longer keeps.
    '"Search chat sessions, projects..."': '"Search chat sessions"',
    # The settings tab that holds the Google/Gmail connection. Keyed by the `label:` prop of the
    # settings route table, which also feeds the page heading -- one rewrite renames both. The
    # injected connect script finds that page by its heading, so `SECTION_HEADING` in
    # `onyx_ui_scripts.py` must carry the *rewritten* text; the two move together.
    'label:"Connectors"': 'label:"Gmail Accounts"',
    # Transitional: a container already carrying the earlier "Google" rewrite has no
    # `label:"Connectors"` left to match. Bare `label:"Google"` is NOT safe -- Onyx uses it for
    # the Google OAuth method and the web-search provider groups -- so the key carries the href.
    '{href:"/app/settings/connectors",label:"Google"}':
        '{href:"/app/settings/connectors",label:"Gmail Accounts"}',
    # The page heading over that tab's content is a separate literal from the nav label. The
    # match carries the full prop run because the admin Chat Preferences page renders an
    # identical heading distinguishable only by its missing `width:"full"`, and an admin table
    # has a bare "Connectors" column header -- both must stay.
    'title:"Connectors",sizePreset:"main-content",variant:"section",width:"full"':
        'title:"Gmail Accounts",sizePreset:"main-content",variant:"section",width:"full"',
    'title:"Google",sizePreset:"main-content",variant:"section",width:"full"':
        'title:"Gmail Accounts",sizePreset:"main-content",variant:"section",width:"full"',
}


class OnyxUILabels:
    """
    Rewrites Onyx's sidebar labels inside a running web server container.
    """

    @classmethod
    def install(cls, container: Optional[str] = None) -> bool:
        """
        Applies every label rewrite to the compiled bundles.

        Args:
            container (Optional[str]): Onyx web server container name; discovered if omitted.

        Returns:
            bool: True if at least one bundle carries the shortened labels.
        """
        target = container or OnyxBrandAssets.find_web_container()
        if not target:
            return False
        return cls.rewrite_labels(target) > 0

    @classmethod
    def rewrite_labels(cls, container: str) -> int:
        """
        Substitutes the label strings in every bundle file that contains one.

        Counts bundles carrying a shortened label afterwards rather than ones it just wrote, so a
        re-run over an already-patched container reports the same number instead of zero.

        Args:
            container (str): Onyx web server container name.

        Returns:
            int: Number of bundles carrying the shortened labels. Zero means none of the expected
                labels were found, which is what a version that renamed them would look like.
        """
        script = (
            "const fs=require('fs'),path=require('path');"
            f"const MAP={json.dumps(LABEL_SUBSTITUTIONS)};"
            "const NEW=Object.values(MAP);"
            "let carrying=0;"
            "const walk=(d,dep)=>{if(dep>8)return;let e=[];"
            "try{e=fs.readdirSync(d,{withFileTypes:true})}catch(x){return}"
            "for(const f of e){const p=path.join(d,f.name);"
            "if(f.isDirectory()){if(!/node_modules|cache/.test(p))walk(p,dep+1);continue}"
            "if(!/\\.(js|mjs)$/.test(f.name))continue;"
            "let s='';try{s=fs.readFileSync(p,'utf8')}catch(x){continue}"
            "let o=s;for(const k in MAP)if(o.includes(k))o=o.split(k).join(MAP[k]);"
            "if(o!==s){try{fs.writeFileSync(p,o)}catch(x){continue}}"
            "if(NEW.some(n=>o.includes(n)))carrying++;}};"
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
