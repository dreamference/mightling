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

import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState, type ClipboardEvent, type DragEvent, type ReactNode } from "react";

import { ASK_PROMPT, askThreads, askTitle, attachmentKind, attachmentName, baseName, fileKindLabel, insideFolder, isAskThread, turnInput, viewTabs, type Attached } from "./ask";
import * as bridge from "./bridge";
import { copyText } from "./clipboard";
import { Composer, type ComposerAttachment } from "./ComposerView";
import { PromptHistory, collaborationMode, reviewTarget, composeInput, effortChoices, effortFor, isLongPaste, keyAnswer, mentionPath, parseShell, parseSlash, pasteLabel, rememberModel, userTexts, withPastes, type SlashCommand } from "./composer";
import { Icon, MightlingMark } from "./icons";
import { revertQuestion, underDirectory } from "./diff";
import { draftKeyOf, withDraft } from "./drafts";
import { ThreadLoader } from "./loader";
import { inGitInternals } from "./files";
import { effectiveKeymap, keyAction, matches, setOverrides, shortcutLabel } from "./keymap";
import { renderMarkdown } from "./markdown";
import { APPEARANCE_TOKENS, DEFAULT_PREFS, appearanceFor, loadPrefs, savePrefs, type Prefs, type WorktreeLink } from "./prefs";
import { theme, useShownTheme } from "./theme";
import { RpcClient, type ParamsOf } from "./rpc";
import type { FuzzyFileSearchResult } from "./protocol/FuzzyFileSearchResult";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { ServerRequest } from "./protocol/ServerRequest";
import type { Model } from "./protocol/v2/Model";
import type { PermissionProfileSummary } from "./protocol/v2/PermissionProfileSummary";
import type { QueuedSubmission } from "./protocol/v2/QueuedSubmission";
import type { SkillMetadata } from "./protocol/v2/SkillMetadata";
import type { Thread } from "./protocol/v2/Thread";
import type { Turn } from "./protocol/v2/Turn";
import type { ThreadGoal } from "./protocol/v2/ThreadGoal";
import type { ThreadItem } from "./protocol/v2/ThreadItem";
import type { UserInput } from "./protocol/v2/UserInput";
import type { Project } from "./protocol/v2/Project";
import {
  contextUse, initialState, isWorking, permissionChoices, projects, reduce, SERVER_EXITED, subagents, workingCount,
  type PendingRequest, type ThreadView, type TurnView,
} from "./store";
import { BottomPanel, EnvironmentEditor, appendOutput, type TaskView } from "./BottomPanel";
import { CommandMenu } from "./CommandMenu";
import { ExtensionsView, type ExtensionsTab } from "./ExtensionsView";
import { GoalRow } from "./GoalRow";
import { LiveChecklist, TurnChecklist } from "./PlanChecklist";
import { ReviewPane } from "./ReviewPane";
import { FileTree } from "./FileTree";
import { FileView } from "./FileView";
import { ArtifactView } from "./ArtifactView";
import { LinkDialog } from "./LinkDialog";
import { artifactKind, createdArtifacts } from "./artifacts";
import { keptQuestions, transcript } from "./recall";
import { FileSearch } from "./FileSearch";
import { TabArea } from "./TabArea";
import { PR_TAB, REVIEW_TAB, fileTab, previewTab, surfaceOf, withTab, withoutTab, type WorkspaceTab } from "./tabs";
import { PreviewTab } from "./PreviewTab";
import { PullRequestsTab } from "./PullRequestsTab";
import { SubagentsPanel } from "./SubagentsPanel";
import { ScheduledView, unseen } from "./ScheduledView";
import { SyncedView, useSynced } from "./SyncedView";
import { ActivityView } from "./ActivityView";
import { attentionList } from "./attention";
import { CONSOLE_TOOL, MAX_LINES, TERMINAL_TOOL, consoleAnswer, linesAsked, terminalAnswer, toolLabel, windowToolNames } from "./windowtools";
import { predict } from "./suggestions";
import { MessageBoardView } from "./MessageBoardView";
import { boardOn, sqliteHome } from "./board";
import { SettingsDialog, type SettingsPage } from "./SettingsDialog";
import { ClosedSurfaces, History, UndoStack, nextScale, type MenuEntry, type Place } from "./shell";
import { findRanges, mark } from "./find";
import { nearBottom } from "./follow";
import { moveToSection, nextAttention, pinnedFirst, recentThreads, renameStart, stepThread, threadLink, toggled, type Section } from "./threads";
import { TerminalHub, decode, terminalMessage, type TerminalSession } from "./terminal";
import { answersFor, composerOf, paneKey, paneOf, type PaneKind, type WindowsState } from "./windows";
import { NewThreadOptions, WorktreeHeader, type NewThreadChoice } from "./WorktreeControls";
import { ExportDialog } from "./ExportDialog";
import { QuestionCard } from "./QuestionCard";
import { threadContext, withThreadContext } from "./tonight";
import { ProjectDialog } from "./ProjectDialog";
import { ProjectPage } from "./ProjectPage";
import { contextItem, contextStamp, isContextText, isRemoved, knowledgeCollection, movedBefore, ordered, primaryFolder, projectContext, secondaryFolders, shownProjects, withMetadata, REMOVED_KEY, type KnowledgeFile } from "./projects";
import { ElicitationCard } from "./ElicitationCard";
import { elicitationAnswer, shownElicitation } from "./elicitation";
import { carry, cleanupVictims, defaultCheckout, projectFolder, protectedOwners, turnCwd, worktreeSettings, type WorktreeSettings } from "./worktrees";
import { commitMessage, dropQueued, forkAt, forkThread, listQueue, movedQueue, reorderQueued, sendMessage, steerQueued, takeBack, updateQueued, type Listen } from "./turns";

const CLIENT_INFO = { name: "mightling_desktop", title: "Mightling", version: "0.1.0" };

/** Server requests this window answers by asking the user; the rest are answered without asking. */
const ASKED = new Set<ServerRequest["method"]>([
  "item/commandExecution/requestApproval",
  "item/fileChange/requestApproval",
  "item/permissions/requestApproval",
  "item/tool/requestUserInput",
  "mcpServer/elicitation/request",
  "execCommandApproval",
  "applyPatchApproval",
]);

type View = "ask" | "work";

const HOST = bridge.host();

function initialView(): View {
  return typeof location !== "undefined" && (location.hash === "#work" || location.hash.startsWith("#popout=") || location.hash.startsWith("#pane=")) ? "work" : "ask";
}

/** A pop-out window's thread (`#popout=<id>`, DESKTOP_PARITY §2.1): that thread alone, no sidebar. */
const POPOUT: string | null = typeof location !== "undefined" && location.hash.startsWith("#popout=") ? decodeURIComponent(location.hash.slice(8)) : null;

/** A detached pane's window (`#pane=<kind>&thread=<id>`, DESKTOP_ELECTRON §13): one thread's terminal or diff, docked back when it closes. */
const PANE = typeof location !== "undefined" ? paneOf(location.hash) : null;

/** The thread a server request is about, if it names one. */
const requestThread = (request: ServerRequest): string | null => {
  const thread = (request.params as { threadId?: unknown } | undefined)?.threadId;
  return typeof thread === "string" ? thread : null;
};

/** A file picked, pasted or dropped for the next message, not yet sent. */
interface PendingFile {
  id: number;
  file: File;
  name: string;
  kind: "image" | "file";
  preview: string | null;
  /** Where it already is on disk (a Work image dropped in the app): sent by path, never uploaded. */
  path: string | null;
  /** A long paste's text (DESKTOP_PARITY_CLAUDE §4.6): uploaded as a file in Ask, written into the message in Work. */
  text?: string;
}

/** What a slash command printed, shown above the turns until dismissed. */
interface Info {
  title: string;
  body: ReactNode;
}

let nextPendingId = 1;

const IMAGE_TYPES = /^image\/(png|jpe?g|gif|webp)$/i;

/** The notice a new thread without a folder gets; it goes once a folder is named. */
const NAME_THE_FOLDER = "Name the project folder for the new thread.";

