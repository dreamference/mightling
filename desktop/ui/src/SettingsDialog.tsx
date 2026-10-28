// The settings (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.15; Ctrl+,): pages in a dialog,
// window settings in the window's preferences and agent settings in config.toml through the
// server. General, Your documents (the local file index's folders, LOCAL_INDEX §8.3), Profile (statistics from the local session files), Keyboard shortcuts (§2.19),
// Notifications, Appearance (System, Light or Dark, the §3.6 tokens over each, a theme shared as a file),
// Personalization (`~/.mightling/AGENTS.md`), Archived threads, Git, Project, Worktrees. Pets,
// Browser, Computer Use and Suggested prompts are not offered here (Phase 3 or not at all).

import { useCallback, useEffect, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

import * as bridge from "./bridge";
import { Icon } from "./icons";
import { chordOf, conflicts, keyLabel, KEYMAP, shortcutLabel, type KeyAction } from "./keymap";
import { boardOn } from "./board";
import { cleanAppearance, READABLE_FONT, type Appearance, type AppearanceColours, type Prefs } from "./prefs";
import { useShownTheme, type ThemeChoice } from "./theme";
import type { Project } from "./protocol/v2/Project";
import type { Thread } from "./protocol/v2/Thread";
import { ACROSS_REPOS_KEY, type WorktreeSettings } from "./worktrees";

export type SettingsPage = "general" | "documents" | "profile" | "shortcuts" | "notifications" | "appearance" | "personalization" | "memories" | "archived" | "git" | "project" | "worktrees" | "browser" | "pets" | "suggestions" | "agents" | "node" | "voice";

export interface SettingsProps {
  page: SettingsPage;
  setPage: (page: SettingsPage) => void;
  prefs: Prefs;
  onChange: (values: Partial<Prefs>) => void;
  onClose: () => void;
  worktrees: WorktreeSettings;
  onWorktree: (key: "git-worktree-root" | "worktree-keep-count" | "worktree-auto-cleanup-enabled" | typeof ACROSS_REPOS_KEY, value: string | number | boolean | null) => void;
  /** Archived threads (`thread/list` with `archived`), and bringing one back (`thread/unarchive`). */
  listArchived: () => Promise<Thread[]>;
  /** Projects removed from the sidebar (projects.ts), and bringing one back. */
  removedProjects?: Project[];
  restoreProject?: (project: Project) => void;
  unarchive: (threadId: string) => Promise<void>;
  notice: (error: unknown) => void;
  /** The agent's settings as `config/read` answers them, and a write of one key (`config/value/write`). */
  readConfig: () => Promise<Record<string, unknown>>;
  writeConfig: (keyPath: string, value: unknown) => Promise<void>;
  /** What the memories hold (`memory/status`), and deleting them (`memory/reset`). */
  memoryStatus: () => Promise<{ v2ConsolidatedThreads: number; v2Ready: boolean } | null>;
  resetMemories: () => Promise<void>;
}

const PAGES: { id: SettingsPage; label: string; desktopOnly?: boolean }[] = [
  { id: "general", label: "General" },
  { id: "documents", label: "Your documents" },
  { id: "profile", label: "Profile", desktopOnly: true },
  { id: "shortcuts", label: "Keyboard shortcuts" },
  { id: "notifications", label: "Notifications", desktopOnly: true },
  { id: "appearance", label: "Appearance" },
  { id: "personalization", label: "Personalization", desktopOnly: true },
  { id: "memories", label: "Memories" },
  { id: "archived", label: "Archived threads" },
  { id: "git", label: "Git", desktopOnly: true },
  { id: "project", label: "Project and terminal", desktopOnly: true },
  { id: "worktrees", label: "Worktrees", desktopOnly: true },
  { id: "browser", label: "Browser", desktopOnly: true },
  { id: "pets", label: "Pets", desktopOnly: true },
  { id: "voice", label: "Voice", desktopOnly: true },
  { id: "suggestions", label: "Suggested prompts" },
  { id: "agents", label: "Agents" },
  { id: "node", label: "Node", desktopOnly: true },
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
            {props.page === "documents" ? <Documents notice={props.notice} /> : null}
            {props.page === "profile" ? <Profile /> : null}
            {props.page === "shortcuts" ? <Shortcuts {...props} /> : null}
            {props.page === "notifications" ? <Notifications {...props} /> : null}
            {props.page === "appearance" ? <AppearancePage {...props} /> : null}
            {props.page === "personalization" ? <Personalization notice={props.notice} /> : null}
            {props.page === "memories" ? <Memories {...props} /> : null}
            {props.page === "archived" ? <Archived {...props} /> : null}
            {props.page === "git" ? <Git {...props} /> : null}
            {props.page === "project" ? <Project {...props} /> : null}
            {props.page === "worktrees" && bridge.git.available() ? <Worktrees {...props} /> : null}
            {props.page === "browser" && bridge.preview.available() ? <Browser {...props} /> : null}
            {props.page === "pets" && bridge.git.available() ? <Pets {...props} /> : null}
            {props.page === "voice" && bridge.git.available() ? <Voice {...props} /> : null}
            {props.page === "suggestions" ? <Suggestions {...props} /> : null}
            {props.page === "agents" ? <Agents {...props} /> : null}
            {props.page === "node" && bridge.git.available() ? <NodePage notice={props.notice} /> : null}
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
        <label><input type="radio" name="enter" checked={prefs.enterBehaviour === "ctrlEnter"} onChange={() => onChange({ enterBehaviour: "ctrlEnter" })} /> Enter starts a new line; {keyLabel("Ctrl+Enter", "composer")} sends</label>
        <label><input type="radio" name="enter" checked={prefs.enterBehaviour === "multiline"} onChange={() => onChange({ enterBehaviour: "multiline" })} /> Enter sends a one-line prompt; once it has several lines, Enter starts a new one and {keyLabel("Ctrl+Enter", "composer")} sends</label>
      </fieldset>
      <fieldset>
        <legend>A message sent while the agent is working</legend>
        <label><input type="radio" name="follow" checked={prefs.followUp === "steer"} onChange={() => onChange({ followUp: "steer" })} /> Steer: the running turn takes it in now</label>
        <label><input type="radio" name="follow" checked={prefs.followUp === "queue"} onChange={() => onChange({ followUp: "queue" })} /> Queue: it waits, and starts the next turn when this one ends</label>
      </fieldset>
      <fieldset>
        <legend>While the agent works</legend>
        <label><input type="checkbox" checked={prefs.preventSleep} onChange={(event) => onChange({ preventSleep: event.target.checked })} /> Keep the machine from sleeping while a turn runs</label>
        <label><input type="checkbox" checked={prefs.openArtifacts} onChange={(event) => onChange({ openArtifacts: event.target.checked })} /> Open a document, PDF or image the agent creates in a tab beside the thread</label>
        <label><input type="checkbox" checked={prefs.pasteAsFile} onChange={(event) => onChange({ pasteAsFile: event.target.checked })} /> Make a long paste (2,000 characters or 40 lines) an attachment: a file in Ask, a block after your words in Work</label>
      </fieldset>
      {bridge.git.available() ? <QuickEntry prefs={prefs} onChange={onChange} /> : null}
      <fieldset>
        <legend>Text size</legend>
        <label className="field">{Math.round(prefs.fontScale * 100)}% ({shortcutLabel("fontBigger")} bigger, {shortcutLabel("fontSmaller")} smaller, {shortcutLabel("fontReset")} back to 100%)
          <input type="range" min={0.75} max={2} step={0.05} value={prefs.fontScale} aria-label="Text size" onChange={(event) => onChange({ fontScale: Number(event.target.value) })} />
        </label>
      </fieldset>
    </>
  );
}

