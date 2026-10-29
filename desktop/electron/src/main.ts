// The app's windows and the one IPC channel between their pages and this process
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3, §13). Every message from a page is checked to
// come from one of the app's windows before it is acted on. A full window (`app://`) shows Ask and
// Work, as `ling web` does: this process enforces the same policy (policy.ts) and keeps the Ask
// folders (ask.ts). The app has several windows since 2026-10-22 (New Window, Ctrl/Cmd+Shift+N; a
// thread popped out; a terminal or diff pane detached), each showing its own thread or project, and
// one app-server, the app's only one: the channel is multiplexed per window (windows.ts), and a
// thread open in two windows has one composer. The menu's Ask and Work, the tray and `ling app`
// bring the most recent window forward, or open one when none is open; a second `ling app` hands
// over to this instance (the single-instance lock, bootstrap.ts). Until 2026-10-22 the app had one
// window; until 2026-10-09 the menu's Ask opened a second one on `ling web`, whose own app-server
// shared `~/.mightling` with this one (specs/DREAMFERENCE_MIGHTLING_ASK.md §18.6).

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import * as knowledge from "./knowledge";
import * as userFs from "./userfs";
import { BrowserWindow, app, dialog, globalShortcut, ipcMain, nativeTheme, screen, shell, type IpcMainInvokeEvent } from "electron";

import { CHANNEL_CHUNK_ACK, CHANNEL_FOR_VIEW, CHANNEL_FROM_VIEW, FAILED, Reassembler, chunked, isChunk, type ForView, type FromView } from "./api";
import { airgapped, airgappedLevel, countsAsOn, setDefault } from "./airgapped";
import { serve } from "./app-protocol";
import { auditPreview, type WorkTarget } from "./args";
import { webUrl } from "./links";
import { codexHome, findLing } from "./bridge";
import { officePreview } from "./office";
import { findEditors, openCommand, runsWhenOpened, terminalCommand, type Editor } from "./editors";
import { environments, writeEnvironment } from "./environments";
import * as git from "./git";
import * as prs from "./prs";
import { changedLines } from "./repolines";
import { boardFile, readBoards } from "./board";
import { Prefs } from "./prefs";
import { AppServer } from "./server";
import { insights, readPersonal, writePersonal } from "./insights";
import { addRepeating, addTask, dropRepeating, dropTask, listRepeating, nightStatus, readReport, readReports, readTasks, runNow, taskDetails } from "./night";
import { applyTask, taskNote } from "./night";
import { ARCHIVE, nightUnread, searchArchive } from "./nightinbox";
import { writeWorkAttachment } from "./attachments";
import { listMemoryFiles, writeMemoryFile } from "./memoryfiles";
import * as mcpb from "./mcpb";
import { Nodes } from "./nodes";
import { docsAdd, docsReindex, docsRemove, docsStatus, locate, syncKnowledge } from "./docs";
import { Tasks } from "./tasks";
import { PANE_KINDS, WindowHub, paneAction, type PaneKind, type WindowKind } from "./windows";
import { TerminalMirror } from "./terminal-mirror";
import { WindowSession, cascade, follow } from "./window-state";
import * as worktree from "./worktree";
import { installMenu, installTray, keepAwake, notify, showContextMenu, updateTray } from "./shell";
import { Notices } from "./notices";
import { SyncHost } from "./sync-host";
import { transcribe } from "./voice";
import { Previews } from "./preview";
import { disableRecall, enableRecall, pruneRecall, recallIndex, recallOn, writeRecall } from "./recall";
import { PetStatus, fitPet, openPet, petImage, type PetSize, type PetState } from "./pet";
import { DEFAULT_SHORTCUT, RecentQuestions, accelerator, openQuick, placeQuick, quickPrompt, quickSize, shortcutsWork, type QuickStatus } from "./quick";
import type { SiteChoice } from "./preview-rules";
import { openWork } from "./work";
import { themeChoice, windowColours, type Theme } from "./theme";

export interface MainOptions {
  /** What `ling app` asked for: Work with a folder or thread, or `null` for Ask. */
  work: WorkTarget | null;
  /** The audit's session: the window hidden, its app-server started, quit after this many seconds. */
  audit: number | null;
}

type View = "ask" | "work";

/** What a link or a second `ling app` names for a window to show (args.ts). */
type LinkPayload = Extract<ForView, { channel: "link" }>["payload"];

interface AppWindow {
  win: BrowserWindow;
  kind: WindowKind;
  /** What the window opened on, and what `work/target` answers it: a folder or a thread from `ling app` or a link. */
  target: WorkTarget;
  /** The view a full window shows now (its page says), for New Window and the session. */
  view?: View;
  /** A detached pane: which, whose thread, and the window it came from (its webContents id). */
  pane?: { kind: PaneKind; thread: string; parent: number };
}

interface Opening {
  kind: WindowKind;
  view?: View;
  popout?: string;
  pane?: { kind: PaneKind; thread: string; parent: number };
  target?: WorkTarget;
  bounds?: Electron.Rectangle;
  maximized?: boolean;
}

const resourcesPath = () => (app.isPackaged ? process.resourcesPath : null);

