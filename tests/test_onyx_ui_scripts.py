import json
import os
import shutil
import subprocess
import textwrap
from unittest.mock import patch

import pytest

from dreamference.chat import OnyxUIScripts
from dreamference.chat.gmail_search_service import CONNECT_PATH as SERVICE_CONNECT_PATH
from dreamference.chat.onyx_ui_scripts import (
    ANCHOR_CLASS, BUTTON_ID, CONNECT_GOOGLE_SCRIPT, CONNECT_PATH, SCRIPT_MARKER,
    SECTION_HEADING, UI_SCRIPTS,
)


def test_script_is_guarded_against_taking_the_app_down():
    # A throwing top-level statement in a bundle chunk breaks the whole application. No button is
    # worth that, so the block is wrapped and runs at most once.
    assert CONNECT_GOOGLE_SCRIPT.startswith(";(function(){try{")
    assert CONNECT_GOOGLE_SCRIPT.endswith("}catch(e){}})();")
    assert "window.__mlingConnect" in CONNECT_GOOGLE_SCRIPT


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
    # -- against a stub of the Connectors page, in the three states it distinguishes.
    harness = tmp_path / "harness.js"
    harness.write_text(textwrap.dedent("""
        let appended = null;
        const section = { appendChild: (el) => { appended = el; } };
        const heading = {
          textContent: '%s',
          closest: (sel) => sel === 'div.w-full' ? { parentElement: section } : null,
        };
        global.window = {};
        global.navigator = { userAgent: 'Mozilla/5.0 AppleWebKit/605.1.15 Safari/605.1.15' };
        global.location = { pathname: '%s' };
        let engine = null;
        global.document = {
          documentElement: { setAttribute: (k, v) => { engine = k + '=' + v; } },
          readyState: 'complete',
          querySelectorAll: (sel) =>
            sel === '.opal-content-md-title-row span' ? [heading] : [],
          getElementById: (id) => (appended && appended.id === id) ? appended : null,
          createElement: () => ({ style: {}, remove() { appended = null; } }),
          addEventListener: () => {},
        };
        global.setInterval = () => 0;
        let served = null;
        global.fetch = () => Promise.resolve({ json: () => Promise.resolve(served) });
        const script = %s;

        async function scenario(status, pathname) {
          appended = null; global.window = {}; served = status;
          global.location = { pathname: pathname || '%s' };
          eval(script);
          await new Promise(r => setImmediate(r));
          await new Promise(r => setImmediate(r));
          return appended;
        }
        (async () => {
          const shown = await scenario({configured: true, connected: false});
          const connected = await scenario({configured: true, connected: true});
          const unconfigured = await scenario({configured: false, connected: false});
          const elsewhere = await scenario({configured: false, connected: false}, '/app');
          console.log(JSON.stringify({
            engine,
            shown: shown !== null,
            text: shown && shown.textContent,
            href: shown && shown.href,
            id: shown && shown.id,
            whenConnected: connected !== null,
            connectedText: connected && connected.textContent,
            whenUnconfigured: unconfigured !== null,
            onAnotherPage: elsewhere !== null,
          }));
        })();
    """) % (
        SECTION_HEADING, CONNECT_PATH, json.dumps(CONNECT_GOOGLE_SCRIPT), CONNECT_PATH,
    ))

    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout.strip().splitlines()[-1])

    # A WebKitGTK user agent has no "Chrome/", so the desktop app is marked as webkit -- which is
    # what the scrollbar rule keys on.
    assert outcome["engine"] == "data-mightling-engine=webkit"
    assert outcome["shown"] is True
    assert outcome["text"] == "Connect to Google"
    assert outcome["id"] == BUTTON_ID
    assert outcome["href"].endswith(SERVICE_CONNECT_PATH)
    # Once connected, the ask changes rather than disappears: the card lists the linked
    # accounts and the link offers one more.
    assert outcome["whenConnected"] is True
    assert outcome["connectedText"] == "Connect another Google account"
    # `configured` is not part of the gate. It reports the same thing as `connected` -- a usable
    # token either exists or it does not -- and the pair survives only because the script reads
    # the status object as a whole.
    assert outcome["whenUnconfigured"] is True
    # And it belongs to one page, not to the whole app.
    assert outcome["onAnotherPage"] is False