/**
 * Settings › General › Quick Entry (DESKTOP_PARITY_CLAUDE §4.2), the desktop app's only: a question from
 * anywhere through a system-wide shortcut, and the app kept in the tray when its window closes. Both off
 * by default. The main process says whether the shortcut was taken, and why not.
 */
function QuickEntry({ prefs, onChange }: Pick<SettingsProps, "prefs" | "onChange">) {
  const [status, setStatus] = useState<bridge.QuickStatus | null>(null);
  const [capturing, setCapturing] = useState(false);
  useEffect(() => {
    let current = true;
    // After the main process has taken the new values in (prefs/write, then the shortcut registered).
    const timer = setTimeout(() => bridge.quickStatus().then((answer) => current && setStatus(answer)).catch(() => {}), 250);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [prefs.quickEntry, prefs.quickEntryShortcut]);
  return (
    <fieldset className="quick-entry">
      <legend>Quick Entry</legend>
      <label><input type="checkbox" checked={prefs.quickEntry} onChange={(event) => onChange({ quickEntry: event.target.checked })} /> Ask a quick question from any app with {keyLabel(prefs.quickEntryShortcut)}: a small box opens above your windows, Enter asks it in Ask</label>
      <label className="field">Shortcut
        <input readOnly value={capturing ? "Press the keys…" : prefs.quickEntryShortcut} aria-label="Quick Entry's shortcut"
          onFocus={() => setCapturing(true)} onBlur={() => setCapturing(false)}
          onKeyDown={(event: ReactKeyboardEvent<HTMLInputElement>) => {
            if (event.key === "Tab") return;
            event.preventDefault();
            const chord = chordOf(event.nativeEvent);
            if (!chord) return;
            onChange({ quickEntryShortcut: chord });
            setCapturing(false);
            event.currentTarget.blur();
          }} />
      </label>
      {prefs.quickEntry && status?.problem ? <p className="dialog-note warning" role="status">{status.problem}</p> : null}
      {prefs.quickEntry && status?.registered ? <p className="dialog-note" role="status">{status.shortcut} opens the box from any app while Mightling runs. The tray's New question… opens it too.</p> : null}
      <label><input type="checkbox" checked={prefs.keepInTray} onChange={(event) => onChange({ keepInTray: event.target.checked })} /> Keep Mightling in the tray when its window closes, so Quick Entry and running turns go on (Quit ends it)</label>
    </fieldset>
  );
}

/** A size in bytes, for people. */
export function humanBytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1)} GB`;
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
  return `${Math.round(n / 1024)} KB`;
}

/** How long ago a Unix time was, roughly. */
export function ago(seconds: number | null, now: number = Date.now() / 1000): string {
  if (seconds === null) return "never";
  const minutes = Math.max(0, Math.round((now - seconds) / 60));
  if (minutes < 1) return "just now";
  if (minutes < 120) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
}

/** One collection's line of state: documents, chunks, what failed or waits for OCR, the last run, disk. */
export function collectionSummary(c: bridge.DocsCollection): string {
  const n = c.counts;
  const parts = [`${n.indexed} documents`, `${n.chunks} passages${n.embedded < n.chunks ? ` (${n.chunks - n.embedded} not yet searchable by meaning)` : ""}`];
  if (n.pending) parts.push(`${n.pending} waiting`);
  if (n.failed) parts.push(`${n.failed} could not be read`);
  if (n.needs_ocr_pages) parts.push(`${n.needs_ocr_pages} scanned pages without text`);
  parts.push(`last indexed ${ago(c.last_run)}${c.last_status && c.last_status !== "ok" ? ` (${c.last_status})` : ""}`);
  parts.push(humanBytes(c.disk_bytes));
  return parts.join(" · ");
}

/** Settings › Your documents (LOCAL_INDEX §8.3): the folders the agent can search, their state, and changing them. */
function Documents({ notice }: { notice: (error: unknown) => void }) {
  const [status, setStatus] = useState<bridge.DocsStatus | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => bridge.docs.status().then((answer) => { setStatus(answer); setFailed(null); }).catch((error: unknown) => setFailed(error instanceof Error ? error.message : String(error)));
  useEffect(() => {
    void load();
    // the state changes while indexing runs
    const timer = window.setInterval(() => void load(), 10_000);
    return () => window.clearInterval(timer);
  }, []);
  const act = (work: Promise<unknown>) => {
    setBusy(true);
    work.then(load).catch(notice).finally(() => setBusy(false));
  };
  const add = (path: string) => {
    if (!path.trim()) return;
    act(bridge.docs.add(path.trim()).then(() => setFolder("")));
  };
  return (
    <>
      <h3>Your documents</h3>
      <p className="dialog-note">Folders the agent can search, in every thread, with citations that open the file. They are indexed on this machine and nothing leaves it; hidden files, ~/.ssh, ~/.config and files named like secrets are never read.</p>
      {failed ? <p className="dialog-note">{failed}</p> : null}
      {status?.runtime_missing ? <p className="dialog-note">Indexing waits: {status.runtime_missing}.</p> : null}
      {status === null && !failed ? <p className="empty small">…</p> : null}
      {status && status.collections.length === 0 ? <p className="empty small">No folders yet.</p> : null}
      <ul className="settings-list documents">
        {status?.collections.map((c) => (
          <li key={c.name}>
            <div><strong>{c.name}</strong> <code>{c.root}</code>{c.enabled ? null : " (off)"}</div>
            <div className="small">{collectionSummary(c)}</div>
            <div className="buttons">
              <button disabled={busy} onClick={() => act(bridge.docs.reindex(c.name))}>Rebuild</button>
              <button className="danger-button" disabled={busy} onClick={() => window.confirm(`Stop searching ${c.root}? Its index (the documents' text) is deleted; the files are not touched.${c.default ? " It is a default folder: it will not be added back." : ""}`) && act(bridge.docs.remove(c.name))}>Remove</button>
            </div>
          </li>
        ))}
      </ul>
      <fieldset>
        <legend>Add a folder</legend>
        <label className="field">Full path
          <input value={folder} placeholder="/home/you/Documents/contracts" aria-label="Folder to add" onChange={(event) => setFolder(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") add(folder); }} />
        </label>
        <div className="buttons">
          {bridge.host() === "electron" ? <button disabled={busy} onClick={() => bridge.chooseFolder().then((chosen) => { if (chosen) add(chosen); }).catch(notice)}>Choose…</button> : null}
          <button disabled={busy || !folder.trim()} onClick={() => add(folder)}>Add</button>
        </div>
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
        <input type="search" value={query} placeholder={byKeys ? `Press keys, then type them here, e.g. ${keyLabel("Ctrl+K")}` : "Search by command"} aria-label="Search shortcuts"
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
                    : action.keys.map((key) => <kbd key={key}>{keyLabel(key, action.context)}</kbd>)}
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
        <label><input type="checkbox" checked={props.worktrees.acrossRepos} onChange={(event) => props.onWorktree(ACROSS_REPOS_KEY, event.target.checked)} /> Count them across all repositories, not per repository</label>
      </fieldset>
    </>
  );
}

