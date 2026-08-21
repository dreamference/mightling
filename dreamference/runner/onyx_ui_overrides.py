"""
Injected Stylesheet Overrides for the Onyx Web UI.

This module provides the OnyxUIOverrides class, which appends Dreamference's own CSS to Onyx's
compiled stylesheets. It is the counterpart to `onyx_ui_fonts.py`: that one *substitutes* rules
Onyx already ships, this one *adds* rules Onyx does not have.

The rules are appended to every stylesheet under `.next` rather than to one global file, because
Next.js splits CSS per route and there is no single sheet every page loads -- the chat view and
the admin panel pull different chunks, and picking the largest one covers whichever route
happened to be biggest at build time rather than whichever route the user is on. The whole
override block is a few hundred bytes, so a copy in each sheet costs less than getting the guess
wrong. `OVERRIDE_MARKER` opens the block and everything after it is Dreamference's, so a re-run
replaces the previous version rather than appending a second copy -- which is what lets the CSS
here be edited and reapplied to a container that already carries an older copy.

Selectors here are pinned to things a build cannot renumber -- `data-testid` and `data-*` state
attributes, and Onyx's hand-written BEM classes (`opal-sidebar-root__column`). Never to Tailwind
utilities: those are a fresh permutation on every build, and a rule keyed to one is a rule that
silently stops matching after an upgrade.
"""

import json
import subprocess
from typing import Final, Optional

from dreamference.runner.onyx_brand_assets import WEB_BUILD_DIR, OnyxBrandAssets

# Opens the appended block. Everything from here to the end of a stylesheet is this module's, so
# a re-run can cut at the marker and write the current block in place of whatever was there.
OVERRIDE_MARKER: Final[str] = "/*dreamference-ui-overrides*/"

# Reveal-on-hover for the assistant message action bar.
#
# Onyx renders one `AgentMessage/toolbar` per assistant message, holding the copy, like, dislike,
# text-to-speech and regenerate controls -- and, in the same flex row, the message switcher and
# the citation list. Those last two are content rather than actions, so the toolbar itself is left
# alone and the `AgentMessage/` controls inside it are hidden instead; hiding the toolbar would
# take the citations with it.
#
# The hover target is `onyx-ai-message`, the per-message wrapper that is the toolbar's own parent,
# so hovering one message reveals only that message's controls. `:focus-within` keeps them
# reachable by keyboard, where there is no hover at all.
#
# `opacity` rather than `display:none` because the toolbar occupies a row of its own: removing it
# from layout makes every message below jump as the pointer crosses it. `pointer-events` is what
# actually stops a transparent button from swallowing a click.
HOVER_TOOLBAR_CSS: Final[str] = (
    '[data-testid="onyx-ai-message"] '
    '[data-testid^="AgentMessage/"]:not([data-testid="AgentMessage/toolbar"])'
    "{opacity:0;pointer-events:none;transition:opacity .12s ease-in-out}"
    '[data-testid="onyx-ai-message"]:hover '
    '[data-testid^="AgentMessage/"]:not([data-testid="AgentMessage/toolbar"]),'
    '[data-testid="onyx-ai-message"]:focus-within '
    '[data-testid^="AgentMessage/"]:not([data-testid="AgentMessage/toolbar"])'
    "{opacity:1;pointer-events:auto}"
)

# Tiffany Blue, the colour the selected sidebar row is filled with. White on it is a shade under
# 2.4:1, which is below WCAG's 4.5:1 for body text -- acceptable on a large, bold, single-line row
# that also carries a filled background as its own signal, and it is the Telegram treatment
# (accent fill, white label) rendered in the requested colour.
TIFFANY_BLUE: Final[str] = "#0ABAB5"
TIFFANY_BLUE_HOVER: Final[str] = "#09A19C"

# A white sidebar with a filled selected row, as Telegram draws it.
#
# Onyx tints the sidebar with `--background-tint-02`; only the column itself carries it, so one
# rule turns the whole panel white. The white is `--background-tint-00` -- Onyx's own token for a
# plain surface, `#fff` in light mode -- rather than a literal, so it follows a palette change.
# The rule is still scoped to `html:not(.dark)`, which leaves dark mode exactly as Onyx ships it
# rather than restyling a theme nobody asked about. The selected-row fill is left unscoped:
# Tiffany reads on either background.
#
# `data-interactive-state` is Onyx's own selection marker (`selected` against `empty`), and it sits
# on the same element as `data-interactive-variant`, so an attribute pair identifies the row
# without touching any other interactive surface in the app. The prefix match on the variant covers
# every sidebar flavour rather than just the `sidebar-heavy` one the chat list happens to use.
#
# These selectors mirror Onyx's own shape exactly -- `.interactive` prefix, `:hover:not([data-
# disabled])`, the JS-driven `[data-interaction=hover]` twin -- because they have to *outrank* it,
# and CSS breaks specificity ties by source order. Drop the leading class and Onyx's rule wins;
# drop the hover pair and the fill reverts to grey under the pointer.
#
# The label and icons are recoloured through `--interactive-foreground` and its icon twin rather
# than with `color`, because that is where Onyx reads them from -- `.interactive` sets
# `color: var(--interactive-foreground)` and transitions it. Setting the variables keeps the
# transition and reaches the icons; setting `color` would fight both.
SIDEBAR_CSS: Final[str] = (
    "html:not(.dark) .opal-sidebar-root__column"
    "{background-color:var(--background-tint-00)}"
    '.interactive[data-interactive-variant^="sidebar"][data-interactive-state="selected"]'
    f"{{background-color:{TIFFANY_BLUE};"
    "--interactive-foreground:#fff;--interactive-foreground-icon:#fff}"
    '.interactive[data-interactive-variant^="sidebar"][data-interactive-state="selected"]'
    ":hover:not([data-disabled]),"
    '.interactive[data-interactive-variant^="sidebar"][data-interactive-state="selected"]'
    "[data-interaction=hover]:not([data-disabled])"
    f"{{background-color:{TIFFANY_BLUE_HOVER}}}"
)