export function App() {
  const [state, dispatch] = useReducer(reduce, initialState);
  const client = useMemo(() => new RpcClient(bridge.bridgeTransport), []);
  const [servedModel, setServedModel] = useState<string | null>(null);
  const servedModelRef = useRef(servedModel);
  servedModelRef.current = servedModel;
  // Threads are read to be viewed and resumed only when a request needs them loaded (loader.ts).
  const loader = useMemo(() => {
    const threads = new ThreadLoader((threadId) => client.request("thread/resume", { threadId, model: servedModelRef.current }));
    client.beforeRequest = (method, params) => threads.before(method, params);
    client.afterResponse = (method, result) => threads.after(method, result);
    return threads;
  }, [client]);
  const [askRoot, setAskRoot] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<PermissionProfileSummary[]>([]);
  const [profile, setProfile] = useState(":workspace");
  const [airgap, setAirgap] = useState<bridge.Airgapped | null>(null);
  /** A web address clicked in an answer, waiting for the user's yes (LinkDialog.tsx). */
  const [linkAsked, setLinkAsked] = useState<{ url: string; thread: string | null; airgappedOn: boolean } | null>(null);
  const [newCwd, setNewCwd] = useState("");
  // The folder named, the notice asking for it goes (it stayed until a restart).
  useEffect(() => {
    if (newCwd.trim()) dispatch({ type: "dropNotice", message: NAME_THE_FOLDER });
  }, [newCwd]);
  const [view, setView] = useState<View>(initialView);
  // One draft per thread (drafts.ts); `setDraft` writes the one in front, `setDraftFor` a named one.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const draftKey = draftKeyOf(view, state.selected);
  const draft = drafts[draftKey] ?? "";
  const draftKeyRef = useRef(draftKey);
  draftKeyRef.current = draftKey;
  const setDraftFor = useCallback((key: string, value: string | ((current: string) => string)) => setDrafts((all) => withDraft(all, key, value)), []);
  const setDraft = useCallback((value: string | ((current: string) => string)) => setDraftFor(draftKeyRef.current, value), [setDraftFor]);
  const [search, setSearch] = useState("");
  const [found, setFound] = useState<{ thread: Thread; snippet: string }[] | null>(null);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const [pending, setPending] = useState<PendingFile[]>([]);
  const [sending, setSending] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [prefs, setPrefs] = useState<Prefs>(DEFAULT_PREFS);
  const [models, setModels] = useState<Model[]>([]);
  const [modelChoice, setModelChoice] = useState<string | null>(null);
  const [picker, setPicker] = useState<"closed" | "open" | "recent">("closed");
  const [skills, setSkills] = useState<Record<string, SkillMetadata[]>>({});
  const [info, setInfo] = useState<Info | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsPage, setSettingsPage] = useState<SettingsPage>("general");
  const [menuOpen, setMenuOpen] = useState<false | "all" | "projects">(false);
  const [rowMenu, setRowMenu] = useState<string | null>(null);
  // Projects (projects.ts): the server's, the dialog that makes or edits one, the project a new thread
  // or question starts in, and the open project menu.
  const [serverProjects, setServerProjects] = useState<Project[]>([]);
  const [projectDialog, setProjectDialog] = useState<{ project: Project | null; folder: string | null } | null>(null);
  const [newProject, setNewProject] = useState<string | null>(null);
  const [projectMenu, setProjectMenu] = useState<string | null>(null);
  /** The thread being exported (exportthread.ts) and the home folder its paths are redacted against. */
  const [exporting, setExporting] = useState<{ thread: Thread; title: string; home: string | null } | null>(null);
  const draggedHeader = useRef<string | null>(null);
  const loadProjects = useCallback(async () => {
    try {
      const answer = (await client.request("project/list", { limit: 200 })) as { data?: Project[] };
      setServerProjects(answer.data ?? []);
    } catch {
      setServerProjects([]);
    }
  }, [client]);
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [findAt, setFindAt] = useState(0);
  const [findCount, setFindCount] = useState(0);
  const [onTop, setOnTop] = useState(false);
  /** Threads in Plan mode (§2.3); "" stands for the next new thread. */
  const [planning, setPlanning] = useState<Set<string>>(new Set());
  const [planAvailable, setPlanAvailable] = useState(false);
  const [extensions, setExtensions] = useState<ExtensionsTab | null>(null);
  const [scheduledOpen, setScheduledOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  // Threads from the user's other devices, on `ling web` with the node's sync hub on (SYNC Phase 1).
  const synced = useSynced(HOST === "web" && !POPOUT);
  const [syncedOpen, setSyncedOpen] = useState(false);
  /** The agent message board (Phase 3 item 10): whether config.toml has it on, and its view. */
  const [board, setBoard] = useState<{ on: boolean; sqliteHome: string | null }>({ on: false, sqliteHome: null });
  const [boardOpen, setBoardOpen] = useState(false);
  /** Suggested follow-ups (Phase 3 item 7), by thread, until a new turn starts there. */
  const [suggestions, setSuggestions] = useState<Record<string, string[]>>({});
  const [agentsHidden, setAgentsHidden] = useState(false);
  const [memoryModes, setMemoryModes] = useState<Record<string, "enabled" | "disabled">>({});
  const [nightUnseen, setNightUnseen] = useState(0);
  const [goalEditing, setGoalEditing] = useState(false);
  const [naming, setNaming] = useState<{ thread: string; name: string } | null>(null);
  const selectedRef = useRef<string | null>(null);
  const updatePrefsRef = useRef<(values: Partial<Prefs>) => void>(() => {});
  const places = useMemo(() => new History(), []);
  const undoStack = useMemo(() => new UndoStack(), []);
  const closedSurfaces = useMemo(() => new ClosedSurfaces(), []);
  /** Set while back/forward moves the window, so the move is not recorded as a new place. */
  const travelling = useRef(false);
  /** The tab area's tabs (§2.1, §2.8): Review and file tabs, and the one in front. */
  const [tabs, setTabs] = useState<WorkspaceTab[]>([]);
  /** The next new question is incognito (DESKTOP_PARITY_CLAUDE §4.3): the header's switch, before the first message. */
  const [incognitoNext, setIncognitoNext] = useState(false);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [treeShown, setTreeShown] = useState(false);
  /** Ask's own tree of the question's folder (DESKTOP_PARITY_CLAUDE §4.1), apart from Work's. */
  const [askTree, setAskTree] = useState(false);
  /** Search files (Ctrl+P) open, with what it starts with (":" for go to line). */
  const [fileSearch, setFileSearch] = useState<string | null>(null);
  const reviewOpen = tabs.some((tab) => tab.kind === "review");
  /** Opens (to the front) or closes the Review tab, as Ctrl+Shift+G and the bar's button do. */
  const setReviewOpen = (open: boolean | ((was: boolean) => boolean)) => {
    const next = typeof open === "function" ? open(reviewOpen) : open;
    if (next) {
      setTabs((list) => withTab(list, list.find((tab) => tab.kind === "review") ?? REVIEW_TAB));
      setActiveTab(REVIEW_TAB.key);
    } else closeTabKey(REVIEW_TAB.key);
  };
  const closeTabKey = (key: string) => {
    const after = withoutTab(tabs, key, activeTab);
    setTabs(after.tabs);
    setActiveTab(after.active);
  };
  /** A file in a tab of its own, in front; the same file's tab is reused (and moved to the line). */
  const openFile = (path: string, line: number | null = null) => {
    setTabs((list) => withTab(list, fileTab(path, line)));
    setActiveTab(fileTab(path).key);
    if (prefsRef.current.layout === "chat") updatePrefsRef.current({ layout: "split" });
  };
  const suggestRef = useRef<(threadId: string) => void>(() => {});
  suggestRef.current = (threadId: string) => {
    const current = stateRef.current;
    const view = current.threads[threadId];
    // Not while a turn this window has seen start is still running: the model server is busy enough.
    if (!view || view.thread.ephemeral || view.thread.parentThreadId || Object.values(current.threads).some((entry) => entry.activeTurnId)) return;
    const last = view.turns[view.turns.length - 1];
    if (!last) return;
    const request = last.items.filter((item) => item.type === "userMessage").map((item) => (item.type === "userMessage" ? item.content.map(describeInput).join(" ") : "")).join("\n");
    const answer = [...last.items].reverse().find((item) => item.type === "agentMessage");
    if (!request.trim() || !answer || answer.type !== "agentMessage") return;
    predict(client, listen, { cwd: view.thread.cwd, model: modelChoice ?? servedModel, request, answer: answer.text })
      .then((found) => found && setSuggestions((known) => ({ ...known, [threadId]: [found] })))
      .catch(() => {});
  };
  const openFileRef = useRef<(path: string) => void>(() => {});
  openFileRef.current = (path: string) => openFile(path);
  /** The pull-request tab (Phase 3 item 9), in front. */
  const openPullRequests = () => {
    setTabs((list) => withTab(list, list.find((tab) => tab.kind === "prs") ?? PR_TAB));
    setActiveTab(PR_TAB.key);
    if (prefsRef.current.layout === "chat") updatePrefsRef.current({ layout: "split" });
  };
  /** A preview tab (Ctrl+T), in front; an empty address waits for the user to type one. */
  const openPreview = (url = "") => {
    const tab = previewTab(url);
    setTabs((list) => withTab(list, tab));
    setActiveTab(tab.key);
    if (view !== "work") showView("work");
    if (prefsRef.current.layout === "chat") updatePrefsRef.current({ layout: "split" });
  };
  const [tasks, setTasks] = useState<TaskView[]>([]);
  const [wtSettings, setWtSettings] = useState<WorktreeSettings>(worktreeSettings(null));
  const [newChoice, setNewChoice] = useState<NewThreadChoice>({ mode: "local", base: null, withChanges: true, environment: null });
  const [newBranches, setNewBranches] = useState<{ current: string | null; branches: string[] } | null>(null);
  const [envs, setEnvs] = useState<{ project: string; found: bridge.LocalEnvironment[]; problems: string[] } | null>(null);
  const [newEnvs, setNewEnvs] = useState<bridge.LocalEnvironment[]>([]);
  const [editingEnv, setEditingEnv] = useState(false);
  const [missingWorktree, setMissingWorktree] = useState<string | null>(null);
  // In the app a tab drawn here starts from what it showed so far, which the main process keeps (several windows, §13).
  const hub = useMemo(() => new TerminalHub(client, bridge.terminals.available() ? (processId) => bridge.terminals.backlog(processId).then(decode) : null), [client]);
  const [terminals, setTerminals] = useState<TerminalSession[]>([]);
  /** Who holds which thread's composer (several windows; null in a browser, where everything is this page's). */
  const [windowsState, setWindowsState] = useState<WindowsState | null>(null);
  const windowsRef = useRef(windowsState);
  windowsRef.current = windowsState;
  /** This window's panes detached into windows of their own (`paneKey`), until they dock back. */
  const [detached, setDetached] = useState<Set<string>>(new Set());
  /** What the main process may ask of this page about windows and panes; set further down, where those handlers are. */
  const windowEvents = useRef<{ docked: (pane: PaneKind, thread: string) => void; paneAction: (action: bridge.PaneAction) => void; command: (command: "detachTerminal" | "detachReview") => void }>({ docked: () => {}, paneAction: () => {}, command: () => {} });
  const [front, setFront] = useState<{ id: string; tick: number } | null>(null);
  const prefsRef = useRef(prefs);
  prefsRef.current = prefs;
  // Listeners besides the store, for the window's own small jobs (a generated commit message).
  const listeners = useRef(new Set<(notification: ServerNotification) => void>());
  const listen: Listen = useCallback((listener) => {
    listeners.current.add(listener);
    return () => listeners.current.delete(listener);
  }, []);
  const promptHistory = useMemo(() => new PromptHistory(), []);
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
    if (typeof window !== "undefined") window.history.replaceState(null, "", next === "work" ? "#work" : "#");
  }, []);

  // The window's own tools (windowtools.ts): the thread's terminal tabs and the previews' console,
  // read when the agent asks (`item/tool/call`), bounded and with secrets masked.
  const terminalsRef = useRef(terminals);
  terminalsRef.current = terminals;
  const answerTool = useCallback(async (request: ServerRequest & { method: "item/tool/call" }) => {
    const { threadId, tool, arguments: args } = request.params;
    const lines = linesAsked(args);
    const reply = (text: string, success = true) => client.respond(request.id, { contentItems: [{ type: "inputText", text }], success });
    if (tool === TERMINAL_TOOL) {
      // The whole of each tab's output as the app keeps it, whichever window started the tab.
      const tabs = await Promise.all(terminalsRef.current.filter((tab) => tab.threadId === threadId)
        .map(async (tab) => ({ title: tab.title, cwd: tab.cwd, running: tab.exitCode === null, output: await hub.tailFrom(tab.processId, MAX_LINES) })));
      return reply(terminalAnswer(tabs, lines));
    }
    if (tool === CONSOLE_TOOL) {
      if (bridge.host() !== "electron") return reply("The preview's console is only in the Mightling desktop app.", false);
      return reply(consoleAnswer(await bridge.preview.console(threadId), lines));
    }
    return reply(`Mightling has no tool named ${tool}.`, false);
  }, [client, hub]);

  const answerUnasked = useCallback((request: ServerRequest) => {
    switch (request.method) {
      case "item/tool/call":
        return answerTool(request);
      case "currentTime/read":
        return client.respond(request.id, { currentTimeAt: Math.floor(Date.now() / 1000) });
      case "mcpServer/elicitation/request":
        // The vendor's own identity check (`openai/userVerification`): nothing here can answer it.
        return client.respond(request.id, elicitationAnswer("decline"));
      default:
        return client.respondError(request.id, -32601, `Mightling does not handle ${request.method} here`);
    }
  }, [client, answerTool]);

  const refreshQueue = useCallback((threadId: string) => {
    listQueue(client, threadId).then((items) => dispatch({ type: "queue", threadId, items })).catch(() => {});
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
      const [list, permissions, level, target, stored] = await Promise.all([
        client.request("thread/list", { limit: 200 }),
        client.request("permissionProfile/list", {}),
        bridge.airgapped(null),
        bridge.workTarget(),
        loadPrefs(),
      ]);
      dispatch({ type: "threads", list: list.data });
      void loadProjects();
      setProfiles(permissions.data);
      setAirgap(level);
      setPrefs(stored);
      // The worktree settings the codex-worktree crate reads too ([desktop] in config.toml).
      client.request("config/read", {}).then((answer) => {
        const config = (answer as { config?: Record<string, unknown> }).config ?? null;
        const desktop = (config as { desktop?: Record<string, unknown> | null } | null)?.desktop;
        setWtSettings(worktreeSettings(desktop));
        // Local or Worktree for new threads: the user's choice, shared with `/worktree on|off`.
        setNewChoice((choice) => ({ ...choice, mode: defaultCheckout(desktop) }));
        setBoard({ on: boardOn(config), sqliteHome: sqliteHome(config) });
      }).catch(() => {});
      // Night Shift's inbox: finished tasks since Scheduled was last opened.
      if (bridge.host() === "electron") bridge.night.list().then((answer) => setNightUnseen(unseen(answer.tasks, stored.nightSeen))).catch(() => {});
      // Plan mode, when the server offers it (`collaborationMode/list`).
      client.request("collaborationMode/list", {}).then((answer) => setPlanAvailable(((answer as { data?: { mode: string | null }[] }).data ?? []).some((mask) => mask.mode === "plan"))).catch(() => setPlanAvailable(false));
      // The catalog the launcher wrote: one served model, with the efforts it allows.
      client.request("model/list", {}).then((answer) => setModels((answer as { data?: Model[] }).data ?? [])).catch(() => setModels([]));
      // A browser has no launch target: `ling web` answers null.
      if (target?.cwd) setNewCwd(target.cwd);
      // Several windows (§13): who holds which composer, and every terminal tab of the app.
      if (bridge.windows.available()) {
        bridge.windows.state().then(setWindowsState).catch(() => {});
        bridge.terminals.list().then(setTerminals).catch(() => {});
      }
      const first = POPOUT ?? PANE?.thread ?? target?.thread ?? null;
      if (first) {
        const resumed = await client.request("thread/resume", { threadId: first, model: started.served_model });
        loader.markLoaded(first);
        dispatch({ type: "opened", thread: resumed.thread });
        if (POPOUT) setView(isAskThread(resumed.thread, started.ask_root ?? null) ? "ask" : "work");
      }
    } catch (error) {
      notice(error);
    }
  }, [client, notice]);

  const loadProjectsRef = useRef(loadProjects);
  loadProjectsRef.current = loadProjects;

  // The bridge's events, then the server; once, for the life of the window.
  const connected = useRef(false);
  useEffect(() => {
    client.onNotification = (notification) => {
      // A terminal's output goes to its tab, not to the store.
      if (hub.deliver(notification)) return;
      dispatch({ type: "notification", notification });
      // A thread this window starts (or forks) is loaded in the server from the start.
      if (notification.method === "thread/started") loader.markLoaded(notification.params.thread.id);
      if (notification.method === "thread/queue/changed") refreshQueue(notification.params.threadId);
      if (notification.method === "project/changed") void loadProjectsRef.current();
      // A turn that ends while its thread is not in front leaves the thread unread.
      if (notification.method === "turn/completed") {
        const threadId = notification.params.threadId;
        const current = prefsRef.current.unreadThreads;
        const known = stateRef.current.threads[threadId]?.thread;
        if (known && !known.ephemeral && (threadId !== selectedRef.current || !document.hasFocus()) && !current.includes(threadId)) updatePrefsRef.current({ unreadThreads: [...current, threadId] });
      }
      for (const listener of listeners.current) listener(notification);
    };
    client.onServerRequest = (request) => {
      // An MCP server's question shows in its thread (item 14); only the vendor's identity check is declined unseen.
      if (ASKED.has(request.method) && (request.method !== "mcpServer/elicitation/request" || shownElicitation(request.params))) dispatch({ type: "serverRequest", request });
      // Every window hears the request; one answers it (windows.ts `answersFor`): the composer's, or the most recent.
      else if (answersFor(windowsRef.current, requestThread(request), PANE !== null)) answerUnasked(request).catch(notice);
    };
    let stop: (() => void) | undefined;
    bridge.listenBridge({
      view: (next) => showView(next),
      link: (target) => linkRef.current(target),
      windows: (next) => setWindowsState(next),
      terminals: (list) => setTerminals(list),
      docked: (pane, thread) => windowEvents.current.docked(pane, thread),
      paneAction: (action) => windowEvents.current.paneAction(action),
      command: (command) => windowEvents.current.command(command),
      syncNotice: (sync) => notice(sync.text),
      task: (event) => setTasks((list) => {
        if (event.kind === "started") {
          setFront({ id: event.id, tick: Date.now() });
          return [...list.slice(-11), { id: event.id, label: event.label ?? "Task", cwd: event.cwd ?? "", output: "", running: true, code: null }];
        }
        return list.map((task) => (task.id !== event.id ? task
          : event.kind === "output" ? { ...task, output: appendOutput(task.output, event.data ?? "") }
            : { ...task, running: false, code: event.code ?? null }));
      }),
      message: (message) => client.receive(message),
      stderr: (line) => dispatch({ type: "stderr", line }),
      protocolError: (line) => dispatch({ type: "protocolError", line }),
      exit: (code) => {
        client.fail(`${SERVER_EXITED}${code === null ? "" : ` with code ${code}`}`);
        loader.reset();
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
  }, [client, connect, answerUnasked, notice, showView, refreshQueue, hub]);

  // The artifacts viewer (Phase 3 item 3): what the agent created in the shown thread's turn opens when the turn ends.
  const stateRef = useRef(state);
  stateRef.current = state;
  useEffect(() => {
    const made = new Map<string, string[]>();
    return listen((notification) => {
      if (notification.method === "item/completed") {
        const cwd = stateRef.current.threads[notification.params.threadId]?.thread.cwd ?? null;
        const paths = createdArtifacts(notification.params.item, cwd);
        if (paths.length) made.set(notification.params.turnId, [...(made.get(notification.params.turnId) ?? []), ...paths]);
      } else if (notification.method === "turn/started") {
        const threadId = notification.params.threadId;
        setSuggestions((known) => (known[threadId] ? Object.fromEntries(Object.entries(known).filter(([id]) => id !== threadId)) : known));
      } else if (notification.method === "turn/completed") {
        // Suggested prompts, when Settings ask for them, for a finished turn of a thread of the user's,
        // once the model server has nothing else to do (the store has taken the turn in by then).
        const { threadId, turn } = notification.params;
        if (prefsRef.current.suggestPrompts && turn.status === "completed") setTimeout(() => suggestRef.current(threadId), 300);
        const paths = made.get(notification.params.turn.id) ?? [];
        made.delete(notification.params.turn.id);
        if (paths.length && notification.params.threadId === selectedRef.current && prefsRef.current.openArtifacts) openFileRef.current(paths[0]);
        // Earlier questions (§4.4): the question's copy brought up to date.
        if (prefsRef.current.recallQuestions) setTimeout(() => void recallRef.current(threadId).catch(() => {}), 500);
      } else if ((notification.method === "thread/archived" || notification.method === "thread/unarchived") && prefsRef.current.recallQuestions) {
        // An archived question's copy goes, an unarchived one's comes back.
        setTimeout(() => void recallRef.current().catch(() => {}), 500);
      }
    });
  }, [listen]);

  // Earlier questions for the agent (DESKTOP_PARITY_CLAUDE §4.4): while Settings › Memories has it on,
  // each Ask question of the user's (never an incognito one) is kept as the Markdown the agent's
  // earlier-questions skill searches: all of them when the app starts or it is turned on, one after
  // each finished turn; archived questions are dropped. The main process keeps the files.
  const recallRef = useRef<(only?: string) => Promise<void>>(async () => {});
  recallRef.current = async (only?: string) => {
    if (!bridge.recall.available() || !prefsRef.current.recallQuestions || stateRef.current.server !== "ready") return;
    const isAsk = (thread: Thread) => isAskThread(thread, askRoot);
    const turnsOf = async (threadId: string): Promise<Turn[]> => {
      const turns: Turn[] = [];
      let cursor: string | null = null;
      for (let page = 0; page < 50; page++) {
        const answer: { data: Turn[]; nextCursor: string | null } = (await client.request("thread/turns/list", { threadId, cursor, limit: 100 })) as { data: Turn[]; nextCursor: string | null };
        turns.push(...answer.data);
        cursor = answer.nextCursor;
        if (!cursor) break;
      }
      // Newest first from the server: the copy reads oldest first.
      return turns.reverse();
    };
    if (only) {
      const thread = stateRef.current.threads[only]?.thread;
      if (thread && keptQuestions([thread], isAsk).length) await bridge.recall.write(thread.id, thread.updatedAt, transcript(thread, await turnsOf(thread.id)));
      return;
    }
    const listed: Thread[] = [];
    let cursor: string | null = null;
    for (let page = 0; page < 20; page++) {
      const answer = (await client.request("thread/list", { limit: 200, cursor })) as { data: Thread[]; nextCursor: string | null };
      listed.push(...answer.data);
      cursor = answer.nextCursor;
      if (!cursor) break;
    }
    const kept = keptQuestions(listed, isAsk);
    const known = await bridge.recall.index();
    for (const thread of kept) {
      if (known[thread.id] === thread.updatedAt) continue;
      await bridge.recall.write(thread.id, thread.updatedAt, transcript(thread, await turnsOf(thread.id)));
    }
    await bridge.recall.prune(kept.map((thread) => thread.id));
  };
  useEffect(() => {
    if (!prefs.recallQuestions || state.server !== "ready" || !bridge.recall.available()) return;
    // After the main process has put the skill in place (prefs/write), and after the start's own requests.
    const timer = setTimeout(() => void recallRef.current().catch(notice), 1500);
    return () => clearTimeout(timer);
  }, [prefs.recallQuestions, state.server, notice]);

  const selectedView: ThreadView | null = state.selected ? state.threads[state.selected] ?? null : null;
  // Each view shows only its own kind of thread.
  const selected = selectedView && isAskThread(selectedView.thread, askRoot) === (view === "ask") ? selectedView : null;
  // Several windows (§13): the main process is told what this window shows, and gives the thread's
  // composer to one window. A pop-out took its thread's when it opened, so it says nothing before
  // the thread is in front; a detached pane has no composer and says nothing.
  const shownThread = selected?.thread.id ?? null;
  useEffect(() => {
    if (!bridge.windows.available() || PANE || state.server !== "ready" || (POPOUT && shownThread === null)) return;
    bridge.windows.showing(view, shownThread).then(setWindowsState).catch(() => {});
  }, [view, shownThread, state.server]);
  /** Whether this window sends for the thread in front, or mirrors it read-only while another window holds its composer. */
  const composerHere = composerOf(windowsState, shownThread) === "here";
  // An incognito question in front (DESKTOP_PARITY_CLAUDE §4.3): an ephemeral Ask thread. Leaving it, to
  // another question, a new one, Work or a setting that changes the thread shown, deletes it: its turn
  // stopped, the server let go of it, its folder and every file in it removed by the main process (which
  // also removes any left when the app quits).
  const incognitoShown = selected !== null && view === "ask" && selected.thread.ephemeral ? selected.thread : null;
  const incognitoRef = useRef<{ id: string; cwd: string } | null>(null);
  useEffect(() => {
    const before = incognitoRef.current;
    incognitoRef.current = incognitoShown ? { id: incognitoShown.id, cwd: incognitoShown.cwd } : null;
    if (!before || before.id === incognitoShown?.id) return;
    const active = stateRef.current.threads[before.id]?.activeTurnId;
    void (async () => {
      if (active) await client.request("turn/interrupt", { threadId: before.id, turnId: active }).catch(() => {});
      await client.request("thread/unsubscribe", { threadId: before.id }).catch(() => {});
      await bridge.incognito.forget(before.id);
      setTabs((list) => list.filter((tab) => !(tab.kind === "file" && insideFolder(tab.path, before.cwd))));
    })().catch((error: unknown) => notice(`The incognito question could not be deleted: ${error instanceof Error ? error.message : String(error)}`));
  }, [incognitoShown?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // The conversation follows its latest message (follow.ts): a thread opens at the end, new output
  // keeps the view there only while the reader is there, and an info panel opens at the top.
  const turnsRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const onTurnsScroll = () => {
    if (turnsRef.current) followRef.current = nearBottom(turnsRef.current);
  };
  useLayoutEffect(() => {
    const element = turnsRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
    followRef.current = true;
  }, [selected?.thread.id]);
  useLayoutEffect(() => {
    const element = turnsRef.current;
    if (element && followRef.current && !info) element.scrollTop = element.scrollHeight;
  });
  useLayoutEffect(() => {
    if (!info || !turnsRef.current) return;
    turnsRef.current.scrollTop = 0;
    followRef.current = false;
  }, [info]);

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

  // The folder the composer's `@` and `$` look in: the shown thread's, or the new thread's.
  const composeRoot = view === "work" ? (selected?.thread.cwd ?? (newCwd.trim() || null)) : (selected?.thread.cwd ?? null);
  // The tabs beside the thread are one list; each view shows its own part of it: Ask the files of the
  // question in front (DESKTOP_PARITY_CLAUDE §4.1), Work everything else.
  const askFolder = view === "ask" ? (selected?.thread.cwd ?? null) : null;
  const paneTabs = viewTabs(tabs, view, askFolder, askRoot);
  const paneActive = paneTabs.find((tab) => tab.key === activeTab) ?? paneTabs[0] ?? null;
  const paneTree = view === "ask" ? askTree && askFolder !== null : treeShown;
  /** The tab area shows when a tab is open or the file tree is, and the layout does not hide it (§2.1). */
  const tabsShown = (paneTabs.length > 0 || paneTree) && prefs.layout !== "chat" && (view === "work" || askFolder !== null);
  useEffect(() => {
    if (state.server !== "ready" || !composeRoot || skills[composeRoot]) return;
    client.request("skills/list", { cwds: [composeRoot] })
      .then((answer) => setSkills((known) => ({ ...known, [composeRoot]: (answer as { data?: { skills: SkillMetadata[] }[] }).data?.flatMap((entry) => entry.skills) ?? [] })))
      .catch(() => setSkills((known) => ({ ...known, [composeRoot]: [] })));
  }, [composeRoot, state.server, client, skills]);
  const rootSkills = composeRoot ? skills[composeRoot] ?? [] : [];

  const searchFiles = useCallback(async (query: string): Promise<FuzzyFileSearchResult[]> => {
    if (!composeRoot) return [];
    const answer = (await client.request("fuzzyFileSearch", { query, roots: [composeRoot], cancellationToken: "composer" })) as { files?: FuzzyFileSearchResult[] };
    // The server's search honours .gitignore, which never names .git itself.
    return (answer.files ?? []).filter((file) => !inGitInternals(file.path));
  }, [client, composeRoot]);

  // Pasted or picked images show a preview until they are sent or removed.
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  useEffect(() => () => pendingRef.current.forEach((item) => item.preview && URL.revokeObjectURL(item.preview)), []);

  const openThread = async (threadId: string) => {
    dispatch({ type: "select", threadId });
    if (prefsRef.current.unreadThreads.includes(threadId)) updatePrefs({ unreadThreads: prefsRef.current.unreadThreads.filter((id) => id !== threadId) });
    setDrawer(false);
    setInfo(null);
    try {
      // Read, not resumed: resuming writes to the thread's rollout, which `thread/list` then reports
      // as activity. A thread loaded already, or running (from another client too), is resumed, so
      // its events stream here.
      const known = state.threads[threadId]?.thread;
      const live = loader.isLoaded(threadId) || known?.status.type === "active";
      if (live) {
        const resumed = await client.request("thread/resume", { threadId, model: servedModel });
        loader.markLoaded(threadId);
        dispatch({ type: "opened", thread: resumed.thread });
      } else {
        const read = await client.request("thread/read", { threadId, includeTurns: true });
        dispatch({ type: "opened", thread: read.thread });
      }
      refreshQueue(threadId);
      client.request("thread/goal/get", { threadId }).then((answer) => dispatch({ type: "goal", threadId, goal: (answer as { goal: ThreadGoal | null }).goal })).catch(() => {});
    } catch (error) {
      notice(error);
    }
  };

  /**
   * Revert: the turn's file changes undone (its diff applied in reverse, in the thread's repository),
   * then the turn and every later one removed from the thread (`thread/revert`, which changes the
   * history only). Asked first: one click on the first turn's Revert emptied a thread on a Mac, its
   * files untouched. If the files cannot be undone, the history stays as it is. What shell commands
   * changed is not in the diff and is not undone; the question says so (`revertQuestion`).
   */
  const revertTurn = async (turn: TurnView) => {
    if (!selected) return;
    const thread = selected.thread;
    const diff = turn.diff?.trim() ? turn.diff : null;
    // What shell commands changed is in no diff: the question says so when the turns ran any.
    if (!window.confirm(revertQuestion(selected.turns, selected.turns.findIndex((entry) => entry.id === turn.id)))) return;
    if (diff) {
      const [root] = bridge.git.available() ? await bridge.git.repos(thread.cwd) : [];
      if (!root) throw new Error("Undoing a turn's file changes needs its folder in a git repository: nothing was changed.");
      const inside = thread.cwd.startsWith(root) ? thread.cwd.slice(root.length).replace(/^[\\/]+/, "") : "";
      try {
        await bridge.git.apply(root, diff, { reverse: true });
      } catch (error) {
        if (!inside) throw error;
        await bridge.git.apply(root, underDirectory(diff, inside), { reverse: true });
      }
    }
    await client.request("thread/revert", { threadId: thread.id, beforeTurnId: turn.id });
    await openThread(thread.id);
  };

  /** The goal row's requests (`thread/goal/*`); the server's notifications update the row. */
  const setGoal = (values: { objective?: string; status?: "active" | "paused" }) => {
    if (!selected) return;
    const threadId = selected.thread.id;
    client.request("thread/goal/set", { threadId, ...values })
      .then((answer) => {
        const goal = (answer as { goal?: ThreadGoal }).goal;
        if (goal) dispatch({ type: "goal", threadId, goal });
        setGoalEditing(false);
      }).catch(notice);
  };
  const clearGoal = () => {
    if (!selected) return;
    const threadId = selected.thread.id;
    client.request("thread/goal/clear", { threadId }).then(() => dispatch({ type: "goal", threadId, goal: null })).catch(notice);
  };

  const switchView = showView;

  /** The project a new thread or question starts in, while it is still shown. */
  const startingProject = newProject ? shownProjects(serverProjects).find((project) => project.id === newProject) ?? null : null;
  /** What `thread/start` adds for a thread of that project: its id and, in Work, the folders after the primary. */
  const projectFields = (work: boolean): Record<string, unknown> => {
    if (!startingProject) return {};
    const others = secondaryFolders(startingProject);
    return { projectId: startingProject.id, ...(work && others.length ? { runtimeWorkspaceRoots: others } : {}) };
  };

  /** A new Work thread may read the window's terminal tabs and previews' console (Settings › Project and terminal; on by default). */
  const windowToolFields = (): Record<string, unknown> => (prefsRef.current.agentReadsWindow ? { windowTools: windowToolNames(bridge.host() === "electron") } : {});

  const startThread = async (): Promise<Thread | null> => {
    const cwd = (startingProject ? primaryFolder(startingProject) : null) ?? newCwd.trim();
    if (startingProject && !primaryFolder(startingProject)) {
      notice(`The project ${startingProject.name} has no folder: add one in Edit project, or ask in Ask.`);
      return null;
    }
    if (!cwd) {
      notice(NAME_THE_FOLDER);
      return null;
    }
    dispatch({ type: "dropNotice", message: NAME_THE_FOLDER });
    try {
      if (newChoice.mode === "worktree" && bridge.git.available()) return await startInWorktree(cwd);
      const started = await client.request("thread/start", { cwd, model: servedModel, permissions: profile, ...projectFields(true), ...windowToolFields() } as ParamsOf<"thread/start">);
      dispatch({ type: "opened", thread: started.thread });
      setDrawer(false);
      return started.thread;
    } catch (error) {
      notice(error);
      return null;
    }
  };

  const links = prefs.worktreeThreads;
  selectedRef.current = state.selected;

  /** Records a thread's worktree (or changes it), saved in the window's preferences. */
  const setLink = (threadId: string, link: WorktreeLink | null) => {
    const next = { ...prefsRef.current.worktreeThreads };
    if (link) next[threadId] = link;
    else delete next[threadId];
    updatePrefs({ worktreeThreads: next });
  };

  /** A new thread in a new managed worktree (§2.6): created, bound to the thread, its setup run, the oldest cleaned up. */
  const startInWorktree = async (cwd: string): Promise<Thread> => {
    const created = await bridge.worktrees.create(cwd, newChoice.base, newChoice.withChanges && !newChoice.base, wtSettings.root);
    const started = await client.request("thread/start", { cwd: created.cwd, model: servedModel, permissions: profile, ...projectFields(true), ...windowToolFields() } as ParamsOf<"thread/start">);
    await bridge.worktrees.bind(created.root, started.thread.id).catch(notice);
    setLink(started.thread.id, { checkout: created.root, cwd: created.cwd, source: created.sourceRoot, sourceCwd: cwd, location: "worktree", snapshot: null });
    dispatch({ type: "opened", thread: started.thread });
    setDrawer(false);
    const environment = newEnvs.find((entry) => entry.file === newChoice.environment) ?? null;
    if (environment?.setup.trim()) {
      updatePrefs({ bottomPanel: true });
      bridge.runTask(cwd, environment.file, "setup", created.cwd, started.thread.id).catch(notice);
    }
    void cleanUp(cwd, created.root);
    return started.thread;
  };

  /** Keeps the most recent managed worktrees (Settings › Worktrees), snapshotting the ones it removes. */
  const cleanUp = async (sourceCwd: string, spare: string | null) => {
    if (!wtSettings.autoCleanup) return;
    try {
      const listed = await bridge.worktrees.list(sourceCwd, wtSettings.root);
      const running = Object.values(state.threads).filter((entry) => entry.activeTurnId).map((entry) => entry.thread.id);
      const kept = protectedOwners(running, state.selected, prefsRef.current.pinnedThreads);
      const victims = cleanupVictims(listed.map((entry) => ({ root: entry.root, owner: entry.owner ?? null, modified: entry.modified ?? 0 })), wtSettings.keep, kept, prefsRef.current.permanentWorktrees, spare);
      for (const checkout of victims) {
        const owner = listed.find((entry) => entry.root === checkout)?.owner ?? null;
        const kept = await bridge.worktrees.remove(sourceCwd, checkout, wtSettings.root, owner ?? `cleanup-${Date.now()}`);
        if (owner && prefsRef.current.worktreeThreads[owner]) setLink(owner, { ...prefsRef.current.worktreeThreads[owner], snapshot: kept });
      }
    } catch (error) {
      notice(error);
    }
  };

  /**
   * A new Ask thread: the UI names the prompt, `ling web` sets its text, folder and sandbox. An
   * incognito one (DESKTOP_PARITY_CLAUDE §4.3) is ephemeral, so the server keeps no session file and
   * no memory of it, and its folder is marked so that leaving the question deletes it; if the mark
   * cannot be made, the question is dropped rather than kept. An incognito question joins no project.
   */
  const startAsk = async (incognito = false): Promise<Thread> => {
    const params = { model: servedModel, prompt: ASK_PROMPT, ...(incognito ? { ephemeral: true } : projectFields(false)) } as unknown as ParamsOf<"thread/start">;
    const started = await client.request("thread/start", params);
    if (incognito) {
      try {
        await bridge.incognito.mark(started.thread.id);
      } catch (error) {
        await client.request("thread/unsubscribe", { threadId: started.thread.id }).catch(() => {});
        throw error;
      }
      setIncognitoNext(false);
    }
    dispatch({ type: "opened", thread: started.thread });
    return started.thread;
  };

  /**
   * A question asked from Quick Entry (DESKTOP_PARITY_CLAUDE §4.2): a new Ask thread with this text and
   * nothing else (no attachment waiting in the composer goes with it); when it cannot be sent, the text
   * waits in the composer of a new question instead.
   */
  const askNow = async (prompt: string) => {
    try {
      const thread = await startAsk();
      await sendMessage(client, { threadId: thread.id, activeTurnId: null, input: turnInput(prompt, []), followUp: prefs.followUp, effort, model: modelChoice, collaborationMode: planFor(thread.id) });
      promptHistory.push(prompt);
    } catch (error) {
      dispatch({ type: "select", threadId: null });
      setDraft(prompt);
      notice(error);
    }
  };

  const newQuestion = () => {
    dispatch({ type: "select", threadId: null });
    setDrawer(false);
  };

  // New thread, like Ctrl+N, only clears the selection: the thread starts with its first message
  // (`sendWork`), so a click never leaves an empty "(new thread)" behind. Without a folder yet, the
  // folder field takes the focus.
  const folderField = useRef<HTMLInputElement>(null);
  const newThread = () => {
    dispatch({ type: "select", threadId: null });
    setDrawer(false);
    if (!newCwd.trim()) folderField.current?.focus();
  };

  const addFiles = (files: Iterable<File>, paths: (string | null)[] = []) => {
    const added: PendingFile[] = [];
    let index = 0;
    for (const file of files) {
      const name = attachmentName(file.name, file.type, index);
      const kind = attachmentKind(file.type, name);
      added.push({ id: nextPendingId++, file, name, kind, preview: kind === "image" ? URL.createObjectURL(file) : null, path: paths[index] ?? null });
      index++;
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
    const files = Array.from(event.clipboardData?.files ?? []);
    // A long paste becomes an attachment (DESKTOP_PARITY_CLAUDE §4.6), so the composer stays readable:
    // in Ask a file in the question's folder, in Work a chip whose text goes into the message.
    const pasted = files.length === 0 ? event.clipboardData?.getData("text/plain") ?? "" : "";
    if (pasted && prefsRef.current.pasteAsFile !== false && isLongPaste(pasted)) {
      event.preventDefault();
      const name = `pasted-${new Date().toISOString().replace(/[-:]/g, "").replace(/\..*$/, "").replace("T", "-")}-${nextPendingId}.txt`;
      setPending((list) => [...list, { id: nextPendingId++, file: new File([pasted], name, { type: "text/plain" }), name, kind: "file", preview: null, path: null, text: pasted }]);
      return;
    }
    if (files.length === 0) return;
    // Work sends images by their path, which only a dropped file has: a pasted one goes to Ask.
    if (view !== "ask") return;
    event.preventDefault();
    addFiles(files);
  };

  /**
   * Files dropped on the composer. Ask attaches them as it attaches picked files. In Work, Shift+drag
   * attaches images (sent by their path); a plain drop writes each file's path into the text as an
   * `@` mention, for the agent to read.
   */
  const onFiles = (files: File[], asImages: boolean) => {
    if (view === "ask") return addFiles(files);
    const paths = files.map((file) => bridge.pathForFile(file));
    if (asImages) {
      const images = files.map((file, index) => ({ file, path: paths[index] })).filter((entry) => entry.path && (IMAGE_TYPES.test(entry.file.type) || attachmentKind(entry.file.type, entry.file.name) === "image"));
      if (images.length === 0) return notice("Shift+drag attaches images that are files on this machine.");
      return addFiles(images.map((entry) => entry.file), images.map((entry) => entry.path));
    }
    const mentions = paths.filter((path): path is string => Boolean(path)).map((path) => `@${composeRoot ? mentionPath(composeRoot, path) : path}`);
    if (mentions.length) setDraft((text) => `${text}${text && !/\s$/.test(text) ? " " : ""}${mentions.join(" ")} `);
  };

  const effort = (() => {
    const model = models.find((entry) => entry.id === (modelChoice ?? servedModel)) ?? models[0] ?? null;
    return effortFor(model, prefs.effort);
  })();

  /** The thread's collaboration mode for its next turn: Plan when it (or, for a new thread, the composer) is in Plan mode. */
  const planFor = (threadId: string) => {
    const inPlan = planning.has(threadId) || (planning.has("") && !selected);
    if (inPlan && !planning.has(threadId)) setPlanning((set) => new Set([...[...set].filter((id) => id !== ""), threadId]));
    return collaborationMode(inPlan ? "plan" : "default", modelChoice ?? servedModel ?? models[0]?.id ?? null, effort);
  };
  const planKey = selected?.thread.id ?? "";
  const togglePlan = () => setPlanning((set) => {
    const next = new Set(set);
    if (next.has(planKey)) next.delete(planKey);
    else next.add(planKey);
    return next;
  });

  /**
   * A project thread's turn carries the project's name, instructions and knowledge files (projects.ts)
   * on its first message and again after the project changed; `stamp` is recorded once the turn is sent.
   */
  const withProjectContext = async (thread: Thread, input: UserInput[], starting: boolean): Promise<{ input: UserInput[]; stamp: string | null }> => {
    const projectId = thread.projectId ?? (starting ? startingProject?.id ?? null : null);
    const project = projectId ? serverProjects.find((entry) => entry.id === projectId) ?? null : null;
    if (!project) return { input, stamp: null };
    const files: KnowledgeFile[] = bridge.knowledge.available() ? await bridge.knowledge.list(project.id).catch(() => []) : [];
    // Searched through ling-docs when its collection is indexed (DESKTOP_PARITY_CLAUDE §4.7), read whole otherwise.
    const collection = knowledgeCollection(project.id);
    const searchable = files.length
      ? await bridge.docs.status().then((status) => (status.collections.some((entry) => entry.name === collection && entry.counts.indexed > 0) ? collection : null)).catch(() => null)
      : null;
    const context = projectContext(project, files, searchable);
    const stamp = contextStamp(context);
    if (!context || prefsRef.current.projectContextSent[thread.id] === stamp) return { input, stamp: null };
    return { input: [...input, contextItem(context)], stamp };
  };
  const contextSent = (threadId: string, stamp: string | null) => {
    if (stamp) updatePrefs({ projectContextSent: { ...prefsRef.current.projectContextSent, [threadId]: stamp } });
  };

  const sendWork = async (message: string, mode: "default" | "steer" | "queue") => {
    // The draft this message came from: a new thread's is cleared, not the thread's it starts.
    const from = draftKeyRef.current;
    const starting = !selected;
    const target = selected?.thread ?? (await startThread());
    if (!target) return;
    const images = pending.filter((item) => item.kind === "image" && item.path).map((item) => item.path as string);
    // Long pastes go into the message itself: Work writes nothing into a project for them.
    const pastes = pending.flatMap((item) => (item.text !== undefined ? [item.text] : []));
    const typed = composeInput(pastes.length ? withPastes(message, pastes) : message, rootSkills, images);
    if (typed.length === 0) return;
    const shownActive = selected?.thread.id === target.id ? selected.activeTurnId : null;
    const { input, stamp } = shownActive ? { input: typed, stamp: null } : await withProjectContext(target, typed, starting);
    setDraftFor(from, "");
    const files = pending;
    setPending([]);
    const shown = selected?.thread.id === target.id ? selected : null;
    try {
      await sendMessage(client, {
        threadId: target.id,
        activeTurnId: shown?.activeTurnId ?? null,
        input,
        followUp: mode === "default" ? prefs.followUp : mode,
        permissions: profile,
        effort,
        model: modelChoice,
        cwd: turnCwd(prefsRef.current.worktreeThreads[target.id]),
        collaborationMode: planFor(target.id),
      });
      contextSent(target.id, stamp);
      promptHistory.push(message);
      files.forEach((item) => item.preview && URL.revokeObjectURL(item.preview));
    } catch (error) {
      setDraftFor(from, message);
      setPending(files);
      notice(error);
    }
  };

  const sendAsk = async (message: string, mode: "default" | "steer" | "queue") => {
    const files = pending;
    const from = draftKeyRef.current;
    setSending(true);
    setDraftFor(from, "");
    setPending([]);
    try {
      const starting = !selected;
      const thread = selected?.thread ?? (await startAsk(incognitoNext && bridge.incognito.available()));
      // Attachments go into the thread's own folder, which exists once the thread does.
      const attached: Attached[] = await Promise.all(
        files.map(async (item) => ({ kind: item.kind, name: item.name, path: (await bridge.upload(thread.id, item.file, item.kind, item.name)).path })),
      );
      const active = selected?.thread.id === thread.id ? selected.activeTurnId : null;
      const { input, stamp } = active ? { input: turnInput(message, attached), stamp: null } : await withProjectContext(thread, turnInput(message, attached), starting);
      await sendMessage(client, { threadId: thread.id, activeTurnId: active, input, followUp: mode === "default" ? prefs.followUp : mode, effort, model: modelChoice, collaborationMode: planFor(thread.id) });
      contextSent(thread.id, stamp);
      promptHistory.push(message);
      files.forEach((item) => item.preview && URL.revokeObjectURL(item.preview));
    } catch (error) {
      setDraftFor(from, message);
      setPending(files);
      notice(error);
    } finally {
      setSending(false);
    }
  };

  /**
   * A `!` command (DESKTOP_PARITY §2.3): run in the user's own shell in the thread's folder, as the
   * Codex app runs it, its output shown in the thread. A thread is started first when none is open.
   * The policy refuses it at `/airgapped on`; the refusal is the notice.
   */
  const runShell = async (command: string) => {
    const from = draftKeyRef.current;
    const target = selected?.thread ?? (await startThread());
    if (!target) return;
    setDraftFor(from, "");
    try {
      await client.request("thread/shellCommand", { threadId: target.id, command });
      promptHistory.push(`!${command}`);
    } catch (error) {
      setDraftFor(from, `!${command}`);
      notice(error);
    }
  };

  const send = async (text: string, mode: "default" | "steer" | "queue") => {
    const message = text.trim();
    if (sending) return;
    const slash = parseSlash(message);
    if (slash) {
      setDraft("");
      return runCommand(slash.command, slash.args);
    }
    const shell = view === "work" ? parseShell(message) : null;
    if (shell) return runShell(shell);
    if (view === "ask") {
      if (message || pending.length) await sendAsk(message, mode);
    } else if (message || pending.length) {
      await sendWork(message, mode);
    }
  };

  const stop = () => {
    if (!selected?.activeTurnId || !composerHere) return;
    client.request("turn/interrupt", { threadId: selected.thread.id, turnId: selected.activeTurnId }).catch(notice);
  };

  const setName = async (threadId: string, name: string) => {
    await client.request("thread/name/set", { threadId, name });
    const updated = { method: "thread/name/updated", params: { threadId, threadName: name } } as ServerNotification;
    dispatch({ type: "notification", notification: updated });
  };

  /** Renames a thread; Ctrl+Z puts the old name back. */
  const rename = async (threadId: string, name: string) => {
    setRenaming(null);
    const trimmed = name.trim();
    const current = state.threads[threadId]?.thread;
    // Left as it opened (a blur, Enter on the shown title): nothing to rename, and a title taken
    // from the first question does not harden into a name.
    if (!trimmed || (current && trimmed === renameStart(current))) return;
    const before = current?.name ?? null;
    try {
      await setName(threadId, trimmed);
      undoStack.push({ label: "Rename", undo: () => setName(threadId, before ?? ""), redo: () => setName(threadId, trimmed) });
    } catch (error) {
      notice(error);
    }
  };

  /** Archives one thread on the server, its managed worktree removed (a snapshot kept for Restore), without an undo entry. */
  const archiveOne = async (threadId: string) => {
    await client.request("thread/archive", { threadId });
    // Archiving deletes a managed worktree (a snapshot is kept for Restore), never a permanent one.
    const link = links[threadId];
    if (link && bridge.git.available() && !prefs.permanentWorktrees.includes(link.checkout)) {
      bridge.worktrees.remove(link.sourceCwd, link.checkout, wtSettings.root, threadId)
        .then((kept) => setLink(threadId, { ...link, snapshot: kept })).catch(notice);
    }
    dispatch({ type: "archived", threadId });
    setFound((list) => list?.filter((result) => result.thread.id !== threadId) ?? null);
  };
  /** One undo entry that brings several archived threads back (a project's Archive chats archives them all at once). */
  const archiveUndo = (label: string, threadIds: string[]) => undoStack.push({
    label,
    undo: async () => {
      for (const threadId of threadIds) {
        const answer = (await client.request("thread/unarchive", { threadId })) as { thread?: Thread };
        if (answer.thread) dispatch({ type: "threads", list: [answer.thread] });
      }
    },
    redo: async () => {
      for (const threadId of threadIds) {
        await client.request("thread/archive", { threadId });
        dispatch({ type: "archived", threadId });
      }
    },
  });

  const archive = async (threadId: string) => {
    try {
      await archiveOne(threadId);
      archiveUndo("Archive", [threadId]);
    } catch (error) {
      notice(error);
    }
  };

  /** A project's menu: Archive chats archives every thread of it (one undo brings them back). */
  const archiveProjectChats = async (project: Project) => {
    const ids = Object.values(state.threads).filter((view) => view.thread.projectId === project.id && !view.thread.ephemeral).map((view) => view.thread.id);
    if (ids.length === 0) return notice(`${project.name} has no threads to archive.`);
    if (!window.confirm(`Archive the ${ids.length} thread${ids.length === 1 ? "" : "s"} of ${project.name}? Ctrl+Z brings them back.`)) return;
    const done: string[] = [];
    try {
      for (const id of ids) {
        await archiveOne(id);
        done.push(id);
      }
    } catch (error) {
      notice(error);
    }
    if (done.length) archiveUndo(`Archive ${project.name}'s threads`, done);
  };

  /** A project's metadata changed (Remove from the sidebar, Restore), the change undoable. */
  const setProjectMetadata = async (project: Project, changes: Record<string, string | null>, label: string) => {
    try {
      const metadata = withMetadata(project.metadata, changes);
      await client.request("project/update", { projectId: project.id, metadata });
      await loadProjects();
      undoStack.push({
        label,
        undo: async () => {
          await client.request("project/update", { projectId: project.id, metadata: withMetadata(project.metadata, {}) });
          await loadProjects();
        },
        redo: async () => {
          await client.request("project/update", { projectId: project.id, metadata });
          await loadProjects();
        },
      });
    } catch (error) {
      notice(error);
    }
  };

  /** A thread moved into a project, or out of its project (`thread/metadata/update`'s `projectId`). */
  const moveToProject = async (threadId: string, projectId: string | null) => {
    try {
      const answer = (await client.request("thread/metadata/update", { threadId, projectId } as ParamsOf<"thread/metadata/update">)) as { thread?: Thread };
      if (answer.thread) dispatch({ type: "threads", list: [answer.thread] });
      else dispatch({ type: "notification", notification: { method: "thread/project/updated", params: { threadId, projectId } } as ServerNotification });
    } catch (error) {
      notice(error);
    }
  };


  /** The agent's settings, read and written through the server (Settings › Memories). */
  const readConfig = useCallback(async () => ((await client.request("config/read", {})) as { config: Record<string, unknown> }).config, [client]);
  const writeConfig = useCallback(async (keyPath: string, value: unknown) => {
    await client.request("config/value/write", { keyPath, value: value as never, mergeStrategy: "upsert" });
  }, [client]);

  const memoryStatus = useCallback(async () => (await client.request("memory/status", {})) as { v2ConsolidatedThreads: number; v2Ready: boolean }, [client]);
  const resetMemories = useCallback(async () => {
    await client.request("memory/reset", undefined);
  }, [client]);

  /** Settings › Archived threads. */
  const listArchived = useCallback(async (): Promise<Thread[]> => ((await client.request("thread/list", { archived: true, limit: 200 })) as { data: Thread[] }).data, [client]);
  const unarchiveThread = useCallback(async (threadId: string) => {
    const answer = (await client.request("thread/unarchive", { threadId })) as { thread?: Thread };
    if (answer.thread) dispatch({ type: "threads", list: [answer.thread] });
  }, [client]);

  // Settings › Appearance: the theme (theme.ts), then the colour and font tokens over the theme shown.
  useEffect(() => theme()?.setChoice(prefs.theme), [prefs.theme]);
  const shownTheme = useShownTheme();
  useEffect(() => {
    const root = document.documentElement;
    const chosen = appearanceFor(prefs.appearance, shownTheme);
    for (const [key, token] of Object.entries(APPEARANCE_TOKENS)) {
      const value = chosen[key as keyof typeof chosen];
      if (value) root.style.setProperty(token, value);
      else root.style.removeProperty(token);
    }
  }, [prefs.appearance, shownTheme]);

  // The user's shortcuts (applied as the page renders, so a handler never sees stale keys), the text
  // size and back/forward follow the window's state.
  setOverrides(prefs.keyOverrides);
  useEffect(() => {
    const root = document.getElementById("root");
    if (root) root.style.zoom = prefs.fontScale === 1 ? "" : String(prefs.fontScale);
  }, [prefs.fontScale]);
  useEffect(() => {
    if (travelling.current) {
      travelling.current = false;
      return;
    }
    places.visit({ view, thread: state.selected });
  }, [view, state.selected, places]);

  /** Back or forward: the view and the thread the window showed. */
  const travel = (place: Place | null) => {
    if (!place) return;
    travelling.current = true;
    setView(place.view);
    if (typeof window !== "undefined") window.history.replaceState(null, "", place.view === "work" ? "#work" : "#");
    if (place.thread && state.threads[place.thread]) void openThread(place.thread);
    else dispatch({ type: "select", threadId: null });
  };
  useEffect(() => {
    const onMouse = (event: MouseEvent) => {
      if (event.button === 3) travel(places.back());
      else if (event.button === 4) travel(places.forward());
    };
    window.addEventListener("mouseup", onMouse);
    return () => window.removeEventListener("mouseup", onMouse);
  });

  const undoAction = async (redo: boolean) => {
    try {
      const label = redo ? await undoStack.redo() : await undoStack.undo();
      if (label) dispatch({ type: "notice", message: `${redo ? "Redone" : "Undone"}: ${label.toLowerCase()}.` });
    } catch (error) {
      notice(error);
    }
  };

  /** Ctrl+W: the frontmost surface beside the thread closes (never the window); Ctrl+Shift+T opens it again. */
  const closeSurface = () => {
    // Only what this view shows: in Ask a file of the question, never one of Work's tabs behind it.
    const front = paneActive;
    if (front) {
      closeTabKey(front.key);
      closedSurfaces.close(surfaceOf(front));
    } else if (view === "work" && prefsRef.current.bottomPanel) {
      updatePrefs({ bottomPanel: false });
      closedSurfaces.close("bottomPanel");
    }
  };
  const reopenSurface = () => {
    const surface = closedSurfaces.reopen();
    if (surface === "review") setReviewOpen(true);
    else if (surface === "bottomPanel") updatePrefs({ bottomPanel: true });
    else if (surface?.startsWith("file:")) openFile(surface.slice(5));
    else if (surface?.startsWith("preview:")) openPreview(surface.slice(8));
    else if (surface === "prs") openPullRequests();
  };

  const openFolderDialog = async () => {
    try {
      const folder = await bridge.chooseFolder();
      if (!folder) return;
      showView("work");
      setNewCwd(folder);
    } catch (error) {
      notice(error);
    }
  };

  /** What the shortcuts and the command menu run, by keymap action. */
  const commands: Record<string, () => void> = {
    commandMenu: () => setMenuOpen((open) => (open ? false : "all")),
    settings: () => { setSettingsPage("general"); setSettingsOpen((open) => !open); },
    shortcuts: () => { setSettingsPage("shortcuts"); setSettingsOpen(true); },
    openFolder: () => void openFolderDialog(),
    back: () => travel(places.back()),
    forward: () => travel(places.forward()),
    fontBigger: () => updatePrefs({ fontScale: nextScale(prefsRef.current.fontScale, 1) }),
    fontSmaller: () => updatePrefs({ fontScale: nextScale(prefsRef.current.fontScale, -1) }),
    fontReset: () => updatePrefs({ fontScale: 1 }),
    toggleSidebar: () => updatePrefs({ sidebarHidden: !prefsRef.current.sidebarHidden }),
    // New Window (§13): another window on this one's view, on the same app-server; the app only.
    newWindow: () => { if (bridge.windows.available()) bridge.windows.open(view).catch(notice); },
    undoAction: () => void undoAction(false),
    redoAction: () => void undoAction(true),
    closeTab: closeSurface,
    reopenTab: reopenSurface,
    closeOtherTabs: () => {
      // The tab in front stays; the others close (and reopen one by one with Ctrl+Shift+T).
      // Only among the tabs this view shows; the other view's stay as they are.
      const keep = paneActive;
      const closing = paneTabs.filter((tab) => tab !== keep);
      for (const tab of closing) closedSurfaces.close(surfaceOf(tab));
      setTabs((list) => list.filter((tab) => !closing.includes(tab)));
      if (view !== "work") return;
      if (prefsRef.current.bottomPanel) closedSurfaces.close("bottomPanel");
      updatePrefs({ bottomPanel: false });
    },
    activityView: () => setActivityOpen((open) => !open),
    togglePet: () => {
      if (HOST !== "electron") return notice("The pet is the desktop app's.");
      updatePrefs({ petShown: !prefsRef.current.petShown });
    },
    previewOpen: () => {
      if (!bridge.preview.available()) return notice("The preview tab is the desktop app's: open the page in your browser.");
      openPreview("");
    },
    searchFiles: () => {
      if (!composeRoot) return notice(view === "ask" ? "Search files looks in a question's folder: open a question first." : "Search files looks in a thread's folder: open a thread in Work, or name a project folder.");
      setFileSearch((open) => (open === null ? "" : null));
    },
    fileTree: () => {
      // In Ask, the question's own folder (DESKTOP_PARITY_CLAUDE §4.1); with no question shown, Work's tree.
      if (view === "ask" && askFolder) {
        if (prefsRef.current.layout === "chat") updatePrefs({ layout: "split" });
        setAskTree((shown) => !shown || prefsRef.current.layout === "chat");
        return;
      }
      if (view !== "work") showView("work");
      if (prefsRef.current.layout === "chat") updatePrefs({ layout: "split" });
      setTreeShown((shown) => !shown || prefsRef.current.layout === "chat");
    },
    fullScreen: () => void bridge.toggleFullScreen().catch(notice),
    quit: () => void bridge.quitApp(),
    viewAsk: () => showView("ask"),
    viewWork: () => showView("work"),
    modelPicker: () => setPicker((open) => (open === "closed" ? "open" : "closed")),
    recentModels: () => setPicker("recent"),
    reviewTab: () => {
      if (reviewOpen && prefsRef.current.layout === "chat") updatePrefs({ layout: "split" });
      else setReviewOpen((open) => !open);
    },
    cycleLayout: () => {
      if (!reviewOpen) setReviewOpen(true);
      const order = ["split", "tabs", "chat"] as const;
      updatePrefs({ layout: order[(order.indexOf(prefsRef.current.layout) + 1) % order.length] });
    },
    fullView: () => {
      if (!reviewOpen) setReviewOpen(true);
      updatePrefs({ layout: prefsRef.current.layout === "tabs" ? "split" : "tabs" });
    },
    chatTabsToggle: () => {
      if (!reviewOpen) setReviewOpen(true);
      updatePrefs({ layout: prefsRef.current.layout === "tabs" ? "chat" : "tabs" });
    },
    bottomPanel: () => updatePrefs({ bottomPanel: !prefsRef.current.bottomPanel }),
  };

  /** The command menu's entries: the commands above, the threads, the project folders, the settings pages. */
  const menuEntries = (): MenuEntry[] => {
    const entries: MenuEntry[] = effectiveKeymap().filter((action) => commands[action.id] && action.id !== "commandMenu")
      .map((action) => ({ id: action.id, kind: "command", label: action.label, detail: action.group, shortcut: action.keys[0] ?? null }));
    for (const entry of Object.values(state.threads)) {
      const thread = entry.thread;
      if (thread.ephemeral || thread.parentThreadId) continue;
      entries.push({ id: thread.id, kind: "thread", label: thread.name || thread.preview || "(new thread)", detail: isAskThread(thread, askRoot) ? "Ask" : thread.cwd, shortcut: null });
    }
    for (const project of projects(state, askRoot, (thread) => projectFolder(thread, links))) {
      entries.push({ id: project.cwd, kind: "project", label: project.cwd.split(/[\\/]/).filter(Boolean).pop() ?? project.cwd, detail: project.cwd, shortcut: null });
    }
    entries.push({ id: "general", kind: "settings", label: "General", detail: "Settings", shortcut: shortcutLabel("settings") });
    entries.push({ id: "shortcuts", kind: "settings", label: "Keyboard shortcuts", detail: "Settings", shortcut: shortcutLabel("shortcuts") });
    if (bridge.git.available()) entries.push({ id: "worktrees", kind: "settings", label: "Worktrees", detail: "Settings", shortcut: null });
    return entries;
  };

  const runEntry = (entry: MenuEntry) => {
    if (entry.kind === "command") return commands[entry.id]?.();
    if (entry.kind === "thread") {
      const thread = state.threads[entry.id]?.thread;
      if (thread) setView(isAskThread(thread, askRoot) ? "ask" : "work");
      return void openThread(entry.id);
    }
    if (entry.kind === "project") {
      showView("work");
      return setNewCwd(entry.id);
    }
    setSettingsPage(entry.id as SettingsPage);
    setSettingsOpen(true);
  };

  // Threads (item 3): pins, sections, unread, the sidebar's order, links, pop-outs, side chats.
  const pin = (threadId: string) => {
    const before = prefsRef.current.pinnedThreads;
    const after = toggled(before, threadId);
    updatePrefs({ pinnedThreads: after });
    undoStack.push({ label: before.includes(threadId) ? "Unpin" : "Pin", undo: () => updatePrefsRef.current({ pinnedThreads: before }), redo: () => updatePrefsRef.current({ pinnedThreads: after }) });
  };
  const pinProject = (cwd: string) => updatePrefs({ pinnedProjects: toggled(prefsRef.current.pinnedProjects, cwd) });
  const markUnread = (threadId: string) => {
    if (!prefsRef.current.unreadThreads.includes(threadId)) updatePrefs({ unreadThreads: [...prefsRef.current.unreadThreads, threadId] });
    if (state.selected === threadId) dispatch({ type: "select", threadId: null });
  };
  /** Moves a thread into a section, a new one (named in the sidebar first), or out of its section. */
  const toSection = (threadId: string, sectionId: string | "new" | null, name?: string) => {
    if (sectionId === "new" && !name) return setNaming({ thread: threadId, name: "" });
    let sections: Section[] = prefsRef.current.sections;
    let target = sectionId;
    if (sectionId === "new" && name) {
      const id = `s${Date.now().toString(36)}`;
      sections = [...sections, { id, name: name.slice(0, 60), threads: [] }];
      target = id;
    }
    updatePrefs({ sections: moveToSection(sections, threadId, target).filter((section) => section.threads.length > 0) });
  };
  const copyLink = (threadId: string) => void copyText(threadLink(threadId)).then((done) => done && dispatch({ type: "notice", message: "Copied a link to the thread." }));

  /** Side chat (Ctrl+Alt+S): a copy of the thread asked for a short recap, beside the running one, never interrupting it. */
  const sideChat = async () => {
    if (!selected) return;
    if (selected.thread.ephemeral) return notice("An incognito question has no side chat: it would be saved.");
    try {
      const thread = await forkThread(client, selected.thread.id, servedModel);
      await client.request("thread/name/set", { threadId: thread.id, name: `Side chat: ${selected.thread.name || selected.thread.preview || "thread"}`.slice(0, 120) });
      await client.request("turn/start", { threadId: thread.id, input: [{ type: "text", text: "In a few lines: where does this work stand, what was done, and what is left? Do not change anything.", text_elements: [] }] });
      if (bridge.host() === "electron") await bridge.popOut(thread.id);
      else await openThread(thread.id);
    } catch (error) {
      notice(error);
    }
  };

  /** A thread with no project (Ctrl+Alt+O): it works in the home folder. */
  const standaloneThread = async () => {
    try {
      const home = await bridge.homeFolder();
      showView("work");
      setNewCwd(home);
    } catch (error) {
      notice(error);
    }
  };

  /** What a `ling://` link names (DESKTOP_PARITY §2.20). */
  const linkRef = useRef<(target: bridge.LinkTarget) => void>(() => {});
  linkRef.current = (target) => {
    if (target.thread) {
      const known = state.threads[target.thread]?.thread;
      if (known) setView(isAskThread(known, askRoot) ? "ask" : "work");
      void openThread(target.thread);
      return;
    }
    if (target.page === "pets") {
      setSettingsPage("pets");
      return setSettingsOpen(true);
    }
    // The pet's quick question and `ling://ask`: a new Ask question with the text in the composer;
    // Quick Entry's (`send`): the question asked at once (DESKTOP_PARITY_CLAUDE §4.2).
    if (target.page === "ask") {
      showView("ask");
      if (target.prompt && target.send) void askNow(target.prompt);
      else if (target.prompt) setDraft(target.prompt);
      return;
    }
    if (target.page === "settings") {
      setSettingsPage("general");
      return setSettingsOpen(true);
    }
    if (target.page === "skills") return setExtensions("skills");
    if (target.page === "night") return setScheduledOpen(true);
    if (target.page === "plugins") return setExtensions("skills");
    showView("work");
    if (target.cwd) setNewCwd(target.cwd);
    if (target.prompt) setDraft(target.prompt);
  };

  // The sidebar's order, which Ctrl+Tab and Ctrl+1–9 walk.
  const sidebarThreads = (): Thread[] => {
    const all = Object.values(state.threads).map((entry) => entry.thread).filter((thread) => !thread.ephemeral && !thread.parentThreadId);
    const mine = all.filter((thread) => isAskThread(thread, askRoot) === (view === "ask"));
    const pinned = prefs.pinnedThreads.map((id) => mine.find((thread) => thread.id === id)).filter((thread): thread is Thread => Boolean(thread));
    const sectioned = new Set(prefs.sections.flatMap((section) => section.threads));
    const rest = view === "ask"
      ? askThreads(mine, askRoot)
      : [
        ...prefs.sections.flatMap((section) => section.threads.map((id) => mine.find((thread) => thread.id === id)).filter((thread): thread is Thread => Boolean(thread))),
        ...pinnedFirst(projects(state, askRoot, (thread) => projectFolder(thread, links)), prefs.pinnedProjects, (project) => project.cwd).flatMap((project) => project.threads.filter((thread) => !sectioned.has(thread.id))),
      ];
    return [...pinned, ...rest.filter((thread) => !prefs.pinnedThreads.includes(thread.id))];
  };
  const goTo = (threadId: string | null) => {
    if (threadId) void openThread(threadId);
  };

  const threadCommands: Record<string, () => void> = {
    newChat: () => (view === "ask" ? newQuestion() : dispatch({ type: "select", threadId: null })),
    standaloneChat: () => void standaloneThread(),
    quickChat: () => { showView("ask"); newQuestion(); },
    archiveChat: () => { if (selected) void archive(selected.thread.id); },
    markUnread: () => { if (selected) markUnread(selected.thread.id); },
    pinChat: () => { if (selected) pin(selected.thread.id); },
    renameChat: () => { if (selected) setRenaming({ id: selected.thread.id, name: renameStart(selected.thread) }); },
    sideChat: () => void sideChat(),
    findInChat: () => { setFindOpen(true); requestAnimationFrame(() => document.querySelector<HTMLInputElement>(".find-bar input")?.select()); },
    findNext: () => setFindAt((at) => (findCount ? (at + 1) % findCount : 0)),
    findPrevious: () => setFindAt((at) => (findCount ? (at - 1 + findCount) % findCount : 0)),
    previousChat: () => goTo(stepThread(sidebarThreads().map((thread) => thread.id), state.selected, -1)),
    nextChat: () => goTo(stepThread(sidebarThreads().map((thread) => thread.id), state.selected, 1)),
    nextAttention: () => goTo(nextAttention(sidebarThreads().map((thread) => thread.id), state.selected, new Set(state.requests.map((request) => request.threadId).filter((id): id is string => Boolean(id))), new Set(prefs.unreadThreads))),
    clearUnread: () => updatePrefs({ unreadThreads: [] }),
    projectPicker: () => setMenuOpen("projects"),
    copyDeepLink: () => { if (selected) copyLink(selected.thread.id); },
    copyConversationPath: () => { if (selected?.thread.path) void copyText(selected.thread.path); },
    copyWorkingDirectory: () => { const folder = workFolder ?? selected?.thread.cwd; if (folder) void copyText(folder); },
  };
  for (let n = 1; n <= 9; n++) threadCommands[`goToChat${n}`] = () => goTo(sidebarThreads()[n - 1]?.id ?? null);
  for (let n = 1; n <= 6; n++) threadCommands[`recentChat${n}`] = () => goTo(recentThreads(Object.values(state.threads).map((entry) => entry.thread).filter((thread) => !thread.ephemeral && !thread.parentThreadId))[n - 1]?.id ?? null);
  Object.assign(commands, threadCommands);

  // Find in the shown thread: the matches are marked as the query or the turns change.
  useEffect(() => {
    const root = document.querySelector(".turns");
    if (!findOpen || !root) {
      mark([], 0);
      setFindCount(0);
      return;
    }
    const ranges = findRanges(root, findQuery);
    setFindCount(ranges.length);
    mark(ranges, ranges.length ? Math.min(findAt, ranges.length - 1) : 0);
  }, [findOpen, findQuery, findAt, selected?.turns, state.selected]);
  useEffect(() => setFindAt(0), [findQuery, state.selected]);

  // Where the shown thread's work is now (its worktree, or the local checkout after a hand-off), and
  // the project its environments are read from.
  const selectedLink = selected ? links[selected.thread.id] : undefined;
  const workFolder = selected ? (turnCwd(selectedLink) ?? selected.thread.cwd) : (newCwd.trim() || null);
  const envProject = view !== "work" ? null : selected ? (selectedLink?.sourceCwd ?? selected.thread.cwd) : (newCwd.trim() || null);
  useEffect(() => {
    if (!bridge.git.available() || !envProject || state.server !== "ready") {
      setEnvs(null);
      return;
    }
    let current = true;
    bridge.localEnvironments(envProject).then((answer) => current && setEnvs({ project: envProject, ...answer })).catch(() => current && setEnvs(null));
    return () => {
      current = false;
    };
  }, [envProject, editingEnv, state.server]);
  const environment = envs?.found[0] ?? null;

  // A new thread's options: its repository's branches and environments.
  const newFolder = view === "work" && !selected ? newCwd.trim() : "";
  useEffect(() => {
    if (!newFolder || !bridge.git.available() || state.server !== "ready") {
      setNewBranches(null);
      setNewEnvs([]);
      return;
    }
    let current = true;
    const timer = setTimeout(() => {
      bridge.git.repos(newFolder).then(async (repos) => {
        if (!current) return;
        const inside = repos[0] && (newFolder === repos[0] || newFolder.startsWith(`${repos[0]}/`)) ? repos[0] : null;
        setNewBranches(inside ? await bridge.worktrees.branches(inside) : null);
        const found = await bridge.localEnvironments(newFolder).catch(() => ({ found: [], problems: [] }));
        if (current) setNewEnvs(found.found);
      }).catch(() => current && setNewBranches(null));
    }, 300);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [newFolder, state.server]);

  // A worktree thread whose worktree was removed (archived, cleaned up): offer Restore.
  useEffect(() => {
    setMissingWorktree(null);
    if (!selected || !selectedLink || selectedLink.location !== "worktree" || !bridge.git.available()) return;
    let current = true;
    bridge.worktrees.list(selectedLink.sourceCwd, wtSettings.root)
      .then((listed) => current && !listed.some((entry) => entry.root === selectedLink.checkout) && setMissingWorktree(selected.thread.id))
      .catch(() => {});
    return () => {
      current = false;
    };
  }, [selected?.thread.id, selectedLink?.checkout, selectedLink?.location, wtSettings.root]);

  /** Brings a removed worktree back from its snapshot (or a fresh one when no work was kept). */
  const restoreWorktree = async () => {
    if (!selected || !selectedLink) return;
    try {
      const created = selectedLink.snapshot
        ? await bridge.worktrees.restore(selectedLink.sourceCwd, selectedLink.snapshot, wtSettings.root)
        : await bridge.worktrees.create(selectedLink.sourceCwd, null, false, wtSettings.root);
      await bridge.worktrees.bind(created.root, selected.thread.id).catch(() => {});
      setLink(selected.thread.id, { ...selectedLink, checkout: created.root, cwd: carry(selectedLink.cwd, selectedLink.checkout, created.root), snapshot: null });
      setMissingWorktree(null);
    } catch (error) {
      notice(error);
    }
  };

  /** Hand-off (§2.6): the thread and its work move between its worktree and the local checkout. */
  const handOff = async (direction: "toLocal" | "toWorktree") => {
    if (!selected) return;
    if (selected.activeTurnId) return notice("Stop the turn before handing the thread off.");
    const threadId = selected.thread.id;
    try {
      let link = links[threadId];
      if (!link) {
        if (direction === "toLocal") return;
        const created = await bridge.worktrees.create(selected.thread.cwd, null, false, wtSettings.root);
        await bridge.worktrees.bind(created.root, threadId).catch(() => {});
        link = { checkout: created.root, cwd: created.cwd, source: created.sourceRoot, sourceCwd: selected.thread.cwd, location: "local", snapshot: null };
      }
      await bridge.worktrees.handOff(direction, link.checkout, link.source, wtSettings.root, threadId);
      setLink(threadId, { ...link, location: direction === "toLocal" ? "local" : "worktree" });
      setGitTick((tick) => tick + 1);
    } catch (error) {
      notice(error);
    }
  };

  // The header's git facts for the shown thread: whether its folder is in a repository, and the branch.
  const [headerGit, setHeaderGit] = useState<{ folder: string; root: string | null; branch: string | null } | null>(null);
  const [gitTick, setGitTick] = useState(0);
  useEffect(() => {
    if (!selected || !workFolder || !bridge.git.available() || view !== "work") {
      setHeaderGit(null);
      return;
    }
    let current = true;
    bridge.git.repos(workFolder).then(async (repos) => {
      const root = repos[0] && (workFolder === repos[0] || workFolder.startsWith(`${repos[0]}/`)) ? repos[0] : null;
      const branch = root ? (await bridge.git.status(root)).branch : null;
      if (current) setHeaderGit({ folder: workFolder, root, branch });
    }).catch(() => current && setHeaderGit({ folder: workFolder, root: null, branch: null }));
    return () => {
      current = false;
    };
  }, [selected?.thread.id, workFolder, view, gitTick, selected?.activeTurnId]);

  const createBranch = (name: string) => {
    if (!selectedLink) return;
    bridge.worktrees.branch(selectedLink.checkout, name).then(() => setGitTick((tick) => tick + 1)).catch(notice);
  };

  const openFolder = (where: "editor" | "terminal" | "folder") => {
    if (!workFolder) return;
    const opened = where === "terminal" ? bridge.openTerminal(workFolder) : where === "folder" ? bridge.openFolder(workFolder) : bridge.openEditorAt(workFolder, prefs.editor);
    opened.catch(notice);
  };

  /** Runs an action of the project's environment in the bottom panel, in the folder the thread works in. */
  const runAction = (action: bridge.EnvironmentAction) => {
    if (!workFolder || !environment || !envProject) return notice("Open a thread in a project with a local environment first.");
    // An action runs in a terminal tab of its own, under the composer's permissions (§2.6, §2.7).
    openTerminal(action.name, action.script);
  };

  /**
   * A new terminal tab for the shown thread (or for `forThread`, which a detached terminal pane asks
   * for), in the folder it works in, under the composer's permissions. In the app every window is
   * told of it, and of its exit, which the server answers to this window alone.
   */
  const openTerminal = (title = "Terminal", script: string | null = null, forThread: string | null = null) => {
    const other = forThread !== null && forThread !== selected?.thread.id ? state.threads[forThread]?.thread ?? null : null;
    const cwd = other ? other.cwd : workFolder ?? (view === "work" ? newCwd.trim() || null : null);
    if (!cwd) return notice("Open a thread in a project, or name a project folder, to open a terminal there.");
    const session = hub.start({ threadId: other ? other.id : selected?.thread.id ?? null, cwd, profile, shell: prefs.terminalShell, size: { rows: 24, cols: 100 }, title, script },
      (processId, code, error) => {
        setTerminals((list) => list.map((entry) => (entry.processId === processId ? { ...entry, exitCode: code, error } : entry)));
        if (bridge.terminals.available()) bridge.terminals.publish({ ...session, exitCode: code, error }).catch(() => {});
      });
    setTerminals((list) => [...list, session]);
    if (bridge.terminals.available()) bridge.terminals.publish(session).catch(notice);
    if (other) return;
    updatePrefs({ bottomPanel: true });
    setFront({ id: session.processId, tick: Date.now() });
  };

  const closeTerminal = (processId: string) => {
    const session = terminals.find((entry) => entry.processId === processId);
    if (session && session.exitCode === null) hub.terminate(processId).catch(() => {});
    hub.forget(processId);
    setTerminals((list) => list.filter((entry) => entry.processId !== processId));
    if (bridge.terminals.available()) bridge.terminals.forget(processId).catch(() => {});
  };

  /**
   * The thread's terminal or diff into a window of its own (the pane's header button, the Window
   * menu), or back when it is out already; in a detached pane's own window, back into the thread's.
   */
  const detachPane = (kind: PaneKind) => {
    if (!bridge.windows.available()) return;
    if (PANE) {
      bridge.windows.dock(PANE.kind, PANE.thread).catch(notice);
      return;
    }
    const thread = selected?.thread.id ?? null;
    if (!thread || view !== "work") return notice(`Open a thread in Work first: its ${kind === "terminal" ? "terminal" : "diff"} moves into a window of its own.`);
    const key = paneKey(kind, thread);
    if (detached.has(key)) {
      bridge.windows.dock(kind, thread).catch(notice);
      return;
    }
    bridge.windows.detach(kind, thread).then(() => {
      setDetached((keys) => new Set(keys).add(key));
      if (kind === "review") closeTabKey(REVIEW_TAB.key);
    }).catch(notice);
  };
  windowEvents.current = {
    command: (command) => detachPane(command === "detachTerminal" ? "terminal" : "review"),
    // A pane docked back: it shows here again, if its thread is in front.
    docked: (pane, thread) => {
      setDetached((keys) => {
        const next = new Set(keys);
        next.delete(paneKey(pane, thread));
        return next;
      });
      if (selectedRef.current !== thread) return;
      if (pane === "review") setReviewOpen(true);
      else updatePrefs({ bottomPanel: true });
    },
    // What a detached pane asks of this window, which holds the thread's composer and permissions.
    paneAction: (action) => {
      if (action.kind === "newTerminal") openTerminal("Terminal", null, action.thread);
      else if (action.kind === "compose") setDraftFor(draftKeyOf("work", action.thread), (text) => (text.trim() ? `${text.trimEnd()}\n\n${action.text}` : action.text));
      else if (action.thread === selectedRef.current) openFile(action.path, action.line);
      else notice("Show the thread in this window to open its file.");
    },
  };

  /** The tab's recent output into the composer: what lets the agent read a dev server's or a build's output. */
  const sendTerminal = (processId: string) => {
    const session = terminals.find((entry) => entry.processId === processId);
    const output = hub.tail(processId);
    if (!session || !output) return notice("The terminal has shown nothing yet.");
    setDraft((text) => (text.trim() ? `${text.trimEnd()}\n\n${terminalMessage(session.cwd, output)}` : terminalMessage(session.cwd, output)));
  };

  /** A permanent worktree of the project: a project of its own, never cleaned up. */
  const permanentWorktree = async (project: string) => {
    try {
      const created = await bridge.worktrees.create(project, null, false, wtSettings.root);
      updatePrefs({ permanentWorktrees: [...prefsRef.current.permanentWorktrees, created.root] });
      setNewCwd(created.cwd);
    } catch (error) {
      notice(error);
    }
  };

  /** Writes a worktree setting into `[desktop]` of config.toml, where the codex-worktree crate reads it. */
  const writeWorktreeSetting = async (key: "git-worktree-root" | "worktree-keep-count" | "worktree-auto-cleanup-enabled", value: string | number | boolean | null) => {
    try {
      await client.request("config/value/write", { keyPath: `desktop.${key}`, value, mergeStrategy: "upsert" });
      const answer = (await client.request("config/read", {})) as { config?: { desktop?: Record<string, unknown> | null } };
      setWtSettings(worktreeSettings(answer.config?.desktop));
    } catch (error) {
      notice(error);
    }
  };

  const answer = (request: PendingRequest, result: unknown) => {
    client.respond(request.id, result).then(() => dispatch({ type: "answered", id: request.id })).catch(notice);
  };

  const threadRequests = state.requests.filter((request) => request.threadId === null || request.threadId === selected?.thread.id);

  /** Enter or Esc on the first open approval of the shown thread. */
  const answerByKey = (key: "approve" | "decline") => {
    const request = threadRequests[0];
    if (!request || !composerHere) return false;
    const result = keyAnswer(request.method, key, request.params as Record<string, unknown>);
    if (!result) return false;
    answer(request, result);
    return true;
  };

  /** Esc Esc or /edit: the last message comes back into the composer, and its turn is undone. */
  /**
   * A new thread from an earlier point (DESKTOP_PARITY §4.5 item 22): through this turn (Fork from
   * here), or before it with its message in the new thread's composer (Edit in a new thread). The
   * thread shown is left as it is; an incognito one is never copied.
   */
  const forkFrom = async (turn: TurnView, where: "through" | "before") => {
    if (!selected || selected.thread.ephemeral) return;
    try {
      const words = turn.items.flatMap((item) => (item.type === "userMessage" ? item.content.flatMap((input) => (input.type === "text" ? [input.text] : [])) : [])).join("\n");
      const thread = await forkAt(client, selected.thread.id, turn.id, where, modelChoice ?? servedModel);
      dispatch({ type: "opened", thread });
      refreshQueue(thread.id);
      if (where === "before" && words.trim()) setDraftFor(draftKeyOf(view, thread.id), words);
    } catch (error) {
      notice(error);
    }
  };

  const editPrevious = async () => {
    if (!selected) return;
    try {
      const text = await takeBack(client, selected);
      if (text === null) return;
      await openThread(selected.thread.id);
      setDraft(text);
    } catch (error) {
      notice(error);
    }
  };

  /** `/review` (§2.5): the server reviews on the served model, inline in this thread or in a review thread of its own. */
  const startReview = async (target: ReturnType<typeof reviewTarget>) => {
    if (!selected) return notice("Open a thread to review its folder's changes.");
    try {
      const answer = (await client.request("review/start", { threadId: selected.thread.id, target, delivery: prefs.reviewDelivery })) as { reviewThreadId?: string };
      if (answer.reviewThreadId && answer.reviewThreadId !== selected.thread.id) await openThread(answer.reviewThreadId);
    } catch (error) {
      notice(error);
    }
  };

  const fork = async () => {
    if (!selected) return;
    if (selected.thread.ephemeral) return notice("An incognito question is not copied: a fork would be saved.");
    try {
      const thread = await forkThread(client, selected.thread.id, servedModel);
      dispatch({ type: "opened", thread });
      refreshQueue(thread.id);
    } catch (error) {
      notice(error);
    }
  };

  const pickModel = (model: string, chosenEffort: string | null) => {
    setModelChoice(model === servedModel ? null : model);
    setPicker("closed");
    updatePrefs({ effort: chosenEffort, recentModels: rememberModel(prefs.recentModels, model) });
  };

  /**
   * Shown at once, then saved; a refused write puts the stored values back. Reads the latest values
   * through prefsRef, so calls made after an await (a new worktree thread) do not undo each other.
   */
  const updatePrefs = (values: Partial<Prefs>) => {
    const before = prefsRef.current;
    const next = { ...before, ...values };
    prefsRef.current = next;
    setPrefs(next);
    savePrefs(before, values).catch((error) => {
      prefsRef.current = before;
      setPrefs(before);
      notice(error);
    });
  };  updatePrefsRef.current = updatePrefs;


  const runCommand = async (command: SlashCommand, args = ""): Promise<void> => {
    const threadId = selected?.thread.id ?? null;
    try {
      switch (command.name) {
        case "new":
          return view === "ask" ? newQuestion() : void dispatch({ type: "select", threadId: null });
        case "fork":
          return fork();
        case "compact":
          if (threadId) await client.request("thread/compact/start", { threadId });
          return;
        case "edit":
          return editPrevious();
        case "rename":
          if (threadId && args) return rename(threadId, args);
          if (threadId) setRenaming({ id: threadId, name: renameStart(state.threads[threadId]?.thread ?? { name: null, preview: "" }) });
          return;
        case "archive":
          if (threadId) return archive(threadId);
          return;
        case "model":
          return setPicker("open");
        case "goal":
          if (args) return setGoal({ objective: args, status: "active" });
          return setGoalEditing(true);
        case "plan":
          if (!planAvailable) return notice("The server offers no Plan mode.");
          if (args) {
            setPlanning((set) => new Set([...set, planKey]));
            setDraft(args);
            return;
          }
          return togglePlan();
        case "permissions":
          return setInfo({ title: "Permissions", body: <PermissionsInfo choices={choices} profile={profile} onChoose={(id) => { setProfile(id); setInfo(null); }} /> });
        case "status":
          return setInfo({ title: "Status", body: <StatusInfo model={modelChoice ?? servedModel} effort={effort} profile={choices.find((choice) => choice.id === profile)?.label ?? profile} cwd={selected?.thread.cwd ?? composeRoot} airgap={airgap} usage={contextUse(selected?.usage ?? null)} threadId={threadId} followUp={prefs.followUp} /> });
        case "skills":
          return setExtensions("skills");
        case "apps":
          return setExtensions("apps");
        case "mention":
          return setDraft((text) => `${text}${text && !/\s$/.test(text) ? " " : ""}@`);
        case "mcp":
          return setExtensions("mcp");
        case "diff": {
          if (!composeRoot) return;
          const diff = (await client.request("gitDiffToRemote", { cwd: composeRoot })) as { sha?: string; diff?: string };
          return setInfo({ title: `Against ${diff.sha?.slice(0, 10) ?? "the remote"}`, body: diff.diff ? <Diff text={diff.diff} /> : <p className="empty small">No differences.</p> });
        }
        case "copy": {
          const last = [...(selected?.turns ?? [])].reverse().flatMap((turn) => [...turn.items].reverse()).find((item) => item.type === "agentMessage");
          if (last && last.type === "agentMessage") await copyText(last.text);
          return;
        }
        case "resume":
          setDrawer(true);
          requestAnimationFrame(() => document.querySelector<HTMLInputElement>(".sidebar .search input")?.focus());
          return;
        case "memories": {
          if (!threadId) return;
          const mode = args === "on" ? "enabled" : args === "off" ? "disabled" : (memoryModes[threadId] ?? "enabled") === "enabled" ? "disabled" : "enabled";
          await client.request("thread/memoryMode/set", { threadId, mode });
          setMemoryModes((modes) => ({ ...modes, [threadId]: mode }));
          return setInfo({ title: "Memories", body: <p>Memories are {mode === "enabled" ? "on" : "off"} for this thread: it {mode === "enabled" ? "uses and adds to" : "neither uses nor adds to"} them.</p> });
        }
        case "agents":
          setAgentsHidden(false);
          if (selected && subagents(state, selected.thread.id).length === 0) setInfo({ title: "Subagents", body: <p className="empty small">This thread has started no subagents.</p> });
          return;
        case "review":
          return startReview(reviewTarget(args));
        case "night": {
          const words = args.match(/^add\s+([\s\S]+)$/);
          if (words) {
            const folder = workFolder ?? composeRoot;
            if (!folder) return notice("Open a thread in a project to queue a task for it.");
            // From a thread, the task carries what was said there (tonight.ts; the owner's decision 7).
            const task = selected ? withThreadContext(words[1], workLabel(selected.thread), threadContext(selected)) : words[1];
            const said = await bridge.night.add(folder, task, null);
            return setInfo({ title: "Night Shift", body: <pre className="info-pre">{said}</pre> });
          }
          return setScheduledOpen(true);
        }
        case "side":
          return commands.sideChat();
        case "pin":
          return commands.pinChat();
        case "pet":
          return commands.togglePet();
        case "project":
          return commands.projectPicker();
        case "chat":
          return commands.standaloneChat();
        case "worktree":
        case "local": {
          if (!selected) return notice(`Open a thread to hand it off to ${command.name === "worktree" ? "a worktree" : "the local checkout"}.`);
          const at = links[selected.thread.id]?.location ?? "local";
          if (command.name === "local" && at === "local") return notice("This thread already works in the local checkout.");
          if (command.name === "worktree" && at === "worktree") return notice("This thread already works in its worktree.");
          return handOff(command.name === "worktree" ? "toWorktree" : "toLocal");
        }
        case "reasoning": {
          const model = models.find((entry) => entry.id === (modelChoice ?? servedModel)) ?? models[0] ?? null;
          const { efforts, preferred } = effortChoices(model);
          const wanted = args.trim().toLowerCase();
          if (!wanted) return setPicker("open");
          if (!efforts.includes(wanted)) return notice(efforts.length ? `The model takes one of: ${efforts.join(", ")}.` : "The served model offers no choice of effort.");
          updatePrefs({ effort: wanted === preferred ? null : wanted });
          return setInfo({ title: "Reasoning", body: <p>The model thinks at <strong>{wanted}</strong> effort from the next message.</p> });
        }
        case "plugins":
          return setExtensions("plugins");
        case "hooks":
          return setExtensions("hooks");
        case "airgapped": {
          const words = args.split(/\s+/).filter(Boolean);
          if (words[0] === "default" && (words[1] === "on" || words[1] === "off")) {
            const lines = await bridge.airgappedDefault(words[1]);
            setAirgap(await bridge.airgapped(threadId));
            return setInfo({ title: "Airgapped", body: <pre className="info-pre">{lines}</pre> });
          }
          if (words.length) return setInfo({ title: "Airgapped", body: <p>In the app a thread's level follows the default; set it with <code>/airgapped default on</code> or <code>off</code>, or inside a <code>ling</code> session.</p> });
          const level = await bridge.airgapped(threadId);
          setAirgap(level);
          return setInfo({ title: "Airgapped", body: <p>Airgapped is <strong>{level.level}</strong> ({level.source}).</p> });
        }
        case "cavemode": {
          // CAVE_MODE §11.5: one thread's level (from its next model request), or the default for new threads.
          const words = args.split(/\s+/).filter(Boolean).map((word) => word.toLowerCase());
          const levels = ["off", "lite", "full", "ultra"];
          if (words[0] === "default" && words.length === 2 && (levels.includes(words[1]) || words[1] === "built-in")) {
            return setInfo({ title: "Cave mode", body: <pre className="info-pre">{await bridge.cavemode.set(words[1], null)}</pre> });
          }
          if (words.length === 1 && levels.includes(words[0])) {
            if (!threadId) return notice("Send a message first, or set the default with /cavemode default <level>.");
            return setInfo({ title: "Cave mode", body: <pre className="info-pre">{await bridge.cavemode.set(words[0], threadId)}</pre> });
          }
          if (words.length) return setInfo({ title: "Cave mode", body: <p>Usage: <code>/cavemode [off|lite|full|ultra]</code> or <code>/cavemode default &lt;level|built-in&gt;</code>.</p> });
          const level = await bridge.cavemode.status(threadId);
          return setInfo({ title: "Cave mode", body: (
            <>
              <p>Answers are <strong>{level.level}</strong> ({level.source}).</p>
              <ul>{level.levels.map((entry) => <li key={entry.name}><code>{entry.name}</code>: {entry.summary}</li>)}</ul>
            </>
          ) });
        }
      }
    } catch (error) {
      notice(error);
    }
  };

  // Shortcuts that work anywhere in the window: the model picker, recent models, settings, and Enter
  // and Esc on an open approval when no text field has focus.
  const keys = useRef<(event: KeyboardEvent) => void>(() => {});
  keys.current = (event: KeyboardEvent) => {
    // A field that already acted on the key (the composer's menus, its approval keys) has the last word.
    if (event.defaultPrevented) return;
    // Shell commands from the keymap; Ctrl+Z and Ctrl+Shift+Z belong to a text field when one has focus.
    const typing = (() => {
      const element = document.activeElement as HTMLElement | null;
      return element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || Boolean(element?.isContentEditable);
    })();
    const shellKeys: [ReturnType<typeof keyAction>, string][] = [
      [keyAction("commandMenu"), "commandMenu"],
      [keyAction("newWindow"), "newWindow"],
      [keyAction("shortcuts"), "shortcuts"],
      [keyAction("openFolder"), "openFolder"],
      [keyAction("back"), "back"],
      [keyAction("forward"), "forward"],
      [keyAction("fontBigger"), "fontBigger"],
      [keyAction("fontSmaller"), "fontSmaller"],
      [keyAction("fontReset"), "fontReset"],
      [keyAction("toggleSidebar"), "toggleSidebar"],
      [keyAction("undoAction"), "undoAction"],
      [keyAction("redoAction"), "redoAction"],
      [keyAction("closeTab"), "closeTab"],
      [keyAction("reopenTab"), "reopenTab"],
      [keyAction("closeOtherTabs"), "closeOtherTabs"],
      [keyAction("fullScreen"), "fullScreen"],
      [keyAction("quit"), "quit"],
      [keyAction("viewAsk"), "viewAsk"],
      [keyAction("viewWork"), "viewWork"],
      [keyAction("cycleLayout"), "cycleLayout"],
      [keyAction("fullView"), "fullView"],
      [keyAction("chatTabsToggle"), "chatTabsToggle"],
      [keyAction("newChat"), "newChat"],
      [keyAction("standaloneChat"), "standaloneChat"],
      [keyAction("quickChat"), "quickChat"],
      [keyAction("archiveChat"), "archiveChat"],
      [keyAction("markUnread"), "markUnread"],
      [keyAction("pinChat"), "pinChat"],
      [keyAction("renameChat"), "renameChat"],
      [keyAction("sideChat"), "sideChat"],
      [keyAction("findInChat"), "findInChat"],
      [keyAction("findNext"), "findNext"],
      [keyAction("findPrevious"), "findPrevious"],
      [keyAction("previousChat"), "previousChat"],
      [keyAction("nextChat"), "nextChat"],
      [keyAction("nextAttention"), "nextAttention"],
      [keyAction("clearUnread"), "clearUnread"],
      [keyAction("projectPicker"), "projectPicker"],
      [keyAction("copyDeepLink"), "copyDeepLink"],
      [keyAction("copyConversationPath"), "copyConversationPath"],
      [keyAction("copyWorkingDirectory"), "copyWorkingDirectory"],
      [keyAction("goToChat1"), "goToChat1"],
      [keyAction("goToChat2"), "goToChat2"],
      [keyAction("goToChat3"), "goToChat3"],
      [keyAction("goToChat4"), "goToChat4"],
      [keyAction("goToChat5"), "goToChat5"],
      [keyAction("goToChat6"), "goToChat6"],
      [keyAction("goToChat7"), "goToChat7"],
      [keyAction("goToChat8"), "goToChat8"],
      [keyAction("goToChat9"), "goToChat9"],
      [keyAction("recentChat1"), "recentChat1"],
      [keyAction("recentChat2"), "recentChat2"],
      [keyAction("recentChat3"), "recentChat3"],
      [keyAction("recentChat4"), "recentChat4"],
      [keyAction("recentChat5"), "recentChat5"],
      [keyAction("recentChat6"), "recentChat6"],
      [keyAction("fileTree"), "fileTree"],
      [keyAction("searchFiles"), "searchFiles"],
      [keyAction("previewOpen"), "previewOpen"],
      [keyAction("activityView"), "activityView"],
      [keyAction("togglePet"), "togglePet"],
    ];
    // With a preview tab in front, Ctrl+Shift+C copies the page's address (the preview's own key, PreviewTab.tsx).
    const previewInFront = tabsShown && paneActive?.kind === "preview";
    for (const [action, id] of shellKeys) {
      if ((id === "undoAction" || id === "redoAction") && typing) continue;
      if (id === "copyWorkingDirectory" && previewInFront) continue;
      if (matches(event, action)) {
        event.preventDefault();
        commands[id]();
        return;
      }
    }
    // Go to line (Ctrl+L) when a file tab is in front; the terminal's Ctrl+L (clear) is the terminal's own.
    const frontTab = paneActive;
    if (matches(event, keyAction("goToLine")) && tabsShown && frontTab?.kind === "file" && !(document.activeElement as HTMLElement | null)?.closest(".terminal-host, .xterm")) {
      event.preventDefault();
      setFileSearch(":");
      return;
    }
    if (matches(event, keyAction("modelPicker"))) {
      event.preventDefault();
      setPicker((open) => (open === "closed" ? "open" : "closed"));
    } else if (matches(event, keyAction("recentModels"))) {
      event.preventDefault();
      setPicker("recent");
    } else if (matches(event, keyAction("terminal")) && view === "work") {
      event.preventDefault();
      const mine = terminals.filter((entry) => entry.threadId === (selected?.thread.id ?? null) && entry.exitCode === null);
      if (mine.length === 0) openTerminal();
      else if (!prefsRef.current.bottomPanel) {
        updatePrefs({ bottomPanel: true });
        setFront({ id: mine[mine.length - 1].processId, tick: Date.now() });
      } else updatePrefs({ bottomPanel: false });
    } else if (matches(event, keyAction("bottomPanel"))) {
      event.preventDefault();
      updatePrefs({ bottomPanel: !prefsRef.current.bottomPanel });
    } else if (matches(event, keyAction("environmentAction1"))) {
      event.preventDefault();
      if (environment?.actions[0]) runAction(environment.actions[0]);
    } else if (matches(event, keyAction("reviewTab"))) {
      event.preventDefault();
      setReviewOpen((open) => !open);
    } else if (matches(event, keyAction("settings"))) {
      event.preventDefault();
      setSettingsPage("general");
      setSettingsOpen((open) => !open);
    } else if (event.key === "Escape" && (picker !== "closed" || settingsOpen || info || menuOpen || findOpen || rowMenu || extensions || scheduledOpen || fileSearch !== null || activityOpen || boardOpen)) {
      setFileSearch(null);
      setBoardOpen(false);
      setActivityOpen(false);
      setScheduledOpen(false);
      setExtensions(null);
      setPicker("closed");
      setSettingsOpen(false);
      setMenuOpen(false);
      setFindOpen(false);
      setRowMenu(null);
      setInfo(null);
    } else if (!editableFocus() && threadRequests.length > 0) {
      if (matches(event, keyAction("approve")) && answerByKey("approve")) event.preventDefault();
      else if (matches(event, keyAction("decline")) && answerByKey("decline")) event.preventDefault();
    }
  };
  useEffect(() => {
    const listener = (event: KeyboardEvent) => keys.current(event);
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);
  // A citation of the user's documents (markdown.ts) opens the document, wherever it is rendered.
  useEffect(() => {
    const open = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== "Enter") return;
      const link = (event.target as Element | null)?.closest?.(".doc-link");
      const doc = link?.getAttribute("data-doc");
      if (!doc) return;
      event.preventDefault();
      const page = link?.getAttribute("data-page");
      bridge.openDocument(doc, page ? Number(page) : null).catch((error: unknown) => notice(`The document could not be opened: ${error instanceof Error ? error.message : String(error)}`));
    };
    document.addEventListener("click", open);
    document.addEventListener("keydown", open);
    return () => {
      document.removeEventListener("click", open);
      document.removeEventListener("keydown", open);
    };
  }, [notice]);
  // A web address in an answer (markdown.ts `webLink`): the dialog shows it in full before anything opens.
  useEffect(() => {
    const ask = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== "Enter") return;
      const url = (event.target as Element | null)?.closest?.(".web-link")?.getAttribute("data-url");
      if (!url) return;
      event.preventDefault();
      // The level as it is now, for the thread in front (the TUI's /airgapped may have changed it).
      const thread = selectedRef.current;
      bridge.airgapped(thread)
        .then((level) => setLinkAsked({ url, thread, airgappedOn: level.level === "on" }))
        .catch(() => setLinkAsked({ url, thread, airgappedOn: true }));
    };
    document.addEventListener("click", ask);
    document.addEventListener("keydown", ask);
    return () => {
      document.removeEventListener("click", ask);
      document.removeEventListener("keydown", ask);
    };
  }, []);

  if (state.server !== "ready") {
    return <StartupScreen state={state.server} stderr={state.stderr} protocolErrors={state.protocolErrors} notices={state.notices} onRetry={connect} />;
  }

  const usage = contextUse(selected?.usage ?? null);
  const otherRequests = state.requests.length - threadRequests.length;
  const allThreads = Object.values(state.threads).map((entry) => entry.thread);
  // Projects (projects.ts): the shown ones, and every thread that belongs to a project the server knows
  // (shown or removed), which leaves the folder groups and the plain question list.
  // Threads waiting for the user or unread, counted on the sidebar's Activity row (item 17).
  const attentionCount = new Set([...state.requests.map((request) => request.threadId).filter((id): id is string => Boolean(id)), ...prefs.unreadThreads.filter((id) => state.threads[id] && !state.threads[id].thread.ephemeral)]).size;
  const listedProjects = ordered(shownProjects(serverProjects), (project) => `project:${project.id}`, prefs.projectOrder);
  const folderGroups = ordered(projects(state, askRoot, (thread) => projectFolder(thread, links)), (group) => `folder:${group.cwd}`, prefs.projectOrder);
  /** Dragging a project or folder header onto another puts it just before that one, kept in the window's preferences. */
  const dragHeader = (key: string) => ({
    draggable: true,
    onDragStart: (event: DragEvent) => {
      draggedHeader.current = key;
      event.dataTransfer.effectAllowed = "move";
    },
    onDragOver: (event: DragEvent) => {
      if (draggedHeader.current && draggedHeader.current !== key) event.preventDefault();
    },
    onDrop: (event: DragEvent) => {
      event.preventDefault();
      const moved = draggedHeader.current;
      draggedHeader.current = null;
      if (!moved || moved === key) return;
      const shownKeys = [...listedProjects.map((project) => `project:${project.id}`), ...folderGroups.map((group) => `folder:${group.cwd}`)];
      updatePrefs({ projectOrder: movedBefore(shownKeys, prefsRef.current.projectOrder, moved, key) });
    },
  });
  const knownProjects = new Set(serverProjects.map((project) => project.id));
  const inProject = (thread: Thread) => Boolean(thread.projectId && knownProjects.has(thread.projectId));
  const asks = found ? found.map((result) => result.thread) : askThreads(allThreads, askRoot).filter((thread) => !inProject(thread));
  const askOfProject = (projectId: string) => askThreads(allThreads, askRoot).filter((thread) => thread.projectId === projectId);
  const snippets = new Map((found ?? []).map((result) => [result.thread.id, result.snippet]));
  const queue: QueuedSubmission[] = selected ? state.queues[selected.thread.id] ?? [] : [];
  const attachments: ComposerAttachment[] = pending.map((item) => ({ id: item.id, name: item.text !== undefined ? pasteLabel(item.text) : item.name, preview: item.preview, title: item.text?.slice(0, 600) }));

  const unread = new Set(prefs.unreadThreads);
  const waiting = new Set(state.requests.map((request) => request.threadId).filter((id): id is string => Boolean(id)));
  const workThreads = Object.values(state.threads).map((entry) => entry.thread).filter((thread) => !thread.ephemeral && !thread.parentThreadId && !isAskThread(thread, askRoot));
  const pinnedHere = prefs.pinnedThreads.map((id) => state.threads[id]?.thread).filter((thread): thread is Thread => Boolean(thread) && !thread!.ephemeral && isAskThread(thread!, askRoot) === (view === "ask"));
  const sectioned = new Set(prefs.sections.flatMap((section) => section.threads));
  const workLabel = (thread: Thread) => thread.name || thread.preview || "(new thread)";

  /** A project in the sidebar: its name (a click makes it where the next thread or question starts), its menu, its threads. */
  const projectSection = (project: Project, threads: Thread[], where: "ask" | "work") => (
    <section key={`project-${project.id}`} className={`project server-project${newProject === project.id ? " starting" : ""}`}>
      <h2 title={[project.name, ...project.roots.map((root) => root.path)].join("\n")} {...dragHeader(`project:${project.id}`)} onClick={() => {
        setNewProject(project.id);
        const primary = primaryFolder(project);
        if (where === "work" && primary) setNewCwd(primary);
        dispatch({ type: "select", threadId: null });
      }}>
        <Icon name="folderTree" size={14} />{project.name}
        <span className="menu-anchor">
          <button className="icon project-action" title="Project" aria-label={`Project ${project.name}`} aria-haspopup="menu"
            onClick={(event) => { event.stopPropagation(); setProjectMenu(projectMenu === project.id ? null : project.id); }}><Icon name="more" size={12} /></button>
          {projectMenu === project.id ? (
            <span className="menu" role="menu" aria-label={`Project ${project.name}`} onClick={(event) => { event.stopPropagation(); setProjectMenu(null); }}>
              <button role="menuitem" onClick={() => { setNewProject(project.id); const primary = primaryFolder(project); if (where === "work" && primary) setNewCwd(primary); dispatch({ type: "select", threadId: null }); }}>{where === "ask" ? "New question in this project" : "New thread in this project"}</button>
              <button role="menuitem" onClick={() => setProjectDialog({ project, folder: null })}>Edit project…</button>
              <button role="menuitem" onClick={() => void archiveProjectChats(project)}>Archive chats</button>
              <button role="menuitem" onClick={() => { if (newProject === project.id) setNewProject(null); void setProjectMetadata(project, { [REMOVED_KEY]: "1" }, `Remove ${project.name}`); }}>Remove from the sidebar</button>
            </span>
          ) : null}
        </span>
      </h2>
      {threads.map((thread) => threadRow(thread, where === "ask" ? askTitle(thread) : workLabel(thread)))}
    </section>
  );

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
      <div key={thread.id} className={`thread-row${thread.id === state.selected ? " selected" : ""}${unread.has(thread.id) ? " unread" : ""}`}>
        <button className="thread" onClick={() => openThread(thread.id)} title={label}>
          <span className="thread-label">{prefs.pinnedThreads.includes(thread.id) ? <Icon name="pin" size={12} /> : null}{label}</span>
          {snippets.get(thread.id) ? <span className="snippet">{snippets.get(thread.id)}</span> : null}
        </button>
        {waiting.has(thread.id) ? <span className="attention" aria-label="waiting for you" title="An approval or a question is waiting">!</span> : null}
        {unread.has(thread.id) && !waiting.has(thread.id) ? <span className="unread-dot" aria-label="unread" /> : null}
        {state.threads[thread.id]?.activeTurnId ? <span className="running" aria-label="running" /> : null}
        <span className="age" title={new Date(thread.updatedAt * 1000).toLocaleString()}>{age(thread.updatedAt)}</span>
        <span className="row-actions">
          <button className="icon" title={prefs.pinnedThreads.includes(thread.id) ? "Unpin" : "Pin"} aria-label={prefs.pinnedThreads.includes(thread.id) ? "Unpin" : "Pin"} onClick={() => pin(thread.id)}><Icon name="pin" size={14} /></button>
          <button className="icon" title="Archive" aria-label="Archive" onClick={() => void archive(thread.id)}><Icon name="archive" size={14} /></button>
          <span className="menu-anchor">
            <button className="icon" title="More" aria-label="More" aria-haspopup="menu" onClick={() => setRowMenu(rowMenu === thread.id ? null : thread.id)}><Icon name="more" size={14} /></button>
            {rowMenu === thread.id ? (
              <span className="menu" role="menu" aria-label="Thread" onClick={() => setRowMenu(null)}>
                <button role="menuitem" onClick={() => setRenaming({ id: thread.id, name: renameStart(thread) })}>Rename</button>
                <button role="menuitem" onClick={() => markUnread(thread.id)}>Mark unread</button>
                {HOST === "electron" ? <button role="menuitem" onClick={() => void bridge.popOut(thread.id).catch(notice)}>Open in a window of its own</button> : null}
                <button role="menuitem" onClick={() => copyLink(thread.id)}>Copy link</button>
                <button role="menuitem" onClick={() => void bridge.homeFolder().catch(() => null).then((home) => setExporting({ thread, title: label, home }))}>Export…</button>
                {view === "work" ? (
                  <>
                    {prefs.sections.map((section) => <button key={section.id} role="menuitem" onClick={() => toSection(thread.id, section.id)}>Move to {section.name}</button>)}
                    <button role="menuitem" onClick={() => toSection(thread.id, "new")}>Move to a new section…</button>
                    {prefs.sections.some((section) => section.threads.includes(thread.id)) ? <button role="menuitem" onClick={() => toSection(thread.id, null)}>Out of its section</button> : null}
                  </>
                ) : null}
                {listedProjects.filter((project) => project.id !== thread.projectId).map((project) => (
                  <button key={project.id} role="menuitem" onClick={() => void moveToProject(thread.id, project.id)}>Move to project {project.name}</button>
                ))}
                {thread.projectId && knownProjects.has(thread.projectId) ? <button role="menuitem" onClick={() => void moveToProject(thread.id, null)}>Out of its project</button> : null}
              </span>
            ) : null}
          </span>
        </span>
      </div>
    )
  );

  /** A dialog over the whole window: a preview's view (drawn above the page) hides under it. */
  const overlayOpen = settingsOpen || menuOpen !== false || fileSearch !== null || scheduledOpen || extensions !== null || editingEnv || activityOpen || boardOpen || linkAsked !== null;
  const title = incognitoShown ? "Incognito question" : selected ? selected.thread.name || selected.thread.preview || (view === "ask" ? "New question" : "New thread") : view === "ask" ? (incognitoNext ? "New incognito question" : "New question") : "Mightling";
  const canCompose = view === "ask" || selected !== null || newCwd.trim() !== "";
  const placeholder = selected?.activeTurnId
    ? prefs.followUp === "queue" ? "Queue a follow-up for the next turn…" : "Steer the running turn…"
    : view === "ask" ? "Ask anything… ($ for a skill, / for commands)" : "Ask Mightling… (@ for a file, $ for a skill, / for commands)";

  if (PANE) {
    // A detached pane's window (§13): the thread's terminal tabs or its diff alone, live, docked back
    // when it closes. What needs the thread's composer or permissions is asked of the window it came from.
    const paneThread = state.threads[PANE.thread]?.thread ?? null;
    const dockPane = () => void bridge.windows.dock(PANE.kind, PANE.thread).catch(notice);
    return (
      <div className={`work view-work pane-window pane-${PANE.kind}`}>
        <main className="thread-pane">
          <header className="bar" onDoubleClick={() => bridge.windowControl("maximize").catch(() => {})}>
            <span className="title">{PANE.kind === "terminal" ? "Terminal" : "Diff"} · {paneThread ? workLabel(paneThread) : "Thread"}</span>
            <span className="spacer" />
            <button className="icon bar-action" aria-label="Dock" title="Back into the thread's window" onClick={dockPane}><Icon name="dock" size={16} /></button>
          </header>
          {state.notices.map((message, index) => (
            <div key={index} className="notice" onClick={() => dispatch({ type: "dismissNotice", index })}>{message}</div>
          ))}
          {PANE.kind === "terminal" ? (
            <BottomPanel tasks={[]} environment={null} problems={[]} project={null}
              terminals={terminals.filter((entry) => entry.threadId === PANE.thread)} hub={hub} front={front}
              onNewTerminal={() => void bridge.windows.relay({ kind: "newTerminal" }).catch(notice)} onCloseTerminal={closeTerminal}
              onSendToThread={(processId) => {
                const session = terminals.find((entry) => entry.processId === processId);
                void hub.tailFrom(processId).then((output) => {
                  if (!session || !output) return notice("The terminal has shown nothing yet.");
                  return bridge.windows.relay({ kind: "compose", text: terminalMessage(session.cwd, output) });
                }).catch(notice);
              }}
              onRun={() => {}} onStop={() => {}} onClose={() => {}} onEdit={() => {}} onHide={dockPane}
              detach={{ docks: true, run: dockPane }} />
          ) : (
            <div className="review-pane">
              <ReviewPane
                cwd={composeRoot} threadId={PANE.thread}
                turnDiff={lastTurnDiff(selected)}
                airgapOn={airgap?.level === "on"}
                layout={prefs.diffLayout} setLayout={(diffLayout) => updatePrefs({ diffLayout })}
                editor={prefs.editor} setEditor={(editor) => updatePrefs({ editor })}
                refreshKey={turnsDone(selected)}
                onComments={(comments) => void bridge.windows.relay({ kind: "compose", text: comments }).catch(notice)}
                onRevertTurn={null}
                generateMessage={(root, diff) => commitMessage(client, listen, root, modelChoice ?? servedModel, diff, prefs.commitPrompt ?? undefined)}
                forcePushDefault={prefs.forcePush}
                notice={notice}
                onOpenFile={(path, line) => void bridge.windows.relay({ kind: "openFile", path, line }).catch(notice)}
                detach={{ docks: true, run: dockPane }}
              />
            </div>
          )}
        </main>
      </div>
    );
  }

  return (
    <div className={`work view-${view}${POPOUT ? " popout sidebar-hidden" : ""}${drawer ? " drawer-open" : ""}${tabsShown ? (prefs.layout === "tabs" ? " review-open tabs-full" : " review-open") : ""}${prefs.sidebarHidden ? " sidebar-hidden" : ""}`} onContextMenu={(event) => {
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
            <button className="nav-row new-question" onClick={newQuestion}><Icon name="squarePen" />New question</button>
            <label className="search">
              <Icon name="search" size={14} />
              <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search questions" aria-label="Search questions" />
            </label>
            {!found && pinnedHere.length ? (
              <>
                <h2 className="section-label">Pinned</h2>
                {pinnedHere.map((thread) => threadRow(thread, askTitle(thread)))}
              </>
            ) : null}
            <h2 className="section-label">Questions</h2>
            {asks.length === 0 ? <p className="empty small">{found ? "Nothing matches." : "No questions yet."}</p> : null}
            {asks.filter((thread) => found || !prefs.pinnedThreads.includes(thread.id)).map((thread) => threadRow(thread, askTitle(thread)))}
            {found ? null : listedProjects.filter((project) => project.roots.length === 0 || askOfProject(project.id).length > 0).map((project) => projectSection(project, askOfProject(project.id).filter((thread) => !prefs.pinnedThreads.includes(thread.id)), "ask"))}
            {found ? null : <button className="nav-row new-project-button" onClick={() => setProjectDialog({ project: null, folder: null })}><Icon name="folderTree" />New project</button>}
          </>
        ) : (
          <>
            <button className="nav-row new-thread-button" onClick={newThread}><Icon name="squarePen" />New thread</button>
            <button className="nav-row new-project-button" onClick={() => setProjectDialog({ project: null, folder: newCwd.trim() || null })}><Icon name="folderTree" />New project</button>
            <label className="new-thread" title="The project folder a new thread starts in">
              <Icon name="folder" size={14} />
              <input ref={folderField} value={startingProject && view === "work" ? `${startingProject.name} (project)` : newCwd} onChange={(event) => { setNewProject(null); setNewCwd(event.target.value); }} placeholder="/path/to/project" aria-label="Project folder" />
            </label>
            <label className="search">
              <Icon name="search" size={14} />
              <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search threads" aria-label="Search threads" />
            </label>
            {found ? (
              <section className="project">
                <h2>Found</h2>
                {found.map((result) => threadRow(result.thread, result.thread.name || result.thread.preview || "(new thread)"))}
              </section>
            ) : (
              <>
                {pinnedHere.length ? (
                  <section className="project pinned">
                    <h2>Pinned</h2>
                    {pinnedHere.map((thread) => threadRow(thread, workLabel(thread)))}
                  </section>
                ) : null}
                <section className="project recents">
                  <h2 onClick={() => updatePrefs({ recentsOpen: !prefs.recentsOpen })} title="The most recent threads of every project"
                    role="button" tabIndex={0} aria-expanded={prefs.recentsOpen}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      updatePrefs({ recentsOpen: !prefs.recentsOpen });
                    }}>
                    <Icon name={prefs.recentsOpen ? "chevronDown" : "chevronRight"} size={12} />Recent
                  </h2>
                  {prefs.recentsOpen ? recentThreads(workThreads, 5).map((thread) => threadRow(thread, workLabel(thread))) : null}
                </section>
                {naming ? (
                  <input className="rename section-name" autoFocus value={naming.name} placeholder="The new section's name" aria-label="Section name"
                    onChange={(event) => setNaming({ ...naming, name: event.target.value })}
                    onBlur={() => setNaming(null)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && naming.name.trim()) {
                        toSection(naming.thread, "new", naming.name.trim());
                        setNaming(null);
                      }
                      if (event.key === "Escape") setNaming(null);
                    }} />
                ) : null}
                {prefs.sections.map((section) => (
                  <section key={section.id} className="project user-section">
                    <h2>{section.name}</h2>
                    {section.threads.map((id) => state.threads[id]?.thread).filter((thread): thread is Thread => Boolean(thread) && !prefs.pinnedThreads.includes(thread!.id)).map((thread) => threadRow(thread, workLabel(thread)))}
                  </section>
                ))}
              </>
            )}
            {found ? null : listedProjects.map((project) => projectSection(project, workThreads.filter((thread) => thread.projectId === project.id && !prefs.pinnedThreads.includes(thread.id) && !sectioned.has(thread.id)), "work"))}
            {found ? null : pinnedFirst(folderGroups.map((group) => ({ ...group, threads: group.threads.filter((thread) => !inProject(thread)) })), prefs.pinnedProjects, (project) => project.cwd)
              .filter((project) => project.threads.some((thread) => !prefs.pinnedThreads.includes(thread.id) && !sectioned.has(thread.id)) || prefs.pinnedProjects.includes(project.cwd)).map((project) => (
              <section key={project.cwd} className="project">
                <h2 title={project.cwd} {...dragHeader(`folder:${project.cwd}`)} onClick={() => { setNewProject(null); setNewCwd(project.cwd); }}>
                  <Icon name="folder" size={14} />{project.cwd.split(/[\\/]/).filter(Boolean).pop() ?? project.cwd}
                  <button className={`icon project-action${prefs.pinnedProjects.includes(project.cwd) ? " on" : ""}`} title={prefs.pinnedProjects.includes(project.cwd) ? "Unpin the project" : "Pin the project"} aria-label={prefs.pinnedProjects.includes(project.cwd) ? "Unpin the project" : "Pin the project"}
                    onClick={(event) => { event.stopPropagation(); pinProject(project.cwd); }}><Icon name="pin" size={12} /></button>
                  {bridge.git.available() ? <button className="icon project-action" title="New permanent worktree: a project of its own, never cleaned up" aria-label="New permanent worktree"
                    onClick={(event) => { event.stopPropagation(); void permanentWorktree(project.cwd); }}><Icon name="gitFork" size={12} /></button> : null}
                  <button className="icon project-action" title="Make a project of this folder: more folders, instructions, knowledge files" aria-label="Make a project of this folder"
                    onClick={(event) => { event.stopPropagation(); setProjectDialog({ project: null, folder: project.cwd }); }}><Icon name="plus" size={12} /></button>
                </h2>
                {project.threads.filter((thread) => !prefs.pinnedThreads.includes(thread.id) && !sectioned.has(thread.id)).map((thread) => threadRow(thread, workLabel(thread)))}
              </section>
            ))}
          </>
        )}
        {view === "work" && prefs.permanentWorktrees.length > 0 && !found ? (
          <section className="project permanent">
            <h2>Permanent worktrees</h2>
            {prefs.permanentWorktrees.map((checkout) => (
              <button key={checkout} className="thread" title={checkout} onClick={() => setNewCwd(checkout)}>
                <span className="thread-label"><Icon name="gitFork" size={12} /> {checkout.split(/[\\/]/).slice(-2).join("/")}</span>
              </button>
            ))}
          </section>
        ) : null}
        <span className="sidebar-spacer" />
        {!POPOUT && HOST === "electron" ? (
          <button className="nav-row scheduled-button" onClick={() => setScheduledOpen(true)} title="Night Shift's queue, inbox and report">
            <Icon name="clock" size={14} />Scheduled{nightUnseen ? <span className="count" aria-label={`${nightUnseen} new`}>{nightUnseen}</span> : null}
          </button>
        ) : null}
        {!POPOUT && board.on && HOST === "electron" ? <button className="nav-row board-button" onClick={() => setBoardOpen(true)} title="What subagents posted for each other"><Icon name="messageSquare" size={14} />Message board</button> : null}
        {!POPOUT && synced.status ? <button className="nav-row synced-button" onClick={() => setSyncedOpen(true)} title="Threads your other devices synced to this node"><Icon name="monitor" size={14} />Other devices{synced.count ? <span className="count" aria-label={`${synced.count} threads`}>{synced.count}</span> : null}</button> : null}
        {!POPOUT ? <button className="nav-row activity-button" onClick={() => setActivityOpen(true)} title={`What needs you, and what the agent did, across threads (${shortcutLabel("activityView")})`}><Icon name="activity" size={14} />Activity{attentionCount ? <span className="count" aria-label={`${attentionCount} need you`}>{attentionCount}</span> : null}</button> : null}
        {!POPOUT ? <button className="nav-row skills-button" onClick={() => setExtensions("skills")} title="Skills, MCP servers and apps"><Icon name="info" size={14} />Skills and tools</button> : null}
        <button className="nav-row settings-button" onClick={() => setSettingsOpen(true)} title={`Settings (${shortcutLabel("settings")})`}><Icon name="settings" size={14} />Settings</button>
        {HOST === "web" ? <a className="devices-link" href="/devices">Pair a phone or another device</a> : null}
      </aside>
      <div className="backdrop" onClick={() => setDrawer(false)} />

      <main className={`thread-pane${incognitoShown || (view === "ask" && !selected && incognitoNext) ? " incognito" : ""}`}>
        <header className="bar" onDoubleClick={() => HOST === "electron" && bridge.windowControl("maximize").catch(() => {})}>
          <button className="icon menu-toggle" aria-label="Threads" onClick={() => setDrawer(!drawer)}><Icon name="panelLeft" size={20} strokeWidth={1.75} /></button>
          <button className="icon sidebar-toggle" aria-label={prefs.sidebarHidden ? "Show the sidebar" : "Hide the sidebar"} title={`${prefs.sidebarHidden ? "Show" : "Hide"} the sidebar (${shortcutLabel("toggleSidebar")})`} onClick={commands.toggleSidebar}><Icon name="panelLeft" size={18} strokeWidth={1.75} /></button>
          <button className="icon nav-button" aria-label="Back" title={`Back (${shortcutLabel("back")})`} disabled={!places.canGoBack} onClick={commands.back}><Icon name="chevronLeft" size={16} /></button>
          <button className="icon nav-button" aria-label="Forward" title={`Forward (${shortcutLabel("forward")})`} disabled={!places.canGoForward} onClick={commands.forward}><Icon name="chevronRight" size={16} /></button>
          <span className="title">{title}</span>
          {selected && view === "work" ? <span className="cwd">{selected.thread.cwd}</span> : null}
          <span className="spacer" />
          {view === "ask" && !selected && bridge.incognito.available() ? (
            <button className={`chip incognito-toggle${incognitoNext ? " active" : ""}`} aria-pressed={incognitoNext} onClick={() => setIncognitoNext((on) => !on)}
              title="An incognito question is not saved: not in your history, your memories or a later search. When you leave it, it is deleted for good, with every file in its folder.">
              <Icon name="eyeOff" size={14} />Incognito
            </button>
          ) : null}
          {selected && !incognitoShown ? <button className="icon bar-action" title="Fork: continue in a copy of this thread" aria-label="Fork" onClick={() => void fork()}><Icon name="gitFork" size={16} /></button> : null}
          {selected && !incognitoShown && HOST === "electron" && !POPOUT ? <button className="icon bar-action" title="Open in a window of its own" aria-label="Pop out" onClick={() => void bridge.popOut(selected.thread.id).catch(notice)}><Icon name="externalLink" size={16} /></button> : null}
          {POPOUT ? <button className={`icon bar-action${onTop ? " active" : ""}`} aria-pressed={onTop} title="Keep this window above the others" aria-label="Always on top" onClick={() => void bridge.alwaysOnTop(!onTop).then(setOnTop).catch(notice)}><Icon name="pin" size={16} /></button> : null}
          {askFolder ? (
            // What the question made, beside the answer (DESKTOP_PARITY_CLAUDE §4.1): its folder's files.
            <button className={`icon bar-action ask-files${paneTree && tabsShown ? " active" : ""}`} aria-pressed={paneTree && tabsShown} aria-label="Files"
              title={`This question's files (${shortcutLabel("fileTree")})`} onClick={commands.fileTree}><Icon name="folderTree" size={16} /></button>
          ) : null}
          {askFolder && HOST === "electron" ? (
            <button className="icon bar-action ask-folder" aria-label="Open the folder" title="Open this question's folder in the file manager"
              onClick={() => void bridge.openFolder(askFolder).catch(notice)}><Icon name="folder" size={16} /></button>
          ) : null}
          {selected && view === "work" && bridge.git.available() ? (
            <WorktreeHeader link={selectedLink} branch={headerGit?.branch ?? null} inGit={Boolean(headerGit?.root)}
              canHandOff={!selected.activeTurnId} environment={environment}
              onCreateBranch={createBranch} branchPrefix={prefs.branchPrefix} onHandOff={(direction) => void handOff(direction)} onOpen={openFolder} onAction={runAction} />
          ) : null}
          {view === "work" && prefs.pullRequests && bridge.prs.available() && composeRoot ? <button className={`icon bar-action${tabs.some((tab) => tab.kind === "prs") ? " active" : ""}`} title="Pull requests, through your GitHub CLI" aria-label="Pull requests" onClick={openPullRequests}><Icon name="gitPullRequest" size={16} /></button> : null}
          {view === "work" ? <button className={`icon bar-action${reviewOpen ? " active" : ""}`} title={`Review: the repository's changes (${shortcutLabel("reviewTab")})`} aria-label="Review" aria-pressed={reviewOpen} onClick={() => { if (reviewOpen && prefs.layout === "chat") updatePrefs({ layout: "split" }); else setReviewOpen(!reviewOpen); }}><Icon name="fileDiff" size={16} /></button> : null}
          {workingCount(state) > 1 ? <span className="chip optional" title="Threads and subagents working at once share the one model server">{workingCount(state)} working</span> : null}
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

        {missingWorktree && missingWorktree === selected?.thread.id ? (
          <div className="notice restore-notice">
            This thread's worktree was removed{selectedLink?.snapshot ? "; a snapshot of its work was kept" : ""}.
            <button className="link-button" onClick={() => void restoreWorktree()}>Restore</button>
          </div>
        ) : null}
        {findOpen ? (
          <div className="find-bar" role="search">
            <Icon name="search" size={14} />
            <input autoFocus value={findQuery} placeholder="Find in this thread" aria-label="Find in this thread" onChange={(event) => setFindQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") { event.preventDefault(); (event.shiftKey ? commands.findPrevious : commands.findNext)(); }
                if (event.key === "Escape") { event.preventDefault(); setFindOpen(false); }
              }} />
            <span className="find-count">{findQuery ? (findCount ? `${Math.min(findAt, findCount - 1) + 1} of ${findCount}` : "none") : ""}</span>
            <button className="icon" aria-label="Previous match" onClick={commands.findPrevious}><Icon name="chevronLeft" size={14} /></button>
            <button className="icon" aria-label="Next match" onClick={commands.findNext}><Icon name="chevronRight" size={14} /></button>
            <button className="icon" aria-label="Close find" onClick={() => setFindOpen(false)}><Icon name="x" size={14} /></button>
          </div>
        ) : null}
        <OpenFileContext.Provider value={view === "work" ? { open: openFile, cwd: selected?.thread.cwd ?? null, ask: false } : askFolder ? { open: openFile, cwd: askFolder, ask: true } : null}>
        <div className="turns" ref={turnsRef} onScroll={onTurnsScroll}>
          {incognitoShown || (view === "ask" && !selected && incognitoNext) ? (
            <div className="incognito-banner" role="note">
              <Icon name="eyeOff" size={16} />
              <span><strong>Incognito.</strong> This question is not saved: it stays out of your history, your memories and later searches. When you leave it (another question, a new one, Work, or closing Mightling), the question, its answers and its folder with every file in it are <strong>deleted for good</strong>; nothing can bring them back.</span>
            </div>
          ) : null}
          {info ? (
            <section className="info-panel" aria-label={info.title}>
              <header><strong>{info.title}</strong><button className="icon" aria-label="Close" onClick={() => setInfo(null)}><Icon name="x" size={14} /></button></header>
              {info.body}
            </section>
          ) : null}
          {selected ? selected.turns.map((turn, index) => (
            <Turn key={turn.id} turn={turn} last={index === selected.turns.length - 1} commandOutput={prefs.commandOutput}
              onFork={selected.thread.ephemeral ? null : (where) => void forkFrom(turn, where)}
              onRevert={() => void revertTurn(turn).catch(notice)}
              onEdit={!selected.activeTurnId && index === selected.turns.length - 1 ? () => void editPrevious() : null} />
          )) : view === "ask" ? (
            startingProject ? (
              <ProjectPage key={startingProject.id} project={startingProject} title={askTitle} notice={notice}
                questions={askThreads(Object.values(state.threads).map((entry) => entry.thread), askRoot).filter((thread) => thread.projectId === startingProject.id)}
                onOpen={(id) => void openThread(id)} onEdit={() => setProjectDialog({ project: startingProject, folder: null })} onLeave={() => setNewProject(null)} />
            ) : <div className="empty ask-empty">
              <MightlingMark size={56} />
              <h1>Ask Mightling</h1>
              <p>A question with no project: Mightling answers in a folder of its own, searching the web when it needs to{airgap?.level === "on" ? ", except that airgapped is on, so web search is off" : ""}. Attach images or files with the paperclip, or paste a screenshot.</p>
            </div>
          ) : <p className="empty">{startingProject ? `A new thread in the project ${startingProject.name}${primaryFolder(startingProject) ? ` (${primaryFolder(startingProject)})` : ""}: write the first message below.` : newCwd.trim() ? `A new thread in ${newCwd.trim()}: write the first message below.` : "Open a thread, or name a project folder for a new one."}</p>}
          {threadRequests.map((request) => (
            // Mirrored read-only while another window holds the thread's composer: that window answers.
            <div key={JSON.stringify(request.id)} className={composerHere ? "approval-host" : "approval-host mirrored"} inert={!composerHere}>
              <Approval request={request} airgappedOn={airgap?.level === "on"} onAnswer={(result) => answer(request, result)} />
            </div>
          ))}
          {otherRequests > 0 ? <div className="notice">{otherRequests} other thread(s) are waiting for an answer.</div> : null}
        </div>
        </OpenFileContext.Provider>

        {view === "work" && !selected && newBranches && bridge.git.available() ? (
          <NewThreadOptions choice={newChoice} onChange={setNewChoice} branches={newBranches} environments={newEnvs} />
        ) : null}
        {selected && !agentsHidden ? (
          <SubagentsPanel agents={subagents(state, selected.thread.id)} working={(id) => isWorking(state, id)}
            onOpen={(id) => void openThread(id)} onStop={(id) => {
              const active = state.threads[id]?.activeTurnId;
              if (active) client.request("turn/interrupt", { threadId: id, turnId: active }).catch(notice);
              else notice("Open the subagent to stop it: this window has not seen its turn start.");
            }} onClose={() => setAgentsHidden(true)} />
        ) : null}
        {(() => {
          const live = selected?.activeTurnId ? selected.turns.find((turn) => turn.id === selected.activeTurnId)?.checklist : null;
          return live ? <LiveChecklist checklist={live} /> : null;
        })()}
        {selected ? (
          <GoalRow key={selected.thread.id} goal={state.goals[selected.thread.id] ?? null} editing={goalEditing} setEditing={setGoalEditing}
            onSet={(objective) => setGoal({ objective, status: "active" })} onStatus={(status) => setGoal({ status })} onClear={clearGoal} />
        ) : null}
        {canCompose && !composerHere && selected ? (
          // Another window holds this thread's composer (several windows, §13): this one follows the thread read-only.
          <div className="composer-mirror" role="status">
            <span>This thread's composer is in another window. This one follows the thread, read-only.</span>
            <span className="spacer" />
            <button className="link-button" onClick={() => void bridge.windows.showThread(selected.thread.id).catch(notice)}>Go to that window</button>
            <button className="primary" onClick={() => void bridge.windows.take(selected.thread.id).then(setWindowsState).catch(notice)}>Use the composer here</button>
          </div>
        ) : canCompose ? (
          <Composer
            draft={draft} setDraft={setDraft} placeholder={placeholder}
            prediction={selected && !selected.activeTurnId ? suggestions[selected.thread.id]?.[0] ?? null : null}
            running={Boolean(selected?.activeTurnId)} sending={sending}
            enterBehaviour={prefs.enterBehaviour} followUp={prefs.followUp}
            root={view === "work" ? composeRoot : null}
            context={{ view, hasThread: selected !== null }}
            history={promptHistory} threadTexts={userTexts(selected)}
            attachments={attachments} onRemoveAttachment={removeFile}
            queue={queue}
            onSteerQueued={(item) => selected && steerQueued(client, selected.thread.id, selected.activeTurnId, item).then(() => refreshQueue(selected.thread.id)).catch(notice)}
            onDropQueued={(item) => selected && dropQueued(client, selected.thread.id, item).then(() => refreshQueue(selected.thread.id)).catch(notice)}
            onEditQueued={(item, text) => selected && updateQueued(client, selected.thread.id, item, text).then(() => refreshQueue(selected.thread.id)).catch(notice)}
            onMoveQueued={(item, by) => {
              const order = selected ? movedQueue(queue.map((entry) => entry.id), item.id, by) : null;
              if (selected && order) reorderQueued(client, selected.thread.id, order).then(() => refreshQueue(selected.thread.id)).catch(notice);
            }}
            approvalOpen={threadRequests.length > 0} onApprovalKey={(key) => void answerByKey(key)}
            onSend={(text, mode) => void send(text, mode)} onStop={stop} onEditPrevious={() => void editPrevious()}
            onCommand={(command) => void runCommand(command)}
            onFiles={onFiles} onPaste={onPaste}
            searchFiles={searchFiles} skills={rootSkills}
            leading={<>
            {view === "ask" ? (
              <>
                <input ref={fileInput} type="file" multiple hidden onChange={(event) => {
                  addFiles(Array.from(event.target.files ?? []));
                  event.target.value = "";
                }} />
                <button className="icon attach" onClick={() => fileInput.current?.click()} title="Attach images or files" aria-label="Attach"><Icon name="paperclip" size={18} /></button>
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
              <button className={`icon mic${dictation === "recording" ? " recording" : ""}`} onClick={() => void toggleDictation()}
                disabled={dictation === "transcribing"} aria-pressed={dictation === "recording"}
                title={dictation === "recording" ? "Stop and write down what you said" : dictation === "transcribing" ? "Writing it down…" : "Dictate (speech-to-text on this machine)"}
                aria-label={dictation === "recording" ? "Stop dictation" : "Dictate"}>{dictation === "transcribing" ? "…" : <Icon name="mic" size={18} />}</button>
            ) : null}
            </>}
            models={models} model={modelChoice ?? servedModel} effort={effort} recentModels={prefs.recentModels}
            picker={picker} setPicker={setPicker} onPick={pickModel}
            plan={planAvailable ? planning.has(planKey) : null} onTogglePlan={togglePlan}
          />
        ) : null}
        {prefs.bottomPanel && view === "work" && bridge.git.available() && selected && detached.has(paneKey("terminal", selected.thread.id)) ? (
          <div className="pane-away" role="status">
            <Icon name="terminal" size={14} /><span>This thread's terminal is in a window of its own.</span>
            <span className="spacer" />
            <button className="link-button" onClick={() => detachPane("terminal")}>Dock it here</button>
          </div>
        ) : prefs.bottomPanel && view === "work" && bridge.git.available() ? (
          <BottomPanel tasks={tasks} environment={environment} problems={envs?.problems ?? []} project={envProject}
            terminals={terminals.filter((entry) => entry.threadId === (selected?.thread.id ?? null))} hub={hub} front={front}
            onNewTerminal={() => openTerminal()} onCloseTerminal={closeTerminal} onSendToThread={sendTerminal}
            onRun={runAction} onStop={(id) => void bridge.stopTask(id).catch(notice)}
            onClose={(id) => setTasks((list) => list.filter((task) => task.id !== id))}
            onEdit={() => setEditingEnv(true)} onHide={() => updatePrefs({ bottomPanel: false })}
            detach={bridge.windows.available() && selected ? { docks: false, run: () => detachPane("terminal") } : null} />
        ) : null}
      </main>
      {editingEnv && envProject ? (
        <EnvironmentEditor project={envProject} initial={environment} onClose={() => setEditingEnv(false)}
          onSave={(edited) => bridge.writeEnvironment(envProject, edited).then(() => setEditingEnv(false)).catch(notice)} />
      ) : null}
      {tabsShown ? (
        <TabArea tabs={paneTabs} active={paneActive?.key ?? null} onSelect={setActiveTab} onClose={(key) => {
          const tab = tabs.find((entry) => entry.key === key);
          if (tab) closedSurfaces.close(surfaceOf(tab));
          closeTabKey(key);
        }}
          workspaceLayout={prefs.layout} setWorkspaceLayout={(layout) => updatePrefs({ layout })}
          treeShown={paneTree} onToggleTree={composeRoot ? () => (view === "ask" ? setAskTree : setTreeShown)((shown) => !shown) : null}
          tree={paneTree && composeRoot ? (
            <FileTree root={composeRoot} client={client} listen={listen} selected={paneActive?.kind === "file" ? paneActive.key.slice(5) : null}
              onOpen={(path) => openFile(path)} onClose={() => (view === "ask" ? setAskTree : setTreeShown)(false)} />
          ) : null}
          render={(tab) => tab.kind === "prs" ? (
            <PullRequestsTab root={composeRoot} thread={selected?.thread.id ?? null} notice={notice}
              onCompose={(text) => setDraft((draft) => (draft.trim() ? `${draft.trimEnd()}\n\n${text}` : text))} />
          ) : tab.kind === "preview" ? (
            <PreviewTab id={tab.id} url={tab.url} thread={selected?.thread.id ?? null} devTools={prefs.previewDevTools}
              visible={tabsShown && tab.key === paneActive?.key && !overlayOpen}
              layoutKey={`${prefs.layout}|${prefs.sidebarHidden}|${prefs.fontScale}|${treeShown}|${prefs.bottomPanel}|${tabs.length}`}
              onTitle={(title, url) => setTabs((list) => list.map((entry) => (entry.key === tab.key && entry.kind === "preview" ? { ...entry, title, url: url || entry.url } : entry)))}
              onAlways={(site, choice) => updatePrefs({ previewSites: { ...prefsRef.current.previewSites, [site]: choice } })}
              onComment={(text) => setDraft((draft) => (draft.trim() ? `${draft.trimEnd()}\n\n${text}` : text))}
              onShortcut={(action) => commands[action === "closeTab" ? "closeTab" : action === "previewOpen" ? "previewOpen" : ""]?.()}
              notice={notice} />
          ) : tab.kind === "review" ? (
            <ReviewPane
              cwd={composeRoot} threadId={selected?.thread.id ?? null}
              turnDiff={lastTurnDiff(selected)}
              airgapOn={airgap?.level === "on"}
              layout={prefs.diffLayout} setLayout={(diffLayout) => updatePrefs({ diffLayout })}
              editor={prefs.editor} setEditor={(editor) => updatePrefs({ editor })}
              refreshKey={turnsDone(selected)}
              onComments={(comments) => setDraft((text) => (text.trim() ? `${text.trimEnd()}\n\n${comments}` : comments))}
              onRevertTurn={selected && selected.turns.length > 0 && !selected.activeTurnId ? () => {
                void revertTurn(selected.turns[selected.turns.length - 1]).catch(notice);
              } : null}
              generateMessage={(root, diff) => commitMessage(client, listen, root, modelChoice ?? servedModel, diff, prefs.commitPrompt ?? undefined)}
              forcePushDefault={prefs.forcePush}
              notice={notice}
              onOpenFile={openFile}
              detach={bridge.windows.available() && selected ? { docks: false, run: () => detachPane("review") } : null}
            />
          ) : (
            artifactKind(tab.path) && !tab.line ? (
              <ArtifactView path={tab.path} line={tab.line} root={composeRoot} client={client} listen={listen} editor={prefs.editor} notice={notice}
                onMention={(path) => setDraft((text) => `${text}${text && !text.endsWith(" ") ? " " : ""}@${path} `)} onOpenFile={(path) => openFile(path)} />
            ) : (
              <FileView path={tab.path} line={tab.line} root={composeRoot} client={client} listen={listen} editor={prefs.editor} notice={notice}
                onMention={(path) => setDraft((text) => `${text}${text && !text.endsWith(" ") ? " " : ""}@${path} `)} />
            )
          )}
        />
      ) : null}
      {linkAsked ? (
        <LinkDialog url={linkAsked.url} airgappedOn={linkAsked.airgappedOn} notice={notice} onClose={() => setLinkAsked(null)}
          onOpen={() => {
            const { url, thread } = linkAsked;
            setLinkAsked(null);
            bridge.openWebLink(url, thread).catch((error: unknown) => notice(`The link was not opened: ${error instanceof Error ? error.message : String(error)}`));
          }} />
      ) : null}
      {settingsOpen ? <SettingsDialog page={settingsPage} setPage={setSettingsPage} prefs={prefs} onChange={updatePrefs} onClose={() => setSettingsOpen(false)} worktrees={wtSettings} onWorktree={(key, value) => void writeWorktreeSetting(key, value)}
        listArchived={listArchived} unarchive={unarchiveThread} notice={notice}
        removedProjects={serverProjects.filter(isRemoved)} restoreProject={(project) => void setProjectMetadata(project, { [REMOVED_KEY]: null }, `Restore ${project.name}`)}
        readConfig={readConfig} writeConfig={async (keyPath, value) => {
          await writeConfig(keyPath, value);
          if (keyPath === "features.agent_message_board" || keyPath === "features.multi_agent_v2") readConfig().then((config) => setBoard({ on: boardOn(config), sqliteHome: sqliteHome(config) })).catch(() => {});
        }}
        memoryStatus={memoryStatus} resetMemories={resetMemories} /> : null}
      {scheduledOpen ? (
        <ScheduledView projects={projects(state, askRoot, (thread) => projectFolder(thread, links)).map((project) => project.cwd)} defaultProject={workFolder ?? null}
          seen={prefs.nightSeen} onSeen={(at) => { updatePrefs({ nightSeen: at }); setNightUnseen(0); }} onClose={() => setScheduledOpen(false)} notice={notice}
          fromThread={selected && view === "work" ? { title: workLabel(selected.thread), context: threadContext(selected) } : null}
          onOpenThread={(threadId) => { showView("work"); void openThread(threadId); }} />
      ) : null}
      {syncedOpen && synced.status ? <SyncedView status={synced.status} onClose={() => setSyncedOpen(false)} onChanged={synced.refresh} notice={notice} /> : null}
      {exporting ? <ExportDialog client={client} thread={exporting.thread} title={exporting.title} home={exporting.home} notice={notice} onClose={() => setExporting(null)} /> : null}
      {projectDialog ? (
        <ProjectDialog client={client} project={projectDialog.project} folder={projectDialog.folder} notice={notice} onClose={() => setProjectDialog(null)}
          onSaved={(saved) => {
            setProjectDialog(null);
            void loadProjects();
            if (!projectDialog.project) {
              // A new project is where the next thread (or question) starts.
              setNewProject(saved.id);
              const primary = primaryFolder(saved);
              if (primary) setNewCwd(primary);
              dispatch({ type: "select", threadId: null });
            }
          }} />
      ) : null}
      {extensions ? <ExtensionsView client={client} listen={listen} cwd={composeRoot} tab={extensions} setTab={setExtensions} onClose={() => setExtensions(null)} notice={notice} /> : null}
      {fileSearch !== null && composeRoot ? (
        <FileSearch root={composeRoot} search={searchFiles} initial={fileSearch}
          front={paneActive?.kind === "file" && tabsShown ? paneActive.path : null}
          onOpen={(path, line) => openFile(path, line)} onClose={() => setFileSearch(null)} />
      ) : null}
      {boardOpen ? (
        <MessageBoardView sqliteHome={board.sqliteHome} threads={Object.fromEntries(Object.entries(state.threads).map(([id, entry]) => [id, entry.thread]))} notice={notice}
          onOpen={(threadId) => void openThread(threadId)} onClose={() => setBoardOpen(false)} />
      ) : null}
      {activityOpen ? (
        <ActivityView client={client} threads={Object.values(state.threads).map((entry) => entry.thread)}
          projects={projects(state, askRoot, (thread) => projectFolder(thread, links)).map((project) => project.cwd)}
          needs={{
            entries: attentionList(Object.values(state.threads).map((entry) => entry.thread), {
              waiting: new Set(state.requests.map((request) => request.threadId).filter((id): id is string => Boolean(id))),
              running: new Set(Object.values(state.threads).filter((entry) => entry.activeTurnId).map((entry) => entry.thread.id)),
              unread: new Set(prefs.unreadThreads),
              pinned: new Set(prefs.pinnedThreads),
              isAsk: (thread) => isAskThread(thread, askRoot),
            }),
            scheduled: nightUnseen,
            label: (thread) => (isAskThread(thread, askRoot) ? askTitle(thread) : workLabel(thread)),
            onMarkAllRead: () => {
              // Every unread mark, and Night Shift's finished tasks counted as seen now.
              updatePrefs({ unreadThreads: [], ...(nightUnseen ? { nightSeen: new Date().toISOString() } : {}) });
              setNightUnseen(0);
            },
            onOpenScheduled: () => setScheduledOpen(true),
          }}
          onOpen={(threadId) => {
            const thread = state.threads[threadId]?.thread;
            if (thread) setView(isAskThread(thread, askRoot) ? "ask" : "work");
            void openThread(threadId);
          }} onClose={() => setActivityOpen(false)} />
      ) : null}
      {menuOpen ? <CommandMenu entries={menuOpen === "projects" ? menuEntries().filter((entry) => entry.kind === "project") : menuEntries()} onRun={runEntry} onClose={() => setMenuOpen(false)} /> : null}
    </div>
  );
}

