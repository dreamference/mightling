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

import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type ClipboardEvent, type ReactNode } from "react";

import { ASK_PROMPT, askThreads, askTitle, attachmentKind, attachmentName, isAskThread, turnInput, type Attached } from "./ask";
import * as bridge from "./bridge";
import { copyText } from "./clipboard";
import { Composer, type ComposerAttachment } from "./ComposerView";
import { PromptHistory, composeInput, effortFor, keyAnswer, mentionPath, parseSlash, rememberModel, userTexts, type SlashCommand } from "./composer";
import { Icon, MightlingMark } from "./icons";
import { keyAction, matches } from "./keymap";
import { renderMarkdown } from "./markdown";
import { DEFAULT_PREFS, loadPrefs, savePrefs, type Prefs } from "./prefs";
import { RpcClient, type ParamsOf } from "./rpc";
import type { FuzzyFileSearchResult } from "./protocol/FuzzyFileSearchResult";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { ServerRequest } from "./protocol/ServerRequest";
import type { Model } from "./protocol/v2/Model";
import type { PermissionProfileSummary } from "./protocol/v2/PermissionProfileSummary";
import type { QueuedSubmission } from "./protocol/v2/QueuedSubmission";
import type { SkillMetadata } from "./protocol/v2/SkillMetadata";
import type { Thread } from "./protocol/v2/Thread";
import type { ThreadItem } from "./protocol/v2/ThreadItem";
import type { UserInput } from "./protocol/v2/UserInput";
import {
  contextUse, initialState, permissionChoices, projects, reduce,
  type PendingRequest, type ThreadView, type TurnView,
} from "./store";
import { ReviewPane } from "./ReviewPane";
import { commitMessage, dropQueued, forkThread, listQueue, sendMessage, steerQueued, takeBack, type Listen } from "./turns";

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

type View = "ask" | "work";

const HOST = bridge.host();

function initialView(): View {
  return typeof location !== "undefined" && location.hash === "#work" ? "work" : "ask";
}

/** A file picked, pasted or dropped for the next message, not yet sent. */
interface PendingFile {
  id: number;
  file: File;
  name: string;
  kind: "image" | "file";
  preview: string | null;
  /** Where it already is on disk (a Work image dropped in the app): sent by path, never uploaded. */
  path: string | null;
}

/** What a slash command printed, shown above the turns until dismissed. */
interface Info {
  title: string;
  body: ReactNode;
}

let nextPendingId = 1;

