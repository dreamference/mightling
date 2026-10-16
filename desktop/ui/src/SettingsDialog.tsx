// The settings (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.15; Ctrl+,): pages in a dialog,
// window settings in the window's preferences and agent settings in config.toml through the
// server. General (what Enter and a follow-up do), Keyboard shortcuts (§2.19: review, rebind,
// reset, search by name or by keystroke), Worktrees. Item 10 adds the other pages.

import { useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

import * as bridge from "./bridge";
import { Icon } from "./icons";
import { KEYMAP, chordOf, conflicts, type KeyAction } from "./keymap";
import type { Prefs } from "./prefs";
import type { WorktreeSettings } from "./worktrees";

export type SettingsPage = "general" | "shortcuts" | "worktrees";

export interface SettingsProps {
  page: SettingsPage;
  setPage: (page: SettingsPage) => void;
  prefs: Prefs;
  onChange: (values: Partial<Prefs>) => void;
  onClose: () => void;
  worktrees: WorktreeSettings;
  onWorktree: (key: "git-worktree-root" | "worktree-keep-count" | "worktree-auto-cleanup-enabled", value: string | number | boolean | null) => void;
}

const PAGES: { id: SettingsPage; label: string; desktopOnly?: boolean }[] = [
  { id: "general", label: "General" },
  { id: "shortcuts", label: "Keyboard shortcuts" },
  { id: "worktrees", label: "Worktrees", desktopOnly: true },
];

export function SettingsDialog(props: SettingsProps) {
  const pages = PAGES.filter((page) => !page.desktopOnly || bridge.git.available());
  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog settings" role="dialog" aria-label="Settings" onClick={(event) => event.stopPropagation()}>
        <header><h2>Settings</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <div className="settings-body">
          <nav className="settings-nav" aria-label="Settings pages">
            {pages.map((page) => (
              <button key={page.id} className={props.page === page.id ? "selected" : ""} aria-current={props.page === page.id ? "page" : undefined} onClick={() => props.setPage(page.id)}>{page.label}</button>
            ))}
          </nav>
          <div className="settings-page">
            {props.page === "general" ? <General {...props} /> : null}
            {props.page === "shortcuts" ? <Shortcuts {...props} /> : null}
            {props.page === "worktrees" && bridge.git.available() ? <Worktrees {...props} /> : null}
          </div>
        </div>
      </section>
    </div>
  );
}

function General({ prefs, onChange }: SettingsProps) {
  return (
    <>
      <h3>General</h3>
      <fieldset>
        <legend>Enter in the composer</legend>
        <label><input type="radio" name="enter" checked={prefs.enterBehaviour === "enter"} onChange={() => onChange({ enterBehaviour: "enter" })} /> Enter sends; Shift+Enter starts a new line</label>
        <label><input type="radio" name="enter" checked={prefs.enterBehaviour === "ctrlEnter"} onChange={() => onChange({ enterBehaviour: "ctrlEnter" })} /> Enter starts a new line; Ctrl+Enter sends</label>
      </fieldset>
      <fieldset>
        <legend>A message sent while the agent is working</legend>
        <label><input type="radio" name="follow" checked={prefs.followUp === "steer"} onChange={() => onChange({ followUp: "steer" })} /> Steer: the running turn takes it in now</label>
        <label><input type="radio" name="follow" checked={prefs.followUp === "queue"} onChange={() => onChange({ followUp: "queue" })} /> Queue: it waits, and starts the next turn when this one ends</label>
      </fieldset>
      <fieldset>
        <legend>Text size</legend>
        <label className="field">{Math.round(prefs.fontScale * 100)}% (Ctrl+= bigger, Ctrl+- smaller, Ctrl+0 back to 100%)
          <input type="range" min={0.75} max={2} step={0.05} value={prefs.fontScale} aria-label="Text size" onChange={(event) => onChange({ fontScale: Number(event.target.value) })} />
        </label>
      </fieldset>
    </>
  );
}