export async function main(options: MainOptions): Promise<void> {
  // The page is the built renderer beside this bundle, packaged or not: app:// serves files,
  // never Vite's dev server.
  serve(path.join(__dirname, "..", "renderer", "main_window"));

  /** Every window of the app's own page, by its webContents id: full windows, pop-outs and detached panes. */
  const appWindows = new Map<number, AppWindow>();
  // Hidden for the audit's session and for an end-to-end test (`MIGHTLING_HIDDEN=1`).
  const show = options.audit === null && !process.env.MIGHTLING_HIDDEN;

  const prefs = new Prefs(app.getPath("userData"));
  // The theme (theme.ts): the preference is nativeTheme's source before any window opens, so the
  // first paint, the native menus and the page's media query all have it.
  nativeTheme.themeSource = themeChoice(prefs.read().theme);
  const shownTheme = (): Theme => (nativeTheme.shouldUseDarkColors ? "dark" : "light");
  const notices = new Notices();
  /** Who shows what, who owns each thread's composer, and the routing of the one channel (windows.ts). */
  const hub = new WindowHub();
  /** The terminal tabs every window sees, with their recent output (terminal-mirror.ts). */
  const terminals = new TerminalMirror();
  /** The full windows open now, for the next start (window-state.ts); the audit's session keeps none. */
  const session = new WindowSession(path.join(app.getPath("userData"), "windows.json"), path.join(app.getPath("userData"), "window-state-work.json"));
  const alive = (entry: BrowserWindow | null | undefined): entry is BrowserWindow => entry !== null && entry !== undefined && !entry.isDestroyed();
  const windows = () => [...appWindows.values()].map((entry) => entry.win).filter(alive);
  /** The app's window the sender is (a full one, a pop-out or a pane), or null for anything else. */
  const windowFor = (senderId: number) => windows().find((entry) => entry.webContents.id === senderId) ?? null;
  /** The most recently focused window of one of `kinds` (a hidden one counts: the tray brings it back). */
  const recent = (kinds: WindowKind[] = ["full"]): BrowserWindow | null => {
    const id = hub.mostRecent(kinds);
    const entry = id === null ? undefined : appWindows.get(Number(id));
    return alive(entry?.win) ? entry.win : null;
  };
  const send = (target: BrowserWindow, message: ForView) => {
    const contents = target.webContents;
    if (contents.isDestroyed()) return;
    for (const piece of chunked(message)) contents.send(CHANNEL_FOR_VIEW, piece);
  };
  /** Tells every window who owns which thread's composer and who answers for the rest. */
  const sendWindows = () => {
    for (const target of windows()) send(target, { channel: "windows", payload: hub.state(String(target.webContents.id)) });
  };
  const sendTerminals = () => {
    const list = terminals.list();
    for (const target of windows()) send(target, { channel: "terminals", payload: list });
  };
  const deliver = (message: ForView) => {
    if (message.channel === "work://message") {
      // A finished turn, an approval or a question waiting: a notification, as Settings › Notifications says;
      // a click shows the thread.
      const notice = notices.take(message.payload, () => prefs.read());
      if (notice) notify(windows(), notice, () => showThread(notice.threadId ?? null));
      if (petStatus.observe(message.payload)) sendPet();
      recentQuestions.observe(message.payload);
      terminals.observe(message.payload);
      const routed = hub.incoming(message.payload);
      for (const delivery of [routed, ...(routed.also ?? [])]) {
        for (const target of windows()) {
          if (delivery.to === "all" || String(target.webContents.id) === delivery.to) send(target, { channel: "work://message", payload: delivery.payload });
        }
      }
      return;
    }
    if (message.channel === "work://exit") {
      syncHost?.stop();
      hub.reset();
      terminals.clear();
      sendTerminals();
      petStatus.reset();
      recentQuestions.reset();
      sendPet();
    }
    for (const target of windows()) send(target, message);
  };
  const server = new AppServer(resourcesPath(), {
    deliver,
    busyChanged: (busy) => {
      // Settings › General and Notifications (prefs.json): prevent sleep, and when a finished turn notifies.
      keepAwake(busy && prefs.read().preventSleep !== false);
    },
  });
  // The app as a sync host (sync-host.ts, specs/DREAMFERENCE_MIGHTLING_SYNC.md §18): while this device is
  // paired and its agent has found the hub, another device follows the turns of this app-server and
  // sends, approves, stops and starts through it, under the same policy. What it did is said on the
  // owner's screen: a notification, and `sync://notice` to every window. Never in the audit's session.
  const syncHost = options.audit === null
    ? new SyncHost(server, {
      told: (text, thread) => {
        for (const target of windows()) send(target, { channel: "sync://notice", payload: { text, thread } });
        // As any other notice: only when no window is in front (the page shows it then).
        notify(windows(), { title: "Mightling", body: text, always: false }, () => showThread(thread));
      },
    })
    : null;

  /** Opens a window of the app: a full one, a thread popped out, or a pane detached from `parent`. */
  function openWindow(opening: Opening): BrowserWindow {
    const win = openWork({
      preload: path.join(__dirname, "preload.cjs"), show, theme: shownTheme(), view: opening.view,
      popout: opening.popout, pane: opening.pane ? { kind: opening.pane.kind, thread: opening.pane.thread } : undefined,
      bounds: opening.bounds, maximized: opening.maximized,
    });
    const id = win.webContents.id;
    const key = String(id);
    appWindows.set(id, { win, kind: opening.kind, target: opening.target ?? { cwd: null, thread: null }, view: opening.view, pane: opening.pane });
    hub.open(key, opening.kind);
    if (opening.popout) hub.show(key, opening.popout, true);
    if (opening.kind === "full" && options.audit === null) {
      session.add(key, { view: opening.view ?? "ask", ...(opening.bounds ? { bounds: opening.bounds } : {}), ...(opening.maximized ? { maximized: true } : {}) },
        () => ({ bounds: win.getNormalBounds(), maximized: win.isMaximized() }));
      follow(win, (state) => session.update(key, state));
    }
    win.on("focus", () => {
      hub.focus(key);
      session.focused(key);
      sendWindows();
    });
    // Settings › General: closing the last full window can leave the app in the tray (DESKTOP_PARITY_CLAUDE
    // §4.2), so Quick Entry and running turns go on; off by default, and never in the audit's session.
    // Quit (Ctrl+Q, the tray, the menu, a signal) still ends everything. Any other window just closes.
    win.on("close", (event) => {
      if (opening.kind !== "full") return;
      const others = [...appWindows.values()].some((entry) => entry.kind === "full" && entry.win !== win && alive(entry.win));
      if (!quitting && !others && options.audit === null && prefs.read().keepInTray === true) {
        event.preventDefault();
        win.hide();
        return;
      }
      if (options.audit === null) session.closing(key, quitting || !others);
    });
    win.on("closed", () => {
      appWindows.delete(id);
      hub.close(key);
      // A pane goes with the window it was detached from; a pane closed docks back into it.
      for (const entry of appWindows.values()) if (entry.pane?.parent === id && alive(entry.win)) entry.win.close();
      const parent = opening.pane ? appWindows.get(opening.pane.parent) : undefined;
      if (opening.pane && parent && alive(parent.win)) send(parent.win, { channel: "pane://docked", payload: { pane: opening.pane.kind, thread: opening.pane.thread } });
      sendWindows();
    });
    sendWindows();
    return win;
  }

  /**
   * Brings the most recent full window forward on `view` (and what `link` names), opening one when
   * none is open: the menu's Ask and Work, the tray, `ling app`, a link, the pet, Quick Entry.
   */
  const showView = (view: View, link?: LinkPayload) => {
    const existing = recent(["full"]);
    if (existing) {
      if (existing.isMinimized()) existing.restore();
      if (show) {
        existing.show();
        existing.focus();
      }
      send(existing, { channel: "view", payload: view });
      if (link) {
        const entry = appWindows.get(existing.webContents.id);
        if (entry) entry.target = { cwd: link.cwd, thread: link.thread };
        send(existing, { channel: "link", payload: link });
      }
      return existing;
    }
    return openWindow({ kind: "full", view, target: link ? { cwd: link.cwd, thread: link.thread } : undefined });
  };
  /** Shows a thread: the window whose composer it is, brought forward, or the most recent full window. */
  const showThread = (thread: string | null) => {
    const owner = thread ? hub.owner(thread) : null;
    const entry = owner === null ? undefined : appWindows.get(Number(owner));
    if (entry && alive(entry.win)) {
      if (entry.win.isMinimized()) entry.win.restore();
      if (show) {
        entry.win.show();
        entry.win.focus();
      }
      return;
    }
    showView("work", thread ? { cwd: null, thread } : undefined);
  };
  /** New Window (Ctrl/Cmd+Shift+N): a full window beside the one it came from, on its view, showing nothing yet. */
  const newWindow = (from: BrowserWindow | null, view?: View) => {
    const source = from && appWindows.get(from.webContents.id)?.kind === "full" ? from : recent(["full"]);
    const bounds = source ? cascade(source.getNormalBounds(), screen.getDisplayMatching(source.getNormalBounds()).workArea) : undefined;
    return openWindow({ kind: "full", view: view ?? (source ? appWindows.get(source.webContents.id)?.view : undefined) ?? "ask", bounds });
  };
  /** Detaches a thread's pane from `parent` into a window of its own, or brings the one already detached forward. */
  const detach = (parent: BrowserWindow, kind: PaneKind, thread: string) => {
    for (const entry of appWindows.values()) {
      if (entry.pane && entry.pane.kind === kind && entry.pane.thread === thread && alive(entry.win)) {
        if (show) entry.win.focus();
        return entry.win;
      }
    }
    return openWindow({ kind: "pane", pane: { kind, thread, parent: parent.webContents.id } });
  };
  /** Docks a thread's pane back: its window closes, and the window it came from shows it again. */
  const dock = (kind: PaneKind, thread: string) => {
    for (const entry of appWindows.values()) if (entry.pane && entry.pane.kind === kind && entry.pane.thread === thread && alive(entry.win)) entry.win.close();
  };
  let quitting = false;
  app.on("before-quit", () => {
    quitting = true;
    session.freeze();
  });
  const actions = {
    openAsk: () => showView("ask"),
    openWork: () => showView("work"),
    newWindow: (from: BrowserWindow | null) => newWindow(from),
    // The Window menu's pane items: the page of the window in front decides (detach, or dock back).
    pane: (kind: PaneKind, from: BrowserWindow | null) => {
      const target = from && appWindows.has(from.webContents.id) ? from : recent(["full", "popout", "pane"]);
      if (target) send(target, { channel: "command", payload: kind === "terminal" ? "detachTerminal" : "detachReview" });
    },
    // File › New Thread, Open Folder…, Settings… (DESKTOP §16 B20): the window in front, or the most recent full one.
    command: (command: "newChat" | "openFolder" | "settings", from: BrowserWindow | null) => {
      const target = from && appWindows.has(from.webContents.id) ? from : recent(["full"]);
      if (!target) return showView("work");
      target.show();
      target.focus();
      send(target, { channel: "command", payload: command });
    },
    // Help › Show Logs: $CODEX_HOME/log, where `ling app` writes ling-app.log and ling writes its own.
    showLogs: () => {
      const home = codexHome();
      if (!home) return;
      const logs = path.join(home, "log");
      void fs.promises.mkdir(logs, { recursive: true }).then(() => shell.openPath(logs)).catch(() => {});
    },
  };

  /** Gives every window the theme shown now: its background, its title bar's symbols, the page's tokens. */
  function paintTheme(): void {
    const theme = shownTheme();
    const colours = windowColours(theme);
    for (const target of windows()) {
      target.setBackgroundColor(colours.backgroundColor);
      try {
        target.setTitleBarOverlay(colours.titleBarOverlay);
      } catch {
        // No overlay to restyle (macOS draws its own traffic lights).
      }
    }
    deliver({ channel: "theme", payload: theme });
    if (pet && !pet.isDestroyed()) send(pet, { channel: "theme", payload: theme });
    if (quick && !quick.isDestroyed()) send(quick, { channel: "theme", payload: theme });
  }

  // The pet (pet.ts, Phase 3 item 6): a window of its own, shown while Settings › Pets says so.
  const petStatus = new PetStatus();
  /** Night Shift's unread results, for the tray (DESKTOP §16 B41, `syncNight`). */
  let nightCount = 0;
  /** Ask's recent questions for Quick Entry's box (quick.ts, DESKTOP §16 B24). */
  const recentQuestions = new RecentQuestions(() => {
    const home = codexHome();
    return home ? path.join(home, "ask") : null;
  });
  let pet: BrowserWindow | null = null;
  let petPicture: { file: unknown; data: string | null } = { file: null, data: null };
  const petSize = (): PetSize => {
    const size = prefs.read().petSize;
    return size === "small" || size === "large" ? size : "medium";
  };
  const petState = (): PetState => {
    const file = prefs.read().petImage;
    if (file !== petPicture.file) petPicture = { file, data: petImage(file) };
    return { ...petStatus.counts(), size: petSize(), image: petPicture.data, needs: petStatus.needs() };
  };
  function sendPet(): void {
    if (pet && !pet.isDestroyed()) send(pet, { channel: "pet://state", payload: petState() });
    // The tray says how many threads work and wait, from the same state (DESKTOP §16 B20).
    updateTray({ ...petStatus.counts(), night: nightCount });
  }
  // Night Shift's unread results in the tray (DESKTOP §16 B41, nightinbox.ts): counted from the files
  // and the window's marks, every two minutes and whenever a window changes the marks.
  const syncNight = () => {
    const home = codexHome();
    try {
      nightCount = home ? nightUnread(path.join(home, "night"), prefs.read()) : 0;
    } catch {
      nightCount = 0;
    }
    updateTray({ ...petStatus.counts(), night: nightCount });
  };
  const syncPet = () => {
    const wanted = prefs.read().petShown === true;
    if (wanted && (!pet || pet.isDestroyed())) {
      pet = openPet(path.join(__dirname, "preload.cjs"), petSize(), show, shownTheme(), prefs.read().petCorner);
      pet.on("closed", () => (pet = null));
    } else if (!wanted && pet && !pet.isDestroyed()) {
      pet.close();
      pet = null;
    } else if (pet && !pet.isDestroyed()) {
      fitPet(pet, petSize(), false);
      sendPet();
    }
  };
  /** What the pet's window may ask for; nothing else of the app's channel answers it. */
  let petCornerTimer: ReturnType<typeof setTimeout> | null = null;
  const handlePet = (message: { type?: string; open?: boolean; dx?: number; dy?: number; prompt?: string; thread?: unknown }): unknown => {
    switch (message.type) {
      case "pet/state":
        return petState();
      case "pet/expand":
        if (pet && !pet.isDestroyed()) fitPet(pet, petSize(), Boolean(message.open));
        if (message.open) {
          petStatus.seen();
          sendPet();
        }
        return null;
      case "pet/move": {
        if (!pet || pet.isDestroyed()) return null;
        const [x, y] = pet.getPosition();
        pet.setPosition(Math.round(x + Number(message.dx || 0)), Math.round(y + Number(message.dy || 0)));
        // Its place is kept (DESKTOP §16 B18): the bottom right corner, a moment after the drag stops.
        if (petCornerTimer) clearTimeout(petCornerTimer);
        petCornerTimer = setTimeout(() => {
          if (!pet || pet.isDestroyed()) return;
          const bounds = pet.getBounds();
          prefs.write({ petCorner: { right: bounds.x + bounds.width, bottom: bounds.y + bounds.height } });
        }, 400);
        return null;
      }
      case "pet/thread": {
        // A thread the bubble listed as needing the user.
        if (!petStatus.listed(message.thread)) return null;
        showThread(message.thread);
        return null;
      }
      case "pet/open": {
        const thread = petStatus.focus();
        if (thread) showThread(thread);
        else showView("ask");
        return null;
      }
      case "pet/ask":
        showView("ask", { cwd: null, thread: null, page: "ask", prompt: String(message.prompt ?? "").slice(0, 4000) });
        return null;
      case "pet/hide":
        prefs.write({ petShown: false });
        syncPet();
        return null;
      default:
        throw new Error("the pet asks for nothing else");
    }
  };

  // Quick Entry (quick.ts, DESKTOP_PARITY_CLAUDE §4.2): the box, made the first time it is asked for
  // and hidden, not closed, after; the system-wide shortcut while Settings › General has it on.
  let quick: BrowserWindow | null = null;
  let quickStatus: QuickStatus = { on: false, shortcut: DEFAULT_SHORTCUT, registered: false, problem: null };
  const hideQuick = () => {
    if (quick && !quick.isDestroyed() && quick.isVisible()) quick.hide();
  };
  const toggleQuick = () => {
    if (quick && !quick.isDestroyed() && quick.isVisible()) return hideQuick();
    if (!quick || quick.isDestroyed()) {
      quick = openQuick(path.join(__dirname, "preload.cjs"), shownTheme());
      quick.on("blur", hideQuick);
      quick.on("closed", () => (quick = null));
    }
    const size = quickSize(recentQuestions.list().length);
    quick.setResizable(true);
    quick.setContentSize(size.width, size.height);
    quick.setResizable(false);
    placeQuick(quick);
    if (show) {
      quick.show();
      quick.focus();
    }
    send(quick, { channel: "quick://open", payload: null });
  };
  const syncQuick = () => {
    const stored = prefs.read();
    globalShortcut.unregisterAll();
    const shortcut = typeof stored.quickEntryShortcut === "string" && stored.quickEntryShortcut ? stored.quickEntryShortcut : DEFAULT_SHORTCUT;
    quickStatus = { on: stored.quickEntry === true, shortcut, registered: false, problem: null };
    if (!quickStatus.on) return;
    const keys = accelerator(shortcut);
    if (!keys) {
      quickStatus.problem = `${shortcut} cannot be a system-wide shortcut: use Ctrl, Alt or Super with a letter, a digit, a function key or Space.`;
    } else if (!shortcutsWork()) {
      quickStatus.problem = "On a Wayland session an app cannot take a system-wide shortcut: bind `xdg-open ling://quick` to a key in your desktop's keyboard settings instead.";
    } else {
      try {
        quickStatus.registered = globalShortcut.register(keys, toggleQuick);
      } catch (error) {
        quickStatus.problem = error instanceof Error ? error.message : String(error);
      }
      if (!quickStatus.registered && !quickStatus.problem) quickStatus.problem = `${shortcut} is taken by another app or by the desktop: choose another.`;
    }
  };
  /** What the box may ask for; nothing else of the app's channel answers it. */
  const handleQuick = (message: { type?: string; prompt?: unknown; thread?: unknown }): unknown => {
    switch (message.type) {
      // The most recent Ask questions, and one of them opened (DESKTOP §16 B24): only a thread the box listed.
      case "quick/recent":
        return recentQuestions.list();
      case "quick/open": {
        if (!recentQuestions.listed(message.thread)) return null;
        hideQuick();
        showThread(message.thread as string);
        return null;
      }
      case "quick/ask": {
        const prompt = quickPrompt(message.prompt);
        hideQuick();
        if (!prompt) return null;
        // A new Ask question, sent at once, in the app window brought forward.
        showView("ask", { cwd: null, thread: null, page: "ask", prompt, send: true });
        return null;
      }
      case "quick/hide":
        hideQuick();
        return null;
      default:
        throw new Error("the quick question asks for nothing else");
    }
  };
  app.on("will-quit", () => globalShortcut.unregisterAll());

  /** Puts the skill and the copies' folder in place, or deletes both, as Settings › Memories says. */
  const syncRecall = () => {
    const home = codexHome();
    if (!home) return;
    try {
      if (prefs.read().recallQuestions === true) enableRecall(home);
      else if (recallOn(home) || fs.existsSync(path.join(home, "ask-history"))) disableRecall(home);
    } catch (error) {
      console.error(`ling-app: earlier questions: ${error instanceof Error ? error.message : String(error)}`);
    }
  };

  installMenu(actions);
  app.on("will-quit", () => session.flush());
  // `.vite/build/../../icons`: inside the asar when packaged, the project's folder otherwise.
  installTray(path.join(__dirname, "..", "..", "icons"), { ...actions, quickQuestion: toggleQuick });
  syncNight();
  setInterval(syncNight, 120_000).unref();

  // The one channel from the page.
  const fromView = new Reassembler();
  ipcMain.handle(CHANNEL_FROM_VIEW, async (event: IpcMainInvokeEvent, data: unknown) => {
    if (pet && !pet.isDestroyed() && event.sender.id === pet.webContents.id) return handlePet(data as { type?: string });
    if (quick && !quick.isDestroyed() && event.sender.id === quick.webContents.id) return handleQuick(data as { type?: string; prompt?: unknown; thread?: unknown });
    const own = windowFor(event.sender.id);
    if (!own) throw new Error("only the app's windows talk to the agent");
    let message = data;
    if (isChunk(data)) {
      message = fromView.take(data);
      if (message === undefined) return null;
    }
    // A refusal is an answer, not a crash: the page gets it as an error, the main process's log stays quiet.
    try {
      return await handle(message as FromView, own);
    } catch (error) {
      return { [FAILED]: error instanceof Error ? error.message : String(error) };
    }
  });
  ipcMain.on(CHANNEL_CHUNK_ACK, () => {});

  /**
   * Refuses what would reach the network for a thread at `/airgapped on` (push, the forge's page), or when `ling airgapped` did not answer.
   * `cwd` is the project's folder when there is one: `ling` reads that repository's own setting there, so a project's `on` is seen too.
   */
  const offline = async (thread: string | null, what: string, cwd: string | null = null) => {
    const ling = findLing(resourcesPath());
    const level = ling ? await airgappedLevel(ling, thread, cwd) : { level: "off" as const, source: "" };
    if (countsAsOn(level)) throw new Error(`airgapped is on (${level.source}): ${what} would reach the network`);
  };
  /** Preview tabs (preview.ts): their rules read Settings › Browser and the thread's air-gap level. */
  const previews = new Previews(
    (target, event) => send(target, { channel: "preview://event", payload: event }),
    () => {
      const stored = prefs.read();
      const sites = (typeof stored.previewSites === "object" && stored.previewSites !== null && !Array.isArray(stored.previewSites) ? stored.previewSites : {}) as Record<string, SiteChoice>;
      return { sites, devTools: stored.previewDevTools === true };
    },
    async (thread) => {
      const ling = findLing(resourcesPath());
      return ling ? countsAsOn(await airgapped(ling, thread)) : false;
    },
  );
  let editors: Editor[] | null = null;
  /** Settings › Node: the last list the window was given, whose choices alone it may send back. */
  const nodes = new Nodes();
  const tasks = new Tasks((event) => deliver({ channel: "task://event", payload: event }));
  /** The worktree root the page read from the agent's config, or `$CODEX_HOME/worktrees`. */
  const worktreeRoot = (root: string | null) => {
    if (root) return worktree.checkWorktreeRoot(root);
    const home = codexHome();
    if (!home) throw new Error("there is no CODEX_HOME for the worktrees");
    return worktree.defaultRoot(home);
  };
  /** A folder the page names for Open: absolute and existing. */
  // Asynchronous (userfs.ts): a folder macOS guards must not stop the main thread while its prompt waits.
  const folder = async (dir: unknown): Promise<string> => {
    if (typeof dir !== "string" || !path.isAbsolute(dir) || !(await userFs.isDirectory(dir))) throw new Error("not a folder");
    return dir;
  };

  /** MCP bundles picked and shown, not installed yet (DESKTOP §16 B45): the token the page holds, to the file and the name it was shown. */
  const pickedBundles = new Map<string, { file: string; name: string }>();

  async function handle(message: FromView, own: BrowserWindow): Promise<unknown> {
    switch (message.type) {
      case "work/start": {
        const started = server.start();
        syncHost?.start();
        return started;
      }
      case "work/send": {
        // Routed per window, refused when the thread's composer is in another window (windows.ts),
        // then vetted against the policy like every message (server.ts).
        const routed = hub.outgoing(String(own.webContents.id), message.message);
        if (routed.reply) send(own, { channel: "work://message", payload: routed.reply });
        if (routed.send !== null) await server.send(routed.send);
        // The page answered a request for the user (an approval): the pet stops waiting on it.
        const sent = message.message as { id?: unknown; method?: unknown; result?: unknown; error?: unknown };
        if (sent && sent.id !== undefined && sent.method === undefined && petStatus.answered(sent.id)) sendPet();
        return null;
      }
      case "window/popout":
        // The thread moves into a window of its own and takes its composer with it.
        openWindow({ kind: "popout", popout: String(message.thread) });
        return null;
      case "window/new":
        newWindow(own, message.view === "work" ? "work" : message.view === "ask" ? "ask" : undefined);
        return null;
      case "window/state":
        return hub.state(String(own.webContents.id));
      case "window/showing": {
        // The page says what it shows: the composer's owner may change, and the session keeps the view.
        const entry = appWindows.get(own.webContents.id);
        const view: View = message.view === "work" ? "work" : "ask";
        if (entry && entry.kind === "full" && entry.view !== view) {
          entry.view = view;
          if (options.audit === null) session.update(String(own.webContents.id), { view });
        }
        hub.show(String(own.webContents.id), typeof message.thread === "string" ? message.thread : null);
        sendWindows();
        return hub.state(String(own.webContents.id));
      }
      case "window/take":
        if (typeof message.thread !== "string" || hub.shown(String(own.webContents.id)) !== message.thread) throw new Error("a window takes the composer of the thread it shows");
        hub.show(String(own.webContents.id), message.thread, true);
        sendWindows();
        return hub.state(String(own.webContents.id));
      case "window/show-thread":
        showThread(typeof message.thread === "string" ? message.thread : null);
        return null;
      case "window/detach": {
        if (!PANE_KINDS.includes(message.pane) || typeof message.thread !== "string" || !message.thread) throw new Error("detach the terminal or the diff of a thread");
        if (appWindows.get(own.webContents.id)?.kind === "pane") throw new Error("a pane is already detached");
        detach(own, message.pane, message.thread);
        return null;
      }
      case "window/dock":
        if (!PANE_KINDS.includes(message.pane) || typeof message.thread !== "string") throw new Error("dock the terminal or the diff of a thread");
        dock(message.pane, message.thread);
        return null;
      case "pane/relay": {
        // A detached pane's request for the window it came from (a new terminal, text for the composer, a file to open).
        const entry = appWindows.get(own.webContents.id);
        const parent = entry?.pane ? appWindows.get(entry.pane.parent) : undefined;
        if (!entry?.pane || !parent || !alive(parent.win)) throw new Error("only a detached pane asks the window it came from");
        const action = paneAction(message.action, entry.pane.thread);
        send(parent.win, { channel: "pane://action", payload: action });
        return null;
      }
      case "terminal/list":
        return terminals.list();
      case "terminal/publish":
        if (terminals.publish(message.session)) sendTerminals();
        return null;
      case "terminal/forget":
        if (terminals.forget(message.processId)) sendTerminals();
        return null;
      case "terminal/backlog":
        return terminals.backlog(message.processId);
      case "window/always-on-top":
        own.setAlwaysOnTop(Boolean(message.on));
        return own.isAlwaysOnTop();
      case "app/home":
        return app.getPath("home");
      case "node/list": {
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        return nodes.list(ling);
      }
      case "node/use": {
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        return nodes.use(ling, message.choice);
      }
      case "night/list": {
        const home = codexHome();
        if (!home) return { tasks: [], reports: [] };
        const dir = path.join(home, "night");
        return { tasks: readTasks(dir), reports: readReports(dir) };
      }
      case "night/report": {
        const home = codexHome();
        if (!home) throw new Error("there is no CODEX_HOME");
        return readReport(path.join(home, "night", message.archived === true ? ARCHIVE : ""), message.name);
      }
      case "night/add": {
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        return addTask(ling, message.repo, message.task, message.test, message.on ?? null, message.bestOf ?? null);
      }
      case "night/status":
        return nightStatus(app.getPath("home"));
      case "night/drop": {
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        return dropTask(ling, message.repo, message.id);
      }
      case "night/every-add":
      case "night/every-list":
      case "night/every-drop": {
        // MIGHTLING_ADMIN names another ling-admin (the e2e's stand-in), as MIGHTLING_GH does for gh.
        const admin = process.env.MIGHTLING_ADMIN || whichOnPath("ling-admin");
        if (!admin) throw new Error("ling-admin is not on PATH: repeating tasks live where Night Shift runs, on the node");
        if (message.type === "night/every-list") return listRepeating(admin);
        if (message.type === "night/every-drop") return dropRepeating(admin, message.id);
        // Each cadence's parameter is checked in cadence.ts (cadenceArgs) before ling-admin runs.
        return addRepeating(admin, message.repo, message.task, message.test, message.cadence, { weekday: message.weekday, days: message.days, every: message.every, day: message.day });
      }
      case "night/details": {
        const home = codexHome();
        if (!home) throw new Error("there is no CODEX_HOME");
        return taskDetails(path.join(home, "night", message.archived === true ? ARCHIVE : ""), message.id);
      }
      case "night/apply": {
        // A thread's task brought into the thread's checkout (DESKTOP §16 B40): git here, no network.
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        return applyTask(ling, message.cwd, message.id);
      }
      case "night/note": {
        const home = codexHome();
        return home ? taskNote(path.join(home, "night"), message.id) : null;
      }
      case "night/archive": {
        // Scheduled's search of the archive (DESKTOP §16 B41): files here, read only.
        const home = codexHome();
        return home ? searchArchive(path.join(home, "night"), message.query) : { tasks: [], reports: [] };
      }
      case "night/run": {
        const admin = whichOnPath("ling-admin");
        if (!admin) throw new Error("ling-admin is not on PATH: Night Shift runs on the node");
        runNow(admin);
        return null;
      }
      case "notify/test":
        notify([own], { title: "Mightling", body: "Notifications reach you like this.", always: true }, () => own.show());
        return null;
      case "insights": {
        const home = codexHome();
        return home ? insights(home) : null;
      }
      case "personal/read": {
        const home = codexHome();
        return home ? { path: path.join(home, "AGENTS.md"), text: readPersonal(home) } : null;
      }
      case "personal/write": {
        const home = codexHome();
        if (!home) throw new Error("there is no CODEX_HOME");
        writePersonal(home, message.text);
        return null;
      }
      case "ask/upload":
        return server.upload(message);
      case "work/attach": {
        // A pasted image for a Work turn (DESKTOP §16 B13): under $CODEX_HOME, never the project.
        const home = codexHome();
        if (!home) throw new Error("ling's home folder is unknown: HOME is not set");
        return writeWorkAttachment(home, message);
      }
      case "ask/incognito":
        server.incognito("mark", String(message.thread));
        return null;
      case "ask/forget":
        return server.incognito("forget", String(message.thread));
      case "recall/index": {
        const home = codexHome();
        return home && recallOn(home) ? recallIndex(home) : {};
      }
      case "recall/write": {
        const home = codexHome();
        if (!home || prefs.read().recallQuestions !== true) throw new Error("searching earlier questions is off (Settings › Memories)");
        writeRecall(home, message.thread, message.updatedAt, message.markdown);
        return null;
      }
      case "recall/prune": {
        const home = codexHome();
        return home ? pruneRecall(home, message.keep) : 0;
      }
      case "voice/transcribe":
        return transcribe(message, fetch, prefs.read().dictationWords);
      case "work/stop":
        server.stop();
        return null;
      case "work/target":
        return appWindows.get(own.webContents.id)?.target ?? { cwd: null, thread: null };
      case "work/airgapped": {
        const ling = findLing(resourcesPath());
        return ling ? airgapped(ling, message.thread) : { level: "off", source: "ling is not installed" };
      }
      case "work/airgapped-default": {
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        return setDefault(ling, message.level);
      }
      case "context-menu":
        showContextMenu(own, message);
        return null;
      case "git/repos": {
        // The e2e's stand-in for a folder that answers slowly (a macOS access prompt): phase2.e2e.ts, "6 review pane".
        const delay = Number(process.env.MIGHTLING_TEST_GIT_REPOS_DELAY_MS ?? 0);
        if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
        return git.repositories(message.cwd);
      }
      case "git/missing":
        return git.gitToolsMissing();
      case "git/status":
        return git.status(await git.checkRoot(message.root));
      case "git/diff":
        return git.diff(await git.checkRoot(message.root), message.scope, message.commit ?? null);
      case "git/log":
        return git.log(await git.checkRoot(message.root));
      case "git/changes":
        return changedLines(await git.checkRoot(message.root));
      case "git/stage":
        return git.stage(await git.checkRoot(message.root), message.paths);
      case "git/unstage":
        return git.unstage(await git.checkRoot(message.root), message.paths);
      case "git/discard":
        return git.discard(await git.checkRoot(message.root), message.paths);
      case "git/apply":
        return git.apply(await git.checkRoot(message.root), message.patch, { cached: message.cached, reverse: message.reverse, index: message.index, zeroContext: message.zeroContext === true });
      case "git/commit":
        return git.commit(await git.checkRoot(message.root), message.message, Boolean(message.all));
      case "git/push": {
        const root = await git.checkRoot(message.root);
        await offline(message.thread, "a push", root);
        return git.push(root, Boolean(message.force));
      }
      case "open/url": {
        // A link in an answer, after the page has shown its full address and the user said open.
        const url = webUrl(message.url);
        if (!url) throw new Error("only a web address (http or https) opens in the browser");
        await offline(message.thread ?? null, "opening a link");
        await shell.openExternal(url);
        return url;
      }
      case "git/pull-request": {
        const root = await git.checkRoot(message.root);
        await offline(message.thread, "the forge's pull-request page", root);
        const url = await git.pullRequestUrl(root);
        await shell.openExternal(url);
        return url;
      }
      case "worktree/create": {
        const root = worktreeRoot(message.root);
        return worktree.create({ sourceCwd: await folder(message.sourceCwd), base: message.base, withChanges: Boolean(message.withChanges), worktreeRoot: root });
      }
      case "worktree/bind":
        return worktree.bindThread(await git.checkRoot(message.checkout), message.thread);
      case "worktree/list":
        return worktree.list(await folder(message.sourceCwd), worktreeRoot(message.root));
      case "worktree/remove":
        return worktree.remove(await folder(message.sourceCwd), message.checkout, worktreeRoot(message.root), message.snapshot);
      case "worktree/restore":
        return worktree.restore(await folder(message.sourceCwd), message.snapshot, worktreeRoot(message.root));
      case "worktree/branch":
        return worktree.createBranch(await git.checkRoot(message.checkout), message.name);
      case "worktree/handoff":
        return worktree.handOff(message.direction === "toLocal" ? "toLocal" : "toWorktree", message.worktree, message.local, worktreeRoot(message.root), message.thread);
      case "git/branches":
        return git.branches(await git.checkRoot(message.root));
      case "office/preview":
        // An office file shown as a PDF (office.ts, DESKTOP §16 B31): LibreOffice with no network, a time limit.
        return officePreview(message.path);
      case "env/list":
        return environments(await folder(message.project));
      case "env/write":
        return writeEnvironment(await folder(message.project), message.environment);
      case "task/run": {
        const project = await folder(message.project);
        const cwd = await folder(message.cwd);
        const environment = (await environments(project)).found.find((entry) => entry.file === message.file);
        if (!environment) throw new Error(`${project} has no environment ${message.file}`);
        const script = message.action === "setup" ? environment.setup : environment.actions[Number(message.action)]?.script;
        if (!script?.trim()) throw new Error("that environment has no such script");
        // The script runs in the project or in one of its worktrees, nowhere else.
        const common = async (dir: string) => userFs.pathKey(path.resolve(dir, (await git.git(dir, ["rev-parse", "--git-common-dir"])).trim()));
        if (!(await userFs.samePath(cwd, project)) && (await common(cwd).catch(() => "")) !== (await common(project).catch(() => "-"))) {
          throw new Error("a project's script runs in the project or one of its worktrees");
        }
        await offline(message.thread, "a project's own script (nothing sandboxes it)", cwd);
        const label = message.action === "setup" ? `Setup: ${environment.name}` : environment.actions[Number(message.action)].name;
        return tasks.run(label, script, cwd);
      }
      case "task/stop":
        tasks.stop(message.id);
        return null;
      case "open/terminal": {
        const dir = await folder(message.dir);
        const terminal = terminalCommand(dir);
        if (!terminal) throw new Error("no terminal emulator was found");
        git.launch(terminal.command, terminal.args, dir);
        return null;
      }
      case "open/editor": {
        const dir = await folder(message.dir);
        const editor = (editors ?? (editors = findEditors())).find((entry) => entry.id === message.editor) ?? editors[0];
        if (!editor) throw new Error("no editor was found; choose one in the review pane");
        const { command, args } = openCommand(editor, dir, null);
        git.launch(command, args, dir);
        return null;
      }
      case "docs/open": {
        // A citation of the user's documents: the id goes to `ling docs locate`, the file it names to the system.
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        const doc = await locate(ling, message.doc);
        const runs = await runsWhenOpened(doc);
        if (runs) throw new Error(runs);
        const failed = await shell.openPath(doc);
        if (failed) throw new Error(failed);
        return null;
      }
      case "docs/status":
      case "docs/add":
      case "docs/remove":
      case "docs/reindex": {
        // Settings › Your documents: every change goes through `ling docs`, which applies the index's rules.
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        if (message.type === "docs/status") return docsStatus(ling);
        if (message.type === "docs/add") return docsAdd(ling, message.folder);
        if (message.type === "docs/remove") return docsRemove(ling, message.name);
        return docsReindex(ling, message.name);
      }
      case "open/reveal": {
        // A file's place in the file manager (DESKTOP §16 B14): an absolute path to a file that exists.
        const file = typeof message.path === "string" && path.isAbsolute(message.path) ? path.normalize(message.path) : null;
        if (!file || !(await fs.promises.stat(file).then((entry) => entry.isFile() || entry.isDirectory(), () => false))) throw new Error("that file is not there");
        shell.showItemInFolder(file);
        return null;
      }
      case "memories/files":
      case "memories/save": {
        // Memories one by one (DESKTOP §16 B17): the consolidated summary and index, nothing else.
        const home = codexHome();
        if (!home) throw new Error("ling's home folder is unknown: HOME is not set");
        return message.type === "memories/files" ? listMemoryFiles(home) : writeMemoryFile(home, message.name, message.text);
      }
      case "mcpb/pick":
      case "mcpb/install":
      case "mcpb/remove":
      case "mcpb/list": {
        // MCP bundles from a file (mcpb.ts, DESKTOP §16 B45): the page names a file only by the token its pick
        // gave and a bundle only by its name; nothing is fetched, so this works at airgapped on, except a bundle
        // whose command is a package runner.
        const home = codexHome();
        if (!home) throw new Error("ling's home folder is unknown: HOME is not set");
        const places = { home: app.getPath("home"), desktop: app.getPath("desktop"), documents: app.getPath("documents"), downloads: app.getPath("downloads") };
        if (message.type === "mcpb/list") return mcpb.listBundles(home);
        if (message.type === "mcpb/remove") return mcpb.removeBundle(home, String(message.name)).then(() => null);
        if (message.type === "mcpb/pick") {
          const chosen = await dialog.showOpenDialog(own, { title: "Install an MCP bundle", properties: ["openFile"], filters: [{ name: "MCP bundles", extensions: ["mcpb"] }] });
          const file = chosen.canceled ? null : chosen.filePaths[0] ?? null;
          if (!file) return null;
          const { summary } = await mcpb.readBundle(file, home, places);
          const token = crypto.randomUUID();
          pickedBundles.set(token, { file, name: summary.name });
          for (const old of [...pickedBundles.keys()].slice(0, -8)) pickedBundles.delete(old);
          return { token, file: path.basename(file), summary };
        }
        const picked = pickedBundles.get(String(message.token));
        if (!picked) throw new Error("choose the bundle's file again");
        const values = typeof message.values === "object" && message.values !== null && !Array.isArray(message.values) ? message.values : {};
        // Checked on the command installBundle fills in from the file as it reads it then, not on an earlier read.
        const fetching = (command: string) => offline(null, `this bundle's command, ${command},`);
        const installed = await mcpb.installBundle(picked.file, home, places, { expected: picked.name, values, replace: message.replace === true, fetching });
        pickedBundles.delete(String(message.token));
        return installed;
      }
      case "folder/check":
        // A link's folder (DESKTOP §16 B19): taken only when it is an absolute folder that exists.
        return folder(message.dir).then(() => true, () => false);
      case "git/origin": {
        // The open projects' remotes, compared with a link's `originUrl` (DESKTOP §16 B19); asked only when a link names one.
        const root = await git.checkRoot(message.dir).catch(() => null);
        return root ? git.originUrl(root) : null;
      }
      case "open/folder": {
        const failed = await shell.openPath(await folder(message.dir));
        if (failed) throw new Error(failed);
        return null;
      }
      case "editors/list":
        editors = findEditors();
        return editors;
      case "editor/open": {
        const root = await git.checkRoot(message.root).catch(() => null);
        const base = root ?? (typeof message.root === "string" && path.isAbsolute(message.root) ? message.root : null);
        if (!base) throw new Error("open a file inside a project");
        const file = path.join(base, git.insidePath(base, message.path));
        const editor = (editors ?? (editors = findEditors())).find((entry) => entry.id === message.editor);
        if (!editor) {
          // The system's choice for the file's type, unless the system would run it (editors.ts).
          const runs = await runsWhenOpened(file);
          if (runs) throw new Error(runs);
          const failed = await shell.openPath(file);
          if (failed) throw new Error(failed);
          return null;
        }
        const { command, args } = openCommand(editor, file, message.line);
        git.launch(command, args, base);
        return null;
      }
      case "preview/open":
        await previews.open(own, String(message.id), String(message.url), message.thread ?? null);
        return null;
      case "preview/navigate":
        await previews.navigate(String(message.id), String(message.url), message.thread ?? null);
        return null;
      case "preview/go":
        previews.go(String(message.id), message.step === "forward" ? "forward" : "back");
        return null;
      case "preview/reload":
        previews.reload(String(message.id), message.fresh === true);
        return null;
      case "preview/place":
        previews.place(String(message.id), message.bounds, Boolean(message.visible));
        return null;
      case "preview/pick":
        return previews.pick(String(message.id));
      case "preview/allow-once":
        previews.allowOnce(String(message.id), String(message.site));
        return null;
      case "preview/devtools":
        previews.devTools(String(message.id));
        return null;
      case "preview/console":
        return previews.consoles(typeof message.thread === "string" ? message.thread : null);
      case "preview/close":
        previews.close(String(message.id));
        return null;
      case "preview/clear":
        await previews.clear();
        return null;
      case "file/size": {
        if (typeof message.path !== "string" || !path.isAbsolute(message.path)) throw new Error("not an absolute path");
        const stat = await userFs.statOrNull(message.path).catch(() => null);
        return stat?.isFile() ? stat.size : null;
      }
      case "prefs/read":
        return prefs.read();
      case "prefs/write": {
        const written = prefs.write(message.values);
        if (["petShown", "petSize", "petImage"].some((key) => key in (message.values ?? {}))) syncPet();
        if ("recallQuestions" in (message.values ?? {})) syncRecall();
        if (["nightSeen", "nightRead"].some((key) => key in (message.values ?? {}))) syncNight();
        if (["quickEntry", "quickEntryShortcut"].some((key) => key in (message.values ?? {}))) syncQuick();
        if ("theme" in (message.values ?? {})) {
          nativeTheme.themeSource = themeChoice(prefs.read().theme);
          paintTheme();
        }
        return written;
      }
      case "quick/status":
        return quickStatus;
      case "window/minimize":
        own.minimize();
        return null;
      case "window/maximize":
        if (own.isMaximized()) own.unmaximize();
        else own.maximize();
        return null;
      case "window/close":
        own.close();
        return null;
      case "window/fullscreen":
        own.setFullScreen(!own.isFullScreen());
        return null;
      case "app/quit":
        app.quit();
        return null;
      case "app/relaunch":
        app.relaunch();
        app.quit();
        return null;
      case "pr/list":
      case "pr/view":
      case "pr/diff":
      case "pr/review":
      case "pr/activity":
      case "pr/create":
        // Off unless Settings › Git turned it on; never at airgapped on (it is GitHub, over the network).
        return prs.handle(message, { enabled: prefs.read().pullRequests === true, offline: async (what) => offline(message.thread ?? null, what, await git.checkRoot(message.root)), checkRoot: git.checkRoot });
      case "board/read": {
        const home = codexHome();
        if (!home) return [];
        return readBoards(boardFile(home, typeof message.sqliteHome === "string" ? message.sqliteHome : null));
      }
      case "pr/status": {
        if (prefs.read().pullRequests !== true) return "The pull-request tab is off.";
        return prs.status(await git.checkRoot(message.root));
      }
      case "dialog/open-image": {
        const chosen = await dialog.showOpenDialog(own, { title: "A picture for the pet", properties: ["openFile"], filters: [{ name: "Pictures", extensions: ["png", "gif", "webp", "svg", "jpg", "jpeg"] }] });
        return chosen.canceled ? null : chosen.filePaths[0] ?? null;
      }
      case "project/knowledge-list":
      case "project/knowledge-add":
      case "project/knowledge-remove": {
        const home = codexHome();
        if (!home) throw new Error("Mightling's home folder is not known");
        const projectId = String(message.projectId);
        if (message.type === "project/knowledge-list") return knowledge.listKnowledge(home, projectId);
        // The knowledge as a collection the agent searches (DESKTOP_PARITY_CLAUDE §4.7), kept in step in the background.
        const searchable = (files: knowledge.KnowledgeFile[]) => {
          const ling = findLing(resourcesPath());
          if (ling) {
            syncKnowledge(ling, knowledge.knowledgeFolder(home, projectId), knowledge.knowledgeCollection(projectId), files.length)
              .catch((error: unknown) => console.error(`ling-app: the knowledge of ${projectId} is read as files only: ${error instanceof Error ? error.message : String(error)}`));
          }
          return files;
        };
        if (message.type === "project/knowledge-remove") return searchable(await knowledge.removeKnowledge(home, projectId, String(message.name)));
        const chosen = await dialog.showOpenDialog(own, { title: "Add knowledge files to the project", properties: ["openFile", "multiSelections"] });
        if (chosen.canceled || chosen.filePaths.length === 0) return knowledge.listKnowledge(home, projectId);
        return searchable(await knowledge.addKnowledge(home, projectId, chosen.filePaths));
      }
      case "dialog/save-text": {
        // Only a file name is suggested (no folder from the page); the user picks where it goes.
        const name = path.basename(String(message.name)) || "thread.md";
        const chosen = await dialog.showSaveDialog(own, { title: "Export the thread", defaultPath: path.join(app.getPath("documents"), name) });
        if (chosen.canceled || !chosen.filePath) return null;
        await fs.promises.writeFile(chosen.filePath, String(message.text), "utf8");
        return chosen.filePath;
      }
      case "dialog/open-folder": {
        const chosen = await dialog.showOpenDialog(own, { title: "Open a project folder", properties: ["openDirectory"] });
        return chosen.canceled ? null : chosen.filePaths[0] ?? null;
      }
      default:
        throw new Error(`the app's windows do not send ${(message as { type: string }).type}`);
    }
  }

  // The system's theme changed, or the preference did (themeSource): every window repaints its own
  // background and the overlay's symbols, and each page, the pet's included, is told (theme.ts).
  // Electron on Linux does not always pass a system change on to the page's media query, so the
  // message, not the query, is what the page follows.
  nativeTheme.on("updated", paintTheme);

  // A second instance (the single-instance lock hands it over), or a `ling://` link: show what it
  // asked for in the most recent window, or in a new one when none is open; never a second app.
  app.on("second-instance", (_event, argv, _cwd, data) => {
    const extra = (data as { argv?: string[] } | undefined)?.argv ?? argv.slice(1);
    const asked = require("./args").workTarget(extra) as WorkTarget | null;
    // `ling://quick`: the box, as the shortcut opens it (a Wayland desktop's own key binding).
    if (asked?.page === "quick") return toggleQuick();
    if (asked) showView("work", asked);
    else showView("ask");
  });
  app.on("open-url", (event, url) => {
    event.preventDefault();
    const asked = require("./args").linkTarget(url) as WorkTarget;
    if (asked.page === "quick") return toggleQuick();
    showView("work", asked);
  });
  app.on("window-all-closed", () => {
    tasks.stopAll();
    server.stop();
    app.quit();
  });
  app.on("before-quit", () => {
    syncHost?.stop();
    server.stop();
  });
  // Incognito questions open when the app quits, or left by one that crashed, are deleted
  // (DESKTOP_PARITY_CLAUDE §4.3); never in the audit's session, which touches no question.
  const sweep = () => {
    try {
      server.incognito("sweep");
    } catch {
      // No home folder: no Ask folders either.
    }
  };
  if (options.audit === null) {
    sweep();
    app.on("will-quit", sweep);
  }
  // Node's own teardown, after the last window and before the process ends, waits for every file-system
  // request still pending, and a read a macOS folder-access prompt holds never ends: the main process
  // stayed alive, windowless, after SIGTERM (1.7.0 Mac test), past the fallback below. With such a
  // read pending, the app ends here, at the last moment JavaScript runs, without that teardown.
  const hardExit = () => process.kill(process.pid, "SIGKILL");
  app.on("will-quit", () => {
    if (userFs.pendingCalls() > 0) hardExit();
  });
  // A SIGTERM (logging out, `kill`, a service manager) or a Ctrl+C in the terminal that started the
  // app quits it as the menu's Quit does, the agent's server and the tasks with it; a quit that has
  // not finished within five seconds (on a Mac one stopped the server and left the window open)
  // ends the app regardless.
  for (const signal of ["SIGTERM", "SIGINT"] as const) {
    process.on(signal, () => {
      tasks.stopAll();
      server.stop();
      setTimeout(() => (userFs.pendingCalls() > 0 ? hardExit() : app.exit(0)), 5_000);
      app.quit();
    });
  }

  // The e2e's stand-in for a folder-access prompt nobody answers: a read of a FIFO no one writes,
  // which holds a libuv worker for good (app.e2e.ts, "quits while a read hangs").
  if (process.env.MIGHTLING_TEST_PENDING_READ) void userFs.readText(process.env.MIGHTLING_TEST_PENDING_READ).catch(() => {});

  // The windows open when the app last quit, where they were (window-state.ts), then the one
  // `ling app` asked for: no argument opens Ask, a folder, a thread or `--work` open Work, in the most
  // recent window. The audit's session opens one hidden window and no other.
  const restored = options.audit === null ? session.restored() : [];
  for (const [index, saved] of restored.entries()) {
    const last = index === restored.length - 1;
    openWindow({ kind: "full", view: last ? (options.work ? "work" : "ask") : saved.view, bounds: saved.bounds, maximized: saved.maximized, target: last ? options.work ?? undefined : undefined });
  }
  if (restored.length === 0) showView(options.work ? "work" : "ask", options.work ?? undefined);
  // The pet, when Settings › Pets has it on (never in the egress audit's session).
  if (options.audit === null) syncPet();
  // Earlier questions (recall.ts): the skill and the copies' folder as Settings › Memories says.
  if (options.audit === null) syncRecall();
  // Quick Entry's shortcut, when Settings › General has it on (never in the audit's session).
  if (options.audit === null) syncQuick();
  if (options.audit !== null) {
    try {
      server.start();
    } catch (error) {
      console.error(`ling-app: ${error instanceof Error ? error.message : String(error)}`);
    }
    // The preview tab under audit: the audit's page in a preview, laid over the (hidden) window as a tab would be.
    const page = auditPreview();
    const host = recent(["full"]);
    if (page && host) {
      void previews.open(host, "audit", page, null).then(() => previews.place("audit", { x: 0, y: 0, width: 800, height: 600 }, true))
        .catch((error) => console.error(`ling-app: the audit's preview: ${error instanceof Error ? error.message : String(error)}`));
    }
    setTimeout(() => app.quit(), options.audit * 1000);
  }
}

/** An executable on PATH, or null. */
function whichOnPath(name: string): string | null {
  for (const dir of (process.env.PATH ?? "").split(path.delimiter)) {
    if (!dir) continue;
    const candidate = path.join(dir, name);
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      // Not here.
    }
  }
  return null;
}
