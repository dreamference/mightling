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
import { BrowserWindow, app, dialog, ipcMain, shell, type IpcMainInvokeEvent } from "electron";

import { CHANNEL_CHUNK_ACK, CHANNEL_FOR_VIEW, CHANNEL_FROM_VIEW, Reassembler, chunked, isChunk, type ForView, type FromView } from "./api";
import { airgapped, setDefault } from "./airgapped";
import { serve } from "./app-protocol";
import type { WorkTarget } from "./args";
import { codexHome, findLing } from "./bridge";
import { findEditors, openCommand, terminalCommand, type Editor } from "./editors";
import { environments, writeEnvironment } from "./environments";
import * as git from "./git";
import { Prefs } from "./prefs";
import { AppServer } from "./server";
import { insights, readPersonal, writePersonal } from "./insights";
import { addTask, dropTask, readReport, readReports, readTasks, runNow } from "./night";
import { Tasks } from "./tasks";
import { RequestRouter } from "./windows";
import * as worktree from "./worktree";
import { installMenu, installTray, keepAwake, notifyTurnDone, showContextMenu } from "./shell";
import { openWork } from "./work";

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
      // An approval or a question waiting: a notification when the window is not in front.
      const payload = message.payload as { id?: unknown; method?: unknown } | null;
      if (payload && payload.id !== undefined && typeof payload.method === "string" && /requestApproval|requestUserInput|Approval$/.test(payload.method) && prefs.read().notifyApprovals !== false) {
        notifyTurnDone(window, "Mightling is waiting for your answer.", false);
      }
      const routed = router.incoming(message.payload);
      for (const target of windows()) {
        if (routed.to === "all" || String(target.webContents.id) === routed.to) send(target, { channel: "work://message", payload: routed.payload });
      }
      return;
    }
    if (message.channel === "work://exit") router.reset();
    // The menu's view and a link are for the app window; the rest for every window.
    const targets = message.channel === "view" || message.channel === "link" ? [window].filter((entry): entry is BrowserWindow => entry !== null && !entry.isDestroyed()) : windows();
    for (const target of targets) send(target, message);
  };
  const server = new AppServer(resourcesPath(), {
    deliver,
    busyChanged: (busy) => {
      // Settings › General and Notifications (prefs.json): prevent sleep, and when a finished turn notifies.
      const chosen = prefs.read();
      keepAwake(busy && chosen.preventSleep !== false);
      if (!busy && chosen.notifyTurns !== "never") notifyTurnDone(window, "The agent finished a turn.", chosen.notifyTurns === "always");
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
    window = openWork({ preload: path.join(__dirname, "preload.cjs"), show, view });
    window.on("closed", () => (window = null));
  };
  const actions = { openAsk: () => showView("ask"), openWork: () => showView("work") };

  installMenu(actions);
  // `.vite/build/../../icons`: inside the asar when packaged, the project's folder otherwise.
  installTray(path.join(__dirname, "..", "..", "icons"), actions);

  // The one channel from the page.
  const fromView = new Reassembler();
  ipcMain.handle(CHANNEL_FROM_VIEW, async (event: IpcMainInvokeEvent, data: unknown) => {
    const own = windowFor(event.sender.id);
    if (!own) throw new Error("only the app's windows talk to the agent");
    let message = data;
    if (isChunk(data)) {
      message = fromView.take(data);
      if (message === undefined) return null;
    }
    return handle(message as FromView, own);
  });
  ipcMain.on(CHANNEL_CHUNK_ACK, () => {});

  /** Refuses what would reach the network for a thread at `/airgapped on` (push, the forge's page). */
  const offline = async (thread: string | null, what: string) => {
    const ling = findLing(resourcesPath());
    const level = ling ? await airgapped(ling, thread) : { level: "off", source: "" };
    if (level.level === "on") throw new Error(`airgapped is on (${level.source}): ${what} would reach the network`);
  };
  let editors: Editor[] | null = null;
  const tasks = new Tasks((event) => deliver({ channel: "task://event", payload: event }));
  /** The worktree root the page read from the agent's config, or `$CODEX_HOME/worktrees`. */
  const worktreeRoot = (root: string | null) => {
    if (root) return worktree.checkWorktreeRoot(root);
    const home = codexHome();
    if (!home) throw new Error("there is no CODEX_HOME for the worktrees");
    return worktree.defaultRoot(home);
  };
  /** A folder the page names for Open: absolute and existing. */
  const folder = (dir: unknown) => {
    if (typeof dir !== "string" || !path.isAbsolute(dir) || !fs.statSync(dir, { throwIfNoEntry: false })?.isDirectory()) throw new Error("not a folder");
    return dir;
  };

  async function handle(message: FromView, own: BrowserWindow): Promise<unknown> {
    switch (message.type) {
      case "work/start":
        return server.start();
      case "work/send": {
        const routed = router.outgoing(String(own.webContents.id), message.message);
        if (routed.reply) send(own, { channel: "work://message", payload: routed.reply });
        if (routed.send !== null) await server.send(routed.send);
        return null;
      }
      case "window/popout": {
        const popout = openWork({ preload: path.join(__dirname, "preload.cjs"), show, popout: message.thread });
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
      case "context-menu":
        showContextMenu(own, message);
        return null;
      case "git/repos":
        return git.repositories(message.cwd);
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
      case "git/pull-request": {
        const root = await git.checkRoot(message.root);
        await offline(message.thread, "the forge's pull-request page");
        const url = await git.pullRequestUrl(root);
        await shell.openExternal(url);
        return url;
      }
      case "worktree/create": {
        const root = worktreeRoot(message.root);
        return worktree.create({ sourceCwd: folder(message.sourceCwd), base: message.base, withChanges: Boolean(message.withChanges), worktreeRoot: root });
      }
      case "worktree/bind":
        return worktree.bindThread(await git.checkRoot(message.checkout), message.thread);
      case "worktree/list":
        return worktree.list(folder(message.sourceCwd), worktreeRoot(message.root));
      case "worktree/remove":
        return worktree.remove(folder(message.sourceCwd), message.checkout, worktreeRoot(message.root), message.snapshot);
      case "worktree/restore":
        return worktree.restore(folder(message.sourceCwd), message.snapshot, worktreeRoot(message.root));
      case "worktree/branch":
        return worktree.createBranch(await git.checkRoot(message.checkout), message.name);
      case "worktree/handoff":
        return worktree.handOff(message.direction === "toLocal" ? "toLocal" : "toWorktree", message.worktree, message.local, worktreeRoot(message.root), message.thread);
      case "git/branches":
        return git.branches(await git.checkRoot(message.root));
      case "env/list":
        return environments(folder(message.project));
      case "env/write":
        return writeEnvironment(folder(message.project), message.environment);
      case "task/run": {
        const project = folder(message.project);
        const cwd = folder(message.cwd);
        const environment = environments(project).found.find((entry) => entry.file === message.file);
        if (!environment) throw new Error(`${project} has no environment ${message.file}`);
        const script = message.action === "setup" ? environment.setup : environment.actions[Number(message.action)]?.script;
        if (!script?.trim()) throw new Error("that environment has no such script");
        // The script runs in the project or in one of its worktrees, nowhere else.
        const common = async (dir: string) => fs.realpathSync(path.resolve(dir, (await git.git(dir, ["rev-parse", "--git-common-dir"])).trim()));
        if (fs.realpathSync(cwd) !== fs.realpathSync(project) && (await common(cwd).catch(() => "")) !== (await common(project).catch(() => "-"))) {
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
        const dir = folder(message.dir);
        const terminal = terminalCommand(dir);
        if (!terminal) throw new Error("no terminal emulator was found");
        git.launch(terminal.command, terminal.args, dir);
        return null;
      }
      case "open/editor": {
        const dir = folder(message.dir);
        const editor = (editors ?? (editors = findEditors())).find((entry) => entry.id === message.editor) ?? editors[0];
        if (!editor) throw new Error("no editor was found; choose one in the review pane");
        const { command, args } = openCommand(editor, dir, null);
        git.launch(command, args, dir);
        return null;
      }
      case "open/folder": {
        const failed = await shell.openPath(folder(message.dir));
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
      case "prefs/read":
        return prefs.read();
      case "prefs/write":
        return prefs.write(message.values);
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
      case "dialog/open-folder": {
        const chosen = await dialog.showOpenDialog(own, { title: "Open a project folder", properties: ["openDirectory"] });
        return chosen.canceled ? null : chosen.filePaths[0] ?? null;
      }
      default:
        throw new Error(`the app window does not send ${(message as { type: string }).type}`);
    }
  }

  // Light only (DESKTOP §14 question 3; dark not approved 2026-10-16): the system theme is not
  // followed, so no nativeTheme change reaches the page or the title bar overlay (work.ts).

  // A second instance, or a `mightling://` link: show what it asked for, in the one window.
  app.on("second-instance", (_event, argv, _cwd, data) => {
    const extra = (data as { argv?: string[] } | undefined)?.argv ?? argv.slice(1);
    const asked = require("./args").workTarget(extra) as WorkTarget | null;
    if (asked) {
      target = asked;
      showView("work");
      deliver({ channel: "link", payload: target });
    } else showView("ask");
  });
  app.on("open-url", (event, url) => {
    event.preventDefault();
    target = require("./args").linkTarget(url) as WorkTarget;
    showView("work");
    deliver({ channel: "link", payload: target });
  });
  app.on("window-all-closed", () => {
    tasks.stopAll();
    server.stop();
    app.quit();
  });
  app.on("before-quit", () => server.stop());

  showView(options.work ? "work" : "ask");
  if (options.audit !== null) {
    try {
      server.start();
    } catch (error) {
      console.error(`ling-app: ${error instanceof Error ? error.message : String(error)}`);
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