# The user's own message bubble, white instead of grey.
#
# Onyx fills it with `bg-background-tint-02`, the same raised-surface tint as the sidebar. Telegram
# puts its message bubbles on a plain white surface and separates them from the page with a soft
# shadow rather than a fill, which is what this does -- without the shadow a white bubble on the
# near-white chat background has no edge at all.
#
# `#onyx-human-message` is an id Onyx writes by hand on the message wrapper, so it both scopes the
# rule to user messages and supplies the specificity to beat the Tailwind utility on its own.
MESSAGE_BUBBLE_CSS: Final[str] = (
    "html:not(.dark) #onyx-human-message .bg-background-tint-02"
    "{background-color:var(--background-tint-00);"
    "box-shadow:0 1px 2px rgba(0,0,0,.08)}"
)

# The chat canvas, white instead of Onyx's `--background-tint-01` grey.
#
# Two rules, because the grey is painted twice. `body` carries it, and so does a full-height app
# shell stacked on top of `body` -- so whitening `body` alone changes a surface nothing can see and
# leaves the visible canvas exactly as grey as before. The shell uses Tailwind's `.bg-background`,
# a theme alias for the very same `--background-tint-01`.
#
# The shell is pinned by `.bg-background` *and* `.min-h-screen` rather than by the alias alone.
# Whitening the alias outright also reaches the citation chips inline in a response, which use it
# as their fill and then have nothing left to distinguish them from the text around them -- they
# go blank. Only the full-viewport-height surface is the canvas.
#
# This is what makes the white message bubble read as Telegram's rather than as a mistake: bubble
# and canvas are the same white, and the bubble is separated by its shadow alone.
CHAT_SURFACE_CSS: Final[str] = (
    "html:not(.dark) body"
    "{background-color:var(--background-tint-00)}"
    "html:not(.dark) .bg-background.min-h-screen"
    "{background-color:var(--background-tint-00)}"
)

# The per-message agent avatar, hidden.
#
# Onyx lays each assistant message out on a timeline: a fixed-width rail on the left holding the
# agent's avatar, and a content column beside it. The avatar is an SVG octagon with the agent's
# initial absolutely positioned inside -- all Tailwind utilities and no id or test id, so the one
# stable handle is the rail cell's arbitrary-value class, matched on the CSS variable it is built
# from rather than on the whole escaped class name.
#
# `visibility` rather than `display`, because the rail is what the header row and the indented
# message body are aligned against: removing it from layout slides the "Thought for Ns" header out
# from over the text it belongs to. Hiding it in place takes the avatar and keeps the grid.
AGENT_AVATAR_CSS: Final[str] = (
    '[data-testid="onyx-ai-message"] [class*="--timeline-rail-width"]'
    "{visibility:hidden}"
)

# Everything this module injects, in the order it is appended.
UI_OVERRIDES: Final[str] = (
    OVERRIDE_MARKER + HOVER_TOOLBAR_CSS + SIDEBAR_CSS + MESSAGE_BUBBLE_CSS
    + CHAT_SURFACE_CSS + AGENT_AVATAR_CSS
)


class OnyxUIOverrides:
    """
    Appends Dreamference's stylesheet overrides to a running Onyx web server container.
    """

    @classmethod
    def install(cls, container: Optional[str] = None) -> bool:
        """
        Writes the current override block into every compiled stylesheet, replacing any older one.

        Args:
            container (Optional[str]): Onyx web server container name; discovered if omitted.

        Returns:
            bool: True if at least one stylesheet carries the overrides afterwards.
        """
        target = container or OnyxBrandAssets.find_web_container()
        if not target:
            return False
        return cls.append_overrides(target) > 0

    @classmethod
    def append_overrides(cls, container: str) -> int:
        """
        Writes the override block into the container's stylesheets.

        Any previous block is cut at the marker and replaced, so this both installs the overrides
        and updates them. Counts stylesheets carrying the block afterwards rather than ones it just
        wrote, so a re-run over an already-patched container reports the same number, not zero.

        Args:
            container (str): Onyx web server container name.

        Returns:
            int: Number of stylesheets carrying the overrides.
        """
        script = (
            "const fs=require('fs'),path=require('path');"
            f"const CSS={json.dumps(UI_OVERRIDES)},MARK={json.dumps(OVERRIDE_MARKER)};"
            "let carrying=0;"
            "const walk=(d,dep)=>{if(dep>8)return;let e=[];"
            "try{e=fs.readdirSync(d,{withFileTypes:true})}catch(x){return}"
            "for(const f of e){const p=path.join(d,f.name);"
            "if(f.isDirectory()){if(!/node_modules|cache/.test(p))walk(p,dep+1);continue}"
            "if(!/\\.css$/.test(f.name))continue;"
            "let s='';try{s=fs.readFileSync(p,'utf8')}catch(x){continue}"
            "const i=s.indexOf(MARK),base=i<0?s:s.slice(0,i);"
            "if(base+CSS!==s){try{fs.writeFileSync(p,base+CSS)}catch(x){continue}}"
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
