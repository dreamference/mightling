// The bottom panel (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1, §2.6, §2.7; Ctrl+J): the
// shown thread's terminal tabs (Ctrl+`), each a PTY on the server (terminal.ts), the environment's
// actions run in a terminal of their own, the setup run of a new worktree with its output, Stop,
// and the editor for the project's environment.

import { useEffect, useRef, useState } from "react";

import type * as bridge from "./bridge";
import { Icon } from "./icons";
import type { TerminalHub, TerminalSession } from "./terminal";
import { TerminalView } from "./TerminalView";
import { shortcutLabel } from "./keymap";

export interface TaskView {
  id: string;
  label: string;
  cwd: string;
  output: string;
  running: boolean;
  code: number | null;
}

/** The output a tab keeps: the last 256 KB, without terminal colour codes. */
export const OUTPUT_KEPT = 256 * 1024;

export function appendOutput(output: string, data: string): string {
  const plain = data.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, "").replace(/\r(?!\n)/g, "\n");
  const next = output + plain;
  return next.length > OUTPUT_KEPT ? next.slice(next.length - OUTPUT_KEPT) : next;
}

export interface BottomPanelProps {
  tasks: TaskView[];
  terminals: TerminalSession[];
  hub: TerminalHub;
  /** The tab to bring forward (a terminal's process id or a task's id), with a counter so asking again focuses it again. */
  front: { id: string; tick: number } | null;
  onNewTerminal: () => void;
  onCloseTerminal: (processId: string) => void;
  onSendToThread: (processId: string) => void;
  environment: bridge.LocalEnvironment | null;
  problems: string[];
  project: string | null;
  onRun: (action: bridge.EnvironmentAction) => void;
  onStop: (id: string) => void;
  onClose: (id: string) => void;
  onEdit: () => void;
  onHide: () => void;
}

export function BottomPanel(props: BottomPanelProps) {
  const [shown, setShown] = useState<string | null>(null);
  const output = useRef<HTMLPreElement>(null);
  const ids = [...props.terminals.map((terminal) => terminal.processId), ...props.tasks.map((task) => task.id)];
  const current = shown && ids.includes(shown) ? shown : props.terminals[props.terminals.length - 1]?.processId ?? props.tasks[props.tasks.length - 1]?.id ?? null;
  const task = props.tasks.find((entry) => entry.id === current) ?? null;
  const terminal = props.terminals.find((entry) => entry.processId === current) ?? null;

  // A tab asked for (a new terminal, an action, a setup run) comes to the front.
  useEffect(() => {
    if (props.front) setShown(props.front.id);
  }, [props.front]);
  useEffect(() => {
    if (output.current) output.current.scrollTop = output.current.scrollHeight;
  }, [task?.output]);

  return (
    <section className="bottom-panel" aria-label="Bottom panel">
      <header className="panel-tabs" role="tablist">
        {props.terminals.map((entry) => (
          <span key={entry.processId} className={`panel-tab${entry.processId === current ? " selected" : ""}`}>
            <button role="tab" aria-selected={entry.processId === current} className="panel-tab-label" onClick={() => setShown(entry.processId)} title={entry.cwd}>
              {entry.exitCode === null ? <Icon name="terminal" size={12} /> : <Icon name={entry.exitCode === 0 ? "check" : "x"} size={12} />}
              {entry.title}
            </button>
            <button className="icon" aria-label={`Close ${entry.title}`} onClick={() => props.onCloseTerminal(entry.processId)}><Icon name="x" size={12} /></button>
          </span>
        ))}
        {props.tasks.map((entry) => (
          <span key={entry.id} className={`panel-tab${entry.id === current ? " selected" : ""}`}>
            <button role="tab" aria-selected={entry.id === current} className="panel-tab-label" onClick={() => setShown(entry.id)} title={entry.cwd}>
              {entry.running ? <span className="running" aria-label="running" /> : <Icon name={entry.code === 0 ? "check" : "x"} size={12} />}
              {entry.label}
            </button>
            {entry.running
              ? <button className="icon" aria-label={`Stop ${entry.label}`} onClick={() => props.onStop(entry.id)}><Icon name="square" size={12} /></button>
              : <button className="icon" aria-label={`Close ${entry.label}`} onClick={() => props.onClose(entry.id)}><Icon name="x" size={12} /></button>}
          </span>
        ))}
        <button className="icon" aria-label="New terminal" title={`New terminal (${shortcutLabel("terminal")})`} onClick={props.onNewTerminal}><Icon name="plus" size={14} /></button>
        <span className="spacer" />
        {terminal ? <button className="link-button" title="Put this terminal's recent output in the composer, for the agent to read" onClick={() => props.onSendToThread(terminal.processId)}>Send to the thread</button> : null}
        {props.environment?.actions.map((action) => (
          <button key={action.name} className="link-button" title={action.script} onClick={() => props.onRun(action)}><Icon name={actionIcon(action.icon)} size={12} /> {action.name}</button>
        ))}
        {props.project ? <button className="link-button" onClick={props.onEdit}>Environment…</button> : null}
        <button className="icon" aria-label="Hide the bottom panel" title={`Hide (${shortcutLabel("bottomPanel")})`} onClick={props.onHide}><Icon name="x" size={14} /></button>
      </header>
      {props.problems.map((problem) => <div key={problem} className="notice">{problem}</div>)}
      <div className="panel-body">
        {props.terminals.map((entry) => (
          <TerminalView key={entry.processId} hub={props.hub} processId={entry.processId} visible={entry.processId === current} focus={props.front?.id === entry.processId ? props.front.tick : 0} />
        ))}
        {terminal?.error ? <div className="notice">{terminal.error}</div> : null}
        {task ? (
          <pre ref={output} className="panel-output" aria-label={`${task.label} output`}>{task.output || (task.running ? "…" : "(no output)")}{!task.running ? `\n[exited with ${task.code ?? "a signal"}]` : ""}</pre>
        ) : null}
        {!task && !terminal ? (
          <p className="empty small panel-empty">{props.environment
            ? `${props.environment.name}: its actions run here (Super+Shift+D runs the first); + opens a terminal.`
            : props.project ? "+ opens a terminal in this thread's folder. This project has no local environment yet: Environment… makes one in .mightling/." : "Open a thread in a project to run a terminal here."}</p>
        ) : null}
      </div>
    </section>
  );
}

