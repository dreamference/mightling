// The agent message board (DESKTOP_PARITY §2.13, Phase 3 item 10): the discussions subagents keep
// with each other (Codex's `ext/agent-message-board`, local backend only), read from its SQLite
// file by the app (desktop/electron/src/board.ts), read only, by agent tree and channel.

import { useEffect, useState } from "react";

import * as bridge from "./bridge";
import { discussions } from "./board";
import { Icon } from "./icons";
import { renderMarkdown } from "./markdown";
import type { Thread } from "./protocol/v2/Thread";

export function MessageBoardView(props: { sqliteHome: string | null; threads: Record<string, Thread>; onOpen: (threadId: string) => void; onClose: () => void; notice: (error: unknown) => void }) {
  const { sqliteHome, notice } = props;
  const [boards, setBoards] = useState<bridge.Board[] | null>(null);
  const [board, setBoard] = useState<string | null>(null);
  const [channel, setChannel] = useState<string | null>(null);
  const load = () => bridge.readBoards(sqliteHome).then((found) => {
    setBoards(found);
    setBoard((current) => current ?? found[0]?.board ?? null);
  }).catch((error) => { setBoards([]); notice(error); });
  useEffect(() => {
    void load();
    // read once when the view opens, and on Read again
  }, []);
  const shown = boards?.find((entry) => entry.board === board) ?? null;
  const name = (id: string) => props.threads[id]?.name || props.threads[id]?.preview || `Agents ${id.slice(0, 8)}`;
  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog board" role="dialog" aria-label="Message board" onClick={(event) => event.stopPropagation()}>
        <header><h2>Message board</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <p className="dialog-note">What subagents posted for each other, by the thread that started them, read from this machine's board file. Read only: the agents write it.</p>
        {boards === null ? <p className="empty small">Reading…</p> : boards.length === 0 ? <p className="empty small">Nothing has been posted yet.</p> : (
          <div className="board-columns">
            <nav className="board-nav" aria-label="Boards">
              {boards.map((entry) => (
                <div key={entry.board}>
                  <button className={`board-name${entry.board === board ? " selected" : ""}`} onClick={() => { setBoard(entry.board); setChannel(null); }}>{name(entry.board)}</button>
                  {entry.board === board ? entry.channels.map((item) => (
                    <button key={item.name} className={`board-channel${channel === item.name ? " selected" : ""}`} onClick={() => setChannel(channel === item.name ? null : item.name)}># {item.name} <span className="count">{item.posts}</span></button>
                  )) : null}
                </div>
              ))}
              <button className="link-button" onClick={() => void load()}>Read again</button>
            </nav>
            <div className="board-posts" role="feed" aria-label="Discussions">
              {shown && props.threads[shown.board] ? <button className="link-button" onClick={() => { props.onClose(); props.onOpen(shown.board); }}>Open the thread</button> : null}
              {shown ? discussions(shown.posts, channel).map((entry) => (
                <article key={entry.root.id} className="board-discussion">
                  {[entry.root, ...entry.replies].map((post) => (
                    <div key={post.id} className={`board-post${post === entry.root ? "" : " reply"}`}>
                      <div className="board-meta"><strong>{post.author}</strong> in #{post.channel} · {new Date(post.at).toLocaleString()}</div>
                      <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(post.text) }} />
                    </div>
                  ))}
                </article>
              )) : null}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