/** Keyboard shortcuts: the table of §2.19, each binding changeable, a change refused when it clashes in its context. */
function Shortcuts({ prefs, onChange }: SettingsProps) {
  const [query, setQuery] = useState("");
  const [byKeys, setByKeys] = useState(false);
  const [recording, setRecording] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const actions = useMemo(() => KEYMAP.map((action) => ({ ...action, keys: prefs.keyOverrides[action.id] ?? action.keys })), [prefs.keyOverrides]);
  const shown = actions.filter((action) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return byKeys ? action.keys.some((key) => key.toLowerCase() === q) : `${action.label} ${action.group}`.toLowerCase().includes(q);
  });

  const record = (action: KeyAction, event: ReactKeyboardEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "Escape" && !event.ctrlKey && !event.altKey && !event.shiftKey) return setRecording(null);
    const chord = chordOf(event);
    if (!chord) return;
    const next = { ...prefs.keyOverrides, [action.id]: [chord] };
    const trial = KEYMAP.map((entry) => ({ ...entry, keys: (next[entry.id] as string[] | undefined) ?? entry.keys }));
    const clash = conflicts(trial);
    if (clash.length) {
      setProblem(`${chord} is taken: ${clash[0]}`);
      return;
    }
    setProblem(null);
    setRecording(null);
    onChange({ keyOverrides: next });
  };

  const reset = (id: string) => {
    const next = { ...prefs.keyOverrides };
    delete next[id];
    onChange({ keyOverrides: next });
  };

  let group = "";
  return (
    <>
      <h3>Keyboard shortcuts</h3>
      <div className="shortcut-search">
        <input type="search" value={query} placeholder={byKeys ? "Press keys, then type them here, e.g. Ctrl+K" : "Search by command"} aria-label="Search shortcuts"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (!byKeys) return;
            const chord = chordOf(event);
            if (chord && (event.ctrlKey || event.altKey || event.metaKey || event.key.length > 1) && event.key !== "Backspace") {
              event.preventDefault();
              setQuery(chord);
            }
          }} />
        <label><input type="checkbox" checked={byKeys} onChange={(event) => { setByKeys(event.target.checked); setQuery(""); }} /> by keystroke</label>
        {Object.keys(prefs.keyOverrides).length ? <button className="link-button" onClick={() => onChange({ keyOverrides: {} })}>Reset all</button> : null}
      </div>
      {problem ? <div className="notice">{problem}</div> : null}
      <table className="shortcut-table">
        <tbody>
          {shown.map((action) => {
            const header = action.group !== group ? (group = action.group) : null;
            return [
              header ? <tr key={`g-${header}`} className="shortcut-group"><th colSpan={3}>{header}</th></tr> : null,
              <tr key={action.id} className={action.status === "planned" ? "planned" : ""}>
                <td>{action.label}{action.status === "planned" ? <span className="planned-note"> (not yet in this build)</span> : null}{action.context !== "global" ? <span className="planned-note"> — {action.context}</span> : null}</td>
                <td>
                  {recording === action.id
                    ? <button className="recording" autoFocus onKeyDown={(event) => record(action, event)} onBlur={() => setRecording(null)}>Press the new keys…</button>
                    : action.keys.map((key) => <kbd key={key}>{key}</kbd>)}
                </td>
                <td className="shortcut-actions">
                  <button className="link-button" onClick={() => { setProblem(null); setRecording(action.id); }}>Change</button>
                  {prefs.keyOverrides[action.id] ? <button className="link-button" onClick={() => reset(action.id)}>Reset</button> : null}
                </td>
              </tr>,
            ];
          })}
        </tbody>
      </table>
    </>
  );
}

function Worktrees(props: SettingsProps) {
  const [root, setRoot] = useState(props.worktrees.root ?? "");
  return (
    <>
      <h3>Worktrees</h3>
      <fieldset>
        <legend>Kept in config.toml, shared with ling's /worktree</legend>
        <label className="field">Worktree root
          <input value={root} placeholder="~/.mightling/worktrees" aria-label="Worktree root" onChange={(event) => setRoot(event.target.value)}
            onBlur={() => root.trim() !== (props.worktrees.root ?? "") && props.onWorktree("git-worktree-root", root.trim() || null)} />
        </label>
        <label><input type="checkbox" checked={props.worktrees.autoCleanup} onChange={(event) => props.onWorktree("worktree-auto-cleanup-enabled", event.target.checked)} /> Delete the oldest worktrees automatically (never a pinned, running or permanent one; a snapshot is kept)</label>
        <label className="field">Keep the most recent
          <input type="number" min={1} max={200} value={props.worktrees.keep} aria-label="Worktrees kept" onChange={(event) => Number(event.target.value) > 0 && props.onWorktree("worktree-keep-count", Math.floor(Number(event.target.value)))} />
        </label>
      </fieldset>
    </>
  );
}