def test_engine_is_marked_before_anything_else_runs():
    # The desktop app renders through WebKitGTK and the browser through Blink, against the same
    # stylesheets. CSS cannot ask which engine it is in and there is no honest `@supports`
    # discriminator between the two, so the script puts it on `<html>`.
    from dreamference.chat.onyx_ui_scripts import ENGINE_ATTRIBUTE

    marker = f'setAttribute("{ENGINE_ATTRIBUTE}"'
    assert marker in CONNECT_GOOGLE_SCRIPT
    # Set before the button logic, so a stylesheet keyed on it applies as early as this runs.
    assert CONNECT_GOOGLE_SCRIPT.index(marker) < CONNECT_GOOGLE_SCRIPT.index("function apply")
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
    assert "window.__mlingScrollbar" in SCROLLBAR_SCRIPT


def test_drawn_scrollbar_survives_react_recreating_the_chat_list():
    # React replaces the chat list wholesale on re-render, so the script re-queries the container
    # on every update instead of holding a reference that would go stale. Scroll events do not
    # bubble, so the listener must be in the capture phase.
    from dreamference.chat.onyx_ui_scripts import SCROLLBAR_SCRIPT

    assert "function sc(){return document.querySelector" in SCROLLBAR_SCRIPT
    assert "addEventListener('scroll',upd,true)" in SCROLLBAR_SCRIPT
    # The interval is the fallback for what no event announces: content changing the height.
    assert "setInterval(upd,1000)" in SCROLLBAR_SCRIPT


def test_the_button_lives_on_the_connectors_page_beside_onyx_own_connectors():
    # It was in the sidebar footer first, beside the account name. It belongs with the connectors
    # it would be one of, and the settings route is where Onyx already lists them.
    from dreamference.chat.onyx_ui_overrides import CONNECT_BUTTON_CSS

    assert CONNECT_PATH == "/app/settings/connectors"
    assert "location.pathname!==PATH" in CONNECT_GOOGLE_SCRIPT
    assert "p.appendChild(a)" in CONNECT_GOOGLE_SCRIPT
    # Nothing is left behind in the footer, in the markup or in the layout.
    assert "opal-sidebar-footer" not in CONNECT_GOOGLE_SCRIPT
    assert "opal-sidebar-footer" not in CONNECT_BUTTON_CSS


def test_the_section_is_found_by_its_heading_rather_than_its_classes():
    # The section is `div.flex.flex-col…` holding `div.w-full` and an `.opal-card` -- all Tailwind
    # utilities, none of them a name anyone chose. The heading is the only stable handle, and the
    # search is scoped to `.opal-content-md-title-row` because the settings nav carries a second
    # node with the same text. "Gmail Accounts" is the post-rewrite heading -- `onyx_ui_labels.py`
    # renames the shipped "Connectors" label, and this constant must match what actually renders.
    assert SECTION_HEADING == "Gmail Accounts"
    assert "'.opal-content-md-title-row span'" in CONNECT_GOOGLE_SCRIPT
    assert "closest('div.w-full')" in CONNECT_GOOGLE_SCRIPT


def test_placement_is_re_checked_because_route_changes_fire_no_event():
    # Navigation inside the app is client-side: nothing loads, and no event this script can see
    # fires. A short interval is what makes the button appear on arriving at the page; it stays
    # cheap because `panel()` compares `location.pathname` before touching the DOM.
    from dreamference.chat.onyx_ui_scripts import PLACE_INTERVAL_MS, POLL_INTERVAL_MS

    assert f"setInterval(apply,{PLACE_INTERVAL_MS})" in CONNECT_GOOGLE_SCRIPT
    # The status fetch stays slow; only the placement check is frequent.
    assert PLACE_INTERVAL_MS < POLL_INTERVAL_MS
    assert f"setInterval(check,{POLL_INTERVAL_MS})" in CONNECT_GOOGLE_SCRIPT


def test_the_link_opens_a_new_tab_only_where_new_tabs_work():
    # Tauri leaves wry's `new_window_req_handler` unset unless a window is built in Rust with
    # `on_new_window`, and wry only connects WebKitGTK's `create` signal when that handler exists.
    # A `target=_blank` click in the desktop app is therefore silently inert -- no window, no
    # error, nothing. The browser keeps the new tab; the app navigates in place.
    assert "a.target=ENG==='blink'?'_blank':'_self';" in CONNECT_GOOGLE_SCRIPT


def test_the_service_pages_offer_a_way_back():
    # Navigating in place means the setup page replaces the chat, and it carries no chrome of its
    # own. Without this link the desktop user is stranded on a bare paragraph.
    import inspect

    from dreamference.chat.gmail_search_service import GmailSearchService, ONYX_ORIGIN

    source = inspect.getsource(GmailSearchService.serve)
    assert '<a href="{ONYX_ORIGIN}/app">Back to Mightling</a>' in source
    assert ONYX_ORIGIN == "http://localhost:3000"