function Profile() {
  const [data, setData] = useState<bridge.Insights | null | "loading">("loading");
  useEffect(() => {
    bridge.insights().then(setData).catch(() => setData(null));
  }, []);
  if (data === "loading") return <p className="empty small">Reading the session files…</p>;
  if (!data) return <p className="empty small">No session files were found.</p>;
  const rows: [string, string][] = [
    ["Sessions", `${data.sessions.toLocaleString()} on ${data.days.toLocaleString()} days`],
    ["Tokens, all time", data.lifetimeTokens.toLocaleString()],
    ["The busiest day", data.peakDay ? `${data.peakDay.day}: ${data.peakDay.tokens.toLocaleString()} tokens` : "none yet"],
    ["Streak", `${data.currentStreak} day${data.currentStreak === 1 ? "" : "s"} now, ${data.longestStreak} at the longest`],
    ["The longest session", data.longestSession ? `${data.longestSession.minutes} minutes` : "none yet"],
  ];
  return (
    <>
      <h3>Profile</h3>
      <p className="dialog-note">Read from this machine's session files (~/.mightling/sessions); nothing is sent anywhere.</p>
      <dl className="info-rows">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    </>
  );
}

function Notifications({ prefs, onChange }: SettingsProps) {
  // The desktop decides whether notifications show at all (its own settings); the button shows one now.
  return (
    <>
      <h3>Notifications</h3>
      <fieldset>
        <legend>When a turn ends</legend>
        <label><input type="radio" name="turns" checked={prefs.notifyTurns === "unfocused"} onChange={() => onChange({ notifyTurns: "unfocused" })} /> Notify when the window is not in front</label>
        <label><input type="radio" name="turns" checked={prefs.notifyTurns === "always"} onChange={() => onChange({ notifyTurns: "always" })} /> Always notify</label>
        <label><input type="radio" name="turns" checked={prefs.notifyTurns === "never"} onChange={() => onChange({ notifyTurns: "never" })} /> Never</label>
      </fieldset>
      <fieldset>
        <legend>When the agent waits for you</legend>
        <label><input type="checkbox" checked={prefs.notifyApprovals} onChange={(event) => onChange({ notifyApprovals: event.target.checked })} /> Notify when it waits for an approval and the window is not in front</label>
        <label><input type="checkbox" checked={prefs.notifyQuestions ?? prefs.notifyApprovals} onChange={(event) => onChange({ notifyQuestions: event.target.checked })} /> Notify when it asks a question and the window is not in front</label>
      </fieldset>
      <p className="dialog-note">A notification is titled with its thread's name and says how the turn ended, or what waits. Notifications are silent: Mightling has no sounds of its own. A click on one shows its thread.</p>
      <div className="buttons dialog-buttons"><button onClick={() => void bridge.testNotification()}>Send a test notification</button></div>
    </>
  );
}

const THEME_LABELS: Record<ThemeChoice, string> = { system: "System", light: "Light", dark: "Dark" };

/**
 * Appearance: the theme (System follows the system's, live), then the accent, canvas and text
 * colours of the theme shown and the fonts of both; a theme is a small JSON file.
 */
