// The app window and the one IPC channel between its page and this process
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3). Every message from the page is checked to
// come from that window before it is acted on. The app window (`app://`) shows Ask and Work, as
// `ling web` does: this process enforces the same policy (policy.ts) and keeps the Ask folders
// (ask.ts). It is the app's only window, and its app-server the app's only one: the menu's Ask,
// the tray's and `ling app` alone show Ask in it. Until 2026-10-09 they opened a second window on
// `ling web`, whose own app-server shared `~/.mightling` with this one
// (specs/DREAMFERENCE_MIGHTLING_ASK.md §18.6).

import fs from "node:fs";
import path from "node:path";
import * as knowledge from "./knowledge";
import * as userFs from "./userfs";
import { BrowserWindow, app, dialog, globalShortcut, ipcMain, nativeTheme, shell, type IpcMainInvokeEvent } from "electron";

import { CHANNEL_CHUNK_ACK, CHANNEL_FOR_VIEW, CHANNEL_FROM_VIEW, FAILED, Reassembler, chunked, isChunk, type ForView, type FromView } from "./api";
import { airgapped, setDefault } from "./airgapped";
import { cavemodeSet, cavemodeStatus } from "./cavemode";
import { serve } from "./app-protocol";
import { auditPreview, type WorkTarget } from "./args";
import { webUrl } from "./links";
import { codexHome, findLing } from "./bridge";
import { findEditors, openCommand, terminalCommand, type Editor } from "./editors";
import { environments, writeEnvironment } from "./environments";
import * as git from "./git";
import * as prs from "./prs";
import { boardFile, readBoards } from "./board";
import { Prefs } from "./prefs";
import { AppServer } from "./server";
import { insights, readPersonal, writePersonal } from "./insights";
import { addTask, dropTask, readReport, readReports, readTasks, runNow } from "./night";
import { Nodes } from "./nodes";
import { docsAdd, docsReindex, docsRemove, docsStatus, locate, syncKnowledge } from "./docs";
import { Tasks } from "./tasks";
import { RequestRouter } from "./windows";
import * as worktree from "./worktree";
import { installMenu, installTray, keepAwake, notify, showContextMenu } from "./shell";
import { Notices } from "./notices";
import { transcribe } from "./voice";
import { Previews } from "./preview";
import { disableRecall, enableRecall, pruneRecall, recallIndex, recallOn, writeRecall } from "./recall";
import { PetStatus, fitPet, openPet, petImage, type PetSize, type PetState } from "./pet";
import { DEFAULT_SHORTCUT, accelerator, openQuick, placeQuick, quickPrompt, shortcutsWork, type QuickStatus } from "./quick";
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

const resourcesPath = () => (app.isPackaged ? process.resourcesPath : null);

