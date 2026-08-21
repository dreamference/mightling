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
# from `UI_OVERRIDES` brings the section back on the next `dream onyx configure`.
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
)

# The account avatar inside a selected sidebar row, inverted.
#
# Onyx draws it as white initials on a black disc, which on the Tiffany fill of a selected row is a
# heavy black hole. Swapping it to black on white keeps the disc legible against the fill. The
# initials are recoloured on the disc *and* its descendants because the text carries its own colour
# class, which `color` on the parent alone would not override.
SIDEBAR_AVATAR_CSS: Final[str] = (
    '.interactive[data-interactive-variant^="sidebar"][data-interactive-state="selected"] '
    ".bg-background-neutral-inverted-00,"
    '.interactive[data-interactive-variant^="sidebar"][data-interactive-state="selected"] '
    ".bg-background-neutral-inverted-00 *"
    "{background-color:#fff;color:#000}"
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
    "{--dream-avatar-initial-size:7.2px;--dream-avatar-disc-size:18px}"
    ".opal-sidebar-root__column .bg-background-neutral-inverted-00"
    "{background-color:var(--text-02)}"
    ".opal-sidebar-root__column .bg-background-neutral-inverted-00 span"
    "{font-size:0!important;width:100%;text-align:center;"
    "line-height:var(--dream-avatar-disc-size)}"
    ".opal-sidebar-root__column .bg-background-neutral-inverted-00 span::first-letter"
    "{font-size:var(--dream-avatar-initial-size);line-height:1}"
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

# The chat list's scrollbar, invisible until the pointer is in the sidebar.
#
# The gutter stays 8px wide at all times and only the *thumb* changes colour. Hiding the scrollbar
# by collapsing its width instead would reflow the whole chat list every time the pointer entered
# or left the sidebar, which is a worse distraction than the scrollbar was.
#
# Both engines are addressed because both are in play: Chromium and WebKitGTK take the
# `::-webkit-scrollbar` pseudo-elements, Firefox takes `scrollbar-color`. `scrollbar-button` is
# hidden outright -- WebKitGTK draws stepper arrows that no other surface in this UI has.
SIDEBAR_SCROLLBAR_CSS: Final[str] = (
    ".opal-sidebar-body__scroll"
    "{scrollbar-width:thin;scrollbar-color:transparent transparent}"
    ".opal-sidebar-body__scroll:hover"
    "{scrollbar-color:rgba(0,0,0,.25) transparent}"
    ".opal-sidebar-body__scroll::-webkit-scrollbar"
    "{width:8px}"
    ".opal-sidebar-body__scroll::-webkit-scrollbar-track,"
    ".opal-sidebar-body__scroll::-webkit-scrollbar-button"
    "{background:transparent;display:none}"
    ".opal-sidebar-body__scroll::-webkit-scrollbar-thumb"
    "{background-color:transparent;border-radius:4px;transition:background-color .15s ease-in-out}"
    ".opal-sidebar-body__scroll:hover::-webkit-scrollbar-thumb"
    "{background-color:rgba(0,0,0,.25)}"
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

# The Connect to Google button, which `onyx_ui_scripts.py` injects into the sidebar footer.
#
# Styled here rather than inline in the script so the button is described in the same place as the
# rest of the UI, and so it picks up the brand colour from one definition. It is only in the DOM
# when a Google client is configured and nobody has consented yet.
CONNECT_BUTTON_CSS: Final[str] = (
    "#puffin-connect-google"
    f"{{display:block;margin:4px 8px 8px;padding:8px 10px;border-radius:8px;"
    f"background-color:{TIFFANY_BLUE};color:#fff;text-align:center;text-decoration:none;"
    "font-size:.8125rem;font-weight:500}"
    "#puffin-connect-google:hover"
    f"{{background-color:{TIFFANY_BLUE_HOVER}}}"
)

# Everything this module injects, in the order it is appended.
UI_OVERRIDES: Final[str] = (
    OVERRIDE_MARKER + HOVER_TOOLBAR_CSS + SIDEBAR_CSS + MESSAGE_BUBBLE_CSS
    + CHAT_SURFACE_CSS + AGENT_AVATAR_CSS + MESSAGE_TEXT_CSS
    + SIDEBAR_LOGO_CSS + MODEL_SELECTOR_CSS + AGENTS_SECTION_CSS
    + PROJECTS_SECTION_CSS + SEARCH_PROJECTS_CSS + SIDEBAR_TEXT_CSS
    + SIDEBAR_HEADER_TEXT_CSS
    + FOOTER_CSS + NOTIFICATION_BADGE_CSS + SIDEBAR_FOLDED_CSS + SHARE_BUTTON_CSS
    + MODEL_CHIP_CSS + SETTINGS_SECTIONS_CSS + SIDEBAR_AVATAR_CSS + HELP_LINK_CSS
    + SIDEBAR_AVATAR_DISC_CSS + SIDEBAR_ACCOUNT_CSS + SIDEBAR_CLOSE_CSS
    + SIDEBAR_SCROLLBAR_CSS + DIVIDER_CSS + CONNECT_BUTTON_CSS
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
