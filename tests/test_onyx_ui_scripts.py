import json
import os
import shutil
import subprocess
import textwrap
from unittest.mock import patch

import pytest

from dreamference.chat import OnyxUIScripts
from dreamference.chat.onyx_ui_scripts import (
    ANCHOR_CLASS, BUTTON_ID, CONNECT_GOOGLE_SCRIPT, SCRIPT_MARKER, UI_SCRIPTS,
)


def test_script_is_guarded_against_taking_the_app_down():
    # A throwing top-level statement in a bundle chunk breaks the whole application. No button is
    # worth that, so the block is wrapped and runs at most once.
    assert CONNECT_GOOGLE_SCRIPT.startswith(";(function(){try{")
    assert CONNECT_GOOGLE_SCRIPT.endswith("}catch(e){}})();")
    assert "window.__puffinConnect" in CONNECT_GOOGLE_SCRIPT


def test_scripts_go_only_into_chunks_that_render_the_anchor():
    # The script attaches to the sidebar footer, so it is appended to the chunks that render it
    # rather than to every bundle: it arrives when the element it needs exists, and a page without
    # a sidebar never runs it.
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "6"
        assert OnyxUIScripts.append_scripts("web") == 6

    injected = run.call_args[0][0][-1]
    assert f"ANCHOR={json.dumps(ANCHOR_CLASS)}" in injected
    assert "if(!base.includes(ANCHOR))continue;" in injected
    # Same replace-in-place contract as the stylesheet block.
    assert "indexOf(MARK)" in injected
    assert UI_SCRIPTS.startswith(SCRIPT_MARKER)


@pytest.mark.skipif(not shutil.which("node"), reason="node is needed to execute the injected script")
def test_button_is_offered_to_anyone_who_has_not_connected_gmail(tmp_path):
    # The injected script is real JavaScript that runs in a browser, so it is tested by running it
    # -- against a stub DOM, in the three states it distinguishes.
    harness = tmp_path / "harness.js"
    harness.write_text(textwrap.dedent("""
        let appended = null;
        const footer = { appendChild: (el) => { appended = el; } };
        global.window = {};
        global.navigator = { userAgent: 'Mozilla/5.0 AppleWebKit/605.1.15 Safari/605.1.15' };
        let engine = null;
        global.document = {
          documentElement: { setAttribute: (k, v) => { engine = k + '=' + v; } },
          readyState: 'complete',
          querySelector: (sel) => sel === '.%s' ? footer : null,
          getElementById: (id) => (appended && appended.id === id) ? appended : null,
          createElement: () => ({ remove() { appended = null; } }),
          addEventListener: () => {},
        };
        global.setInterval = () => 0;
        let served = null;
        global.fetch = () => Promise.resolve({ json: () => Promise.resolve(served) });
        const script = %s;

        async function scenario(status) {
          appended = null; global.window = {}; served = status;
          eval(script);
          await new Promise(r => setImmediate(r));
          await new Promise(r => setImmediate(r));
          return appended;
        }
        (async () => {
          const shown = await scenario({configured: true, connected: false});
          const connected = await scenario({configured: true, connected: true});
          const unconfigured = await scenario({configured: false, connected: false});
          console.log(JSON.stringify({
            engine,
            shown: shown !== null,
            text: shown && shown.textContent,
            href: shown && shown.href,
            id: shown && shown.id,
            whenConnected: connected !== null,
            whenUnconfigured: unconfigured !== null,
          }));
        })();
    """) % (ANCHOR_CLASS, json.dumps(CONNECT_GOOGLE_SCRIPT)))

    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout.strip().splitlines()[-1])

    # A WebKitGTK user agent has no "Chrome/", so the desktop app is marked as webkit -- which is
    # what the scrollbar rule keys on.
    assert outcome["engine"] == "data-puffin-engine=webkit"
    assert outcome["shown"] is True
    assert outcome["text"] == "Connect to Google"
    assert outcome["id"] == BUTTON_ID
    assert outcome["href"].endswith("/oauth/start")
    # Nothing left to ask for once connected.
    assert outcome["whenConnected"] is False
    # But someone who has never configured a client is exactly who the button is for: gating on
    # `configured` as well showed it only to people already half-way through the setup. The click
    # is not a dead end -- `/oauth/start` answers an unconfigured request with the command to run.
    assert outcome["whenUnconfigured"] is True


def test_engine_is_marked_before_anything_else_runs():
    # The desktop app renders through WebKitGTK and the browser through Blink, against the same
    # stylesheets. CSS cannot ask which engine it is in and there is no honest `@supports`
    # discriminator between the two, so the script puts it on `<html>`.
    from dreamference.chat.onyx_ui_scripts import ENGINE_ATTRIBUTE

    marker = f'setAttribute("{ENGINE_ATTRIBUTE}"'
    assert marker in CONNECT_GOOGLE_SCRIPT
    # Set before the button logic, so a stylesheet keyed on it applies as early as this runs.
    assert CONNECT_GOOGLE_SCRIPT.index(marker) < CONNECT_GOOGLE_SCRIPT.index("function place")
    assert 'Chrome' in CONNECT_GOOGLE_SCRIPT