export async function main(options: MainOptions): Promise<void> {
  // The page is the built renderer beside this bundle, packaged or not: app:// serves files,
  // never Vite's dev server.
  serve(path.join(__dirname, "..", "renderer", "main_window"));

  let window: BrowserWindow | null = null;
  let target: WorkTarget = options.work ?? { cwd: null, thread: null };
  // Hidden for the audit's session and for an end-to-end test (`MIGHTLING_HIDDEN=1`).
  const show = options.audit === null && !process.env.MIGHTLING_HIDDEN;

  const prefs = new Prefs(app.getPath("userData"));
  // The theme (theme.ts): the preference is nativeTheme's source before any window opens, so the
  // first paint, the native menus and the page's media query all have it.
  nativeTheme.themeSource = themeChoice(prefs.read().theme);
  const shownTheme = (): Theme => (nativeTheme.shouldUseDarkColors ? "dark" : "light");
  const notices = new Notices();
  /** Pop-outs: a thread in a window of its own, on the same app-server (windows.ts). */
  const popouts = new Map<number, BrowserWindow>();
  const router = new RequestRouter();
  const windows = () => [window, ...popouts.values()].filter((entry): entry is BrowserWindow => entry !== null && !entry.isDestroyed());
  /** The app window or a pop-out the sender is, or null for anything else. */
  const windowFor = (senderId: number) => windows().find((entry) => entry.webContents.id === senderId) ?? null;
  const send = (target: BrowserWindow, message: ForView) => {
    const contents = target.webContents;
    if (contents.isDestroyed()) return;
    for (const piece of chunked(message)) contents.send(CHANNEL_FOR_VIEW, piece);
  };
  const deliver = (message: ForView) => {
    if (message.channel === "work://message") {
      // A finished turn, an approval or a question waiting: a notification, as Settings › Notifications says;
      // a click shows the thread.
      const notice = notices.take(message.payload, () => prefs.read());
      if (notice) {
        notify(windows(), notice, () => {
          showView("work");
          if (notice.threadId) deliver({ channel: "link", payload: { cwd: null, thread: notice.threadId } });
        });
      }
      if (petStatus.observe(message.payload)) sendPet();
      const routed = router.incoming(message.payload);
      for (const target of windows()) {
        if (routed.to === "all" || String(target.webContents.id) === routed.to) send(target, { channel: "work://message", payload: routed.payload });
      }
      return;
    }
    if (message.channel === "work://exit") {
      router.reset();
      petStatus.reset();
      sendPet();
    }
    // The menu's view and a link are for the app window; the rest for every window.
    const targets = message.channel === "view" || message.channel === "link" ? [window].filter((entry): entry is BrowserWindow => entry !== null && !entry.isDestroyed()) : windows();
    for (const target of targets) send(target, message);
  };
  const server = new AppServer(resourcesPath(), {
    deliver,
    busyChanged: (busy) => {
      // Settings › General and Notifications (prefs.json): prevent sleep, and when a finished turn notifies.
      keepAwake(busy && prefs.read().preventSleep !== false);
    },
  });

  /** Brings the app window forward on `view`, opening it when it is not open. */
  const showView = (view: View) => {
    if (window && !window.isDestroyed()) {
      if (window.isMinimized()) window.restore();
      if (show) {
        window.show();
        window.focus();
      }
      deliver({ channel: "view", payload: view });
      return;
    }
    window = openWork({ preload: path.join(__dirname, "preload.cjs"), show, view, theme: shownTheme() });
    window.on("closed", () => (window = null));
    // Settings › General: closing the window can leave the app in the tray (DESKTOP_PARITY_CLAUDE §4.2),
    // so Quick Entry and running turns go on; off by default, and never in the audit's session. Quit
    // (Ctrl+Q, the tray, the menu, a signal) still ends everything.
    window.on("close", (event) => {
      if (quitting || options.audit !== null || prefs.read().keepInTray !== true) return;
      event.preventDefault();
      window?.hide();
    });
  };
  let quitting = false;
  app.on("before-quit", () => (quitting = true));
  const actions = { openAsk: () => showView("ask"), openWork: () => showView("work") };

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
  let pet: BrowserWindow | null = null;
  let petPicture: { file: unknown; data: string | null } = { file: null, data: null };
  const petSize = (): PetSize => {
    const size = prefs.read().petSize;
    return size === "small" || size === "large" ? size : "medium";
  };
  const petState = (): PetState => {
    const file = prefs.read().petImage;
    if (file !== petPicture.file) petPicture = { file, data: petImage(file) };
    return { ...petStatus.counts(), size: petSize(), image: petPicture.data };
  };
  function sendPet(): void {
    if (pet && !pet.isDestroyed()) send(pet, { channel: "pet://state", payload: petState() });
  }
  const syncPet = () => {
    const wanted = prefs.read().petShown === true;
    if (wanted && (!pet || pet.isDestroyed())) {
      pet = openPet(path.join(__dirname, "preload.cjs"), petSize(), show, shownTheme());
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
  const handlePet = (message: { type?: string; open?: boolean; dx?: number; dy?: number; prompt?: string }): unknown => {
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
        return null;
      }
      case "pet/open": {
        const thread = petStatus.focus();
        showView(thread ? "work" : "ask");
        if (thread) deliver({ channel: "link", payload: { cwd: null, thread } });
        return null;
      }
      case "pet/ask":
        showView("ask");
        deliver({ channel: "link", payload: { cwd: null, thread: null, page: "ask", prompt: String(message.prompt ?? "").slice(0, 4000) } });
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
  const handleQuick = (message: { type?: string; prompt?: unknown }): unknown => {
    switch (message.type) {
      case "quick/ask": {
        const prompt = quickPrompt(message.prompt);
        hideQuick();
        if (!prompt) return null;
        // A new Ask question, sent at once, in the app window brought forward.
        showView("ask");
        deliver({ channel: "link", payload: { cwd: null, thread: null, page: "ask", prompt, send: true } });
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
  // `.vite/build/../../icons`: inside the asar when packaged, the project's folder otherwise.
  installTray(path.join(__dirname, "..", "..", "icons"), { ...actions, quickQuestion: toggleQuick });

  // The one channel from the page.
  const fromView = new Reassembler();
  ipcMain.handle(CHANNEL_FROM_VIEW, async (event: IpcMainInvokeEvent, data: unknown) => {
    if (pet && !pet.isDestroyed() && event.sender.id === pet.webContents.id) return handlePet(data as { type?: string });
    if (quick && !quick.isDestroyed() && event.sender.id === quick.webContents.id) return handleQuick(data as { type?: string });
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

  /** Refuses what would reach the network for a thread at `/airgapped on` (push, the forge's page). */
  const offline = async (thread: string | null, what: string) => {
    const ling = findLing(resourcesPath());
    const level = ling ? await airgapped(ling, thread) : { level: "off", source: "" };
    if (level.level === "on") throw new Error(`airgapped is on (${level.source}): ${what} would reach the network`);
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
      return ling ? (await airgapped(ling, thread)).level === "on" : false;
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

  async function handle(message: FromView, own: BrowserWindow): Promise<unknown> {
    switch (message.type) {
      case "work/start":
        return server.start();
      case "work/send": {
        // The page answering a request for the user (an approval): the pet stops waiting on it.
        const sent = message.message as { id?: unknown; method?: unknown; result?: unknown; error?: unknown };
        if (sent && sent.id !== undefined && sent.method === undefined && petStatus.answered(sent.id)) sendPet();
        const routed = router.outgoing(String(own.webContents.id), message.message);
        if (routed.reply) send(own, { channel: "work://message", payload: routed.reply });
        if (routed.send !== null) await server.send(routed.send);
        return null;
      }
      case "window/popout": {
        const popout = openWork({ preload: path.join(__dirname, "preload.cjs"), show, popout: message.thread, theme: shownTheme() });
        const id = popout.webContents.id;
        popouts.set(id, popout);
        popout.on("closed", () => popouts.delete(id));
        return null;
      }
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
        return readReport(path.join(home, "night"), message.name);
      }
      case "night/add": {
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        return addTask(ling, message.repo, message.task, message.test);
      }
      case "night/drop": {
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        return dropTask(ling, message.repo, message.id);
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
        return transcribe(message);
      case "work/stop":
        server.stop();
        return null;
      case "work/target":
        return target;
      case "work/airgapped": {
        const ling = findLing(resourcesPath());
        return ling ? airgapped(ling, message.thread) : { level: "off", source: "ling is not installed" };
      }
      case "work/airgapped-default": {
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        return setDefault(ling, message.level);
      }
      case "cave/status":
      case "cave/set": {
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        return message.type === "cave/status" ? cavemodeStatus(ling, message.thread) : cavemodeSet(ling, message.level, message.thread);
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
      case "git/stage":
        return git.stage(await git.checkRoot(message.root), message.paths);
      case "git/unstage":
        return git.unstage(await git.checkRoot(message.root), message.paths);
      case "git/discard":
        return git.discard(await git.checkRoot(message.root), message.paths);
      case "git/apply":
        return git.apply(await git.checkRoot(message.root), message.patch, { cached: message.cached, reverse: message.reverse, index: message.index });
      case "git/commit":
        return git.commit(await git.checkRoot(message.root), message.message, Boolean(message.all));
      case "git/push": {
        const root = await git.checkRoot(message.root);
        await offline(message.thread, "a push");
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
        await offline(message.thread, "the forge's pull-request page");
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
        await offline(message.thread, "a project's own script (nothing sandboxes it)");
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
        const failed = await shell.openPath(await locate(ling, message.doc));
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
          // The system's choice for the file's type.
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
      case "pr/review": {
        // Off unless Settings › Git turned it on; never at airgapped on (it is GitHub, over the network).
        if (prefs.read().pullRequests !== true) throw new Error("the pull-request tab is off: turn it on in Settings › Git");
        await offline(message.thread ?? null, "the pull-request tab");
        const root = await git.checkRoot(message.root);
        if (message.type === "pr/list") return prs.list(root, message.inbox);
        if (message.type === "pr/view") return prs.view(root, message.number);
        if (message.type === "pr/diff") return prs.diff(root, message.number);
        return prs.review(root, message.number, message.verdict, message.body);
      }
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
      case "dialog/open-folder": {
        const chosen = await dialog.showOpenDialog(own, { title: "Open a project folder", properties: ["openDirectory"] });
        return chosen.canceled ? null : chosen.filePaths[0] ?? null;
      }
      default:
        throw new Error(`the app window does not send ${(message as { type: string }).type}`);
    }
  }

  // The system's theme changed, or the preference did (themeSource): every window repaints its own
  // background and the overlay's symbols, and each page, the pet's included, is told (theme.ts).
  // Electron on Linux does not always pass a system change on to the page's media query, so the
  // message, not the query, is what the page follows.
  nativeTheme.on("updated", paintTheme);

  // A second instance, or a `mightling://` link: show what it asked for, in the one window.
  app.on("second-instance", (_event, argv, _cwd, data) => {
    const extra = (data as { argv?: string[] } | undefined)?.argv ?? argv.slice(1);
    const asked = require("./args").workTarget(extra) as WorkTarget | null;
    // `ling://quick`: the box, as the shortcut opens it (a Wayland desktop's own key binding).
    if (asked?.page === "quick") return toggleQuick();
    if (asked) {
      target = asked;
      showView("work");
      deliver({ channel: "link", payload: target });
    } else showView("ask");
  });
  app.on("open-url", (event, url) => {
    event.preventDefault();
    const asked = require("./args").linkTarget(url) as WorkTarget;
    if (asked.page === "quick") return toggleQuick();
    target = asked;
    showView("work");
    deliver({ channel: "link", payload: target });
  });
  app.on("window-all-closed", () => {
    tasks.stopAll();
    server.stop();
    app.quit();
  });
  app.on("before-quit", () => server.stop());
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

  showView(options.work ? "work" : "ask");
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
    if (page && window) {
      const host = window;
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