/** The diff of the shown thread's last turn that changed files, for the review pane's Last turn scope. */
function lastTurnDiff(view: ThreadView | null): string | null {
  if (!view) return null;
  for (let index = view.turns.length - 1; index >= 0; index--) if (view.turns[index].diff) return view.turns[index].diff;
  return null;
}

/** How many of the shown thread's turns have ended: the review pane reads the repository again when it grows. */
function turnsDone(view: ThreadView | null): number {
  return view ? view.turns.filter((turn) => turn.status !== "inProgress").length : 0;
}

/** Whether the keyboard is in a text field, where Enter and Esc belong to the field. */
function editableFocus(): boolean {
  const element = document.activeElement as HTMLElement | null;
  if (!element) return false;
  if (element instanceof HTMLTextAreaElement) return element.value.trim() !== "";
  return element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element.isContentEditable;
}

function PermissionsInfo(props: { choices: ReturnType<typeof permissionChoices>; profile: string; onChoose: (id: string) => void }) {
  return (
    <div className="info-list">
      {props.choices.map((choice) => (
        <button key={choice.id} disabled={choice.disabled} className={choice.id === props.profile ? "selected" : ""} title={choice.reason ?? undefined} onClick={() => props.onChoose(choice.id)}>
          {choice.label}{choice.id === props.profile ? " (in use)" : ""}{choice.reason ? ` — ${choice.reason}` : ""}
        </button>
      ))}
    </div>
  );
}

