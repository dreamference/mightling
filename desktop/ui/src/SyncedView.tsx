// Other devices (specs/DREAMFERENCE_MIGHTLING_SYNC.md, Phases 1 and 2): every thread the user's other
// devices published to the node's sync hub, newest first and grouped by device, each opening on its
// transcript and then live (§18.4): the turn as it streams on its device, the approvals waiting, and
// the messages still queued. With `control` (every pairing's, unless narrowed to `watch`) the page
// answers too: a message with one of the device's models, Approve or Decline, Stop, a new thread in
// one of the device's folders, and archiving or deleting a thread everywhere. The device it runs on
// carries each of those out with its own policy, as if its user had acted there; whatever the hub or
// the device refuses is shown here in their words.

import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "./icons";
import { renderMarkdown } from "./markdown";
import {
  SETTLE_WITHIN, ago, approvalOf, approveAsk, awaitCommand, banner, byDevice, canControl, elsewhere, emptyLive, folderName, foldLive, hub, liveShown,
  queuedLabel, sendAsk, settleLive, startAsk, startDevices, stopAsk,
  type LiveAnswer, type LiveView, type SyncCommand, type SyncDevice, type SyncStatus, type SyncedItem, type SyncedThread, type WaitingRequest,
} from "./sync";

const api = hub();

const message = (error: unknown): string => (error instanceof Error ? error.message : String(error));
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

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
          await pause(15000);
        }
      }
    })();
    return () => { stopped = true; };
  }, [enabled, refresh]);
  return { status, count, refresh };
}

