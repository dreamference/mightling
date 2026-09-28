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

from dreamference.chat.onyx_brand_assets import (
    TIFFANY_BLUE, WEB_BUILD_DIR, OnyxBrandAssets,
)

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

# The selected sidebar row is filled with the brand colour itself, imported from the brand assets
# so that the row and the favicon cannot drift apart. White on it is a shade under 2.4:1, which is
# below WCAG's 4.5:1 for body text -- acceptable on a large, bold, single-line row that also
# carries a filled background as its own signal, and it is the Telegram treatment (accent fill,
# white label) rendered in the brand colour.
TIFFANY_BLUE_HOVER: Final[str] = "#09A19C"

# The brand colour at about a tenth strength, for surfaces that are tinted rather than filled.
TIFFANY_TINT: Final[str] = "#E4F7F6"

# The brand colour at about a twentieth strength, for the streaming caret. `TIFFANY_TINT` is the
# wrong tool there: a wash chosen to sit under a whole message bubble disappears entirely at
# 8x16 pixels, and a caret nobody can see is worse than a dark one.
TIFFANY_CARET: Final[str] = "#F4FCFC"

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


# The user's own message bubble, tinted instead of grey.
#
# Onyx fills it with `bg-background-tint-02`, the same raised-surface tint as the sidebar. It is
# refilled with a light wash of the brand colour, which is what makes the user's own turn findable
# at a glance down a white page. The shadow stays: the tint is faint enough that without it the
# bubble would have only the softest edge against the white canvas.
#
# `#onyx-human-message` is an id Onyx writes by hand on the message wrapper, so it both scopes the
# rule to user messages and supplies the specificity to beat the Tailwind utility on its own.
MESSAGE_BUBBLE_CSS: Final[str] = (
    "html:not(.dark) #onyx-human-message .bg-background-tint-02"
    f"{{background-color:{TIFFANY_TINT};"
    'box-shadow:0 1px 2px rgba(0,0,0,.08)}'
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
#
# The rule is deliberately *not* scoped to `[data-testid="onyx-ai-message"]`. Onyx sets that test id
# only once a message is complete, so scoping it there left the avatar on screen for the whole time
# an answer was streaming -- which is when it is most visible.
AGENT_AVATAR_CSS: Final[str] = (
    '[class*="--timeline-rail-width"]'
    "{visibility:hidden}"
)

# Message text at full black.
#
# Onyx's text tokens are black at partial alpha -- `--text-04` is `#000000bf` for body copy and
# `--text-05` is `#000000e5` for bold -- so message text renders as dark grey rather than black.
#
# This redefines the two tokens on the message containers instead of setting `color` on their
# contents. Nothing here has to outrank anything: `.text-text-04{color:var(--text-04)}` still wins
# as the rule, custom properties inherit, and the value it resolves is simply ours. Setting `color`
# instead would mean a selector per element that carries its own text class, and a blanket
# `*{color:#000}` would flatten the syntax highlighting in code blocks and the link colour with it.
#
# `--text-03` is deliberately left alone: that is the "Thought for Ns" meta label, chrome rather
# than message text. The light-mode scope is not optional here -- in dark mode these same tokens
# are *white* at partial alpha, and forcing them black would render every message invisible.
MESSAGE_TEXT_CSS: Final[str] = (
    'html:not(.dark) [data-testid="onyx-ai-message"],'
    "html:not(.dark) #onyx-human-message"
    "{--text-04:#000;--text-05:#000}"
)

# The sidebar header mark, hidden so the header is just the wordmark.
#
# `Logo` lays two inline SVGs side by side -- the 64x64 mark and the 152x64 wordmark -- and neither
# carries a class or an id worth selecting on. The viewBox does the work instead: it is Onyx's own
# artwork geometry, the same 64x64 grid `ONYX_LOGO_PATHS` in `onyx_brand_assets.py` already keys
# its path substitutions to, so the two would break together rather than one silently missing.
#
# Unscoped, because the same mark is drawn in more than one place -- the sidebar header and the
# foot of the account menu -- and it was asked for gone in both. The wordmark and the favicon carry
# the brand now; the trade is that the login page loses its mark too.
SIDEBAR_LOGO_CSS: Final[str] = (
    'svg[viewBox="0 0 64 64"]'
    "{display:none}"
)

# The "Add Model" button beside the model picker, removed.
#
# It opens a dialog for registering another LLM provider, which on a single-node deployment serving
# one local model is a dead end. The button has no test id, so it is pinned as the model picker's
# first child, and its trailing divider goes with it -- leaving the divider behind would put a
# rule between the model name and nothing at all.
MODEL_SELECTOR_CSS: Final[str] = (
    '[data-testid="model-selector"]>button:first-child,'
    '[data-testid="model-selector"]>.opal-divider-vertical'
    "{display:none}"
)

# The sidebar's Agents section, hidden.
#
# Hidden rather than removed: nothing is taken out of Onyx's bundle, and dropping this one constant
# from `UI_OVERRIDES` brings the section back on the next `puffin-admin puffin configure`.
#
# The section wrapper is a bare `flex flex-col` with no handle of its own, so it is selected by
# what it *contains* -- `:has()` on the More Agents entry, which does carry a test id. That reads
# as "the sidebar body section holding the agents list", which is what it is, and survives the
# heading being renamed or the entries changing.
AGENTS_SECTION_CSS: Final[str] = (
    '.opal-sidebar-body__content>*:has(>[data-testid="AppSidebar/more-agents"])'
    "{display:none}"
)

# The sidebar's Projects section, hidden. Same reasoning as the agents one: hidden, not removed.
#
# This section has no handle at all -- no id, no test id, no link -- so it is identified as the one
# `flex flex-col` section of the sidebar body that is *not* the agents section. That is a weaker
# grip than the rest of this file and worth knowing: a third section of the same shape would be
# caught by it too.
PROJECTS_SECTION_CSS: Final[str] = (
    ".opal-sidebar-body__content>div.flex.flex-col"
    ':not(:has([data-testid="AppSidebar/more-agents"]))'
    "{display:none}"
)

# The Projects group inside the search palette, hidden to match the sidebar.
#
# The palette is a flat list -- headings and entries are siblings, not a heading wrapping its
# group -- so the heading has to be hidden separately from the entries. It carries no attribute of
# its own, so it is selected as "whatever sits directly above New Project" with `:has(+ …)`, which
# is precisely what it is. The entries are the New Project action and any `project-<id>` rows.
SEARCH_PROJECTS_CSS: Final[str] = (
    '[data-command-menu-list] [data-command-item="new-project"],'
    '[data-command-menu-list] [data-command-item^="project-"],'
    '[data-command-menu-list] *:has(+[data-command-item="new-project"])'
    "{display:none}"
)

# Sidebar row labels at full black -- in the chat list only.
#
# Onyx dims an unselected row to `--text-03` (`#0000008c`, 55% black) through the same
# `--interactive-foreground` variable the selected row uses, so chat titles read as grey. This
# promotes the unselected and filled states to solid black, matching the message text.
#
# Scoped to `.opal-sidebar-body__content`, which is the chat list. New and Search live in the
# header and are handled by `SIDEBAR_HEADER_TEXT_CSS` instead, at the section-title colour: one
# selector covering both made the navigation as loud as the content.
#
# `--interactive-foreground-icon` is left alone: the request was the session names.
#
# Light-mode scope for the same reason as the message text -- in dark mode this variable resolves
# to white at partial alpha, and forcing it black would erase the sidebar.
SIDEBAR_TEXT_CSS: Final[str] = (
    "html:not(.dark) .opal-sidebar-body__content "
    '.interactive[data-interactive-variant^="sidebar"][data-interactive-state="empty"],'
    "html:not(.dark) .opal-sidebar-body__content "
    '.interactive[data-interactive-variant^="sidebar"][data-interactive-state="filled"]'
    "{--interactive-foreground:#000}"
)

# The sidebar header -- New, Search and the Puffin wordmark -- at the section-title colour.
#
# `--text-02` is what "Recents" is drawn in, so this is less a colour choice than pointing the
# header at the token the section titles already use: navigation chrome recedes, the chat titles
# below stay black, and the two cannot drift apart if the palette changes.
#
# The wordmark needs its own rule because it is an inline SVG whose paths carry a fill rather than
# text inheriting a colour. It is matched on its 152x64 viewBox -- Onyx's own artwork geometry, the
# same grip `SIDEBAR_LOGO_CSS` uses for the mark beside it.
SIDEBAR_HEADER_TEXT_CSS: Final[str] = (
    ".opal-sidebar-header "
    '.interactive[data-interactive-variant^="sidebar"]'
    "{--interactive-foreground:var(--text-02)}"
    '.opal-sidebar-header svg[viewBox="0 0 152 64"] path'
    "{fill:var(--text-02)}"
)

# The "Onyx v4.5.6 - Open Source AI Platform" line under the composer, hidden.
#
# Targeted as "the span in the page footer that wraps a link" rather than by hiding the `<footer>`
# outright: on some layouts the composer itself is rendered into that slot, and a rule that would
# take the input box with it is not one to leave lying around. The version line is the only anchor
# in there.
FOOTER_CSS: Final[str] = (
    ".opal-root-layout__footer span:has(a)"
    "{display:none}"
)

# The unread badge in the sidebar, in Tiffany rather than Onyx's blue.
#
# The badge carries its colour in an *inline* style, which no stylesheet can outrank without
# `!important` -- but the inline value is `var(--action-link-05)`, so redefining that variable in
# the sidebar's scope recolours it with an ordinary rule. Scoped to the sidebar column rather than
# `:root` because the same token is the app's link colour everywhere else.
NOTIFICATION_BADGE_CSS: Final[str] = (
    ".opal-sidebar-root__column"
    f"{{--action-link-05:{TIFFANY_BLUE}}}"
)

# The expand control on a collapsed sidebar, always visible.
#
# Onyx swaps the two: `__logo-rest` shows at rest and `__logo-fold` -- the "Open Sidebar" button --
# only appears on `:hover` of the column. On a collapsed sidebar that hides the one control that
# gets you back, so this pins the swap to the collapsed state instead of the pointer.
SIDEBAR_FOLDED_CSS: Final[str] = (
    ".opal-sidebar-root__column[data-folded=true] .opal-sidebar-header__logo-fold"
    "{display:flex}"
    ".opal-sidebar-root__column[data-folded=true] .opal-sidebar-header__logo-rest"
    "{display:none}"
)

# The chat header's Share button, hidden. Hidden rather than removed, like the agents and projects
# sections -- drop the constant and it comes back.
#
# It is the one control in that header with an `aria-label` of its own, which makes it the rare
# case where the handle needs no reasoning about structure at all.
SHARE_BUTTON_CSS: Final[str] = (
    '[aria-label="share-chat-button"]'
    "{display:none}"
)

# The model chip, hidden. Hidden, not removed -- drop the constant and it comes back.
#
# It was asked for on the composer's toolbar row first, and three attempts are recorded here because
# the reason they failed is a property of the DOM rather than of the attempts. CSS cannot reparent,
# and the chip is not inside the composer box -- it sits in a wrapper above it, and on the new-chat
# screen it is not even a sibling, a name prompt sits between them. `position:absolute` against an
# inherited containing block, the same against a named one, and staying in flow with `order` plus a
# negative margin each landed correctly on the chat page and wrongly on the new-chat page. Moving it
# for real means moving the element in the composer's JSX inside the bundle.
#
# The wrapper goes rather than the chip itself, so the row it occupied collapses instead of leaving
# a gap above the composer.
MODEL_CHIP_CSS: Final[str] = (
    'div:has(>[data-testid="model-selector"])'
    "{display:none}"
)

# Chat Preferences: the Chats card and the Memory card, hidden so the settings they hold stay at
# their defaults. Hidden, not removed, like the rest.
#
# The settings page nests four sections in a container, each section a heading plus a `.card`. That
# `.card` is the handle: `div:has(> .card)` matches a section and *not* its ancestors, because the
# container's own children are sections rather than cards -- the child combinator inside `:has()`
# is what keeps the match from climbing.
#
# The Chats section goes whole, heading included, since hiding its four rows would leave an empty
# card behind. Memory is trimmed rather than removed: its heading and card go, but the
# "Personal Preferences" label above it stays, because that label heads the group that Prompt
# Shortcuts and Voice also belong to.
#
# Both are additionally pinned by position. That is redundant with the structure on purpose: if
# Onyx reorders these sections the rules stop matching and the settings reappear, which is a
# visible failure rather than a silent one that hides the wrong card.
SETTINGS_SECTIONS_CSS: Final[str] = (
    "div:has(>.card):first-child"
    "{display:none}"
    "div:has(>.card):nth-child(2)>div.w-full,"
    "div:has(>.card):nth-child(2)>.card"
    "{display:none}"
    # On the General tab the second section is Appearance, and the child-hiding rule above
    # empties it without removing it -- a zero-height flex item that still spends two of the
    # pane's 32px gaps. It can be collapsed outright, but only there: on Chat Preferences the
    # same position holds Personal Preferences, so the section that carries a textarea is the
    # one that must survive. The divider below is Onyx's own line between Appearance and the
    # Danger Zone, orphaned once Appearance is gone. Both are scoped to the settings pane via
    # the nav's test id rather than left as bare structural guesses.
    'div:has(>[data-testid="settings-left-tab-navigation"])'
    '>:not([data-testid="settings-left-tab-navigation"])'
    ">div:has(>.card):nth-child(2):not(:has(textarea))"
    "{display:none}"
    'div:has(>[data-testid="settings-left-tab-navigation"])'
    '>:not([data-testid="settings-left-tab-navigation"])'
    ">.opal-divider"
    "{display:none}"
)

# The Accounts & Access tab in Settings, hidden. Password and MFA management is surplus on a
# single-user appliance where `puffin-admin puffin configure` owns the one account. The tab renders as a
# div stack with no href in the DOM (the route lives only in the router data), so the anchor is
# the nav's own test id plus the label the span carries verbatim in its `title` attribute; the
# `.relative` wrapper is the per-tab row, so hiding it removes the hover target and the row's
# height along with the text. The route itself stays reachable -- chrome removed, not capability.
ACCOUNTS_ACCESS_CSS: Final[str] = (
    '[data-testid="settings-left-tab-navigation"] '
    '.relative:has(span[title="Accounts & Access"])'
    "{display:none}"
)

# The app shell, stripped inside the settings modal. `onyx_ui_scripts.py` opens Settings in an
# iframe and a framed document marks its own <html> with `data-puffin-framed`; without this the
# modal would contain a miniature copy of the whole app, sidebar and all. Keyed on the attribute
# rather than on being an iframe because CSS cannot ask.
#
# The second half scopes the scrolling: left alone, the framed *document* scrolls, carrying the
# "Settings" header and the tab nav away with the content. Pinning the body and handing
# `overflow-y:auto` to the content pane alone needs the whole ancestor chain between them to be
# a min-height:0 flex column -- and every element on that chain is an anonymous Tailwind div, so
# the chain is addressed from the one stable handle on the page: each ancestor *containing* the
# tab nav (`:has(...)`) becomes a flex column, the nav's direct parent (`:has(>...)`) is the row
# and is excluded from that, and the row's other child is the pane that scrolls. If Onyx renames
# the test id the rules stop matching and the page scrolls whole again -- visible, not silent.
SETTINGS_MODAL_SHELL_CSS: Final[str] = (
    "html[data-puffin-framed] .opal-sidebar-root__column"
    "{display:none}"
    "html[data-puffin-framed],html[data-puffin-framed] body"
    "{height:100%;overflow:hidden}"
    "html[data-puffin-framed] body "
    '*:has([data-testid="settings-left-tab-navigation"])'
    ':not(:has(>[data-testid="settings-left-tab-navigation"]))'
    "{display:flex;flex-direction:column;flex:1 1 auto;min-height:0;overflow:hidden}"
    "html[data-puffin-framed] "
    'div:has(>[data-testid="settings-left-tab-navigation"])'
    "{flex:1 1 auto;min-height:0;overflow:hidden}"
    # `justify-content:flex-start` undoes the pane's own `justify-center`, which is harmless at
    # natural height but centers overflowing content once the height is constrained -- flexbox
    # puts the excess *above the scroll start*, where no amount of scrolling reaches it.
    "html[data-puffin-framed] "
    'div:has(>[data-testid="settings-left-tab-navigation"])'
    '>:not([data-testid="settings-left-tab-navigation"])'
    "{overflow-y:auto;min-height:0;justify-content:flex-start}"
    # The pane is itself a flex column, and a flex column with a constrained height *shrinks its
    # items* to fit rather than overflowing -- the sections compressed into each other instead
    # of producing a scrollbar. Overflow only exists if the children refuse to shrink. And
    # `height:auto` undoes the sections' own `h-full`, which resolves to nothing while the pane
    # is natural-height but inflates every section to one full pane-height once it is
    # constrained, centering each section's content in its own empty viewport.
    "html[data-puffin-framed] "
    'div:has(>[data-testid="settings-left-tab-navigation"])'
    '>:not([data-testid="settings-left-tab-navigation"])>*'
    "{flex-shrink:0;height:auto}"
)

# Chat Preferences sections promoted to settings tabs -- the styling half; the tabs themselves
# and the `data-puffin-section` marker are `onyx_ui_scripts.py`'s work (`SYNTHETIC_TABS`). The
# page is recognised by the textarea its Personal Preferences section carries -- the same
# signature `SETTINGS_SECTIONS_CSS` leans on. Sections are pinned by position from the *front*
# of the pane -- the hidden Chats placeholder is child 1, Personal Preferences child 2, Prompt
# Shortcuts child 3, Voice child 4 -- because the Gmail card is *appended* to the same pane by
# the connect script, so anything counted from the end moves the moment it arrives. If Onyx
# reorders the sections the wrong one shows under a tab -- visible, not silent. Outside any
# synthetic mode everything from child 3 on is hidden (the promoted sections and the injected
# Gmail elements live under their tabs); in a mode, only that slug's content shows, the real
# Chat Preferences pill -- selected in Onyx's eyes, the route matches -- is put back to rest,
# and the synthetic tab is lit in its place. The gmail slug shows the three elements the
# connect script injects rather than a native section, and the real Connectors route tab is
# hidden from the nav in favour of the synthetic one -- by every label it has carried, so a
# stale chunk cannot resurrect it -- while the route itself stays reachable by URL as a
# fallback.
SYNTHETIC_TABS_CSS: Final[str] = (
    "html:not([data-puffin-section]) "
    'div:has(>[data-testid="settings-left-tab-navigation"])>:not([data-testid="settings-left-tab-navigation"]):has(textarea)'
    ">*:nth-child(n+3)"
    "{display:none}"
    "html[data-puffin-section] "
    'div:has(>[data-testid="settings-left-tab-navigation"])>:not([data-testid="settings-left-tab-navigation"]):has(textarea)'
    ">*"
    "{display:none}"
    'html[data-puffin-section="chat"] '
    'div:has(>[data-testid="settings-left-tab-navigation"])>:not([data-testid="settings-left-tab-navigation"]):has(textarea)'
    ">*:nth-child(2)"
    "{display:flex}"
    'html[data-puffin-section="shortcuts"] '
    'div:has(>[data-testid="settings-left-tab-navigation"])>:not([data-testid="settings-left-tab-navigation"]):has(textarea)'
    ">*:nth-child(3)"
    "{display:flex}"
    'html[data-puffin-section="voice"] '
    'div:has(>[data-testid="settings-left-tab-navigation"])>:not([data-testid="settings-left-tab-navigation"]):has(textarea)'
    ">*:nth-child(4)"
    "{display:flex}"
    'html[data-puffin-section="gmail"] '
    'div:has(>[data-testid="settings-left-tab-navigation"])>:not([data-testid="settings-left-tab-navigation"]):has(textarea)'
    ">#puffin-connect-google-head"
    "{display:block}"
    # Margin zeroing: the card and button carry their own margins for the connectors-page
    # fallback, which has no flex gap; in this pane the 32px gap is the rhythm every native
    # section spaces by, and the margins stacked on top of it read as inconsistent indentation.
    # The card's own margins are inline (the script sets them per context -- an inline style is
    # unbeatable from here without !important), so only the button's stylesheet margin is
    # neutralised below; the card pulls itself up 20px in this pane so the heading sits 12px
    # above it, the .75rem a native section gives its title.
    'html[data-puffin-section="gmail"] '
    'div:has(>[data-testid="settings-left-tab-navigation"])>:not([data-testid="settings-left-tab-navigation"]):has(textarea)'
    ">#puffin-connect-google-card"
    "{display:block}"
    'html[data-puffin-section="gmail"] '
    'div:has(>[data-testid="settings-left-tab-navigation"])>:not([data-testid="settings-left-tab-navigation"]):has(textarea)'
    ">#puffin-connect-google"
    "{display:inline-block;margin-top:0}"
    "html[data-puffin-section] "
    '[data-testid="settings-left-tab-navigation"] '
    '.interactive-container[data-interactive-state="selected"]:not([id^="puffin-tab-"] *)'
    "{background-color:transparent}"
    "html[data-puffin-section] "
    '[data-testid="settings-left-tab-navigation"] '
    '.interactive-container[data-interactive-state="selected"]:not([id^="puffin-tab-"] *) span'
    "{color:#4b5563}"
    'html[data-puffin-section] [id^="puffin-tab-"] '
    '.interactive-container[data-interactive-state="selected"]'
    "{background-color:" + TIFFANY_BLUE + "}"
    'html[data-puffin-section] [id^="puffin-tab-"] '
    '.interactive-container[data-interactive-state="selected"] span'
    "{color:#fff}"
    '[data-testid="settings-left-tab-navigation"] '
    '.relative:has(span[title="General"]):not(#puffin-tab-general),'
    '[data-testid="settings-left-tab-navigation"] '
    '.relative:has(span[title="Chat Preferences"]):not(#puffin-tab-chat),'
    '[data-testid="settings-left-tab-navigation"] '
    '.relative:has(span[title="Gmail Accounts"]):not(#puffin-tab-gmail),'
    '[data-testid="settings-left-tab-navigation"] '
    '.relative:has(span[title="Google"]):not(#puffin-tab-gmail),'
    '[data-testid="settings-left-tab-navigation"] '
    '.relative:has(span[title="Connectors"]):not(#puffin-tab-gmail)'
    "{display:none}"
)

# The sliders icon above the Settings title, dropped. The word says everything the glyph did,
# and in the settings modal the stacked icon spent ~70px of a height-constrained header on
# decoration. Scoped by the title span so admin pages sharing `opal-content-xl` keep theirs;
# the icon row is hidden rather than the icon, because the row carries its own min-height.
SETTINGS_HEADER_ICON_CSS: Final[str] = (
    '[aria-label="admin-page-title"] '
    '.opal-content-xl:has(span[title="Settings"]) '
    ".opal-content-xl-icon-row"
    "{display:none}"
)

# The image search gallery -- the styling half; the tiles, badges and data-count are built by
# `onyx_ui_scripts.py`. Three commitments: raw /puffin-images/ embeds never paint (the images
# streamed in one by one and then jumped into a gallery; CSS applies before first paint, so
# hiding the originals unconditionally and re-showing only the gallery's and lightbox's own
# copies removes the flash entirely); counts 2-4 get the search-results mosaic, hero left and
# tiles right, keyed per count because CSS cannot span a row "n-1 times"; and five or more --
# a user-requested count -- switch to aspect-preserving masonry columns, because object-fit
# crops behead portraits at tile size. Source badges sit bottom-right per tile and hide
# themselves while empty (the metadata fetch fills them asynchronously).
GALLERY_CSS: Final[str] = 'img[src*="/puffin-images/"]{display:none}[data-puffin-gallery] img,#puffin-lightbox img{display:block}p:has(>img[src*="/puffin-images/"]){display:none}[data-puffin-gallery]{display:grid;gap:8px;margin:12px 0;max-width:720px;grid-template-columns:repeat(2,1fr)}[data-puffin-gallery] .puffin-tile{position:relative;overflow:hidden;border-radius:12px;cursor:pointer}[data-puffin-gallery] .puffin-tile img{width:100%;height:100%;object-fit:cover;margin:0;transition:transform .2s}[data-puffin-gallery] .puffin-tile:hover img{transform:scale(1.03)}[data-puffin-gallery] .puffin-badge{position:absolute;right:8px;bottom:8px;background:rgba(17,24,39,.65);color:#fff;font-size:11px;padding:2px 8px;border-radius:8px;max-width:70%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}[data-puffin-gallery] .puffin-badge:empty{display:none}[data-puffin-gallery][data-count="1"]{grid-template-columns:1fr;max-width:480px}[data-puffin-gallery][data-count="1"] .puffin-tile img{height:auto;object-fit:contain}[data-puffin-gallery][data-count="2"] .puffin-tile img{aspect-ratio:4/3}[data-puffin-gallery][data-count="3"],[data-puffin-gallery][data-count="4"]{grid-template-columns:2fr 1fr}[data-puffin-gallery][data-count="3"]{grid-auto-rows:150px}[data-puffin-gallery][data-count="3"] .puffin-tile:first-child{grid-row:1/span 2}[data-puffin-gallery][data-count="4"]{grid-auto-rows:110px}[data-puffin-gallery][data-count="4"] .puffin-tile:first-child{grid-row:1/span 3}[data-puffin-gallery][data-large]{display:block;columns:3 180px;column-gap:8px}[data-puffin-gallery][data-large] .puffin-tile{break-inside:avoid;margin-bottom:8px}[data-puffin-gallery][data-large] .puffin-tile img{height:auto;object-fit:unset}#puffin-img-progress{margin:10px 0 4px;max-width:420px}#puffin-img-progress .lbl{font-size:12px;color:#6b7280;margin-bottom:6px}#puffin-img-progress .track{height:6px;border-radius:3px;background:#e5e7eb;overflow:hidden}#puffin-img-progress .bar{height:100%;width:35%;border-radius:3px;background:#0ABAB5;animation:puffin-slide 1.2s ease-in-out infinite}@keyframes puffin-slide{0%{transform:translateX(-120%)}100%{transform:translateX(400%)}}'


# Log Out below the separator in the account menu, not above it.
#
# The menu is a flex column whose children are, in DOM order: email, divider, Settings,
# Notifications, Help & FAQ (hidden), Log Out, divider, version footer (hidden). Onyx draws
# the second divider *after* Log Out; moving the destructive action below the line is one
# flex `order` on the Log Out row -- every sibling keeps order 0, so it alone sorts past the
# divider, landing visually last (the two hidden rows do not paint). The row carries no
# test id, so it is pinned as third-from-last -- anchored from the end because appends are
# rarer than prepends in this menu's history; if Onyx reorders it, the wrong row drops below
# the line, which is visible, not silent. The menu container is found through the one child
# that does carry a test id.
USER_MENU_LOGOUT_CSS: Final[str] = (
    'div:has(>div>[data-testid="Settings/user-settings"])'
    ">div:nth-last-child(3)"
    "{order:1}"
)

# The account avatar inside a selected sidebar row, inverted.
#
# Onyx draws it as white initials on a black disc, which on the Tiffany fill of a selected row is a
# heavy black hole. Swapping it to black on white keeps the disc legible against the fill. The
# initials are recoloured on the disc *and* its descendants because the text carries its own colour
# class, which `color` on the parent alone would not override.
# History matters here: this rule originally *inverted* the disc to white-on-black-text,
# because a selected row was a Tiffany pill and the grey disc vanished against it. The footer
# row no longer paints on select (`SIDEBAR_ACCOUNT_CSS` renders that state as rest -- "selected"
# there just means the menu is open), so the inversion became white-on-white: an invisible disc
# beside a bare black letter. The rule now does the opposite of its first job -- it *holds* the
# rest look through the selected state, same grey disc, same white initial.
SIDEBAR_AVATAR_CSS: Final[str] = (
    '.interactive[data-interactive-variant^="sidebar"][data-interactive-state="selected"] '
    ".bg-background-neutral-inverted-00,"
    '.interactive[data-interactive-variant^="sidebar"][data-interactive-state="selected"] '
    ".bg-background-neutral-inverted-00 *"
    "{background-color:var(--text-02);color:#fff}"
)

# The "Help & FAQ" entry in the account menu, hidden.
#
# It links to docs.onyx.app, which is both off-brand and unreachable from an air-gapped machine.
# The link target is the handle: the entry has no id or test id, but a menu item pointing at a
# specific external host is unambiguous, and a sturdier grip than the item's position.
HELP_LINK_CSS: Final[str] = (
    '[href^="https://docs.onyx.app"]'
    "{display:none}"
)

# The account avatar in the sidebar footer: a grey disc showing one initial.
#
# Onyx draws it as a black disc with two initials. The disc takes `--text-02`, the same token as
# the name beside it, so the pair reads as one muted unit rather than a black dot on a white panel.
#
# Dropping the second initial is the one piece of text manipulation in this file. The initials are
# computed in JavaScript, so CSS cannot recompute them -- but it can decline to draw them: the span
# collapses to `font-size:0` and `::first-letter` is given the size back. That works only because
# Onyx already renders the span as a block; `::first-letter` does not apply to inline boxes.
#
# The span also needs a box to be centred in. Collapsed to zero font size it has neither width nor
# height: `width:100%; text-align:center` gives it the first, and `line-height` set to the disc's
# own size gives it the second. Without the line-height the glyph sits high in the circle, because
# a zero font-size means `line-height:normal` resolves to zero and there is no line box to centre
# the letter within.
#
# `!important` is needed on exactly one declaration here, and not for want of specificity: Onyx sizes
# the initials with an *inline* `font-size`, scaled to the disc. That is the same trap as the unread
# badge, but without the escape -- the badge's inline value reads a custom property, so redefining
# the property was enough; this one is a literal, and a literal inline declaration can only be
# outranked. The restored size on `::first-letter` needs no such thing, since inline styles do not
# reach pseudo-elements.
SIDEBAR_AVATAR_DISC_CSS: Final[str] = (
    ".opal-sidebar-root__column"
    "{--dream-avatar-initial-size:9px;--dream-avatar-disc-size:18px}"
    ".opal-sidebar-root__column .bg-background-neutral-inverted-00"
    "{background-color:var(--text-02)}"
    ".opal-sidebar-root__column .bg-background-neutral-inverted-00 span"
    "{font-size:0!important;width:100%;text-align:center;"
    "line-height:var(--dream-avatar-disc-size)}"
    # The letter's line box is the full disc height, not `1`: with a 1-height box the glyph
    # aligns by baseline against the span's 18px strut and lands measurably high (2.5px on an
    # 18px disc). A disc-height line box centres the glyph by its own half-leading instead.
    ".opal-sidebar-root__column .bg-background-neutral-inverted-00 span::first-letter"
    "{font-size:var(--dream-avatar-initial-size);"
    # The remaining offset comes from baseline metrics -- ascent and descent are not symmetric
    # around the glyph -- and `vertical-align` is one of the few properties ::first-letter
    # honours. -3px was dialled in against a live Range measurement of the glyph's box versus
    # the disc's (0.0px on both axes), not guessed; the response is nonlinear because the line
    # box redistributes half-leading as the baseline moves.
    "vertical-align:-3px;"
    "line-height:var(--dream-avatar-disc-size)}"
)

# The account name in the sidebar footer, matched to the section titles.
#
# It takes the section titles' size and weight (12px/400, down from Onyx's 14px/500) but black
# rather than their grey -- the name is the one piece of the footer worth reading. `span.truncate`
# is the label specifically: the avatar's initials sit in a span too, and this must not reach them.
SIDEBAR_ACCOUNT_CSS: Final[str] = (
    '.opal-sidebar-footer .interactive[data-interactive-variant^="sidebar"]'
    "{--interactive-foreground:#000}"
    '.opal-sidebar-footer .interactive[data-interactive-variant^="sidebar"] span.truncate'
    "{font-size:.75rem;font-weight:400}"
    # The account row is a popover trigger, and its "selected" state means the menu is open --
    # not a navigation state. Painting it Tiffany made the row read as stuck-highlighted the
    # whole time the menu (or the settings modal opened from it) was up, so in the footer that
    # state renders as rest. The hover variants are overridden too; the Tiffany hover rule
    # otherwise outranks this on specificity.
    '.opal-sidebar-footer .interactive[data-interactive-variant^="sidebar"]'
    '[data-interactive-state="selected"]'
    "{background-color:transparent;--interactive-foreground:#000;"
    "--interactive-foreground-icon:currentColor}"
    '.opal-sidebar-footer .interactive[data-interactive-variant^="sidebar"]'
    '[data-interactive-state="selected"]:hover:not([data-disabled]),'
    '.opal-sidebar-footer .interactive[data-interactive-variant^="sidebar"]'
    '[data-interactive-state="selected"][data-interaction=hover]:not([data-disabled])'
    "{background-color:transparent}"
)

# The sidebar's collapse control, removed so the sidebar stays open.
#
# `aria-label="Close Sidebar"` is the handle -- the button carries nothing else, and the label is
# written by hand rather than generated. `SIDEBAR_FOLDED_CSS` is what keeps this from being a trap:
# anyone whose sidebar is *already* collapsed still sees the expand control, so hiding the way in
# does not strand them with no way back.
SIDEBAR_CLOSE_CSS: Final[str] = (
    '[aria-label="Close Sidebar"]'
    "{display:none}"
)

# The chat list's scrollbar: permanent in the desktop app, revealed on hover in the browser.
#
# The default is the *visible* one and the hover behaviour is the special case, deliberately.
# Hiding depends on the engine marker `onyx_ui_scripts.py` writes onto `<html>`; if that script has
# not run, an "invisible until hover" default leaves a scroll container with no affordance at all.
# Defaulting to visible makes the failure mode a scrollbar that is merely always there.
#
# **Both styling systems are used, because the two engines disagree about which one wins.** Blink
# ignores `::-webkit-scrollbar` entirely whenever `scrollbar-color` is set, so there the standard
# properties do the work. WebKitGTK invented those pseudo-elements and honours them, and it draws a
# track hairline down the container that the standard `transparent` track colour does not remove --
# that is the grey line in the desktop app which the browser never showed. So the track is painted
# transparent both ways, and the thumb is given the same weight by both.
#
# `scrollbar-button` is hidden separately from the track: sharing one selector applied
# `display:none` to both and collapsed the scrollbar into the hairline this is meant to remove.
#
# The trough is painted rather than left transparent, and that is the fix for the line itself.
# WebKitGTK draws the trough's edge on the *scrollbar* element, not the track, and a transparent
# trough leaves that edge showing as a 1px rule about ten pixels to the left of the thumb -- which
# is exactly where it appeared. Painting both the scrollbar and its track in the surface colour
# hides the edge against the sidebar. `--background-tint-00` rather than a literal white, so it
# stays correct if the theme changes: this is the one rule here that would otherwise draw a white
# stripe down a dark sidebar.
#
# The width never changes between states, only the colour, so nothing reflows on hover.
SIDEBAR_SCROLLBAR_THUMB: Final[str] = "rgba(0,0,0,.125)"
SIDEBAR_SCROLLBAR_CSS: Final[str] = (
    ".opal-sidebar-body__scroll"
    f"{{scrollbar-width:thin;scrollbar-color:{SIDEBAR_SCROLLBAR_THUMB} var(--background-tint-00)}}"
    'html[data-puffin-engine="blink"] .opal-sidebar-body__scroll'
    "{scrollbar-color:transparent var(--background-tint-00)}"
    'html[data-puffin-engine="blink"] .opal-sidebar-body__scroll:hover'
    f"{{scrollbar-color:{SIDEBAR_SCROLLBAR_THUMB} var(--background-tint-00)}}"
    ".opal-sidebar-body__scroll::-webkit-scrollbar"
    "{width:8px;background-color:var(--background-tint-00);border:0;box-shadow:none}"
    ".opal-sidebar-body__scroll::-webkit-scrollbar-track"
    "{background-color:var(--background-tint-00);border:0;box-shadow:none}"
    ".opal-sidebar-body__scroll::-webkit-scrollbar-button"
    "{display:none}"
    ".opal-sidebar-body__scroll::-webkit-scrollbar-thumb"
    f"{{background-color:{SIDEBAR_SCROLLBAR_THUMB};border-radius:4px;border:0}}"
    # WebKitGTK draws its scrollbar as engine chrome, outside the page: a `get_snapshot` of the
    # rendered page has no non-white pixel anywhere near the sidebar's edge, yet the line is on
    # screen. Nothing in CSS reaches that trough -- the track colour, borders and box-shadow above
    # all leave it drawn -- so the only remedy the engine honours is to not draw a scrollbar at all.
    # The list still scrolls by wheel, trackpad and keyboard; it simply has no visible bar.
    'html[data-puffin-engine="webkit"] .opal-sidebar-body__scroll'
    "{scrollbar-width:none}"
)

# The vertical rule between the sidebar and the chat, removed.
#
# `.opal-divider-line-vertical` is Onyx's 1px vertical divider. The rule is unscoped because the
# sidebar-to-content one is the only place it is visible in this UI -- the model picker's copy is
# already hidden with the Add Model button. If a future version uses it inside a popover, this is
# the first rule to narrow.
DIVIDER_CSS: Final[str] = (
    ".opal-divider-line-vertical"
    "{display:none}"
)

# The Connect to Google button, which `onyx_ui_scripts.py` injects into Settings -> Connectors.
#
# Styled here rather than inline in the script so the button is described in the same place as the
# rest of the UI, and so it picks up the brand colour from one definition. It is in the DOM
# whenever nobody has consented to Gmail yet, which for a fresh install is everybody.
#
# `align-self:flex-start` is the one declaration doing real work. The section it is appended to is
# `flex flex-col items-center`, so an appended child is centred; the heading and the connector card
# above it are not, because they carry `w-full` and fill the row instead. Left-aligning the button
# puts it under the text it belongs to rather than adrift in the middle of the panel.
CONNECT_BUTTON_CSS: Final[str] = (
    # Styled as the standard primary action -- the near-black pill Onyx's own dialogs use
    # (the share sheet's "Create Share Link") -- rather than the brand teal, which read as
    # louder than any control Onyx ships on this page.
    "#puffin-connect-google"
    "{align-self:flex-start;margin-top:12px;padding:10px 18px;border-radius:12px;"
    "background-color:#111827;color:#fff;text-decoration:none;white-space:nowrap;"
    "font-size:.875rem;font-weight:600}"
    "#puffin-connect-google:hover"
    "{background-color:#1f2937}"
)

# The block caret that trails a streaming answer, lightened.
#
# It renders as `animate-pulse flex-none bg-theme-primary-05 … inline-block w-2 h-4`, so it takes
# the brand-neutral primary colour and lands as a solid dark block mid-sentence. `TIFFANY_CARET`
# puts it in the brand's own colour, light enough to read as a caret rather than as a word.
#
# This is the one rule keyed on Tailwind utilities, because the caret has no id, test id or BEM
# class of its own. It is pinned on the *combination* -- a pulsing 8x16 inline block in the primary
# colour -- since `bg-theme-primary-05` alone is also buttons and badges. If Onyx restyles the
# caret this stops matching, which shows up as a dark caret returning rather than as damage.
STREAMING_CURSOR_CSS: Final[str] = (
    ".animate-pulse.bg-theme-primary-05.inline-block.w-2.h-4"
    f"{{background-color:{TIFFANY_CARET}}}"
)

# The drawn scrollbar's thumb, which `onyx_ui_scripts.py` positions in the desktop app.
#
# Only appearance lives here; geometry is the script's job. `display:none` is the resting state --
# the script flips it per update, so a page where the script never ran shows nothing rather than a
# stray mispositioned bar. The hover shade gives the thumb drag affordance the flat colour lacks.
CUSTOM_SCROLLBAR_CSS: Final[str] = (
    "#puffin-scrollbar"
    f"{{position:fixed;width:6px;border-radius:3px;z-index:50;display:none;"
    f"background-color:{SIDEBAR_SCROLLBAR_THUMB}}}"
    "#puffin-scrollbar:hover,#puffin-scrollbar:active"
    "{background-color:rgba(0,0,0,.3)}"
)

# Everything this module injects, in the order it is appended.
UI_OVERRIDES: Final[str] = (
    OVERRIDE_MARKER + HOVER_TOOLBAR_CSS + SIDEBAR_CSS + MESSAGE_BUBBLE_CSS
    + CHAT_SURFACE_CSS + AGENT_AVATAR_CSS + MESSAGE_TEXT_CSS
    + SIDEBAR_LOGO_CSS + MODEL_SELECTOR_CSS + AGENTS_SECTION_CSS
    + PROJECTS_SECTION_CSS + SEARCH_PROJECTS_CSS + SIDEBAR_TEXT_CSS
    + SIDEBAR_HEADER_TEXT_CSS
    + FOOTER_CSS + NOTIFICATION_BADGE_CSS + SIDEBAR_FOLDED_CSS + SHARE_BUTTON_CSS
    + MODEL_CHIP_CSS + SETTINGS_SECTIONS_CSS + ACCOUNTS_ACCESS_CSS + SETTINGS_MODAL_SHELL_CSS + SIDEBAR_AVATAR_CSS + HELP_LINK_CSS
    + SIDEBAR_AVATAR_DISC_CSS + SIDEBAR_ACCOUNT_CSS + SIDEBAR_CLOSE_CSS
    + SIDEBAR_SCROLLBAR_CSS + DIVIDER_CSS + CONNECT_BUTTON_CSS
    + STREAMING_CURSOR_CSS + CUSTOM_SCROLLBAR_CSS + SYNTHETIC_TABS_CSS
    + SETTINGS_HEADER_ICON_CSS + GALLERY_CSS + USER_MENU_LOGOUT_CSS
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