function StatusInfo(props: { model: string | null; effort: string | null; profile: string; cwd: string | null; airgap: bridge.Airgapped | null; usage: ReturnType<typeof contextUse>; threadId: string | null; followUp: string }) {
  const rows: [string, string][] = [
    ["Model", props.model ?? "unknown"],
    ["Reasoning effort", props.effort ?? "the model's default"],
    ["Permissions", props.profile],
    ["Folder", props.cwd ?? "none yet"],
    ["Airgapped", props.airgap ? `${props.airgap.level} (${props.airgap.source})` : "unknown"],
    ["Context", props.usage ? `${props.usage.percent}% (${props.usage.used.toLocaleString()} of ${props.usage.window.toLocaleString()} tokens)` : "no turn yet"],
    ["Follow-ups", props.followUp === "queue" ? "queue for the next turn" : "steer the running turn"],
    ["Thread", props.threadId ?? "none yet"],
  ];
  return <dl className="info-rows">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

/** A button that copies `text`, with the fallback a plain-HTTP page needs (clipboard.ts). */
function CopyButton({ text: value }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  return (
    <button className="icon copy" title="Copy" aria-label={state === "copied" ? "copied" : state === "failed" ? "could not copy" : "copy"} onClick={() => {
      void copyText(value).then((copied) => {
        setState(copied ? "copied" : "failed");
        setTimeout(() => setState("idle"), 1500);
      });
    }}><Icon name={state === "copied" ? "check" : "copy"} size={14} />{state === "failed" ? "could not copy" : null}</button>
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

function Turn(props: { turn: TurnView; last: boolean; onRevert: () => void; onEdit: (() => void) | null; onFork: ((where: "through" | "before") => void) | null; commandOutput: Prefs["commandOutput"] }) {
  const { turn } = props;
  return (
    <section className={`turn turn-${turn.status}`}>
      {turn.items.map((item) => (props.commandOutput === "hidden" && item.type === "commandExecution" && item.source !== "userShell" ? null : <Item key={item.id} item={item} folded={props.commandOutput === "collapsed"} />))}
      {turn.checklist && turn.status !== "inProgress" ? <TurnChecklist checklist={turn.checklist} /> : null}
      {turn.error ? <div className="turn-error">{turn.error.message}</div> : null}
      {turn.status === "interrupted" ? <div className="turn-note">Stopped.</div> : null}
      {turn.diff ? (
        <details className="turn-diff">
          <summary>Changes in this turn</summary>
          <Diff text={turn.diff} />
        </details>
      ) : null}
      {turn.status !== "inProgress" && (props.last || props.onFork) ? (
        <span className={`turn-actions${props.last ? "" : " earlier"}`}>
          {props.last ? <button className="icon revert" onClick={props.onRevert} title="Undo this turn and what it changed"><Icon name="undo" size={14} />Revert</button> : null}
          {props.last && props.onEdit ? <button className="icon edit-previous" onClick={props.onEdit} title="Take this message back to edit it (Esc Esc)"><Icon name="pencil" size={14} />Edit</button> : null}
          {props.onFork ? <button className="icon fork-here" onClick={() => props.onFork?.("through")} title="A new thread with this conversation up to here; this one stays as it is"><Icon name="gitFork" size={14} />Fork from here</button> : null}
          {props.onFork ? <button className="icon edit-in-new" onClick={() => props.onFork?.("before")} title="A new thread with the conversation before this message, the message in its composer to change; this one stays as it is"><Icon name="squarePen" size={14} />Edit in a new thread</button> : null}
        </span>
      ) : null}
    </section>
  );
}

/**
 * Opens a file in a tab (Phase 3 item 1), for the items of the stream: Work's threads, and Ask's
 * questions beside their answers (`ask`, DESKTOP_PARITY_CLAUDE §4.1); null with no thread shown.
 */
const OpenFileContext = createContext<{ open: (path: string, line: number | null) => void; cwd: string | null; ask: boolean } | null>(null);

/** A file an Ask answer made, as a card that opens it beside the answer, the diff folded under it. */
function MadeFile(props: { path: string; diff: string; onOpen: () => void }) {
  const kind = artifactKind(props.path);
  return (
    <details className="made-file">
      <summary>
        <Icon name="file" size={16} />
        <span className="made-file-name"><strong>{baseName(props.path)}</strong><span className="made-file-kind">{fileKindLabel(kind)}</span></span>
        <button className="made-file-open" title="Open it beside the answer" aria-label={`Open ${baseName(props.path)}`}
          onClick={(event) => { event.preventDefault(); props.onOpen(); }}>Open</button>
      </summary>
      <Diff text={props.diff} />
    </details>
  );
}

function Item({ item, folded }: { item: ThreadItem; folded: boolean }) {
  const files = useContext(OpenFileContext);
  switch (item.type) {
    case "userMessage":
      return <div className="item user">{item.content.map(describeInput).filter(Boolean).join("\n")}</div>;
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
        // A `!` command is the user's own: its output stays open, as the Codex app shows it.
        <details className={`item command status-${item.status}${item.source === "userShell" ? " user-shell" : ""}`} open={item.source === "userShell" || (!folded && item.status === "inProgress")}>
          <summary><Icon name="terminal" size={14} />{item.source === "userShell" ? <span className="shell-mark" title="Your command, run in your shell">!</span> : null}<code>{item.command}</code>{item.exitCode !== null && item.exitCode !== 0 ? <span className="exit"> exit {item.exitCode}</span> : null}</summary>
          {item.aggregatedOutput ? <pre>{item.aggregatedOutput}</pre> : null}
        </details>
      );
    case "fileChange": {
      const at = (path: string) => (path.startsWith("/") || !files?.cwd ? path : `${files.cwd.replace(/\/+$/, "")}/${path}`);
      // In Ask, a file the answer made is a card, as the compared app shows what it made (§4.1); edits stay diffs.
      const made = files?.ask ? item.changes.filter((change) => change.kind.type === "add") : [];
      const edited = files?.ask ? item.changes.filter((change) => change.kind.type !== "add") : item.changes;
      return (
        <div className={`item files status-${item.status}`}>
          {made.map((change) => <MadeFile key={change.path} path={change.path} diff={change.diff} onOpen={() => files?.open(at(change.path), null)} />)}
          {edited.map((change) => (
            <details key={change.path}>
              <summary><Icon name="fileDiff" size={14} />{change.kind.type === "add" ? "Added" : change.kind.type === "delete" ? "Deleted" : "Edited"} <code>{change.path}</code><DiffStat text={change.diff} />
                {files && change.kind.type !== "delete" ? (
                  <button className="icon open-file" title="Open in a tab" aria-label={`Open ${change.path} in a tab`}
                    onClick={(event) => { event.preventDefault(); files.open(at(change.path), null); }}><Icon name="file" size={13} /></button>
                ) : null}
              </summary>
              <Diff text={change.diff} />
            </details>
          ))}
        </div>
      );
    }
    case "mcpToolCall":
      return <div className={`item tool status-${item.status}`}><code>{item.server}.{item.tool}</code>{item.error ? ` — ${item.error.message}` : ""}</div>;
    case "dynamicToolCall": {
      // The window's own tools (windowtools.ts) in words, what they returned folded under them.
      const label = toolLabel(item.tool) ?? item.tool;
      const text = (item.contentItems ?? []).map((content) => (content.type === "inputText" ? content.text : "")).join("\n").trim();
      return (
        <details className={`item tool status-${item.status}`}>
          <summary>{label}{item.success === false ? " — not available" : ""}</summary>
          {text ? <pre>{text}</pre> : null}
        </details>
      );
    }
    case "contextCompaction":
      return <div className="item note">Earlier turns were compacted.</div>;
    case "enteredReviewMode":
      return <div className="item note review-mode">Reviewing: {item.review}</div>;
    case "exitedReviewMode":
      return <div className="item agent review-result"><div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(item.review) }} /></div>;
    case "collabAgentToolCall":
      return <div className="item note">Subagents: {item.tool} {item.receiverThreadIds.length ? `(${item.receiverThreadIds.length})` : ""}{item.prompt ? ` — ${item.prompt.slice(0, 160)}` : ""}</div>;
    case "subAgentActivity":
      return <div className="item note">Subagent {item.agentPath}: {item.kind}</div>;
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
      // A project's context (projects.ts) is for the agent; the transcript shows what the user wrote.
      return isContextText(input.text) ? "" : input.text;
    case "localImage":
      return `[image: ${input.path.split(/[\\/]/).pop()}]`;
    case "skill":
    case "mention":
      // The text already names it (`$name`, `@path`).
      return "";
    default:
      return `[${input.type}]`;
  }
}

