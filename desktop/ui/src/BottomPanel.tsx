// The bottom panel (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1, §2.6; Ctrl+J): the local
// environment's setup and actions, each run a tab of its own with its output, Stop, and the editor
// for the project's environment. The terminal tabs of item 8 join it here.

import { useEffect, useRef, useState } from "react";

import type * as bridge from "./bridge";
import { Icon } from "./icons";

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
  const current = props.tasks.find((task) => task.id === shown) ?? props.tasks[props.tasks.length - 1] ?? null;

  // A new task comes to the front.
  const newest = props.tasks[props.tasks.length - 1]?.id ?? null;
  useEffect(() => setShown(newest), [newest]);
  useEffect(() => {
    if (output.current) output.current.scrollTop = output.current.scrollHeight;
  }, [current?.output]);

  return (
    <section className="bottom-panel" aria-label="Bottom panel">
      <header className="panel-tabs">
        {props.tasks.map((task) => (
          <span key={task.id} className={`panel-tab${task.id === current?.id ? " selected" : ""}`}>
            <button className="panel-tab-label" onClick={() => setShown(task.id)} title={task.cwd}>
              {task.running ? <span className="running" aria-label="running" /> : <Icon name={task.code === 0 ? "check" : "x"} size={12} />}
              {task.label}
            </button>
            {task.running
              ? <button className="icon" aria-label={`Stop ${task.label}`} onClick={() => props.onStop(task.id)}><Icon name="square" size={12} /></button>
              : <button className="icon" aria-label={`Close ${task.label}`} onClick={() => props.onClose(task.id)}><Icon name="x" size={12} /></button>}
          </span>
        ))}
        <span className="spacer" />
        {props.environment?.actions.map((action) => (
          <button key={action.name} className="link-button" title={action.script} onClick={() => props.onRun(action)}><Icon name={actionIcon(action.icon)} size={12} /> {action.name}</button>
        ))}
        {props.project ? <button className="link-button" onClick={props.onEdit}>Environment…</button> : null}
        <button className="icon" aria-label="Hide the bottom panel" title="Hide (Ctrl+J)" onClick={props.onHide}><Icon name="x" size={14} /></button>
      </header>
      {props.problems.map((problem) => <div key={problem} className="notice">{problem}</div>)}
      {current ? (
        <pre ref={output} className="panel-output" aria-label={`${current.label} output`}>{current.output || (current.running ? "…" : "(no output)")}{!current.running ? `\n[exited with ${current.code ?? "a signal"}]` : ""}</pre>
      ) : (
        <p className="empty small panel-empty">{props.environment
          ? `${props.environment.name}: its actions run here (Super+Shift+D runs the first).`
          : props.project ? "This project has no local environment yet: Environment… makes one in .mightling/." : "Open a thread in a project to run its environment's actions here."}</p>
      )}
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
