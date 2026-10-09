// The Mightling UI (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md, specs/DREAMFERENCE_MIGHTLING_ASK.md):
// Ask and Work, one page in two hosts. Ask is questions with no project, each thread in a scratch
// folder the host's policy layer creates; Work is projects and their threads. The selected
// thread's turns stream in the middle, approvals inline, and a composer starts a turn, steers a
// running one, or stops it. Everything goes through `ling app-server`.
//
// Ask needs the policy layer that turns the `ask` prompt name into its text and confines the
// thread to its folder. Both hosts have it, from the same `policy.json`: `ling web`
// (ling-rs/web/src/policy.rs) and the desktop app's main process (desktop/electron/src/policy.ts).
// So both show Ask by default and Work one click away (`#work`).

import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type ClipboardEvent } from "react";

import { ASK_PROMPT, askThreads, askTitle, attachmentKind, attachmentName, isAskThread, turnInput, type Attached } from "./ask";
import * as bridge from "./bridge";
import { copyText } from "./clipboard";
import { renderMarkdown } from "./markdown";
import { RpcClient, type ParamsOf } from "./rpc";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { ServerRequest } from "./protocol/ServerRequest";
import type { PermissionProfileSummary } from "./protocol/v2/PermissionProfileSummary";
import type { Thread } from "./protocol/v2/Thread";
import type { ThreadItem } from "./protocol/v2/ThreadItem";
import type { UserInput } from "./protocol/v2/UserInput";
import {
  contextUse, initialState, permissionChoices, projects, reduce,
  type PendingRequest, type ThreadView, type TurnView,
} from "./store";

const CLIENT_INFO = { name: "mightling_desktop", title: "Mightling", version: "0.1.0" };

/** Server requests this window answers by asking the user; the rest are answered without asking. */
const ASKED = new Set<ServerRequest["method"]>([
  "item/commandExecution/requestApproval",
  "item/fileChange/requestApproval",
  "item/permissions/requestApproval",
  "item/tool/requestUserInput",
  "execCommandApproval",
  "applyPatchApproval",
]);

const text = (value: string): UserInput => ({ type: "text", text: value, text_elements: [] });

type View = "ask" | "work";

const HOST = bridge.host();

function initialView(): View {
  return typeof location !== "undefined" && location.hash === "#work" ? "work" : "ask";
}

/** A file picked or pasted for the next question, not yet in the thread's folder. */
interface PendingFile {
  id: number;
  file: File;
  name: string;
  kind: "image" | "file";
  preview: string | null;
}

let nextPendingId = 1;