const IMAGE_TYPES = /^image\/(png|jpe?g|gif|webp)$/i;

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
  const [prefs, setPrefs] = useState<Prefs>(DEFAULT_PREFS);
  const [models, setModels] = useState<Model[]>([]);
  const [modelChoice, setModelChoice] = useState<string | null>(null);
  const [picker, setPicker] = useState<"closed" | "open" | "recent">("closed");
  const [skills, setSkills] = useState<Record<string, SkillMetadata[]>>({});
  const [info, setInfo] = useState<Info | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  // Listeners besides the store, for the window's own small jobs (a generated commit message).
  const listeners = useRef(new Set<(notification: ServerNotification) => void>());
  const listen: Listen = useCallback((listener) => {
    listeners.current.add(listener);
    return () => listeners.current.delete(listener);
  }, []);
  const promptHistory = useMemo(() => new PromptHistory(), []);
  const fileInput = useRef<HTMLInputElement>(null);
  const notice = useCallback((error: unknown) => dispatch({ type: "notice", message: error instanceof Error ? error.message : String(error) }), []);
  /** Shows Ask or Work: the view buttons, and in the app its menu. */
  const showView = useCallback((next: View) => {
    setView(next);
    setSearch("");
    dispatch({ type: "select", threadId: null });
    if (typeof window !== "undefined") window.history.replaceState(null, "", next === "work" ? "#work" : "#");
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
      setProfiles(permissions.data);
      setAirgap(level);
      setPrefs(stored);
      // The catalog the launcher wrote: one served model, with the efforts it allows.
      client.request("model/list", {}).then((answer) => setModels((answer as { data?: Model[] }).data ?? [])).catch(() => setModels([]));
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
    client.onNotification = (notification) => {
      dispatch({ type: "notification", notification });
      if (notification.method === "thread/queue/changed") refreshQueue(notification.params.threadId);
      for (const listener of listeners.current) listener(notification);
    };
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
  }, [client, connect, answerUnasked, notice, showView, refreshQueue]);

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

  // The folder the composer's `@` and `$` look in: the shown thread's, or the new thread's.
  const composeRoot = view === "work" ? (selected?.thread.cwd ?? (newCwd.trim() || null)) : (selected?.thread.cwd ?? null);
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
    return answer.files ?? [];
  }, [client, composeRoot]);

  // Pasted or picked images show a preview until they are sent or removed.
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  useEffect(() => () => pendingRef.current.forEach((item) => item.preview && URL.revokeObjectURL(item.preview)), []);

  const openThread = async (threadId: string) => {
    dispatch({ type: "select", threadId });
    setDrawer(false);
    setInfo(null);
    try {
      const resumed = await client.request("thread/resume", { threadId, model: servedModel });
      dispatch({ type: "opened", thread: resumed.thread });
      refreshQueue(threadId);
    } catch (error) {
      notice(error);
    }
  };

  const switchView = showView;

  const startThread = async (): Promise<Thread | null> => {
    const cwd = newCwd.trim();
    if (!cwd) {
      notice("Name the project folder for the new thread.");
      return null;
    }
    try {
      const started = await client.request("thread/start", { cwd, model: servedModel, permissions: profile });
      dispatch({ type: "opened", thread: started.thread });
      setDrawer(false);
      return started.thread;
    } catch (error) {
      notice(error);
      return null;
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

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(event.clipboardData?.files ?? []);
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

  const sendWork = async (message: string, mode: "default" | "steer" | "queue") => {
    const target = selected?.thread ?? (await startThread());
    if (!target) return;
    const images = pending.filter((item) => item.kind === "image" && item.path).map((item) => item.path as string);
    const input = composeInput(message, rootSkills, images);
    if (input.length === 0) return;
    setDraft("");
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
      });
      promptHistory.push(message);
      files.forEach((item) => item.preview && URL.revokeObjectURL(item.preview));
    } catch (error) {
      setDraft(message);
      setPending(files);
      notice(error);
    }
  };

  const sendAsk = async (message: string, mode: "default" | "steer" | "queue") => {
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
      await sendMessage(client, { threadId: thread.id, activeTurnId: active, input, followUp: mode === "default" ? prefs.followUp : mode, effort, model: modelChoice });
      promptHistory.push(message);
      files.forEach((item) => item.preview && URL.revokeObjectURL(item.preview));
    } catch (error) {
      setDraft(message);
      setPending(files);
      notice(error);
    } finally {
      setSending(false);
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
    if (view === "ask") {
      if (message || pending.length) await sendAsk(message, mode);
    } else if (message || pending.length) {
      await sendWork(message, mode);
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

  const threadRequests = state.requests.filter((request) => request.threadId === null || request.threadId === selected?.thread.id);

  /** Enter or Esc on the first open approval of the shown thread. */
  const answerByKey = (key: "approve" | "decline") => {
    const request = threadRequests[0];
    if (!request) return false;
    const result = keyAnswer(request.method, key, request.params as Record<string, unknown>);
    if (!result) return false;
    answer(request, result);
    return true;
  };

  /** Esc Esc or /edit: the last message comes back into the composer, and its turn is undone. */
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

  const fork = async () => {
    if (!selected) return;
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

  /** Shown at once, then saved; a refused write puts the stored values back. */
  const updatePrefs = (values: Partial<Prefs>) => {
    const before = prefs;
    setPrefs({ ...prefs, ...values });
    savePrefs(before, values).then(setPrefs).catch((error) => {
      setPrefs(before);
      notice(error);
    });
  };

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
          if (threadId) setRenaming({ id: threadId, name: selected?.thread.name ?? "" });
          return;
        case "archive":
          if (threadId) return archive(threadId);
          return;
        case "model":
          return setPicker("open");
        case "permissions":
          return setInfo({ title: "Permissions", body: <PermissionsInfo choices={choices} profile={profile} onChoose={(id) => { setProfile(id); setInfo(null); }} /> });
        case "status":
          return setInfo({ title: "Status", body: <StatusInfo model={modelChoice ?? servedModel} effort={effort} profile={choices.find((choice) => choice.id === profile)?.label ?? profile} cwd={selected?.thread.cwd ?? composeRoot} airgap={airgap} usage={contextUse(selected?.usage ?? null)} threadId={threadId} followUp={prefs.followUp} /> });
        case "skills":
          return setInfo({ title: "Skills", body: <SkillsInfo skills={rootSkills} root={composeRoot} /> });
        case "mention":
          return setDraft((text) => `${text}${text && !/\s$/.test(text) ? " " : ""}@`);
        case "mcp": {
          const answerList = (await client.request("mcpServerStatus/list", {})) as { data?: { name: string; tools: Record<string, unknown>; toolsError: string | null }[] };
          return setInfo({ title: "MCP servers", body: <McpInfo servers={answerList.data ?? []} /> });
        }
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
    if (matches(event, keyAction("modelPicker"))) {
      event.preventDefault();
      setPicker((open) => (open === "closed" ? "open" : "closed"));
    } else if (matches(event, keyAction("recentModels"))) {
      event.preventDefault();
      setPicker("recent");
    } else if (matches(event, keyAction("reviewTab"))) {
      event.preventDefault();
      setReviewOpen((open) => !open);
    } else if (matches(event, keyAction("settings"))) {
      event.preventDefault();
      setSettingsOpen((open) => !open);
    } else if (event.key === "Escape" && (picker !== "closed" || settingsOpen || info)) {
      setPicker("closed");
      setSettingsOpen(false);
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

  if (state.server !== "ready") {
    return <StartupScreen state={state.server} stderr={state.stderr} protocolErrors={state.protocolErrors} notices={state.notices} onRetry={connect} />;
  }

  const usage = contextUse(selected?.usage ?? null);
  const otherRequests = state.requests.length - threadRequests.length;
  const allThreads = Object.values(state.threads).map((entry) => entry.thread);
  const asks = found ? found.map((result) => result.thread) : askThreads(allThreads, askRoot);
  const snippets = new Map((found ?? []).map((result) => [result.thread.id, result.snippet]));
  const queue: QueuedSubmission[] = selected ? state.queues[selected.thread.id] ?? [] : [];
  const attachments: ComposerAttachment[] = pending.map((item) => ({ id: item.id, name: item.name, preview: item.preview }));

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
        <span className="age" title={new Date(thread.updatedAt * 1000).toLocaleString()}>{age(thread.updatedAt)}</span>
        <span className="row-actions">
          <button className="icon" title="Rename" aria-label="Rename" onClick={() => setRenaming({ id: thread.id, name: thread.name || label })}><Icon name="pencil" size={14} /></button>
          <button className="icon" title="Archive" aria-label="Archive" onClick={() => void archive(thread.id)}><Icon name="archive" size={14} /></button>
        </span>
      </div>
    )
  );

  const title = selected ? selected.thread.name || selected.thread.preview || (view === "ask" ? "New question" : "New thread") : view === "ask" ? "New question" : "Mightling";
  const canCompose = view === "ask" || selected !== null || newCwd.trim() !== "";
  const placeholder = selected?.activeTurnId
    ? prefs.followUp === "queue" ? "Queue a follow-up for the next turn…" : "Steer the running turn…"
    : view === "ask" ? "Ask anything… ($ for a skill, / for commands)" : "Ask Mightling… (@ for a file, $ for a skill, / for commands)";

  return (
    <div className={`work view-${view}${drawer ? " drawer-open" : ""}${reviewOpen && view === "work" ? " review-open" : ""}`} onContextMenu={(event) => {
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
            <h2 className="section-label">Questions</h2>
            {asks.length === 0 ? <p className="empty small">{found ? "Nothing matches." : "No questions yet."}</p> : null}
            {asks.map((thread) => threadRow(thread, askTitle(thread)))}
          </>
        ) : (
          <>
            <button className="nav-row new-thread-button" onClick={() => void startThread()}><Icon name="squarePen" />New thread</button>
            <label className="new-thread" title="The project folder a new thread starts in">
              <Icon name="folder" size={14} />
              <input value={newCwd} onChange={(event) => setNewCwd(event.target.value)} placeholder="/path/to/project" aria-label="Project folder" />
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
            ) : projects(state, askRoot).map((project) => (
              <section key={project.cwd} className="project">
                <h2 title={project.cwd} onClick={() => setNewCwd(project.cwd)}><Icon name="folder" size={14} />{project.cwd.split(/[\\/]/).filter(Boolean).pop() ?? project.cwd}</h2>
                {project.threads.map((thread) => threadRow(thread, thread.name || thread.preview || "(new thread)"))}
              </section>
            ))}
          </>
        )}
        <span className="sidebar-spacer" />
        <button className="nav-row settings-button" onClick={() => setSettingsOpen(true)} title="Settings (Ctrl+,)"><Icon name="settings" size={14} />Settings</button>
        {HOST === "web" ? <a className="devices-link" href="/devices">Pair a phone or another device</a> : null}
      </aside>
      <div className="backdrop" onClick={() => setDrawer(false)} />

      <main className="thread-pane">
        <header className="bar" onDoubleClick={() => HOST === "electron" && bridge.windowControl("maximize").catch(() => {})}>
          <button className="icon menu-toggle" aria-label="Threads" onClick={() => setDrawer(!drawer)}><Icon name="panelLeft" size={20} strokeWidth={1.75} /></button>
          <span className="title">{title}</span>
          {selected && view === "work" ? <span className="cwd">{selected.thread.cwd}</span> : null}
          <span className="spacer" />
          {selected ? <button className="icon bar-action" title="Fork: continue in a copy of this thread" aria-label="Fork" onClick={() => void fork()}><Icon name="gitFork" size={16} /></button> : null}
          {view === "work" ? <button className={`icon bar-action${reviewOpen ? " active" : ""}`} title="Review: the repository's changes (Ctrl+Shift+G)" aria-label="Review" aria-pressed={reviewOpen} onClick={() => setReviewOpen(!reviewOpen)}><Icon name="fileDiff" size={16} /></button> : null}
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
          {info ? (
            <section className="info-panel" aria-label={info.title}>
              <header><strong>{info.title}</strong><button className="icon" aria-label="Close" onClick={() => setInfo(null)}><Icon name="x" size={14} /></button></header>
              {info.body}
            </section>
          ) : null}
          {selected ? selected.turns.map((turn, index) => (
            <Turn key={turn.id} turn={turn} last={index === selected.turns.length - 1}
              onRevert={() => client.request("thread/revert", { threadId: selected.thread.id, beforeTurnId: turn.id })
                .then(() => openThread(selected.thread.id)).catch(notice)}
              onEdit={!selected.activeTurnId && index === selected.turns.length - 1 ? () => void editPrevious() : null} />
          )) : view === "ask" ? (
            <div className="empty ask-empty">
              <MightlingMark size={56} />
              <h1>Ask Mightling</h1>
              <p>A question with no project: Mightling answers in a folder of its own, searching the web when it needs to{airgap?.level === "on" ? ", except that airgapped is on, so web search is off" : ""}. Attach images or files with the paperclip, or paste a screenshot.</p>
            </div>
          ) : <p className="empty">{newCwd.trim() ? `A new thread in ${newCwd.trim()}: write the first message below.` : "Open a thread, or name a project folder for a new one."}</p>}
          {threadRequests.map((request) => (
            <Approval key={JSON.stringify(request.id)} request={request} airgappedOn={airgap?.level === "on"} onAnswer={(result) => answer(request, result)} />
          ))}
          {otherRequests > 0 ? <div className="notice">{otherRequests} other thread(s) are waiting for an answer.</div> : null}
        </div>

        {canCompose ? (
          <Composer
            draft={draft} setDraft={setDraft} placeholder={placeholder}
            running={Boolean(selected?.activeTurnId)} sending={sending}
            enterBehaviour={prefs.enterBehaviour} followUp={prefs.followUp}
            root={view === "work" ? composeRoot : null}
            context={{ view, hasThread: selected !== null }}
            history={promptHistory} threadTexts={userTexts(selected)}
            attachments={attachments} onRemoveAttachment={removeFile}
            queue={queue}
            onSteerQueued={(item) => selected && steerQueued(client, selected.thread.id, selected.activeTurnId, item).then(() => refreshQueue(selected.thread.id)).catch(notice)}
            onDropQueued={(item) => selected && dropQueued(client, selected.thread.id, item).then(() => refreshQueue(selected.thread.id)).catch(notice)}
            approvalOpen={threadRequests.length > 0} onApprovalKey={(key) => void answerByKey(key)}
            onSend={(text, mode) => void send(text, mode)} onStop={stop} onEditPrevious={() => void editPrevious()}
            onCommand={(command) => void runCommand(command)}
            onFiles={onFiles} onPaste={onPaste}
            searchFiles={searchFiles} skills={rootSkills}
            leading={view === "ask" ? (
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
            models={models} model={modelChoice ?? servedModel} effort={effort} recentModels={prefs.recentModels}
            picker={picker} setPicker={setPicker} onPick={pickModel}
          />
        ) : null}
      </main>
      {reviewOpen && view === "work" ? (
        <ReviewPane
          cwd={composeRoot} threadId={selected?.thread.id ?? null}
          turnDiff={lastTurnDiff(selected)}
          airgapOn={airgap?.level === "on"}
          layout={prefs.diffLayout} setLayout={(diffLayout) => updatePrefs({ diffLayout })}
          editor={prefs.editor} setEditor={(editor) => updatePrefs({ editor })}
          refreshKey={turnsDone(selected)}
          onComments={(comments) => setDraft((text) => (text.trim() ? `${text.trimEnd()}\n\n${comments}` : comments))}
          onRevertTurn={selected && selected.turns.length > 0 && !selected.activeTurnId ? () => {
            const last = selected.turns[selected.turns.length - 1];
            client.request("thread/revert", { threadId: selected.thread.id, beforeTurnId: last.id }).then(() => openThread(selected.thread.id)).catch(notice);
          } : null}
          generateMessage={(root, diff) => commitMessage(client, listen, root, modelChoice ?? servedModel, diff)}
          notice={notice}
          onClose={() => setReviewOpen(false)}
        />
      ) : null}
      {settingsOpen ? <Settings prefs={prefs} onChange={updatePrefs} onClose={() => setSettingsOpen(false)} /> : null}
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

/** The window's settings (DESKTOP_PARITY §2.15); General for now: what Enter does and what a follow-up does. */
function Settings(props: { prefs: Prefs; onChange: (values: Partial<Prefs>) => void; onClose: () => void }) {
  const { prefs, onChange } = props;
  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog settings" role="dialog" aria-label="Settings" onClick={(event) => event.stopPropagation()}>
        <header><h2>Settings</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
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
      </section>
    </div>
  );
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

function SkillsInfo(props: { skills: SkillMetadata[]; root: string | null }) {
  if (!props.root) return <p className="empty small">Open a thread or name a folder to see its skills.</p>;
  if (props.skills.length === 0) return <p className="empty small">No skills for {props.root}.</p>;
  return (
    <ul className="info-items">
      {props.skills.map((skill) => (
        <li key={skill.path}><code>${skill.name}</code>{skill.enabled ? "" : " (off)"} — {skill.shortDescription || skill.description}</li>
      ))}
    </ul>
  );
}

function McpInfo(props: { servers: { name: string; tools: Record<string, unknown>; toolsError: string | null }[] }) {
  if (props.servers.length === 0) return <p className="empty small">No MCP servers are configured.</p>;
  return (
    <ul className="info-items">
      {props.servers.map((server) => (
        <li key={server.name}><strong>{server.name}</strong>: {server.toolsError ? `tools unavailable (${server.toolsError})` : Object.keys(server.tools).sort().join(", ") || "no tools"}</li>
      ))}
    </ul>
  );
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

function Turn(props: { turn: TurnView; last: boolean; onRevert: () => void; onEdit: (() => void) | null }) {
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
        <span className="turn-actions">
          <button className="icon revert" onClick={props.onRevert} title="Undo this turn and what it changed"><Icon name="undo" size={14} />Revert</button>
          {props.onEdit ? <button className="icon edit-previous" onClick={props.onEdit} title="Take this message back to edit it (Esc Esc)"><Icon name="pencil" size={14} />Edit</button> : null}
        </span>
      ) : null}
    </section>
  );
}

function Item({ item }: { item: ThreadItem }) {
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
        <details className={`item command status-${item.status}`} open={item.status === "inProgress"}>
          <summary><Icon name="terminal" size={14} /><code>{item.command}</code>{item.exitCode !== null && item.exitCode !== 0 ? <span className="exit"> exit {item.exitCode}</span> : null}</summary>
          {item.aggregatedOutput ? <pre>{item.aggregatedOutput}</pre> : null}
        </details>
      );
    case "fileChange":
      return (
        <div className={`item files status-${item.status}`}>
          {item.changes.map((change) => (
            <details key={change.path}>
              <summary><Icon name="fileDiff" size={14} />{change.kind.type === "add" ? "Added" : change.kind.type === "delete" ? "Deleted" : "Edited"} <code>{change.path}</code><DiffStat text={change.diff} /></summary>
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