/** The `+added −removed` counts of a unified diff, as the summary line shows them. */
function DiffStat({ text: diff }: { text: string }) {
  const lines = diff.split("\n");
  const added = lines.filter((line) => line.startsWith("+") && !line.startsWith("+++")).length;
  const removed = lines.filter((line) => line.startsWith("-") && !line.startsWith("---")).length;
  return <span className="diffstat"><span className="plus">+{added}</span> <span className="minus">−{removed}</span></span>;
}

/** How long ago a thread changed, as a sidebar row shows it: now, 5m, 3h, 2d, 4w, then the date. */
function age(seconds: number): string {
  const elapsed = Math.max(0, Date.now() / 1000 - seconds);
  if (elapsed < 60) return "now";
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)}m`;
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)}h`;
  if (elapsed < 7 * 86400) return `${Math.floor(elapsed / 86400)}d`;
  if (elapsed < 5 * 7 * 86400) return `${Math.floor(elapsed / (7 * 86400))}w`;
  return new Date(seconds * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
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
    case "mcpServer/elicitation/request":
      return <ElicitationCard params={params} airgappedOn={props.airgappedOn} onAnswer={onAnswer} />;
    case "item/tool/requestUserInput":
      return <QuestionCard params={params} onAnswer={onAnswer} />;
    default:
      return null;
  }
}

/** A permission request granted as asked, without the fields it left out. */
function grant(requested: unknown): object {
  const profile = (requested ?? {}) as Record<string, unknown>;
  return Object.fromEntries(Object.entries(profile).filter(([, value]) => value !== null && value !== undefined));
}