export function App() {
  const [state, dispatch] = useReducer(reduce, initialState);
  const client = useMemo(() => new RpcClient(bridge.bridgeTransport), []);
  const [servedModel, setServedModel] = useState<string | null>(null);
  const [askRoot, setAskRoot] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<PermissionProfileSummary[]>([]);
  const [profile, setProfile] = useState(":workspace");
  const [airgap, setAirgap] = useState<bridge.Airgapped | null>(null);
  const [newCwd, setNewCwd] = useState("");
  const [draft, setDraft] = useState("");
  const [view, setView] = useState<View>(initialView);
  const [search, setSearch] = useState("");
  const [found, setFound] = useState<{ thread: Thread; snippet: string }[] | null>(null);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const [pending, setPending] = useState<PendingFile[]>([]);
  const [sending, setSending] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  // Dictation (specs/DREAMFERENCE_MIGHTLING_ASK.md §7): only where the page may record at all.
  const recorder = useRef<MediaRecorder | null>(null);
  const [dictation, setDictation] = useState<"idle" | "recording" | "transcribing">("idle");
  const canDictate = useMemo(() => bridge.canRecord(), []);
  const notice = useCallback((error: unknown) => dispatch({ type: "notice", message: error instanceof Error ? error.message : String(error) }), []);
  /** Shows Ask or Work: the view buttons, and in the app its menu. */
  const showView = useCallback((next: View) => {
    setView(next);
    setSearch("");
    dispatch({ type: "select", threadId: null });
    if (typeof history !== "undefined") history.replaceState(null, "", next === "work" ? "#work" : "#");
  }, []);

  const answerUnasked = useCallback((request: ServerRequest) => {
    switch (request.method) {
      case "currentTime/read":
        return client.respond(request.id, { currentTimeAt: Math.floor(Date.now() / 1000) });
      case "mcpServer/elicitation/request":
        return client.respond(request.id, { action: "decline", content: null, _meta: null });
      default:
        return client.respondError(request.id, -32601, `Mightling does not handle ${request.method} here`);
    }
  }, [client]);

  const connect = useCallback(async () => {
    dispatch({ type: "server", status: "starting" });
    try {
      const started = await bridge.startServer();
      setServedModel(started.served_model);
      setAskRoot(started.ask_root ?? null);
      await client.request("initialize", { clientInfo: CLIENT_INFO, capabilities: { experimentalApi: true, requestAttestation: false } });
      await client.initialized();
      dispatch({ type: "server", status: "ready" });
      const [list, permissions, level, target] = await Promise.all([
        client.request("thread/list", { limit: 200 }),
        client.request("permissionProfile/list", {}),
        bridge.airgapped(null),
        bridge.workTarget(),
      ]);
      dispatch({ type: "threads", list: list.data });
      setProfiles(permissions.data);
      setAirgap(level);
      // A browser has no launch target: `ling web` answers null.
      if (target?.cwd) setNewCwd(target.cwd);
      if (target?.thread) {
        const resumed = await client.request("thread/resume", { threadId: target.thread, model: started.served_model });
        dispatch({ type: "opened", thread: resumed.thread });
      }
    } catch (error) {
      notice(error);
    }
  }, [client, notice]);

  // The bridge's events, then the server; once, for the life of the window.
  const connected = useRef(false);
  useEffect(() => {
    client.onNotification = (notification) => dispatch({ type: "notification", notification });
    client.onServerRequest = (request) => {
      if (ASKED.has(request.method)) dispatch({ type: "serverRequest", request });
      else answerUnasked(request).catch(notice);
    };
    let stop: (() => void) | undefined;
    bridge.listenBridge({
      view: (next) => showView(next),
      message: (message) => client.receive(message),
      stderr: (line) => dispatch({ type: "stderr", line }),
      protocolError: (line) => dispatch({ type: "protocolError", line }),
      exit: (code) => {
        client.fail(`the agent's server exited${code === null ? "" : ` with code ${code}`}`);
        dispatch({ type: "server", status: "exited" });
      },
    }).then((unlisten) => {
      stop = unlisten;
      if (!connected.current) {
        connected.current = true;
        void connect();
      }
    }).catch(notice);
    return () => stop?.();
  }, [client, connect, answerUnasked, notice, showView]);

  const selectedView: ThreadView | null = state.selected ? state.threads[state.selected] ?? null : null;
  // Each view shows only its own kind of thread.
  const selected = selectedView && isAskThread(selectedView.thread, askRoot) === (view === "ask") ? selectedView : null;

  // The level can differ per thread (`/airgapped` in the TUI writes a session file).
  useEffect(() => {
    if (state.server === "ready") bridge.airgapped(state.selected).then(setAirgap).catch(notice);
  }, [state.selected, state.server, notice]);

  const choices = permissionChoices(profiles, airgap?.level === "on");
  useEffect(() => {
    if (choices.find((choice) => choice.id === profile)?.disabled) setProfile(":workspace");
  }, [choices, profile]);

  // Search over every thread's text (`thread/search`), Ask threads only in the Ask list.
  useEffect(() => {
    const term = search.trim();
    if (!term || state.server !== "ready") {
      setFound(null);
      return;
    }
    let current = true;
    const timer = setTimeout(() => {
      client.request("thread/search", { searchTerm: term, limit: 50 })
        .then((answer) => {
          if (current) setFound(answer.data.filter((result) => (view === "ask") === isAskThread(result.thread, askRoot)));
        })
        .catch(notice);
    }, 250);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [search, view, askRoot, state.server, client, notice]);

  // Pasted or picked images show a preview until they are sent or removed.
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  useEffect(() => () => pendingRef.current.forEach((item) => item.preview && URL.revokeObjectURL(item.preview)), []);

  const openThread = async (threadId: string) => {
    dispatch({ type: "select", threadId });
    setDrawer(false);
    try {
      const resumed = await client.request("thread/resume", { threadId, model: servedModel });
      dispatch({ type: "opened", thread: resumed.thread });
    } catch (error) {
      notice(error);
    }
  };

  const switchView = showView;

  const startThread = async () => {
    const cwd = newCwd.trim();
    if (!cwd) return notice("Name the project folder for the new thread.");
    try {
      const started = await client.request("thread/start", { cwd, model: servedModel, permissions: profile });
      dispatch({ type: "opened", thread: started.thread });
      setDrawer(false);
    } catch (error) {
      notice(error);
    }
  };

  /** A new Ask thread: the UI names the prompt, `ling web` sets its text, folder and sandbox. */
  const startAsk = async (): Promise<Thread> => {
    const params = { model: servedModel, prompt: ASK_PROMPT } as unknown as ParamsOf<"thread/start">;
    const started = await client.request("thread/start", params);
    dispatch({ type: "opened", thread: started.thread });
    return started.thread;
  };

  const newQuestion = () => {
    dispatch({ type: "select", threadId: null });
    setDrawer(false);
  };

  const addFiles = (files: Iterable<File>) => {
    const added: PendingFile[] = [];
    let index = 0;
    for (const file of files) {
      const name = attachmentName(file.name, file.type, index++);
      const kind = attachmentKind(file.type, name);
      added.push({ id: nextPendingId++, file, name, kind, preview: kind === "image" ? URL.createObjectURL(file) : null });
    }
    if (added.length) setPending((list) => [...list, ...added]);
  };

  const removeFile = (id: number) => {
    setPending((list) => {
      const gone = list.find((item) => item.id === id);
      if (gone?.preview) URL.revokeObjectURL(gone.preview);
      return list.filter((item) => item.id !== id);
    });
  };

  /** Starts recording, or stops it and puts the text into the draft: read before it is sent. */
  const toggleDictation = async () => {
    if (dictation === "recording") {
      recorder.current?.stop();
      return;
    }
    if (dictation !== "idle") return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const media = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      media.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      };
      media.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        recorder.current = null;
        const recording = new Blob(chunks, { type: media.mimeType || "audio/webm" });
        if (recording.size === 0) return setDictation("idle");
        setDictation("transcribing");
        bridge.transcribe(recording)
          .then((text) => {
            if (text) setDraft((current) => (current.trim() ? `${current.replace(/\s+$/, "")} ${text}` : text));
          })
          .catch(notice)
          .finally(() => setDictation("idle"));
      };
      recorder.current = media;
      media.start();
      setDictation("recording");
    } catch (error) {
      notice(error);
    }
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (view !== "ask") return;
    const files = Array.from(event.clipboardData?.files ?? []);
    if (files.length === 0) return;
    event.preventDefault();
    addFiles(files);
  };

  const sendWork = async (message: string) => {
    if (!selected) return;
    setDraft("");
    const threadId = selected.thread.id;
    try {
      if (selected.activeTurnId) {
        await client.request("turn/steer", { threadId, input: [text(message)], expectedTurnId: selected.activeTurnId });
      } else {
        await client.request("turn/start", { threadId, input: [text(message)], permissions: profile });
      }
    } catch (error) {
      setDraft(message);
      notice(error);
    }
  };

  const sendAsk = async (message: string) => {
    const files = pending;
    setSending(true);
    setDraft("");
    setPending([]);
    try {
      const thread = selected?.thread ?? (await startAsk());
      // Attachments go into the thread's own folder, which exists once the thread does.
      const attached: Attached[] = await Promise.all(
        files.map(async (item) => ({ kind: item.kind, name: item.name, path: (await bridge.upload(thread.id, item.file, item.kind, item.name)).path })),
      );
      const input = turnInput(message, attached);
      const active = selected?.thread.id === thread.id ? selected.activeTurnId : null;
      if (active) await client.request("turn/steer", { threadId: thread.id, input, expectedTurnId: active });
      else await client.request("turn/start", { threadId: thread.id, input });
      files.forEach((item) => item.preview && URL.revokeObjectURL(item.preview));
    } catch (error) {
      setDraft(message);
      setPending(files);
      notice(error);
    } finally {
      setSending(false);
    }
  };

  const send = async () => {
    const message = draft.trim();
    if (sending) return;
    if (view === "ask") {
      if (message || pending.length) await sendAsk(message);
    } else if (message) {
      await sendWork(message);
    }
  };

  const stop = () => {
    if (!selected?.activeTurnId) return;
    client.request("turn/interrupt", { threadId: selected.thread.id, turnId: selected.activeTurnId }).catch(notice);
  };

  const rename = async (threadId: string, name: string) => {
    setRenaming(null);
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      await client.request("thread/name/set", { threadId, name: trimmed });
      const updated = { method: "thread/name/updated", params: { threadId, threadName: trimmed } } as ServerNotification;
      dispatch({ type: "notification", notification: updated });
    } catch (error) {
      notice(error);
    }
  };

  const archive = async (threadId: string) => {
    try {
      await client.request("thread/archive", { threadId });
      dispatch({ type: "archived", threadId });
      setFound((list) => list?.filter((result) => result.thread.id !== threadId) ?? null);
    } catch (error) {
      notice(error);
    }
  };

  const answer = (request: PendingRequest, result: unknown) => {
    client.respond(request.id, result).then(() => dispatch({ type: "answered", id: request.id })).catch(notice);
  };

  if (state.server !== "ready") {
    return <StartupScreen state={state.server} stderr={state.stderr} protocolErrors={state.protocolErrors} notices={state.notices} onRetry={connect} />;
  }

  const usage = contextUse(selected?.usage ?? null);
  const threadRequests = state.requests.filter((request) => request.threadId === null || request.threadId === selected?.thread.id);
  const otherRequests = state.requests.length - threadRequests.length;
  const allThreads = Object.values(state.threads).map((entry) => entry.thread);
  const asks = found ? found.map((result) => result.thread) : askThreads(allThreads, askRoot);
  const snippets = new Map((found ?? []).map((result) => [result.thread.id, result.snippet]));

  const threadRow = (thread: Thread, label: string) => (
    renaming?.id === thread.id ? (
      <input key={thread.id} className="rename" autoFocus value={renaming.name} aria-label="Thread name"
        onChange={(event) => setRenaming({ id: thread.id, name: event.target.value })}
        onBlur={() => void rename(thread.id, renaming.name)}
        onKeyDown={(event) => {
          if (event.key === "Enter") void rename(thread.id, renaming.name);
          if (event.key === "Escape") setRenaming(null);
        }} />
    ) : (
      <div key={thread.id} className={thread.id === state.selected ? "thread-row selected" : "thread-row"}>
        <button className="thread" onClick={() => openThread(thread.id)} title={label}>
          <span className="thread-label">{label}</span>
          {snippets.get(thread.id) ? <span className="snippet">{snippets.get(thread.id)}</span> : null}
        </button>
        {state.threads[thread.id]?.activeTurnId ? <span className="running" aria-label="running" /> : null}
        <span className="row-actions">
          <button className="icon" title="Rename" aria-label="Rename" onClick={() => setRenaming({ id: thread.id, name: thread.name || label })}>✎</button>
          <button className="icon" title="Archive" aria-label="Archive" onClick={() => void archive(thread.id)}>⌫</button>
        </span>
      </div>
    )
  );

  const title = selected ? selected.thread.name || selected.thread.preview || (view === "ask" ? "New question" : "New thread") : view === "ask" ? "New question" : "Mightling";
  const canCompose = view === "ask" || selected !== null;

  return (
    <div className={`work view-${view}${drawer ? " drawer-open" : ""}`} onContextMenu={(event) => {
      // In the desktop app the context menu is native, drawn by the main process
      // (desktop/electron/src/shell.ts); a browser keeps its own, which a phone needs to copy.
      if (HOST !== "electron") return;
      event.preventDefault();
      const target = event.target as HTMLElement;
      const editable = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target.isContentEditable;
      bridge.contextMenu(event.clientX, event.clientY, editable, window.getSelection()?.toString() ?? "").catch(() => {});
    }}>
      <aside className="sidebar" aria-label="Threads">
        <nav className="views" aria-label="View">
          <button className={view === "ask" ? "selected" : ""} onClick={() => switchView("ask")}>Ask</button>
          <button className={view === "work" ? "selected" : ""} onClick={() => switchView("work")}>Work</button>
        </nav>
        {view === "ask" ? (
          <>
            <button className="primary new-question" onClick={newQuestion}>New question</button>
            <input className="search" type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search questions" aria-label="Search questions" />
            {asks.length === 0 ? <p className="empty small">{found ? "Nothing matches." : "No questions yet."}</p> : null}
            {asks.map((thread) => threadRow(thread, askTitle(thread)))}
          </>
        ) : (
          <>
            <div className="new-thread">
              <input value={newCwd} onChange={(event) => setNewCwd(event.target.value)} placeholder="/path/to/project" aria-label="Project folder" />
              <button onClick={startThread}>New thread</button>
            </div>
            {found ? (
              <section className="project">
                <h2>Found</h2>
                {found.map((result) => threadRow(result.thread, result.thread.name || result.thread.preview || "(new thread)"))}
              </section>
            ) : projects(state, askRoot).map((project) => (
              <section key={project.cwd} className="project">
                <h2 title={project.cwd} onClick={() => setNewCwd(project.cwd)}>{project.cwd.split(/[\\/]/).filter(Boolean).pop() ?? project.cwd}</h2>
                {project.threads.map((thread) => threadRow(thread, thread.name || thread.preview || "(new thread)"))}
              </section>
            ))}
            <input className="search" type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search threads" aria-label="Search threads" />
          </>
        )}
        {HOST === "web" ? <a className="devices-link" href="/devices">Pair a phone or another device</a> : null}
      </aside>
      <div className="backdrop" onClick={() => setDrawer(false)} />

      <main className="thread-pane">
        <header className="bar" onDoubleClick={() => HOST === "electron" && bridge.windowControl("maximize").catch(() => {})}>
          <button className="icon menu-toggle" aria-label="Threads" onClick={() => setDrawer(!drawer)}>☰</button>
          <span className="title">{title}</span>
          {selected && view === "work" ? <span className="cwd">{selected.thread.cwd}</span> : null}
          <span className="spacer" />
          {servedModel ? <span className="chip optional" title="The model the launcher serves">{servedModel}</span> : null}
          {airgap ? <span className={`chip airgap-${airgap.level}`} title={airgap.source}>airgapped {airgap.level}</span> : null}
          {usage ? (
            <span className="chip optional" title={`${usage.used.toLocaleString()} of ${usage.window.toLocaleString()} tokens`}>
              context {usage.percent}%
              <button className="link-button" onClick={() => selected && client.request("thread/compact/start", { threadId: selected.thread.id }).catch(notice)}>compress</button>
            </span>
          ) : null}
        </header>

        {state.notices.map((message, index) => (
          <div key={index} className="notice" onClick={() => dispatch({ type: "dismissNotice", index })}>{message}</div>
        ))}
        {state.protocolErrors.length > 0 ? <div className="notice">The agent's server wrote something that is not a protocol message: {state.protocolErrors[state.protocolErrors.length - 1]}</div> : null}

        <div className="turns">
          {selected ? selected.turns.map((turn, index) => (
            <Turn key={turn.id} turn={turn} last={index === selected.turns.length - 1}
              onRevert={() => client.request("thread/revert", { threadId: selected.thread.id, beforeTurnId: turn.id })
                .then(() => openThread(selected.thread.id)).catch(notice)} />
          )) : view === "ask" ? (
            <div className="empty ask-empty">
              <h1>Ask Mightling</h1>
              <p>A question with no project: Mightling answers in a folder of its own, searching the web when it needs to{airgap?.level === "on" ? ", except that airgapped is on, so web search is off" : ""}. Attach images or files with the clip, or paste a screenshot.</p>
            </div>
          ) : <p className="empty">Open a thread, or start one in a project folder.</p>}
          {threadRequests.map((request) => (
            <Approval key={JSON.stringify(request.id)} request={request} airgappedOn={airgap?.level === "on"} onAnswer={(result) => answer(request, result)} />
          ))}
          {otherRequests > 0 ? <div className="notice">{otherRequests} other thread(s) are waiting for an answer.</div> : null}
        </div>

        {canCompose ? (
          <footer className="composer">
            {pending.length > 0 ? (
              <div className="attachments">
                {pending.map((item) => (
                  <span key={item.id} className="attachment">
                    {item.preview ? <img src={item.preview} alt="" /> : null}
                    <span className="attachment-name">{item.name}</span>
                    <button className="icon" aria-label={`Remove ${item.name}`} onClick={() => removeFile(item.id)}>×</button>
                  </span>
                ))}
              </div>
            ) : null}
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={3} onPaste={onPaste}
              placeholder={selected?.activeTurnId ? "Steer the running turn…" : view === "ask" ? "Ask anything…" : "Ask Mightling…"}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
                if (event.key === "Escape" && selected?.activeTurnId) stop();
              }} />
            <div className="composer-actions">
              {view === "ask" ? (
                <>
                  <input ref={fileInput} type="file" multiple hidden onChange={(event) => {
                    addFiles(Array.from(event.target.files ?? []));
                    event.target.value = "";
                  }} />
                  <button className="attach" onClick={() => fileInput.current?.click()} title="Attach images or files" aria-label="Attach">📎</button>
                  <span className="spacer" />
                </>
              ) : (
                <select value={profile} onChange={(event) => setProfile(event.target.value)} aria-label="Permissions">
                  {choices.map((choice) => (
                    <option key={choice.id} value={choice.id} disabled={choice.disabled} title={choice.reason ?? undefined}>
                      {choice.label}{choice.disabled ? " (disabled)" : ""}
                    </option>
                  ))}
                </select>
              )}
              {canDictate ? (
                <button className={`mic${dictation === "recording" ? " recording" : ""}`} onClick={() => void toggleDictation()}
                  disabled={dictation === "transcribing"} aria-pressed={dictation === "recording"}
                  title={dictation === "recording" ? "Stop and write down what you said" : dictation === "transcribing" ? "Writing it down…" : "Dictate (speech-to-text on this machine)"}
                  aria-label={dictation === "recording" ? "Stop dictation" : "Dictate"}>{dictation === "transcribing" ? "…" : "🎤"}</button>
              ) : null}
              {selected?.activeTurnId ? <button onClick={stop}>Stop</button> : null}
              <button className="primary" disabled={sending} onClick={() => void send()}>{selected?.activeTurnId ? "Steer" : sending ? "Sending…" : "Send"}</button>
            </div>
          </footer>
        ) : null}
      </main>
    </div>
  );
}

