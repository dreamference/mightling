// One terminal tab's screen (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.7, §3.5): xterm.js
// with its fit add-on, bundled, drawing what the hub routes to it and sending keystrokes and its
// size back through the hub (terminal.ts). Ctrl+L clears the screen as well as reaching the shell.

import { useEffect, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

import { keyAction, matches } from "./keymap";
import type { TerminalHub } from "./terminal";

/** The terminal's colours: the app's light tokens, read from the page so a theme change follows. */
function theme(): Record<string, string> {
  const css = getComputedStyle(document.documentElement);
  const token = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    background: token("--canvas", "#ffffff"),
    foreground: token("--text", "#0d0d0d"),
    cursor: token("--text", "#0d0d0d"),
    selectionBackground: token("--accent-soft", "#e8f7f6"),
  };
}

export function TerminalView(props: { hub: TerminalHub; processId: string; visible: boolean; focus: number }) {
  const host = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const fit = useRef<FitAddon | null>(null);
  const { hub, processId } = props;

  useEffect(() => {
    if (!host.current) return;
    const css = getComputedStyle(document.documentElement);
    const terminal = new Terminal({
      fontFamily: css.getPropertyValue("--font-code").trim() || "monospace",
      fontSize: 13,
      cursorBlink: true,
      scrollback: 5000,
      allowProposedApi: false,
      theme: theme(),
    });
    const fitter = new FitAddon();
    terminal.loadAddon(fitter);
    terminal.open(host.current);
    term.current = terminal;
    fit.current = fitter;
    terminal.attachCustomKeyEventHandler((event) => {
      if (event.type === "keydown" && matches(event, keyAction("clearTerminal"))) terminal.clear();
      // The window's own shortcuts are not typed into the shell.
      if (event.type === "keydown" && (matches(event, keyAction("terminal")) || matches(event, keyAction("bottomPanel")))) return false;
      return true;
    });
    const typed = terminal.onData((data) => void hub.write(processId, data).catch(() => {}));
    const resized = terminal.onResize(({ rows, cols }) => void hub.resize(processId, rows, cols).catch(() => {}));
    const detach = hub.attach(processId, (bytes) => terminal.write(bytes));
    const observer = new ResizeObserver(() => {
      try {
        if (host.current && host.current.offsetWidth > 0) fitter.fit();
      } catch {
        // Not laid out yet.
      }
    });
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      detach();
      typed.dispose();
      resized.dispose();
      terminal.dispose();
      term.current = null;
    };
  }, [hub, processId]);

  useEffect(() => {
    if (!props.visible) return;
    requestAnimationFrame(() => {
      try {
        fit.current?.fit();
      } catch {
        // Not laid out yet.
      }
      term.current?.focus();
    });
  }, [props.visible, props.focus]);

  return <div ref={host} className="terminal-host" style={{ display: props.visible ? "block" : "none" }} aria-label="Terminal" role="application" />;
}
