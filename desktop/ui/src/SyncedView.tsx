// Other devices (specs/DREAMFERENCE_MIGHTLING_SYNC.md, Phase 1): every thread the user's other
// devices published to the node's sync hub, newest first and grouped by device, each opening on its
// transcript, read only. With `control` (every pairing's, unless narrowed to `watch`) a thread can be
// archived or deleted everywhere; the device it runs on applies that with Codex's own commands.

import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "./icons";
import { renderMarkdown } from "./markdown";
import { ago, byDevice, elsewhere, folderName, hub, type SyncStatus, type SyncedItem, type SyncedThread } from "./sync";

const api = hub();

/**
 * Whether this page has the node's sync hub to read (`ling web` with `ling sync enable` on the node),
 * and how many unarchived threads other devices have there, kept current while the page is open.
 */
export function useSynced(enabled: boolean): { status: SyncStatus | null; count: number; refresh: () => void } {
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [count, setCount] = useState(0);
  const refresh = useCallback(() => {
    api.threads().then((answer) => setCount(elsewhere(answer.threads, answer.this_device))).catch(() => {});
  }, []);
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    void (async () => {
      const found = await api.status();
      if (stopped || !found) return;
      setStatus(found);
      refresh();
      let cursor = found.cursor;
      while (!stopped) {
        try {
          const next = await api.changed(cursor);
          if (next !== cursor) {
            cursor = next;
            if (!stopped) refresh();
          }
        } catch {
          await new Promise((resolve) => setTimeout(resolve, 15000));
        }
      }
    })();
    return () => { stopped = true; };
  }, [enabled, refresh]);
  return { status, count, refresh };
}

function Item({ item }: { item: SyncedItem }) {
  switch (item.kind) {
    case "user":
      return <div className="synced-item user"><strong>You</strong><p>{item.text}</p></div>;
    case "agent":
      return <div className="synced-item agent markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(item.text) }} />;
    case "command":
      return (
        <div className="synced-item command">
          <code>$ {item.text}</code>
          {item.exit_code !== null && item.exit_code !== undefined ? <span className="planned-note"> · exit {item.exit_code}</span> : null}
          {item.output ? <pre>{item.output}</pre> : null}
        </div>
      );
    case "files":
      return <div className="synced-item files planned-note">Changed: {item.text.split("\n").join(", ")}</div>;
    case "tool":
      return <div className="synced-item tool planned-note">{item.text}</div>;
    case "turn":
      return <div className="synced-item turn planned-note">{item.text}</div>;
  }
}

export function SyncedView(props: { status: SyncStatus; onClose: () => void; onChanged: () => void; notice: (error: unknown) => void }) {
  const { notice } = props;
  const [threads, setThreads] = useState<SyncedThread[] | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [opened, setOpened] = useState<{ thread: string; items: SyncedItem[] | null } | null>(null);
  const openedId = useRef<string | null>(null);
  openedId.current = opened?.thread ?? null;
  const control = props.status.caller.scope === "control";

  const load = useCallback(() => {
    api.threads().then((answer) => setThreads(answer.threads)).catch((error) => { setThreads([]); notice(error); });
  }, [notice]);
  useEffect(load, [load]);

  // Follows the hub while open: a thread another device publishes appears within a second of the
  // agent's upload, and an open transcript grows with it.
  useEffect(() => {
    let cursor = props.status.cursor;
    let stopped = false;
    const follow = async () => {
      while (!stopped) {
        try {
          const next = await api.changed(cursor);
          if (stopped) return;
          if (next !== cursor) {
            cursor = next;
            load();
            const shown = openedId.current;
            if (shown) api.read(shown).then((answer) => setOpened((now) => (now?.thread === shown ? { thread: shown, items: answer.items } : now))).catch(() => {});
          }
        } catch {
          await new Promise((resolve) => setTimeout(resolve, 5000));
        }
      }
    };
    void follow();
    return () => { stopped = true; };
  }, [props.status.cursor, load]);

  const open = (thread: string) => {
    if (opened?.thread === thread) return setOpened(null);
    setOpened({ thread, items: null });
    api.read(thread).then((answer) => setOpened((current) => (current?.thread === thread ? { thread, items: answer.items } : current))).catch(notice);
  };
  const act = (done: Promise<unknown>) => done.then(() => { load(); props.onChanged(); }).catch(notice);
  const groups = byDevice(threads ?? [], props.status.this_device, showArchived);

  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog synced" role="dialog" aria-label="Other devices" onClick={(event) => event.stopPropagation()}>
        <header><h2>Other devices</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <p className="dialog-note">Threads your other devices synced to this node. Each runs on the device that holds its code, so here it is read only; {control ? "archiving or deleting one does it on every device." : "this device may watch them, not change them."}</p>
        <label className="night-context"><input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} /> Archived threads</label>
        {threads === null ? <p className="empty small">…</p> : groups.length === 0 ? <p className="empty small">{showArchived ? "No archived threads from other devices." : "No threads from other devices yet. `ling sync enable` on another computer publishes its threads here."}</p> : groups.map((group) => (
          <Fragment key={group.device}>
            <h3>On {group.name}</h3>
            <table className="shortcut-table">
              <tbody>
                {group.threads.map((thread) => (
                  <Fragment key={thread.thread}>
                    <tr className={`synced-thread${opened?.thread === thread.thread ? " open" : ""}`}>
                      <td><button className="link-button" aria-expanded={opened?.thread === thread.thread} onClick={() => open(thread.thread)}>{thread.title}</button></td>
                      <td className="planned-note">{[folderName(thread.cwd), ago(thread.updated)].filter(Boolean).join(" · ")}</td>
                      <td className="shortcut-actions">
                        {control ? <button className="link-button" onClick={() => act(api.archive(thread.thread, !thread.archived))}>{thread.archived ? "Unarchive" : "Archive"}</button> : null}
                        {control ? <button className="link-button" onClick={() => {
                          if (window.confirm(`Delete "${thread.title}" on every device, ${group.name} included? This cannot be undone.`)) act(api.remove(thread.thread));
                        }}>Delete</button> : null}
                      </td>
                    </tr>
                    {opened?.thread === thread.thread ? (
                      <tr className="synced-transcript"><td colSpan={3}>
                        <p className="planned-note">Running on {group.name}; read only here.</p>
                        {opened.items === null ? <p className="empty small">Reading the thread…</p> : opened.items.length === 0 ? <p className="empty small">Nothing said yet.</p> : opened.items.map((item, index) => <Item key={index} item={item} />)}
                      </td></tr>
                    ) : null}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </Fragment>
        ))}
      </section>
    </div>
  );
}