/** A button that copies `text`, with the fallback a plain-HTTP page needs (clipboard.ts). */
function CopyButton({ text: value }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  return (
    <button className="link-button copy" onClick={() => {
      void copyText(value).then((copied) => {
        setState(copied ? "copied" : "failed");
        setTimeout(() => setState("idle"), 1500);
      });
    }}>{state === "copied" ? "copied" : state === "failed" ? "could not copy" : "copy"}</button>
  );
}


function StartupScreen(props: { state: string; stderr: string[]; protocolErrors: string[]; notices: string[]; onRetry: () => void }) {
  return (
    <div className="startup">
      <h1>{props.state === "exited" ? "The agent's server stopped" : "Starting Mightling…"}</h1>
      <p>{props.state === "exited"
        ? "ling app-server exited. Its last messages are below."
        : "ling app-server is waiting for the model server. A cold load takes a few minutes; `ling-admin server start` starts it if it is stopped."}</p>
      <pre className="log">{[...props.stderr, ...props.protocolErrors, ...props.notices].join("\n") || "…"}</pre>
      {props.state === "exited" ? <button className="primary" onClick={props.onRetry}>Start again</button> : null}
    </div>
  );
}

function Turn(props: { turn: TurnView; last: boolean; onRevert: () => void }) {
  const { turn } = props;
  return (
    <section className={`turn turn-${turn.status}`}>
      {turn.items.map((item) => <Item key={item.id} item={item} />)}
      {turn.error ? <div className="turn-error">{turn.error.message}</div> : null}
      {turn.status === "interrupted" ? <div className="turn-note">Stopped.</div> : null}
      {turn.diff ? (
        <details className="turn-diff">
          <summary>Changes in this turn</summary>
          <Diff text={turn.diff} />
        </details>
      ) : null}
      {turn.status !== "inProgress" && props.last ? (
        <button className="link-button" onClick={props.onRevert} title="Undo this turn and what it changed">revert</button>
      ) : null}
    </section>
  );
}

