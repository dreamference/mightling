// Quick Entry's box (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY_CLAUDE.md §4.2;
// desktop/electron/src/quick.ts): a question from anywhere. Enter sends it as a new Ask question in
// the app window, Shift+Enter starts a new line, Esc puts the box away. It may ask the main process
// for two things only: `quick/ask` and `quick/hide`.

import { useEffect, useRef, useState } from "react";

import { MightlingMark } from "./icons";

function send(message: object): Promise<unknown> {
  const bridge = window.electronBridge;
  if (!bridge) return Promise.reject(new Error("Quick Entry lives in the desktop app"));
  return bridge.sendMessageFromView(message) as Promise<unknown>;
}

/** What a key does in the box: send, hide, or nothing (typing). */
export function quickKey(event: { key: string; shiftKey: boolean; isComposing?: boolean }): "send" | "hide" | null {
  if (event.isComposing) return null;
  if (event.key === "Escape") return "hide";
  if (event.key === "Enter" && !event.shiftKey) return "send";
  return null;
}

export function QuickView() {
  const [text, setText] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    document.documentElement.classList.add("quick-page");
    field.current?.focus();
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { channel?: string } | null;
      if (data?.channel === "quick://open") {
        setText("");
        field.current?.focus();
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const ask = () => {
    const question = text.trim();
    if (!question) return;
    setText("");
    send({ type: "quick/ask", prompt: question }).catch(() => {});
  };

  return (
    <div className="quick-box">
      <div className="quick-row">
        <MightlingMark size={22} />
        <textarea ref={field} value={text} rows={3} placeholder="Ask Mightling…" aria-label="A quick question"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            const action = quickKey(event.nativeEvent);
            if (!action) return;
            event.preventDefault();
            if (action === "send") ask();
            else void send({ type: "quick/hide" }).catch(() => {});
          }} />
      </div>
      <p className="quick-hint">Enter asks it as a new question in Ask · Shift+Enter for a new line · Esc to close</p>
    </div>
  );
}