function Item({ item, streaming }: { item: SyncedItem; streaming?: boolean }) {
  switch (item.kind) {
    case "user":
      return <div className="synced-item user"><strong>You</strong><p>{item.text}</p></div>;
    case "agent":
      return <div className={`synced-item agent markdown${streaming ? " streaming" : ""}`} dangerouslySetInnerHTML={{ __html: renderMarkdown(item.text) }} />;
    case "command":
      return (
        <div className="synced-item command">
          <code>$ {item.text}</code>
          {item.exit_code !== null && item.exit_code !== undefined ? <span className="planned-note"> · exit {item.exit_code}</span> : item.status === "inProgress" ? <span className="planned-note"> · running</span> : null}
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

/** One approval waiting on the device: what it asks, and Approve or Decline with `control`. */
function Approval(props: { request: WaitingRequest; view: LiveView; owner: string; control: boolean; answering: boolean; answer: (request: WaitingRequest, decision: "accept" | "decline") => void }) {
  const asked = approvalOf(props.request, props.view);
  if (!asked) return <div className="approval"><p>{props.owner} is asking something only it can answer. Answer this on {props.owner}.</p></div>;
  return (
    <div className="approval">
      <strong>{asked.kind === "command" ? "Run this command?" : "Change these files?"}</strong>
      {asked.kind === "command"
        ? <pre>{asked.command || "(the command is on the device)"}</pre>
        : asked.files.length ? <ul className="synced-files">{asked.files.map((file) => <li key={file}>{file}</li>)}</ul> : <p>The files are listed on {props.owner}.</p>}
      {asked.kind === "command" && asked.cwd ? <p>In {asked.cwd}</p> : null}
      {asked.reason ? <p>{asked.reason}</p> : null}
      {props.control ? (
        <div className="buttons">
          <button className="primary" disabled={props.answering} onClick={() => props.answer(props.request, "accept")}>Approve</button>
          <button disabled={props.answering} onClick={() => props.answer(props.request, "decline")}>Decline</button>
        </div>
      ) : <p>Waiting for an answer on {props.owner}.</p>}
    </div>
  );
}

/**
 * An open thread from another device, live: the transcript, the turn as it streams (a long-poll of
 * `/api/sync/live/<thread>`, ended when the thread closes), and with `control` the composer, Stop
 * and the answers to its approvals.
 */
function LiveThread(props: { thread: SyncedThread; items: SyncedItem[]; control: boolean; reread: () => void }) {
  const { thread, items, control, reread } = props;
  const [view, setView] = useState<LiveView>(() => emptyLive(items));
  const [live, setLive] = useState<LiveAnswer | null>(null);
  const [text, setText] = useState("");
  const [model, setModel] = useState("");
  const [sending, setSending] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [failures, setFailures] = useState<{ id: number; what: string; why: string }[]>([]);
  // Approvals answered from here and not yet resolved on the device: one answer each.
  const [answering, setAnswering] = useState<string[]>([]);
  const itemsNow = useRef(items);
  itemsNow.current = items;
  const rereadNow = useRef(reread);
  rereadNow.current = reread;
  const closed = useRef(new AbortController());

  // The rollout takes over each finished turn once the transcript has its end.
  useEffect(() => setView((now) => settleLive(now, items)), [items]);

  useEffect(() => {
    const controller = new AbortController();
    closed.current = controller;
    const timers: ReturnType<typeof setTimeout>[] = [];
    void (async () => {
      let after = 0;
      let first = true;
      while (!controller.signal.aborted) {
        try {
          // The first read answers at once, so the banner shows before anything happens.
          const answer = await api.live(thread.thread, after, first ? 0 : 25, controller.signal);
          if (controller.signal.aborted) return;
          if (first || answer.reset) {
            setView(foldLive(emptyLive(itemsNow.current), answer.events, Date.now(), true));
            if (answer.reset) rereadNow.current();
          } else if (answer.events.length) {
            setView((now) => foldLive(now, answer.events));
          }
          if (answer.events.some((event) => event.message.method === "turn/completed")) {
            // The agent uploads the rollout within a second; read it then, and once more before
            // the finished turn is let go.
            rereadNow.current();
            timers.push(setTimeout(() => rereadNow.current(), 1500), setTimeout(() => rereadNow.current(), SETTLE_WITHIN + 500));
          }
          setLive(answer);
          after = answer.next;
          first = false;
        } catch {
          if (controller.signal.aborted) return;
          await pause(5000);
        }
      }
    })();
    return () => { controller.abort(); timers.forEach(clearTimeout); };
  }, [thread.thread]);

  const owner = live?.owner_name || thread.owner_name || thread.owner;
  const track = (command: SyncCommand, what: string) => {
    void awaitCommand(api, command, () => {}, { signal: closed.current.signal }).then((final) => {
      if (final.state !== "failed" && final.state !== "expired") return;
      setFailures((now) => [...now, { id: final.id, what, why: queuedLabel(final) }]);
      if (final.kind === "approve") setAnswering((now) => now.filter((id) => id !== JSON.stringify(final.request)));
    });
  };
  const ask = async (done: Promise<SyncCommand>, what: string): Promise<boolean> => {
    try {
      const command = await done;
      setRefusal(null);
      track(command, what);
      return true;
    } catch (error) {
      setRefusal(message(error));
      return false;
    }
  };
  const send = async () => {
    const words = text.trim();
    if (!words || sending) return;
    setSending(true);
    if (await ask(api.command(sendAsk(thread.thread, words, model)), `“${words.length > 60 ? `${words.slice(0, 60)}…` : words}”`)) setText("");
    setSending(false);
  };
  const answer = (request: WaitingRequest, decision: "accept" | "decline") => {
    const key = JSON.stringify(request.id);
    setAnswering((now) => [...now, key]);
    void ask(api.command(approveAsk(thread.thread, request, decision)), decision === "accept" ? "Approve" : "Decline")
      .then((sent) => { if (!sent) setAnswering((now) => now.filter((id) => id !== key)); });
  };
  // An approval the device resolved is no longer being answered.
  const pendingKeys = (live?.pending ?? []).map((request) => JSON.stringify(request.id)).join(" ");
  useEffect(() => {
    const waiting = new Set(pendingKeys.split(" "));
    setAnswering((now) => (now.every((id) => waiting.has(id)) ? now : now.filter((id) => waiting.has(id))));
  }, [pendingKeys]);
  const stop = () => void ask(api.command(stopAsk(thread.thread)), "Stop");

  const shown = liveShown(view, items);
  const queued = (live?.queued ?? []).filter((command) => command.kind === "send");
  return (
    <div className="synced-live">
      {live ? <p className={`synced-banner${live.online ? "" : " offline"}`} role="status">{banner({ ...live, owner_name: owner })}</p> : null}
      {shown.transcript.length === 0 && shown.live.length === 0 ? <p className="empty small">Nothing said yet.</p> : null}
      {shown.transcript.map((item, index) => <Item key={index} item={item} />)}
      {shown.live.map((item) => <Item key={item.id} item={item} streaming={item.streaming} />)}
      {view.error ? <p className="synced-error">{view.error}</p> : null}
      {(live?.pending ?? []).map((request) => <Approval key={String(request.id)} request={request} view={view} owner={owner} control={control} answering={answering.includes(JSON.stringify(request.id))} answer={answer} />)}
      {queued.length ? (
        <ul className="synced-queued" aria-label="Messages queued">
          {queued.map((command) => <li key={command.id}><span className="text">{command.text}</span><span className="planned-note">{queuedLabel(command)}</span></li>)}
        </ul>
      ) : null}
      {failures.map((failure) => (
        <p key={failure.id} className="synced-error">
          {failure.what}: {failure.why}{" "}
          <button className="link-button" onClick={() => setFailures((now) => now.filter((known) => known.id !== failure.id))}>Dismiss</button>
        </p>
      ))}
      {control ? (
        <div className="synced-composer">
          <textarea aria-label={`Message to ${owner}`} placeholder={`Message ${owner}`} value={text} rows={3}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void send(); } }} />
          <div className="buttons">
            {live?.models.length ? (
              <select aria-label="Model" value={model} onChange={(event) => setModel(event.target.value)}>
                <option value="">The thread's model</option>
                {live.models.map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            ) : null}
            <span className="spacer" />
            {live?.running ? <button onClick={stop}>Stop</button> : null}
            <button className="primary" disabled={!text.trim() || sending} onClick={() => void send()}>Send</button>
          </div>
        </div>
      ) : null}
      {refusal ? <p className="synced-error" role="alert">{refusal}</p> : null}
    </div>
  );
}

/** "New thread on <device>": one of the device's folders and the first message. */
function StartForm(props: { device: SyncDevice; onStarted: (thread: string | null) => void }) {
  const { device, onStarted } = props;
  const [cwd, setCwd] = useState(device.projects[0] ?? "");
  const [text, setText] = useState("");
  const [state, setState] = useState<{ busy: boolean; note: string | null; error: string | null }>({ busy: false, note: null, error: null });
  const closed = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    closed.current = controller;
    return () => controller.abort();
  }, []);
  const folder = device.projects.includes(cwd) ? cwd : (device.projects[0] ?? "");

  const start = async () => {
    const words = text.trim();
    if (!words || !folder) return;
    setState({ busy: true, note: `Starting on ${device.name}…`, error: null });
    try {
      const command = await api.command(startAsk(device.id, folder, words));
      const final = await awaitCommand(api, command, () => {}, { signal: closed.current?.signal });
      if (final.state === "done") {
        setText("");
        setState({ busy: false, note: `Started. It shows below once ${device.name} publishes it.`, error: null });
        onStarted(final.thread);
      } else if (final.state === "failed" || final.state === "expired") {
        setState({ busy: false, note: null, error: queuedLabel(final) });
      }
    } catch (error) {
      setState({ busy: false, note: null, error: message(error) });
    }
  };

  return (
    <details className="synced-start">
      <summary>New thread on {device.name}</summary>
      <label className="field">Folder
        <select value={folder} onChange={(event) => setCwd(event.target.value)}>
          {device.projects.map((project) => <option key={project} value={project}>{project}</option>)}
        </select>
      </label>
      <label className="field">Message
        <textarea rows={3} value={text} onChange={(event) => setText(event.target.value)} />
      </label>
      <div className="dialog-buttons">
        <button className="primary" disabled={state.busy || !text.trim()} onClick={() => void start()}>Start</button>
        {state.note ? <span className="planned-note" role="status">{state.note}</span> : null}
      </div>
      {state.error ? <p className="synced-error" role="alert">{state.error}</p> : null}
    </details>
  );
}

