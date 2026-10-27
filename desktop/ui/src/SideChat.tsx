// The side chat's pane (DESKTOP §16 B37, sidechatstate.ts): beside the thread, its messages, a box to write
// in, **Keep as a thread** and Close. Nothing in it is saved until it is kept; closed, it is gone.

import { useEffect, useRef, useState } from "react";

import { Icon } from "./icons";
import { renderMarkdown } from "./markdown";
import type { SideMessage } from "./sidechatstate";

export interface SideChatProps {
  /** The thread it was opened beside. */
  title: string;
  /** How many of the thread's latest messages its first message carries. */
  recapped: number;
  messages: SideMessage[];
  running: boolean;
  /** Keeping is under way (or the chat has nothing to keep yet). */
  keepDisabled: boolean;
  onSend: (text: string) => void;
  onKeep: () => void;
  onClose: () => void;
}

export function SideChat(props: SideChatProps) {
  const [draft, setDraft] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => box.current?.focus(), []);
  useEffect(() => end.current?.scrollIntoView?.({ block: "end" }), [props.messages.length, props.messages[props.messages.length - 1]?.text]);
  const send = () => {
    const text = draft.trim();
    if (!text) return;
    props.onSend(text);
    setDraft("");
  };
  return (
    <aside className="side-chat" aria-label="Side chat">
      <header>
        <Icon name="messageSquare" size={14} />
        <strong>Side chat</strong>
        <span className="side-chat-of" title={props.title}>{props.title}</span>
        <span className="spacer" />
        <button className="link-button" disabled={props.keepDisabled} onClick={props.onKeep} title="Copy this side chat into a saved thread">Keep as a thread</button>
        <button className="icon" aria-label="Close the side chat" title="Close: the side chat is deleted" onClick={props.onClose}><Icon name="x" size={14} /></button>
      </header>
      <div className="side-chat-turns">
        <p className="side-chat-note" role="note">
          Not saved: closing it deletes it. {props.recapped ? `Your first message carries this thread's recap, its ${props.recapped} latest message${props.recapped > 1 ? "s" : ""}.` : "This thread has said nothing yet, so there is no recap to carry."}
        </p>
        {props.messages.map((message) => message.role === "user" ? (
          <div key={message.id} className="side-chat-message user">{message.text}</div>
        ) : (
          <div key={message.id} className="side-chat-message agent markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(message.text) }} />
        ))}
        {props.running ? <div className="side-chat-message agent working" role="status">Working…</div> : null}
        <div ref={end} />
      </div>
      <div className="side-chat-composer">
        <textarea ref={box} value={draft} rows={2} placeholder="Ask beside the thread…" aria-label="Side chat message"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              send();
            } else if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              props.onClose();
            }
          }} />
        <button className="icon" aria-label="Send to the side chat" disabled={!draft.trim()} onClick={send}><Icon name="arrowUp" size={16} /></button>
      </div>
    </aside>
  );
}
