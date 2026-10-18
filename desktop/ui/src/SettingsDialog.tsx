// The settings (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.15; Ctrl+,): pages in a dialog,
// window settings in the window's preferences and agent settings in config.toml through the
// server. General, Profile (statistics from the local session files), Keyboard shortcuts (§2.19),
// Notifications, Appearance (the §3.6 tokens, light base only, a theme shared as a file),
// Personalization (`~/.mightling/AGENTS.md`), Archived threads, Git, Project, Worktrees. Pets,
// Browser, Computer Use and Suggested prompts are not offered here (Phase 3 or not at all).

import { useEffect, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

import * as bridge from "./bridge";
import { Icon } from "./icons";
import { KEYMAP, chordOf, conflicts, type KeyAction } from "./keymap";
import { cleanAppearance, type Appearance, type Prefs } from "./prefs";
import type { Thread } from "./protocol/v2/Thread";
import type { WorktreeSettings } from "./worktrees";

export type SettingsPage = "general" | "profile" | "shortcuts" | "notifications" | "appearance" | "personalization" | "memories" | "archived" | "git" | "project" | "worktrees" | "browser" | "pets";

export interface SettingsProps {
  page: SettingsPage;
  setPage: (page: SettingsPage) => void;
  prefs: Prefs;
  onChange: (values: Partial<Prefs>) => void;
  onClose: () => void;
  worktrees: WorktreeSettings;
  onWorktree: (key: "git-worktree-root" | "worktree-keep-count" | "worktree-auto-cleanup-enabled", value: string | number | boolean | null) => void;
  /** Archived threads (`thread/list` with `archived`), and bringing one back (`thread/unarchive`). */
  listArchived: () => Promise<Thread[]>;
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
        <legend>While the agent works</legend>
        <label><input type="checkbox" checked={prefs.preventSleep} onChange={(event) => onChange({ preventSleep: event.target.checked })} /> Keep the machine from sleeping while a turn runs</label>
        <label><input type="checkbox" checked={prefs.openArtifacts} onChange={(event) => onChange({ openArtifacts: event.target.checked })} /> Open a document, PDF or image the agent creates in a tab beside the thread</label>
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
        <legend>Approvals and questions</legend>
        <label><input type="checkbox" checked={prefs.notifyApprovals} onChange={(event) => onChange({ notifyApprovals: event.target.checked })} /> Notify when the agent waits for an answer and the window is not in front</label>
      </fieldset>
      <p className="dialog-note">Notifications are silent: Mightling has no sounds of its own. A click on one shows its thread.</p>
      <div className="buttons dialog-buttons"><button onClick={() => void bridge.testNotification()}>Send a test notification</button></div>
    </>
  );
}

/** Appearance: the accent, canvas and text colours and the fonts over the light base; a theme is a small JSON file. */
function AppearancePage({ prefs, onChange, notice }: SettingsProps) {
  const set = (values: Appearance) => onChange({ appearance: cleanAppearance({ ...prefs.appearance, ...values }) });
  const current = getComputedStyle(document.documentElement);
  const colour = (key: "accent" | "canvas" | "text", token: string) => prefs.appearance[key] ?? (current.getPropertyValue(token).trim() || "#000000");
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
      <p className="dialog-note">The base is light (a dark base is not offered); these colours and fonts go over it.</p>
      <fieldset>
        <legend>Colours</legend>
        <label className="colour"><input type="color" value={colour("accent", "--accent")} aria-label="Accent colour" onChange={(event) => set({ accent: event.target.value })} /> Accent</label>
        <label className="colour"><input type="color" value={colour("canvas", "--canvas")} aria-label="Background colour" onChange={(event) => set({ canvas: event.target.value })} /> Background</label>
        <label className="colour"><input type="color" value={colour("text", "--text")} aria-label="Text colour" onChange={(event) => set({ text: event.target.value })} /> Text</label>
      </fieldset>
      <fieldset>
        <legend>Fonts</legend>
        <label className="field">Interface font <input defaultValue={prefs.appearance.fontUi ?? ""} placeholder="Inter, sans-serif" aria-label="Interface font" onBlur={(event) => set({ fontUi: event.target.value.trim() || undefined })} /></label>
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

function Archived({ listArchived, unarchive, notice }: SettingsProps) {
  const [threads, setThreads] = useState<Thread[] | null>(null);
  useEffect(() => {
    listArchived().then(setThreads).catch((error) => { setThreads([]); notice(error); });
  }, [listArchived, notice]);
  return (
    <>
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
      <p className="dialog-note">A pull request opens the forge's own page in your browser, which writes its description.</p>
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
        <p className="dialog-note">Terminal tabs open in the bottom panel (Ctrl+`), in the thread's folder, under the permissions the composer shows, so airgapped holds for what you type there too.</p>
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

function Memories({ readConfig, writeConfig, notice, memoryStatus, resetMemories }: SettingsProps) {
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
      <p className="dialog-note">{status ? `${status.v2ConsolidatedThreads} thread${status.v2ConsolidatedThreads === 1 ? "" : "s"} remembered${status.v2Ready ? "" : "; the memories are still being made"}.` : ""} /memories in a thread turns them off or on for that thread.</p>
      <div className="buttons dialog-buttons">
        <button className="danger-button" onClick={() => window.confirm("Delete everything the agent remembers? This cannot be undone.") && resetMemories().then(() => memoryStatus().then(setStatus)).catch(notice)}>Delete the memories</button>
      </div>
    </>
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
      <p className="dialog-note">The preview tab (Ctrl+T) opens pages on this machine, on the local network and files without asking. A site on the internet asks first; what you chose is kept here. With airgapped on, only this machine is reachable, whatever is allowed. A download always asks where to save it.</p>
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