export function SyncedView(props: { status: SyncStatus; onClose: () => void; onChanged: () => void; notice: (error: unknown) => void }) {
  const { notice } = props;
  const [threads, setThreads] = useState<SyncedThread[] | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [opened, setOpened] = useState<{ thread: string; items: SyncedItem[] | null } | null>(null);
  const [status, setStatus] = useState(props.status);
  const [awaited, setAwaited] = useState<string | null>(null);
  const openedId = useRef<string | null>(null);
  openedId.current = opened?.thread ?? null;
  const control = canControl(props.status);

  const load = useCallback(() => {
    api.threads().then((answer) => setThreads(answer.threads)).catch((error) => { setThreads([]); notice(error); });
  }, [notice]);
  useEffect(load, [load]);

  // Which devices are online is presence, not a change the manifest reports: read it while open.
  useEffect(() => {
    const read = () => { api.status().then((found) => { if (found) setStatus(found); }).catch(() => {}); };
    read();
    const timer = setInterval(read, 20000);
    return () => clearInterval(timer);
  }, []);

  const reread = useCallback(() => {
    const shown = openedId.current;
    if (shown) api.read(shown).then((answer) => setOpened((now) => (now?.thread === shown ? { thread: shown, items: answer.items } : now))).catch(() => {});
  }, []);

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
            reread();
          }
        } catch {
          await pause(5000);
        }
      }
    };
    void follow();
    return () => { stopped = true; };
  }, [props.status.cursor, load, reread]);

  const open = useCallback((thread: string, toggle = true) => {
    if (toggle && openedId.current === thread) return setOpened(null);
    setOpened({ thread, items: null });
    api.read(thread).then((answer) => setOpened((current) => (current?.thread === thread ? { thread, items: answer.items } : current))).catch(notice);
  }, [notice]);

  // A thread started from here opens once its device has published it.
  useEffect(() => {
    if (awaited && threads?.some((thread) => thread.thread === awaited)) {
      setAwaited(null);
      open(awaited, false);
    }
  }, [awaited, threads, open]);

  const act = (done: Promise<unknown>) => done.then(() => { load(); props.onChanged(); }).catch(notice);
  const groups = byDevice(threads ?? [], props.status.this_device, showArchived);
  const starts = startDevices({ ...status, caller: props.status.caller });

  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog synced" role="dialog" aria-label="Other devices" onClick={(event) => event.stopPropagation()}>
        <header><h2>Other devices</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <p className="dialog-note">Threads your other devices synced to this node. Each runs on the device that holds its code; {control ? "from here you can follow a turn, answer it, or start a new one there, and archiving or deleting a thread does it on every device." : "this device may watch them, not answer or change them."}</p>
        {starts.map((device) => <StartForm key={device.id} device={device} onStarted={(thread) => { if (thread) setAwaited(thread); load(); }} />)}
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
                        {opened.items === null ? <p className="empty small">Reading the thread…</p> : <LiveThread key={thread.thread} thread={thread} items={opened.items} control={control} reread={reread} />}
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
