// The Work window (specs/DREAMFERENCE_PUFFIN_DESKTOP.md, Phase 1): projects and threads on the
// left, the selected thread's turns streaming in the middle, approvals inline, and a composer that
// starts a turn, steers a running one, or stops it. Everything goes through `puffin app-server`.

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import * as bridge from "./bridge";
import { renderMarkdown } from "./markdown";
import { RpcClient } from "./rpc";
import type { ServerRequest } from "./protocol/ServerRequest";
import type { PermissionProfileSummary } from "./protocol/v2/PermissionProfileSummary";
import type { ThreadItem } from "./protocol/v2/ThreadItem";
import type { UserInput } from "./protocol/v2/UserInput";
import {
  contextUse, initialState, permissionChoices, projects, reduce,
  type PendingRequest, type ThreadView, type TurnView,
} from "./store";

const CLIENT_INFO = { name: "puffin_desktop", title: "Puffin Desktop", version: "0.1.0" };

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

export function App() {
  const [state, dispatch] = useReducer(reduce, initialState);
  const client = useMemo(() => new RpcClient(bridge.bridgeTransport), []);
  const [servedModel, setServedModel] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<PermissionProfileSummary[]>([]);
  const [profile, setProfile] = useState(":workspace");
  const [airgap, setAirgap] = useState<bridge.Airgapped | null>(null);
  const [newCwd, setNewCwd] = useState("");
  const [draft, setDraft] = useState("");
  const notice = useCallback((error: unknown) => dispatch({ type: "notice", message: error instanceof Error ? error.message : String(error) }), []);

  const answerUnasked = useCallback((request: ServerRequest) => {
    switch (request.method) {
      case "currentTime/read":
        return client.respond(request.id, { currentTimeAt: Math.floor(Date.now() / 1000) });
      case "mcpServer/elicitation/request":
        return client.respond(request.id, { action: "decline", content: null, _meta: null });
      default:
        return client.respondError(request.id, -32601, `Puffin's Work window does not handle ${request.method}`);
    }
  }, [client]);

  const connect = useCallback(async () => {
    dispatch({ type: "server", status: "starting" });
    try {
      const started = await bridge.startServer();
      setServedModel(started.served_model);
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
      if (target.cwd) setNewCwd(target.cwd);
      if (target.thread) {
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
  }, [client, connect, answerUnasked, notice]);

  const selected: ThreadView | null = state.selected ? state.threads[state.selected] ?? null : null;

  // The level can differ per thread (`/airgapped` in the TUI writes a session file).
  useEffect(() => {
    if (state.server === "ready") bridge.airgapped(state.selected).then(setAirgap).catch(notice);
  }, [state.selected, state.server, notice]);

  const choices = permissionChoices(profiles, airgap?.level === "on");
  useEffect(() => {
    if (choices.find((choice) => choice.id === profile)?.disabled) setProfile(":workspace");
  }, [choices, profile]);

  const openThread = async (threadId: string) => {
    dispatch({ type: "select", threadId });
    try {
      const resumed = await client.request("thread/resume", { threadId, model: servedModel });
      dispatch({ type: "opened", thread: resumed.thread });
    } catch (error) {
      notice(error);
    }
  };

  const startThread = async () => {
    const cwd = newCwd.trim();
    if (!cwd) return notice("Name the project folder for the new thread.");
    try {
      const started = await client.request("thread/start", { cwd, model: servedModel, permissions: profile });
      dispatch({ type: "opened", thread: started.thread });
    } catch (error) {
      notice(error);
    }
  };

  const send = async () => {
    const message = draft.trim();
    if (!message || !selected) return;
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

  const stop = () => {
    if (!selected?.activeTurnId) return;
    client.request("turn/interrupt", { threadId: selected.thread.id, turnId: selected.activeTurnId }).catch(notice);
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

  return (
    <div className="work">
      <aside className="sidebar">
        <div className="new-thread">
          <input value={newCwd} onChange={(event) => setNewCwd(event.target.value)} placeholder="/path/to/project" aria-label="Project folder" />
          <button onClick={startThread}>New thread</button>
        </div>
        {projects(state).map((project) => (
          <section key={project.cwd} className="project">
            <h2 title={project.cwd} onClick={() => setNewCwd(project.cwd)}>{project.cwd.split("/").filter(Boolean).pop() ?? project.cwd}</h2>
            {project.threads.map((thread) => (
              <button key={thread.id} className={thread.id === state.selected ? "thread selected" : "thread"} onClick={() => openThread(thread.id)}>
                {thread.name || thread.preview || "(new thread)"}
                {state.threads[thread.id]?.activeTurnId ? <span className="running" aria-label="running" /> : null}
              </button>
            ))}
          </section>
        ))}
      </aside>

      <main className="thread-pane">
        <header className="bar">
          <span className="title">{selected ? selected.thread.name || selected.thread.preview || "New thread" : "Puffin"}</span>
          {selected ? <span className="cwd">{selected.thread.cwd}</span> : null}
          <span className="spacer" />
          {servedModel ? <span className="chip" title="The model the launcher serves">{servedModel}</span> : null}
          {airgap ? <span className={`chip airgap-${airgap.level}`} title={airgap.source}>airgapped {airgap.level}</span> : null}
          {usage ? (
            <span className="chip" title={`${usage.used.toLocaleString()} of ${usage.window.toLocaleString()} tokens`}>
              context {usage.percent}%
              <button className="link-button" onClick={() => selected && client.request("thread/compact/start", { threadId: selected.thread.id }).catch(notice)}>compress</button>
            </span>
          ) : null}
          <button onClick={() => bridge.openChat().catch(notice)}>Chat</button>
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
          )) : <p className="empty">Open a thread, or start one in a project folder.</p>}
          {threadRequests.map((request) => (
            <Approval key={JSON.stringify(request.id)} request={request} airgappedOn={airgap?.level === "on"} onAnswer={(result) => answer(request, result)} />
          ))}
          {otherRequests > 0 ? <div className="notice">{otherRequests} other thread(s) are waiting for an answer.</div> : null}
        </div>

        {selected ? (
          <footer className="composer">
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={3}
              placeholder={selected.activeTurnId ? "Steer the running turn…" : "Ask Puffin…"}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); }
                if (event.key === "Escape" && selected.activeTurnId) stop();
              }} />
            <div className="composer-actions">
              <select value={profile} onChange={(event) => setProfile(event.target.value)} aria-label="Permissions">
                {choices.map((choice) => (
                  <option key={choice.id} value={choice.id} disabled={choice.disabled} title={choice.reason ?? undefined}>
                    {choice.label}{choice.disabled ? " (disabled)" : ""}
                  </option>
                ))}
              </select>
              {selected.activeTurnId ? <button onClick={stop}>Stop</button> : null}
              <button className="primary" onClick={() => void send()}>{selected.activeTurnId ? "Steer" : "Send"}</button>
            </div>
          </footer>
        ) : null}
      </main>
    </div>
  );
}

function StartupScreen(props: { state: string; stderr: string[]; protocolErrors: string[]; notices: string[]; onRetry: () => void }) {
  return (
    <div className="startup">
      <h1>{props.state === "exited" ? "The agent's server stopped" : "Starting Puffin…"}</h1>
      <p>{props.state === "exited"
        ? "puffin app-server exited. Its last messages are below."
        : "puffin app-server is waiting for the model server. A cold load takes a few minutes; `puffin-admin server start` starts it if it is stopped."}</p>
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
      return <div className="item user">{item.content.map((input) => (input.type === "text" ? input.text : `[${input.type}]`)).join("\n")}</div>;
    case "agentMessage":
      return <div className="item agent markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(item.text) }} />;
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