@pytest.mark.skipif(not shutil.which("node"), reason="node is needed to execute the injected script")
def test_settings_opens_in_a_modal_instead_of_navigating(tmp_path):
    # Executed for real in both of the states the script distinguishes: at the top level a click
    # on a settings anchor must be swallowed and become an overlay holding an iframe of the same
    # route, and inside a frame the script must only mark the document -- the marked state is
    # what the shell-stripping CSS keys on -- and install no interceptor at all.
    from dreamference.chat.onyx_ui_scripts import (
        FRAMED_ATTRIBUTE,
        SETTINGS_MODAL_ID,
        SETTINGS_MODAL_SCRIPT,
    )

    harness = tmp_path / "harness.js"
    harness.write_text(textwrap.dedent("""
        const FRAMED = %s;
        function fresh(framed) {
          const listeners = {};
          const doc = {
            documentElement: { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } },
            body: { appendChild(el) { doc._overlay = el; } },
            _overlay: null,
            addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
            removeEventListener() {},
            getElementById(id) { return doc._overlay && doc._overlay.id === id ? doc._overlay : null; },
            createElement(tag) {
              return { tagName: tag.toUpperCase(), style: {}, children: [],
                       appendChild(c) { this.children.push(c); }, addEventListener() {},
                       setAttribute() {}, remove() { doc._overlay = null; } };
            },
            dispatchEvent() {},
          };
          global.document = doc;
          global.window = framed ? { top: 1, self: 2 } : { top: 1, self: 1 };
          global.KeyboardEvent = function (type, opts) { this.type = type; Object.assign(this, opts); };
          eval(%s);
          return { listeners, doc };
        }

        const top = fresh(false);
        const anchor = { tagName: 'A', getAttribute: (k) => (k === 'href' ? '/app/settings' : null),
                         parentNode: null };
        const ev = { target: anchor, prevented: false, stopped: false,
                     preventDefault() { this.prevented = true; },
                     stopPropagation() { this.stopped = true; } };
        top.listeners.click[0](ev);
        const overlay = top.doc._overlay;
        const frame = overlay && overlay.children[0] && overlay.children[0].children[0];
        const framed = fresh(true);
        console.log(JSON.stringify({
          prevented: ev.prevented,
          stopped: ev.stopped,
          overlayId: overlay && overlay.id,
          frameSrc: frame && frame.src,
          topMarked: FRAMED in top.doc.documentElement.attrs,
          framedMarked: framed.doc.documentElement.attrs[FRAMED] === "1",
          framedIntercepts: !!(framed.listeners.click && framed.listeners.click.length),
        }));
    """) % (json.dumps(FRAMED_ATTRIBUTE), json.dumps(SETTINGS_MODAL_SCRIPT)))

    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout.strip().splitlines()[-1])

    assert outcome["prevented"] is True
    assert outcome["stopped"] is True
    assert outcome["overlayId"] == SETTINGS_MODAL_ID
    assert outcome["frameSrc"] == "/app/settings"
    # The top-level document is never marked; only a framed one strips its own shell.
    assert outcome["topMarked"] is False
    assert outcome["framedMarked"] is True
    assert outcome["framedIntercepts"] is False


@pytest.mark.skipif(not shutil.which("node"), reason="node is needed to execute the injected script")
def test_only_the_image_search_response_json_is_hidden(tmp_path):
    # The sweep anchors on the payload -- /puffin-images/ plus the tool's response keys --
    # because only this tool's JSON carries them; every other code block, including other
    # tools' Response payloads, stays visible.
    from dreamference.chat.onyx_ui_scripts import IMAGE_TOOL_STEP_SCRIPT

    harness = tmp_path / "harness.js"
    harness.write_text(textwrap.dedent("""
        function el(text) {
          return { textContent: text, style: {}, previousElementSibling: null,
                   closest: function (sel) { return sel === 'pre' ? this : null; } };
        }
        const label = el('Response');
        const ours = el('{"response": "![x](/puffin-images/abc.jpg)", "instructions": "..."}');
        ours.previousElementSibling = label;
        const other = el('{"response": "plain web search payload"}');
        const code = el('print(1)');
        let tick = null;
        global.window = {};
        global.setInterval = (fn) => { tick = fn; return 0; };
        global.document = { querySelectorAll: () => [ours, other, code] };
        eval(%s);
        tick();
        console.log(JSON.stringify({
          oursHidden: ours.style.display === 'none',
          labelHidden: label.style.display === 'none',
          otherHidden: other.style.display === 'none',
          codeHidden: code.style.display === 'none',
        }));
    """) % (json.dumps(IMAGE_TOOL_STEP_SCRIPT),))

    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    assert outcome["oursHidden"] is True
    assert outcome["labelHidden"] is True
    assert outcome["otherHidden"] is False
    assert outcome["codeHidden"] is False