/** The icon an environment action names, from the icons the UI bundles. */
export function actionIcon(icon: string): "arrowUp" | "check" | "terminal" | "settings" | "search" | "square" {
  switch (icon) {
    case "test":
      return "check";
    case "build":
    case "tool":
      return "settings";
    case "debug":
      return "search";
    case "terminal":
      return "terminal";
    default:
      return "arrowUp";
  }
}

/** The environment editor: writes `.mightling/environments/default.toml`; `.codex/` files are read, never written. */
export function EnvironmentEditor(props: {
  project: string;
  initial: bridge.LocalEnvironment | null;
  onSave: (environment: { name: string; setup: string; actions: bridge.EnvironmentAction[] }) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(props.initial?.name ?? "Default");
  const [setup, setSetup] = useState(props.initial?.setup ?? "");
  const [actions, setActions] = useState<bridge.EnvironmentAction[]>(props.initial?.actions ?? []);
  const edit = (index: number, change: Partial<bridge.EnvironmentAction>) => setActions((list) => list.map((action, at) => (at === index ? { ...action, ...change } : action)));
  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog environment-editor" role="dialog" aria-label="Local environment" onClick={(event) => event.stopPropagation()}>
        <header><h2>Local environment</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <p className="dialog-note">For {props.project}. Saved to <code>.mightling/environments/default.toml</code>, which you can commit to share; environments in <code>.codex/</code> are read too, never written.{props.initial && props.initial.folder === ".codex" ? " This one was read from .codex/; saving writes a copy to .mightling/." : ""}</p>
        <label className="field">Name <input value={name} onChange={(event) => setName(event.target.value)} aria-label="Environment name" /></label>
        <label className="field">Setup script, run when a worktree is made <textarea rows={3} value={setup} onChange={(event) => setSetup(event.target.value)} aria-label="Setup script" placeholder="npm install" /></label>
        <h3>Actions</h3>
        {actions.map((action, index) => (
          <div key={index} className="action-row">
            <input value={action.name} onChange={(event) => edit(index, { name: event.target.value })} aria-label={`Action ${index + 1} name`} placeholder="Run" />
            <select value={action.icon} onChange={(event) => edit(index, { icon: event.target.value })} aria-label={`Action ${index + 1} icon`}>
              {["play", "test", "build", "debug", "terminal", "globe", "tool"].map((icon) => <option key={icon}>{icon}</option>)}
            </select>
            <input className="script" value={action.script} onChange={(event) => edit(index, { script: event.target.value })} aria-label={`Action ${index + 1} script`} placeholder="npm start" />
            <button className="icon" aria-label={`Remove action ${index + 1}`} onClick={() => setActions((list) => list.filter((_, at) => at !== index))}><Icon name="x" size={14} /></button>
          </div>
        ))}
        <button className="link-button" onClick={() => setActions((list) => [...list, { name: "", icon: "play", script: "" }])}>Add an action</button>
        <div className="buttons dialog-buttons">
          <button className="primary" onClick={() => props.onSave({ name, setup, actions: actions.filter((action) => action.script.trim()) })}>Save</button>
          <button onClick={props.onClose}>Cancel</button>
        </div>
      </section>
    </div>
  );
}