function AppearancePage({ prefs, onChange, notice }: SettingsProps) {
  const shown = useShownTheme();
  const set = (values: Appearance) => onChange({ appearance: cleanAppearance({ ...prefs.appearance, ...values }) });
  // The colours belong to the theme shown: the light set at the top level, the dark one under `dark`.
  const own: AppearanceColours = shown === "dark" ? { accent: prefs.appearance.dark?.accent ?? prefs.appearance.accent, ...prefs.appearance.dark } : prefs.appearance;
  const setColour = (values: AppearanceColours) => (shown === "dark" ? set({ dark: { ...prefs.appearance.dark, ...values } }) : set(values));
  const current = getComputedStyle(document.documentElement);
  const colour = (key: "accent" | "canvas" | "text", token: string) => own[key] ?? (current.getPropertyValue(token).trim() || "#000000");
  const exportTheme = () => {
    const blob = new Blob([JSON.stringify({ mightlingTheme: 1, ...prefs.appearance }, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "mightling-theme.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  };
  const importTheme = async (file: File | undefined) => {
    if (!file) return;
    try {
      onChange({ appearance: cleanAppearance(JSON.parse(await file.text())) });
    } catch (error) {
      notice(error);
    }
  };
  return (
    <>
      <h3>Appearance</h3>
      <fieldset>
        <legend>Theme</legend>
        <span className="segmented theme-choice" role="radiogroup" aria-label="Theme">
          {(Object.keys(THEME_LABELS) as ThemeChoice[]).map((choice) => (
            <button key={choice} role="radio" aria-checked={prefs.theme === choice} className={prefs.theme === choice ? "selected" : ""} onClick={() => onChange({ theme: choice })}>
              <Icon name={choice === "system" ? "monitor" : choice === "light" ? "sun" : "moon"} size={14} /> {THEME_LABELS[choice]}
            </button>
          ))}
        </span>
        <p className="dialog-note">{prefs.theme === "system" ? `System follows this computer's theme; it is ${shown} now.` : `${THEME_LABELS[prefs.theme]} whatever this computer's theme is.`}</p>
      </fieldset>
      <fieldset>
        <legend>Colours of the {shown} theme</legend>
        <label className="colour"><input type="color" value={colour("accent", "--accent")} aria-label="Accent colour" onChange={(event) => setColour({ accent: event.target.value })} /> Accent</label>
        <label className="colour"><input type="color" value={colour("canvas", "--canvas")} aria-label="Background colour" onChange={(event) => setColour({ canvas: event.target.value })} /> Background</label>
        <label className="colour"><input type="color" value={colour("text", "--text")} aria-label="Text colour" onChange={(event) => setColour({ text: event.target.value })} /> Text</label>
        <p className="dialog-note">The {shown === "dark" ? "light" : "dark"} theme keeps colours of its own{shown === "dark" ? "; the accent is the light theme's unless set here" : ""}.</p>
      </fieldset>
      <fieldset>
        <legend>Fonts</legend>
        <label><input type="checkbox" checked={prefs.appearance.fontUi === READABLE_FONT} onChange={(event) => set({ fontUi: event.target.checked ? READABLE_FONT : undefined })} /> A font made for reading: Atkinson Hyperlegible, its letters hard to mistake for each other</label>
        <label className="field">Interface font <input key={prefs.appearance.fontUi ?? ""} defaultValue={prefs.appearance.fontUi ?? ""} placeholder="Inter, sans-serif" aria-label="Interface font" onBlur={(event) => set({ fontUi: event.target.value.trim() || undefined })} /></label>
        <label className="field">Code font <input defaultValue={prefs.appearance.fontCode ?? ""} placeholder='"JetBrains Mono", monospace' aria-label="Code font" onBlur={(event) => set({ fontCode: event.target.value.trim() || undefined })} /></label>
      </fieldset>
      <div className="buttons dialog-buttons">
        <button onClick={exportTheme}>Save the theme to a file</button>
        <label className="file-button">Use a theme file<input type="file" accept="application/json,.json" hidden onChange={(event) => void importTheme(event.target.files?.[0])} /></label>
        <button onClick={() => onChange({ appearance: {} })}>Back to Mightling's own</button>
      </div>
    </>
  );
}

function Personalization({ notice }: { notice: (error: unknown) => void }) {
  const [file, setFile] = useState<{ path: string; text: string } | null>(null);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    bridge.readPersonal().then((answer) => {
      setFile(answer);
      setText(answer?.text ?? "");
    }).catch(notice);
  }, [notice]);
  return (
    <>
      <h3>Personalization</h3>
      <p className="dialog-note">Instructions every thread on this machine reads, before a project's own AGENTS.md{file ? ` (${file.path})` : ""}.</p>
      <textarea className="personal" rows={14} value={text} aria-label="Personal instructions" onChange={(event) => { setText(event.target.value); setSaved(false); }}
        placeholder="For example: answer in British English; prefer small commits." />
      <div className="buttons dialog-buttons">
        <button className="primary" onClick={() => bridge.writePersonal(text).then(() => setSaved(true)).catch(notice)}>Save</button>
        {saved ? <span className="dialog-note">Saved. New threads read it.</span> : null}
      </div>
    </>
  );
}

/** How a node is called on the page: its name, else the start of its id (never its address). */
function nodeLabel(node: { name: string; id: string }): string {
  return node.name || (node.id ? `node ${node.id.slice(0, 8)}` : "a node with no name");
}

/**
 * Settings › Node: the Mightling nodes on the network (`ling node list`), the one in use marked,
 * and Use for another (`ling node use <name>`). Names only: nothing here takes or shows an address.
 * The list is browsed when the page opens and on Refresh, never at start-up.
 */
function NodePage({ notice }: { notice: (error: unknown) => void }) {
  const [list, setList] = useState<bridge.NodeList | null>(null);
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState<{ label: string; waiting: boolean } | null>(null);
  const load = useCallback(() => {
    setBusy(true);
    bridge.nodes.list().then(setList).catch(notice).finally(() => setBusy(false));
  }, [notice]);
  useEffect(load, [load]);
  const use = (node: bridge.NodeEntry) => {
    if (!node.choice) return;
    setBusy(true);
    bridge.nodes.use(node.choice)
      .then((said) => {
        setChosen({ label: nodeLabel(node), waiting: /does not answer/.test(said) });
        load();
      })
      .catch((error) => {
        setBusy(false);
        notice(error);
      });
  };
  const client = list?.thisMachine === "client";
  return (
    <>
      <h3>Node</h3>
      <p className="dialog-note">The Mightling nodes on this network: the machine whose model server this one&apos;s agent uses. A node is chosen by its name; the one chosen is remembered, and found again by name when it moves.</p>
      {list?.thisMachine === "node" ? <p className="dialog-note node-note">This machine is a node: its agent uses its own model server, so none is chosen here.</p> : null}
      {list?.thisMachine === "configured" ? <p className="dialog-note node-note">This machine is set to use one model server (DREAMFERENCE_VLLM_HOST or vllm_host), so no node is chosen here.</p> : null}
      {list === null ? <p className="empty small">{busy ? "Looking for nodes…" : "No list."}</p> : list.nodes.length === 0 && !list.rememberedAway ? (
        <p className="empty small">No Mightling node answered on this network.</p>
      ) : (
        <table className="shortcut-table nodes" aria-label="Nodes">
          <tbody>
            {list.nodes.map((node) => (
              <tr key={node.id || node.name} className={node.inUse ? "node in-use" : "node"}>
                <td>{nodeLabel(node)}{node.inUse ? <span className="node-current"> · in use</span> : null}</td>
                <td className="planned-note">
                  {node.model ? `${node.model}${node.contextTokens ? ` (${node.contextTokens.toLocaleString()} tokens)` : ""}` : node.state || "not answering"}
                  {node.coding ? "" : " · not a coding model"}
                  {node.version ? ` · Mightling ${node.version}` : ""}
                </td>
                <td className="shortcut-actions">
                  {client && !node.inUse && node.choice ? <button className="link-button" disabled={busy} onClick={() => use(node)}>Use</button> : null}
                </td>
              </tr>
            ))}
            {list.rememberedAway ? (
              <tr className="node in-use away">
                <td>{nodeLabel(list.rememberedAway)}<span className="node-current"> · in use</span></td>
                <td className="planned-note">remembered; did not answer this time</td>
                <td />
              </tr>
            ) : null}
          </tbody>
        </table>
      )}
      {list?.hint ? <p className="dialog-note">{list.hint}</p> : null}
      {chosen ? (
        <p className="dialog-note">
          {`Using ${chosen.label}.`}{chosen.waiting ? " Its model server does not answer right now; Mightling waits for it." : ""} The agent&apos;s server keeps the node it started with until Mightling starts again.{" "}
          <button className="link-button" onClick={() => void bridge.relaunchApp().catch(notice)}>Restart Mightling</button>
        </p>
      ) : null}
      <div className="buttons dialog-buttons">
        <button disabled={busy} onClick={load}>{busy ? "Looking…" : "Refresh"}</button>
      </div>
    </>
  );
}

function Archived({ listArchived, unarchive, notice, removedProjects, restoreProject }: SettingsProps) {
  const [threads, setThreads] = useState<Thread[] | null>(null);
  useEffect(() => {
    listArchived().then(setThreads).catch((error) => { setThreads([]); notice(error); });
  }, [listArchived, notice]);
  return (
    <>
      {removedProjects && removedProjects.length > 0 ? (
        <>
          <h3>Removed projects</h3>
          <table className="shortcut-table archived">
            <tbody>
              {removedProjects.map((project) => (
                <tr key={project.id}>
                  <td>{project.name}</td>
                  <td className="planned-note">{project.roots.map((root) => root.path.split(/[\\/]/).filter(Boolean).pop()).join(", ") || "no folder"}</td>
                  <td className="shortcut-actions"><button className="link-button" onClick={() => restoreProject?.(project)}>Restore</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
      <h3>Archived threads</h3>
      {threads === null ? <p className="empty small">…</p> : threads.length === 0 ? <p className="empty small">No archived threads.</p> : (
        <table className="shortcut-table archived">
          <tbody>
            {threads.map((thread) => (
              <tr key={thread.id}>
                <td>{thread.name || thread.preview || "(new thread)"}</td>
                <td className="planned-note">{thread.cwd.split(/[\\/]/).filter(Boolean).pop()} · {new Date(thread.updatedAt * 1000).toLocaleDateString()}</td>
                <td className="shortcut-actions"><button className="link-button" onClick={() => unarchive(thread.id).then(() => setThreads((list) => list?.filter((entry) => entry.id !== thread.id) ?? null)).catch(notice)}>Unarchive</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function Git({ prefs, onChange }: SettingsProps) {
  return (
    <>
      <h3>Git</h3>
      <fieldset>
        <legend>Branches</legend>
        <label className="field">Create branch here starts the name with
          <input defaultValue={prefs.branchPrefix} aria-label="Branch prefix" onBlur={(event) => onChange({ branchPrefix: event.target.value.trim() })} />
        </label>
        <label><input type="checkbox" checked={prefs.forcePush} onChange={(event) => onChange({ forcePush: event.target.checked })} /> Push with --force-with-lease by default</label>
      </fieldset>
      <fieldset>
        <legend>The instruction a written commit message starts from</legend>
        <textarea rows={4} defaultValue={prefs.commitPrompt ?? ""} aria-label="Commit message instruction" placeholder="Empty: Mightling's own (a summary line in the imperative, then why)"
          onBlur={(event) => onChange({ commitPrompt: event.target.value.trim() || null })} />
      </fieldset>
      <fieldset>
        <legend>/review delivers its findings</legend>
        <label><input type="radio" name="review" checked={prefs.reviewDelivery === "inline"} onChange={() => onChange({ reviewDelivery: "inline" })} /> Inline, in the thread</label>
        <label><input type="radio" name="review" checked={prefs.reviewDelivery === "detached"} onChange={() => onChange({ reviewDelivery: "detached" })} /> Detached, in a review thread of their own</label>
      </fieldset>
      <p className="dialog-note">The review pane's Pull request opens the forge's own page in your browser; Create pull request in the pull-request tab opens one through <code>gh</code>.</p>
      <fieldset>
        <legend>Pull requests</legend>
        <label><input type="checkbox" checked={prefs.pullRequests} onChange={(event) => onChange({ pullRequests: event.target.checked })} /> A pull-request tab, through your GitHub CLI (<code>gh</code>) and its sign-in</label>
        <span className="planned-note">Off by default: it talks to GitHub, so it is refused while airgapped is on. Review with Mightling and Fix put a request in the composer; a review is submitted as you only after you confirm.</span>
      </fieldset>
      <fieldset>
        <legend>The instruction a written pull request description starts from</legend>
        <textarea rows={4} defaultValue={prefs.pullRequestPrompt ?? ""} aria-label="Pull request description instruction" placeholder="Empty: Mightling's own (what changes and why, then how it was tested)"
          onBlur={(event) => onChange({ pullRequestPrompt: event.target.value.trim() || null })} />
      </fieldset>
    </>
  );
}

function Project({ prefs, onChange }: SettingsProps) {
  const [editors, setEditors] = useState<bridge.Editor[]>([]);
  useEffect(() => {
    bridge.listEditors().then(setEditors).catch(() => setEditors([]));
  }, []);
  return (
    <>
      <h3>Project and terminal</h3>
      <fieldset>
        <legend>Commands the agent runs, in the thread</legend>
        <label><input type="radio" name="commands" checked={prefs.commandOutput === "running"} onChange={() => onChange({ commandOutput: "running" })} /> Show the output while a command runs</label>
        <label><input type="radio" name="commands" checked={prefs.commandOutput === "collapsed"} onChange={() => onChange({ commandOutput: "collapsed" })} /> Fold every command (click to open)</label>
        <label><input type="radio" name="commands" checked={prefs.commandOutput === "hidden"} onChange={() => onChange({ commandOutput: "hidden" })} /> Hide commands</label>
      </fieldset>
      <fieldset>
        <legend>Terminal tabs</legend>
        <label className="field">Shell (empty for your login shell, $SHELL)
          <input defaultValue={prefs.terminalShell ?? ""} placeholder="/bin/bash" aria-label="Terminal shell" onBlur={(event) => onChange({ terminalShell: event.target.value.trim() || null })} />
        </label>
        <label><input type="radio" name="terminal-place" checked={prefs.terminalPlace === "panel"} onChange={() => onChange({ terminalPlace: "panel" })} /> Open terminals in the bottom panel</label>
        <label><input type="radio" name="terminal-place" checked={prefs.terminalPlace === "tabs"} onChange={() => onChange({ terminalPlace: "tabs" })} /> Open terminals as tabs beside the thread, with its files</label>
        <p className="dialog-note">A terminal opens with {shortcutLabel("terminal")} or New tab › Terminal, in the thread's folder, under the permissions the composer shows, so airgapped holds for what you type there too.</p>
        <label><input type="checkbox" checked={prefs.agentReadsWindow} onChange={(event) => onChange({ agentReadsWindow: event.target.checked })} /> Let the agent read the thread's terminal tabs and the preview's console when it needs to (new threads)</label>
        <p className="dialog-note">It reads them on this machine, the latest lines only, with anything that looks like a password or token masked; nothing is sent anywhere else.</p>
      </fieldset>
      <fieldset>
        <legend>Where files open</legend>
        <label className="field">From the review pane and the header's Open
          <select value={prefs.editor ?? ""} aria-label="Editor" onChange={(event) => onChange({ editor: event.target.value || null })}>
            <option value="">The system's choice</option>
            {editors.map((editor) => <option key={editor.id} value={editor.id}>{editor.name}</option>)}
          </select>
        </label>
      </fieldset>
    </>
  );
}

/** The memory switches in config.toml (Codex's `features.memories` and `[memories]`), and what they mean here. */
export function memorySwitches(config: Record<string, unknown>): { enabled: boolean; use: boolean; generate: boolean; external: boolean } {
  const features = (config.features ?? {}) as Record<string, unknown>;
  const memories = (config.memories ?? {}) as Record<string, unknown>;
  return {
    enabled: features.memories === true,
    use: memories.use_memories !== false,
    generate: memories.generate_memories !== false,
    external: memories.disable_on_external_context !== true,
  };
}

function Memories({ readConfig, writeConfig, notice, memoryStatus, resetMemories, prefs, onChange }: SettingsProps) {
  const [switches, setSwitches] = useState<ReturnType<typeof memorySwitches> | null>(null);
  const [status, setStatus] = useState<{ v2ConsolidatedThreads: number; v2Ready: boolean } | null>(null);
  useEffect(() => {
    memoryStatus().then(setStatus).catch(() => setStatus(null));
  }, [memoryStatus]);
  const load = () => readConfig().then((config) => setSwitches(memorySwitches(config))).catch((error) => { setSwitches(null); notice(error); });
  useEffect(() => {
    void load();
    // load once when the page opens
  }, []);
  const write = (keyPath: string, value: boolean) => writeConfig(keyPath, value).then(load).catch(notice);
  if (!switches) return <p className="empty small">…</p>;
  return (
    <>
      <h3>Memories</h3>
      <p className="dialog-note">Memories are notes the agent keeps between threads, under ~/.mightling/memories/. Making them runs on the model this machine serves; nothing leaves it. Kept in config.toml, shared with ling.</p>
      <fieldset>
        <label><input type="checkbox" checked={switches.enabled} aria-label="Memories" onChange={(event) => void write("features.memories", event.target.checked)} /> Memories on</label>
        <label><input type="checkbox" checked={switches.use} disabled={!switches.enabled} onChange={(event) => void write("memories.use_memories", event.target.checked)} /> New threads use what is remembered</label>
        <label><input type="checkbox" checked={switches.generate} disabled={!switches.enabled} onChange={(event) => void write("memories.generate_memories", event.target.checked)} /> Threads add to the memories</label>
        <label><input type="checkbox" checked={switches.external} disabled={!switches.enabled} onChange={(event) => void write("memories.disable_on_external_context", !event.target.checked)} /> Including threads that used the web or an MCP server</label>
      </fieldset>
      {bridge.recall.available() ? (
        <fieldset className="recall">
          <legend>Earlier questions</legend>
          <label><input type="checkbox" checked={prefs.recallQuestions} onChange={(event) => onChange({ recallQuestions: event.target.checked })} /> Let the agent search your earlier Ask questions</label>
          <p className="dialog-note">Off by default. When on, Mightling keeps a copy of each Ask question and its answers as a Markdown file under ~/.mightling/ask-history/ and gives the agent a skill, earlier-questions, to search and read them on this machine, read only. Incognito questions are never kept, archived ones are removed, and turning this off deletes the copies.</p>
        </fieldset>
      ) : null}
      {bridge.memoryFiles.available() ? <MemoryFiles notice={notice} /> : null}
      <p className="dialog-note">{status ? `${status.v2ConsolidatedThreads} thread${status.v2ConsolidatedThreads === 1 ? "" : "s"} remembered${status.v2Ready ? "" : "; the memories are still being made"}.` : ""} /memories in a thread turns them off or on for that thread.</p>
      <div className="buttons dialog-buttons">
        <button className="danger-button" onClick={() => window.confirm("Delete everything the agent remembers? This cannot be undone.") && resetMemories().then(() => memoryStatus().then(setStatus)).catch(notice)}>Delete the memories</button>
      </div>
    </>
  );
}

/** What the agent remembers, one file at a time (DESKTOP §16 B17): shown, edited, saved. */
function MemoryFiles({ notice }: { notice: (error: unknown) => void }) {
  const [files, setFiles] = useState<bridge.MemoryFile[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const load = useCallback(() => bridge.memoryFiles.list().then((list) => { setFiles(list); setDrafts({}); }).catch((error) => { setFiles([]); notice(error); }), [notice]);
  useEffect(() => { void load(); }, [load]);
  if (files === null) return null;
  return (
    <fieldset className="memory-files">
      <legend>What the agent remembers</legend>
      {files.length === 0 ? <p className="dialog-note">Nothing yet: the memories are made from finished threads, a while after they end.</p> : files.map((file) => {
        const text = drafts[file.name] ?? file.text;
        const changed = drafts[file.name] !== undefined && drafts[file.name] !== file.text;
        return (
          <div key={file.name} className="memory-file">
            <label className="field">
              <span><code>{file.name}</code>: {file.about}</span>
              <textarea rows={8} aria-label={file.name} value={text} readOnly={file.cut} spellCheck={false} onChange={(event) => setDrafts({ ...drafts, [file.name]: event.target.value })} />
            </label>
            {file.cut ? <p className="dialog-note">Too long to edit here; open it from ~/.mightling/memories/.</p> : (
              <div className="buttons">
                <button className="primary" disabled={!changed} onClick={() => void bridge.memoryFiles.save(file.name, text).then(load).catch(notice)}>Save</button>
                <button disabled={!changed} onClick={() => setDrafts(({ [file.name]: _gone, ...rest }) => rest)}>Undo</button>
              </div>
            )}
          </div>
        );
      })}
      <p className="dialog-note">Edit a line to correct it, or delete it so the agent forgets it. The memory pipeline reads your edits the next time it brings the memories up to date.</p>
    </fieldset>
  );
}

/** Settings › Browser (DESKTOP_PARITY §2.9, §2.15): the preview tab's sites, developer mode, its data. */
function Browser({ prefs, onChange, notice }: SettingsProps) {
  const [site, setSite] = useState("");
  const sites = Object.entries(prefs.previewSites).sort(([a], [b]) => a.localeCompare(b));
  const set = (host: string, choice: "allow" | "block" | null) => {
    const next = { ...prefs.previewSites };
    if (choice) next[host] = choice;
    else delete next[host];
    onChange({ previewSites: next });
  };
  return (
    <>
      <h3>Browser</h3>
      <p className="dialog-note">The preview tab ({shortcutLabel("previewOpen")}) opens pages on this machine, on the local network and files without asking. A site on the internet asks first; what you chose is kept here. With airgapped on, only this machine is reachable, whatever is allowed. A download always asks where to save it.</p>
      <fieldset>
        <legend>Sites on the internet</legend>
        {sites.length === 0 ? <p className="empty small">None yet: every site asks.</p> : null}
        <table className="sites">
          <tbody>
            {sites.map(([host, choice]) => (
              <tr key={host}>
                <td>{host}</td>
                <td>
                  <select aria-label={`${host}: allow or block`} value={choice} onChange={(event) => set(host, event.target.value as "allow" | "block")}>
                    <option value="allow">Allow</option>
                    <option value="block">Block</option>
                  </select>
                </td>
                <td><button className="link-button" onClick={() => set(host, null)}>Ask again</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <form className="add-site" onSubmit={(event) => {
          event.preventDefault();
          const host = site.trim().toLowerCase().replace(/^[a-z]+:\/\//, "").split(/[/:?#]/)[0];
          if (host) set(host, "block");
          setSite("");
        }}>
          <input value={site} placeholder="example.com" aria-label="A site to block" onChange={(event) => setSite(event.target.value)} />
          <button type="submit">Block</button>
        </form>
      </fieldset>
      <fieldset>
        <legend>Developer mode</legend>
        <label><input type="checkbox" checked={prefs.previewDevTools} onChange={(event) => onChange({ previewDevTools: event.target.checked })} /> Developer tools for the preview tab (preview tabs opened from now on)</label>
      </fieldset>
      <fieldset>
        <legend>Data</legend>
        <button onClick={() => bridge.preview.clear().then(() => notice("The preview's cookies, storage and cache are cleared.")).catch(notice)}>Clear the preview's cookies, storage and cache</button>
      </fieldset>
    </>
  );
}

/** Settings › Pets (DESKTOP_PARITY §2.15, Phase 3 item 6): the pet on or off, its size, Mightling's mark or a picture of your own. */
/** The word list as the text area shows it (one per line), and back. */
export function wordsFromText(text: string): string[] {
  return [...new Set(text.split("\n").map((line) => line.trim()).filter(Boolean))].slice(0, 200);
}

function Voice({ prefs, onChange }: SettingsProps) {
  // Kept as typed until the field is left, so a half-typed line is not tidied away under the caret.
  const [text, setText] = useState(prefs.dictationWords.join("\n"));
  return (
    <>
      <h3>Voice</h3>
      <p className="dialog-note">Dictation turns speech into text on this machine (<code>ling-admin voice start</code>); nothing is sent anywhere, so it works with airgapped on. {shortcutLabel("voice")} starts and stops it, as the microphone button does; the text goes into the composer for you to read before sending.</p>
      <label className="field">
        Words to spell as written: names, paths, symbols, one per line
        <textarea rows={8} aria-label="Words to spell as written" value={text} placeholder={"Mightling\nfetcher/client.py\nConnectionError"}
          onChange={(event) => setText(event.target.value)}
          onBlur={() => onChange({ dictationWords: wordsFromText(text) })} />
      </label>
      <p className="dialog-note">They are given to the speech-to-text service with each recording, so it prefers these spellings. The first few hundred characters are used.</p>
    </>
  );
}

function Pets({ prefs, onChange, notice }: SettingsProps) {
  return (
    <>
      <h3>Pets</h3>
      <p className="dialog-note">A small companion above your other windows that shows what the agent is doing: resting, working, waiting for you, done. Click it for a quick question or to open Mightling; drag it anywhere. Super+Alt+P shows or hides it.</p>
      <fieldset>
        <legend>The pet</legend>
        <label><input type="checkbox" checked={prefs.petShown} onChange={(event) => onChange({ petShown: event.target.checked })} /> Show the pet</label>
      </fieldset>
      <fieldset>
        <legend>Size</legend>
        {(["small", "medium", "large"] as const).map((size) => (
          <label key={size}><input type="radio" name="pet-size" checked={prefs.petSize === size} onChange={() => onChange({ petSize: size })} /> {size === "small" ? "Mini" : size === "medium" ? "Medium" : "Large"}</label>
        ))}
      </fieldset>
      <fieldset>
        <legend>Look</legend>
        <label><input type="radio" name="pet-look" checked={!prefs.petImage} onChange={() => onChange({ petImage: null })} /> Mightling's ling</label>
        <label><input type="radio" name="pet-look" checked={Boolean(prefs.petImage)} onChange={() => bridge.chooseImage().then((file) => file && onChange({ petImage: file })).catch(notice)} /> A picture of your own{prefs.petImage ? `: ${prefs.petImage.split("/").pop()}` : " (PNG, GIF, WebP, SVG or JPEG, 2 MB at most)"}</label>
        {prefs.petImage ? <button className="link-button" onClick={() => bridge.chooseImage().then((file) => file && onChange({ petImage: file })).catch(notice)}>Choose another picture</button> : null}
      </fieldset>
    </>
  );
}

/** Settings › Suggested prompts (DESKTOP_PARITY §2.15, Phase 3 item 7). */
function Suggestions({ prefs, onChange }: SettingsProps) {
  return (
    <>
      <h3>Suggested prompts</h3>
      <p className="dialog-note">When a turn ends, the served model predicts the message you are most likely to send next. It shows in grey in the empty composer; Tab or → puts it there to edit or send, and typing anything else ignores it. It is never sent by itself. Each prediction is one short extra turn on this machine's model, in a read-only thread that is not kept, and only when nothing else is running.</p>
      <fieldset>
        <legend>Next message</legend>
        <label><input type="checkbox" checked={prefs.suggestPrompts} onChange={(event) => onChange({ suggestPrompts: event.target.checked })} /> Predict my next message when a turn ends</label>
      </fieldset>
      <fieldset>
        <legend>Summaries of finished chats</legend>
        <label><input type="checkbox" checked={prefs.summarizeThreads} onChange={(event) => onChange({ summarizeThreads: event.target.checked })} /> Summarize a chat left alone for a day</label>
        <p className="dialog-note">Two lines from this machine's model, shown under the chat's title when you point at it and in Search threads, and found by a search. Made one at a time, only when nothing else is running, in a read-only thread that is not kept; a chat that changes is summarized again a day later.</p>
      </fieldset>
    </>
  );
}

/** Settings › Agents (DESKTOP_PARITY §2.13, Phase 3 item 10): the message board subagents post to each other on. */
function Agents({ readConfig, writeConfig, notice }: SettingsProps) {
  const [on, setOn] = useState<boolean | null>(null);
  const load = () => readConfig().then((config) => setOn(boardOn(config))).catch((error) => { setOn(null); notice(error); });
  useEffect(() => {
    void load();
    // read once when the page opens
  }, []);
  const set = async (value: boolean) => {
    try {
      await writeConfig("features.multi_agent_v2", value);
      await writeConfig("features.agent_message_board", value);
      await load();
    } catch (error) {
      notice(error);
    }
  };
  return (
    <>
      <h3>Agents</h3>
      <p className="dialog-note">Subagents can keep discussions with each other on a message board: channels and threads of posts, stored on this machine (agent_message_board_1.sqlite beside the sessions). It uses the second version of the multi-agent tools, and the board is still marked as under development in Codex. Kept in config.toml, shared with ling; new threads use it.</p>
      <fieldset>
        <label><input type="checkbox" checked={on === true} disabled={on === null} onChange={(event) => void set(event.target.checked)} /> A message board for subagents (multi_agent_v2 and agent_message_board)</label>
      </fieldset>
      <p className="dialog-note">With it on, Message board in the sidebar shows what they posted, read only.</p>
    </>
  );
}
