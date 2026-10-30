// Quick Entry's box (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY_CLAUDE.md §4.2;
// desktop/electron/src/quick.ts): a question from anywhere. Enter sends it as a new Ask question in
// the app window, Shift+Enter starts a new line, Esc puts the box away. Below it, the five most recent
// Ask questions, each a click away (DESKTOP §16 B24). It may ask the main process for four things
// only: `quick/ask`, `quick/hide`, `quick/recent` and `quick/open` (a question it listed).

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

/** A recent question as the main process lists it; anything else is dropped. */
export function recentQuestions(value: unknown): { id: string; title: string }[] {
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is { id: string; title: string } =>
    typeof entry === "object" && entry !== null && typeof (entry as { id?: unknown }).id === "string" && typeof (entry as { title?: unknown }).title === "string").slice(0, 5);
}

export function QuickView() {
  const [text, setText] = useState("");
  const [recent, setRecent] = useState<{ id: string; title: string }[]>([]);
  const field = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    document.documentElement.classList.add("quick-page");
    field.current?.focus();
    const refresh = () => void send({ type: "quick/recent" }).then((list) => setRecent(recentQuestions(list))).catch(() => {});
    refresh();
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { channel?: string } | null;
      if (data?.channel === "quick://open") {
        setText("");
        refresh();
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
    // Not sent: the question comes back to the box (unless something new was typed meanwhile).
    send({ type: "quick/ask", prompt: question }).catch(() => setText((now) => now || question));
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
      {recent.length ? (
        <nav className="quick-recent" aria-label="Recent questions">
          {recent.map((question) => (
            <button key={question.id} title={question.title} onClick={() => void send({ type: "quick/open", thread: question.id }).catch(() => {})}>{question.title}</button>
          ))}
        </nav>
      ) : null}
    </div>
  );
}