def test_block_is_written_on_its_own_line():
    # Turbopack ends its chunks with a `//# debugId=…` line comment and no trailing newline, so a
    # block appended directly onto the end lands inside that comment and never executes. The script
    # was silently inert for exactly this reason -- no error, no attribute, no button -- and it took
    # an offscreen WebKitGTK probe to notice, because nothing about the page looked wrong.
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "6"
        OnyxUIScripts.append_scripts("web")

    injected = run.call_args[0][0][-1]
    assert r"replace(/\n+$/,'')" in injected
    assert "base+'\\n'+JS" in injected


def test_drawn_scrollbar_replaces_the_suppressed_native_one():
    # WebKitGTK's native scrollbar carries a trough line no CSS colour reaches, so the stylesheet
    # sets `scrollbar-width:none` there and this script draws the bar instead. Blink keeps its
    # native hover-revealed scrollbar, so the script must refuse to run there.
    from dreamference.chat.onyx_ui_scripts import SCROLLBAR_SCRIPT, UI_SCRIPTS

    assert SCROLLBAR_SCRIPT in UI_SCRIPTS
    assert "!=='webkit')return" in SCROLLBAR_SCRIPT
    # Same containment contract as the connect script: guarded, wrapped, run-once.
    assert SCROLLBAR_SCRIPT.startswith(";(function(){try{")
    assert SCROLLBAR_SCRIPT.endswith("}catch(e){}})();")
    assert "window.__puffinScrollbar" in SCROLLBAR_SCRIPT


def test_drawn_scrollbar_survives_react_recreating_the_chat_list():
    # React replaces the chat list wholesale on re-render, so the script re-queries the container
    # on every update instead of holding a reference that would go stale. Scroll events do not
    # bubble, so the listener must be in the capture phase.
    from dreamference.chat.onyx_ui_scripts import SCROLLBAR_SCRIPT

    assert "function sc(){return document.querySelector" in SCROLLBAR_SCRIPT
    assert "addEventListener('scroll',upd,true)" in SCROLLBAR_SCRIPT
    # The interval is the fallback for what no event announces: content changing the height.
    assert "setInterval(upd,1000)" in SCROLLBAR_SCRIPT


def test_button_is_appended_beside_the_account_row_rather_than_above_it():
    # The footer's one child is the account row, so appending puts the button after the name. The
    # footer is laid out as a flex row in the stylesheet to make "after" mean "to the right of".
    from dreamference.chat.onyx_ui_overrides import CONNECT_BUTTON_CSS

    assert "f.appendChild(a)" in CONNECT_GOOGLE_SCRIPT
    assert "f.prepend(a)" not in CONNECT_GOOGLE_SCRIPT
    assert ".opal-sidebar-footer{display:flex;align-items:center" in CONNECT_BUTTON_CSS


def test_the_account_row_is_selected_structurally_not_by_its_classes():
    # It is `div > #onyx-user-dropdown > div.relative > button.interactive > …` -- four wrappers of
    # Onyx's own naming above the name. Keying the flex rule on any of them would break on a
    # rename; "whatever else is in the footer" does not. `min-width:0` is what lets the row shrink:
    # a flex item floors at its content width otherwise, and the truncating name span inside would
    # push the button off the sidebar's edge instead of ellipsing.
    from dreamference.chat.onyx_ui_overrides import CONNECT_BUTTON_CSS

    assert ".opal-sidebar-footer>*:not(#puffin-connect-google){flex:1 1 auto;min-width:0}" \
        in CONNECT_BUTTON_CSS
    assert "interactive" not in CONNECT_BUTTON_CSS


def test_the_link_opens_a_new_tab_only_where_new_tabs_work():
    # Tauri leaves wry's `new_window_req_handler` unset unless a window is built in Rust with
    # `on_new_window`, and wry only connects WebKitGTK's `create` signal when that handler exists.
    # A `target=_blank` click in the desktop app is therefore silently inert -- no window, no
    # error, nothing. The browser keeps the new tab; the app navigates in place.
    assert "a.target=ENG==='blink'?'_blank':'_self';" in CONNECT_GOOGLE_SCRIPT


def test_the_service_pages_offer_a_way_back():
    # Navigating in place means the OAuth pages replace the chat, and they carry no chrome of their
    # own. Without this link the desktop user is stranded on a bare paragraph.
    import inspect

    from dreamference.chat.gmail_search_service import GmailSearchService, ONYX_ORIGIN

    source = inspect.getsource(GmailSearchService.serve)
    assert '<a href="{ONYX_ORIGIN}/app">Back to Puffin</a>' in source
    assert ONYX_ORIGIN == "http://localhost:3000"