function Item({ item }: { item: ThreadItem }) {
  switch (item.type) {
    case "userMessage":
      return <div className="item user">{item.content.map(describeInput).join("\n")}</div>;
    case "agentMessage":
      return (
        <div className="item agent">
          <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(item.text) }} />
          {item.text ? <CopyButton text={item.text} /> : null}
        </div>
      );
    case "reasoning":
      return item.summary.length || item.content.length ? (
        <details className="item reasoning"><summary>Thinking</summary><pre>{[...item.summary, ...item.content].join("\n")}</pre></details>
      ) : null;
    case "plan":
      return <div className="item plan markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(item.text) }} />;
    case "commandExecution":
      return (
        <details className={`item command status-${item.status}`} open={item.status === "inProgress"}>
          <summary><code>{item.command}</code>{item.exitCode !== null && item.exitCode !== 0 ? <span className="exit"> exit {item.exitCode}</span> : null}</summary>
          {item.aggregatedOutput ? <pre>{item.aggregatedOutput}</pre> : null}
        </details>
      );
    case "fileChange":
      return (
        <div className={`item files status-${item.status}`}>
          {item.changes.map((change) => (
            <details key={change.path}>
              <summary>{change.kind.type === "add" ? "added" : change.kind.type === "delete" ? "deleted" : "edited"} <code>{change.path}</code></summary>
              <Diff text={change.diff} />
            </details>
          ))}
        </div>
      );
    case "mcpToolCall":
      return <div className={`item tool status-${item.status}`}><code>{item.server}.{item.tool}</code>{item.error ? ` — ${item.error.message}` : ""}</div>;
    case "contextCompaction":
      return <div className="item note">Earlier turns were compacted.</div>;
    case "webSearch":
      return <div className="item note">Searched the web.</div>;
    default:
      return <div className="item note">{item.type}</div>;
  }
}

