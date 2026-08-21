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
def test_button_appears_only_when_a_client_is_configured_but_unconnected(tmp_path):
    # The injected script is real JavaScript that runs in a browser, so it is tested by running it
    # -- against a stub DOM, in the three states it distinguishes.
    harness = tmp_path / "harness.js"
    harness.write_text(textwrap.dedent("""
        let appended = null;
        const footer = { prepend: (el) => { appended = el; } };
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
    # Nothing to ask for once connected, and nothing to connect to without a client.
    assert outcome["whenConnected"] is False
    assert outcome["whenUnconfigured"] is False


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