/** What the user sent, as text: attachments by their file name. */
function describeInput(input: UserInput): string {
  switch (input.type) {
    case "text":
      return input.text;
    case "localImage":
      return `[image: ${input.path.split(/[\\/]/).pop()}]`;
    default:
      return `[${input.type}]`;
  }
}

function Diff({ text: diff }: { text: string }) {
  return (
    <pre className="diff">
      {diff.split("\n").map((line, index) => (
        <span key={index} className={line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : line.startsWith("@@") ? "hunk" : ""}>
          {line}{"\n"}
        </span>
      ))}
    </pre>
  );
}

function Approval(props: { request: PendingRequest; airgappedOn: boolean; onAnswer: (result: unknown) => void }) {
  const { request, onAnswer } = props;
  const params = request.params as Record<string, unknown>;
  const [answers, setAnswers] = useState<Record<string, string>>({});
  switch (request.method) {
    case "item/commandExecution/requestApproval":
    case "item/fileChange/requestApproval": {
      const network = params.networkApprovalContext as { host?: string } | null | undefined;
      return (
        <div className="approval">
          <strong>{request.method === "item/fileChange/requestApproval" ? "Apply these changes?" : "Run this command?"}</strong>
          {typeof params.command === "string" ? <pre>{params.command}</pre> : null}
          {typeof params.reason === "string" ? <p>{params.reason}</p> : null}
          {network?.host ? <p className="warn">It asks for the network ({network.host}).{props.airgappedOn ? " Airgapped is on: approving lets it reach the network." : ""}</p> : null}
          <div className="buttons">
            <button className="primary" onClick={() => onAnswer({ decision: "accept" })}>Allow</button>
            <button onClick={() => onAnswer({ decision: "acceptForSession" })}>Allow for this session</button>
            <button onClick={() => onAnswer({ decision: "decline" })}>Decline</button>
            <button onClick={() => onAnswer({ decision: "cancel" })}>Stop the turn</button>
          </div>
        </div>
      );
    }
    case "item/permissions/requestApproval":
      return (
        <div className="approval">
          <strong>Grant more permissions?</strong>
          <pre>{JSON.stringify(params.permissions, null, 2)}</pre>
          {typeof params.reason === "string" ? <p>{params.reason}</p> : null}
          <div className="buttons">
            <button className="primary" onClick={() => onAnswer({ permissions: grant(params.permissions), scope: "turn" })}>Grant for this turn</button>
            <button onClick={() => onAnswer({ permissions: {}, scope: "turn" })}>Decline</button>
          </div>
        </div>
      );
    case "execCommandApproval":
    case "applyPatchApproval":
      return (
        <div className="approval">
          <strong>{request.method === "execCommandApproval" ? "Run this command?" : "Apply these changes?"}</strong>
          {Array.isArray(params.command) ? <pre>{(params.command as string[]).join(" ")}</pre> : null}
          <div className="buttons">
            <button className="primary" onClick={() => onAnswer({ decision: "approved" })}>Allow</button>
            <button onClick={() => onAnswer({ decision: "approved_for_session" })}>Allow for this session</button>
            <button onClick={() => onAnswer({ decision: "abort" })}>Stop the turn</button>
          </div>
        </div>
      );
    case "item/tool/requestUserInput": {
      const questions = (params.questions ?? []) as { id: string; header: string; question: string; options: { label: string }[] | null }[];
      return (
        <div className="approval">
          {questions.map((question) => (
            <label key={question.id} className="question">
              <strong>{question.header}</strong> {question.question}
              {question.options ? (
                <select value={answers[question.id] ?? ""} onChange={(event) => setAnswers({ ...answers, [question.id]: event.target.value })}>
                  <option value="" disabled>Choose…</option>
                  {question.options.map((option) => <option key={option.label}>{option.label}</option>)}
                </select>
              ) : (
                <input value={answers[question.id] ?? ""} onChange={(event) => setAnswers({ ...answers, [question.id]: event.target.value })} />
              )}
            </label>
          ))}
          <div className="buttons">
            <button className="primary" onClick={() => onAnswer({ answers: Object.fromEntries(questions.map((q) => [q.id, { answers: answers[q.id] ? [answers[q.id]] : [] }])) })}>Answer</button>
          </div>
        </div>
      );
    }
    default:
      return null;
  }
}

/** A permission request granted as asked, without the fields it left out. */
function grant(requested: unknown): object {
  const profile = (requested ?? {}) as Record<string, unknown>;
  return Object.fromEntries(Object.entries(profile).filter(([, value]) => value !== null && value !== undefined));
}
