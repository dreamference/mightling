// The preview tab (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.9, Phase 3 item 4): a page shown
// in a `WebContentsView` laid over the tab's body, on a session of its own (`persist:mightling-
// preview`, its own cookies and history), with the rules of preview-rules.ts applied to every
// request and every navigation before anything connects. Nothing of the app reaches the page: no
// preload, `sandbox`, `contextIsolation`, no Node, no permission granted (camera, microphone,
// location, notifications, clipboard), no pop-up window. A download asks where to save every time
// and is refused from a blocked site or at `/airgapped on`. Comment mode runs a picker in an
// isolated world of the page and hands the clicked element back. Developer tools only with
// Settings › Browser › Developer mode.

import { BrowserWindow, WebContentsView, session, type Session, type WebContents } from "electron";

import { PICK_SCRIPT, consoleLine, decide, inward, type Decision, type SiteChoice } from "./preview-rules";

/**
 * The proxy the preview session holds while any tab is at `/airgapped on`: a proxy auto-config script
 * that answers DIRECT for loopback (a dev server loads as ever) and fails every other address, so it
 * never connects anywhere, not even to a dead proxy's port. Chromium does not fall back to a direct
 * connection when the script fails. The request rules cannot see a peer connection's STUN or TURN
 * traffic (TURN over TCP would go direct, and ask a resolver for its name); this closes that.
 */
export const AIR_GAP_PAC = [
  "function FindProxyForURL(url, host) {",
  '  if (host === "localhost" || host === "127.0.0.1" || host === "[::1]" || host === "::1") return "DIRECT";',
  '  throw new Error("air gap");',
  "}",
].join("\n");
export const AIR_GAP_PROXY = { mode: "pac_script" as const, pacScript: `data:application/x-ns-proxy-autoconfig,${encodeURIComponent(AIR_GAP_PAC)}` };

export const PREVIEW_PARTITION = "persist:mightling-preview";

/** The isolated world comment mode's picker runs in (anything but 0, the page's own). */
const PICK_WORLD = 1307;

/** How often an open tab's air-gap level is asked again, for a change made outside the app (`ling`, a project's setting). */
const RECHECK_MS = 15_000;

export interface PreviewEvent {
  id: string;
  kind: "state" | "ask" | "blocked" | "download" | "key";
  url?: string;
  title?: string;
  loading?: boolean;
  canGoBack?: boolean;
  canGoForward?: boolean;
  site?: string;
  reason?: string;
  error?: string | null;
  key?: string;
}

/** Shortcuts the window's tab area answers even while the previewed page has the focus (§2.19's preview keys). */
const FORWARDED: Record<string, string> = {
  "Ctrl+W": "closeTab", "Ctrl+T": "previewOpen", "Ctrl+L": "addressBar", "Ctrl+R": "previewReload", "Ctrl+Shift+R": "previewReloadHard",
  "Alt+ArrowLeft": "previewBack", "Alt+ArrowRight": "previewForward", "Ctrl+Shift+>": "commentMode", "Ctrl+Shift+.": "commentMode",
  "Ctrl+Shift+C": "copyUrl",
};

export interface PreviewSettings {
  sites: Record<string, SiteChoice>;
  devTools: boolean;
}

interface Preview {
  view: WebContentsView;
  window: BrowserWindow;
  thread: string | null;
  airgapOn: boolean;
  /** The page's recent console messages and errors, oldest first (the agent's console tool, desktop/ui/src/windowtools.ts). */
  console: string[];
}

/** How many console lines each preview keeps, and how long one may be. */
const CONSOLE_KEPT = 400;

export class Previews {
  private readonly previews = new Map<string, Preview>();
  /** Where the page last asked a tab's view to be, before the view existed (an empty tab's first address). */
  private readonly placements = new Map<string, { bounds: { x: number; y: number; width: number; height: number }; visible: boolean }>();
  /** Sites allowed for this run of the app only (Allow once). */
  private readonly once = new Set<string>();
  /** The last site a tab asked about, so one blocked page asks once, not once per image. */
  private readonly asked = new Map<string, string>();
  private configured: Session | null = null;
  private rechecking: ReturnType<typeof setInterval> | null = null;
  /** Whether the session holds the air-gap proxy now, and the change in flight (changes run one after another). */
  private proxyDead = false;
  private proxying: Promise<void> = Promise.resolve();

  constructor(
    private readonly emit: (window: BrowserWindow, event: PreviewEvent) => void,
    private readonly settings: () => PreviewSettings,
    private readonly airgapOn: (thread: string | null) => Promise<boolean>,
  ) {}

  /** The preview session's own rules, set once: every request is decided before it connects. */
  private session(): Session {
    if (this.configured) return this.configured;
    const ses = session.fromPartition(PREVIEW_PARTITION);
    ses.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    ses.setPermissionCheckHandler(() => false);
    ses.setSpellCheckerEnabled(false);
    ses.webRequest.onBeforeRequest({ urls: ["<all_urls>"] }, (details, callback) => {
      const found = this.byContents(details.webContentsId ?? -1);
      // A request from no preview of ours (a service worker, a stray) has nothing to answer to: refused.
      if (!found) return callback({ cancel: !details.url.startsWith("devtools:") });
      let decision = this.decision(found[1], details.url);
      if (decision.verdict === "allow" && inward(decision, details.resourceType, details.frame?.origin, found[1].view.webContents.getURL())) {
        decision = { ...decision, verdict: "block", reason: "a page from the internet does not reach this machine or the local network" };
      }
      if (decision.verdict !== "allow") this.refused(found[0], found[1], decision);
      callback({ cancel: decision.verdict !== "allow" });
    });
    // Chromium resolves names a page hints at (`<link rel="dns-prefetch">`, links) without a request
    // the rules above would see: every page of the preview says not to (DESKTOP_PARITY §5's audit found it).
    ses.webRequest.onHeadersReceived({ urls: ["<all_urls>"] }, (details, callback) => {
      callback({ responseHeaders: { ...details.responseHeaders, "X-DNS-Prefetch-Control": ["off"] } });
    });
    ses.on("will-download", (event, item, contents) => {
      const found = this.byContents(contents.id);
      const decision = found ? this.decision(found[1], item.getURL()) : null;
      if (!found || !decision || decision.verdict !== "allow") {
        event.preventDefault();
        if (found && decision) this.emit(found[1].window, { id: found[0], kind: "blocked", site: decision.site, reason: `A download was refused: ${decision.reason}` });
        return;
      }
      // No save path set: Electron asks where to save it, which is the user's approval of this download.
      item.setSaveDialogOptions({ title: "Save the download from the preview" });
      this.emit(found[1].window, { id: found[0], kind: "download", url: item.getURL(), site: decision.site });
    });
    this.configured = ses;
    return ses;
  }

  /** Puts the session's proxy where the tabs' air-gap levels say: the air-gap one while any tab is at `on`, the system's otherwise. */
  private syncProxy(): Promise<void> {
    this.proxying = this.proxying.then(async () => {
      const dead = [...this.previews.values()].some((preview) => preview.airgapOn);
      if (dead === this.proxyDead) return;
      this.proxyDead = dead;
      const ses = this.session();
      await ses.setProxy(dead ? AIR_GAP_PROXY : { mode: "system" });
      // What a page already had open goes through the old setting; it closes, so the new one decides.
      if (dead) await ses.closeAllConnections();
    }).catch(() => {});
    return this.proxying;
  }

  private byContents(id: number): [string, Preview] | null {
    for (const entry of this.previews) if (!entry[1].view.webContents.isDestroyed() && entry[1].view.webContents.id === id) return entry;
    return null;
  }

  private decision(preview: Preview, url: string): Decision {
    return decide(url, this.settings().sites, this.once, preview.airgapOn);
  }

  private refused(id: string, preview: Preview, decision: Decision): void {
    if (this.asked.get(id) === `${decision.verdict}:${decision.site}`) return;
    this.asked.set(id, `${decision.verdict}:${decision.site}`);
    this.emit(preview.window, { id, kind: decision.verdict === "ask" ? "ask" : "blocked", site: decision.site, reason: decision.reason });
  }

  private state(id: string, preview: Preview, error: string | null = null): void {
    const contents = preview.view.webContents;
    if (contents.isDestroyed()) return;
    this.emit(preview.window, {
      id, kind: "state", url: contents.getURL(), title: contents.getTitle(), loading: contents.isLoading(),
      canGoBack: contents.navigationHistory.canGoBack(), canGoForward: contents.navigationHistory.canGoForward(), error,
    });
  }

  /** A preview tab: a view over the window's tab body, hidden until the page says where. */
  async open(window: BrowserWindow, id: string, url: string, thread: string | null): Promise<void> {
    if (this.previews.has(id)) return this.navigate(id, url, thread);
    const view = new WebContentsView({
      webPreferences: {
        partition: PREVIEW_PARTITION,
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        webSecurity: true,
        spellcheck: false,
        devTools: this.settings().devTools,
      },
    });
    this.session();
    const preview: Preview = { view, window, thread, airgapOn: await this.airgapOn(thread), console: [] };
    this.previews.set(id, preview);
    await this.syncProxy();
    if (!this.rechecking) {
      this.rechecking = setInterval(() => void this.recheck().catch(() => {}), RECHECK_MS);
      this.rechecking.unref?.();
    }
    view.setVisible(false);
    window.contentView.addChildView(view);
    // The tab's body is already laid out: the view goes there at once, not at the next layout change.
    const placed = this.placements.get(id);
    if (placed) this.place(id, placed.bounds, placed.visible);
    const contents = view.webContents;
    // A peer connection's STUN and TURN traffic never passes the request rules above: no UDP for it
    // (TURN over TCP would still go direct; the preview has no proxy).
    contents.setWebRTCIPHandlingPolicy("disable_non_proxied_udp");
    // A page's new window opens in the same tab, if the rules let it.
    contents.setWindowOpenHandler(({ url: target }) => {
      if (this.decision(preview, target).verdict === "allow") void contents.loadURL(target);
      else this.refused(id, preview, this.decision(preview, target));
      return { action: "deny" };
    });
    const guard = (event: { preventDefault: () => void }, target: string) => {
      const decision = this.decision(preview, target);
      if (decision.verdict === "allow") return;
      event.preventDefault();
      this.refused(id, preview, decision);
    };
    contents.on("will-navigate", (event) => guard(event, event.url));
    contents.on("will-redirect", (event) => guard(event, event.url));
    contents.on("will-frame-navigate", (event) => guard(event, event.url));
    for (const name of ["did-navigate", "did-navigate-in-page", "page-title-updated", "did-start-loading", "did-stop-loading"] as const) {
      contents.on(name as "did-stop-loading", () => this.state(id, preview));
    }
    contents.on("before-input-event", (event, input) => {
      if (input.type !== "keyDown") return;
      const key = input.key.length === 1 ? input.key.toUpperCase() : input.key;
      // The page's `Ctrl` is ⌘ on a Mac (desktop/ui/src/keymap.ts).
      const command = process.platform === "darwin" ? input.meta : input.control;
      const chord = [command ? "Ctrl" : "", input.alt ? "Alt" : "", input.shift ? "Shift" : "", key].filter(Boolean).join("+");
      const action = FORWARDED[chord];
      if (!action) return;
      event.preventDefault();
      this.emit(window, { id, kind: "key", key: action });
    });
    const keep = (line: string) => {
      preview.console.push(line);
      if (preview.console.length > CONSOLE_KEPT) preview.console.splice(0, preview.console.length - CONSOLE_KEPT);
    };
    contents.on("console-message", (...args: unknown[]) => keep(consoleLine(args[0], ...args.slice(1))));
    contents.on("render-process-gone", (_event, details) => keep(`[error] the page's renderer ended: ${details.reason}`));
    contents.on("did-fail-load", (_event, code, description, failed, isMainFrame) => {
      if (code !== -3) keep(`[error] could not load ${failed}: ${description} (${code})`);
      // -3 is an aborted load (a navigation replaced it, or the rules cancelled it and said why).
      if (isMainFrame && code !== -3) this.state(id, preview, `${description} (${failed})`);
    });
    window.on("closed", () => {
      this.previews.delete(id);
      void this.syncProxy();
      if (!contents.isDestroyed()) contents.close();
      if (!this.previews.size && this.rechecking) {
        clearInterval(this.rechecking);
        this.rechecking = null;
      }
    });
    await this.navigate(id, url, thread);
  }

  async navigate(id: string, url: string, thread: string | null): Promise<void> {
    const preview = this.previews.get(id);
    if (!preview) return;
    preview.thread = thread;
    preview.airgapOn = await this.airgapOn(thread);
    await this.syncProxy();
    this.asked.delete(id);
    const decision = this.decision(preview, url);
    if (decision.verdict !== "allow") {
      this.refused(id, preview, decision);
      return;
    }
    await preview.view.webContents.loadURL(url).catch(() => {});
  }

  /**
   * Asks each tab's air-gap level again (`/airgapped` changed it, or the timer above). The session's rules
   * read the level a tab holds, so a page opened at `off` would otherwise go on fetching, opening sockets
   * and loading images after `on`. A tab that turns `on` loads again: what it had open (a socket, a
   * stream) closes, and every request of the new load is decided at `on`.
   */
  async recheck(): Promise<void> {
    await Promise.all([...this.previews].map(async ([id, preview]) => {
      const was = preview.airgapOn;
      preview.airgapOn = await this.airgapOn(preview.thread);
      if (preview.airgapOn && !was && !preview.view.webContents.isDestroyed()) {
        this.asked.delete(id);
        preview.view.webContents.reload();
      }
    }));
    await this.syncProxy();
  }

  /** Allow once (this run of the app) or after Settings › Browser changed: load the page again. */
  allowOnce(id: string, site: string): void {
    this.once.add(site);
    this.reload(id);
  }

  /** Loads the page again; `fresh` skips the cache, as Ctrl+Shift+R does (a dev server's stale assets). */
  reload(id: string, fresh = false): void {
    const preview = this.previews.get(id);
    if (!preview) return;
    this.asked.delete(id);
    if (fresh) preview.view.webContents.reloadIgnoringCache();
    else preview.view.webContents.reload();
  }

  go(id: string, step: "back" | "forward"): void {
    const history = this.previews.get(id)?.view.webContents.navigationHistory;
    if (step === "back" && history?.canGoBack()) history.goBack();
    if (step === "forward" && history?.canGoForward()) history.goForward();
  }

  /** Where the tab's body is in the window, in the page's pixels; hidden when another tab, a dialog or the layout covers it. */
  place(id: string, bounds: { x: number; y: number; width: number; height: number }, visible: boolean): void {
    this.placements.set(id, { bounds, visible });
    const preview = this.previews.get(id);
    if (!preview) return;
    preview.view.setBounds({ x: Math.round(bounds.x), y: Math.round(bounds.y), width: Math.max(0, Math.round(bounds.width)), height: Math.max(0, Math.round(bounds.height)) });
    preview.view.setVisible(visible);
  }

  /** Comment mode: resolves with the element the user clicks in the page, or null (Escape). */
  async pick(id: string): Promise<unknown> {
    const preview = this.previews.get(id);
    if (!preview) return null;
    preview.view.webContents.focus();
    return preview.view.webContents.executeJavaScriptInIsolatedWorld(PICK_WORLD, [{ code: PICK_SCRIPT }], true);
  }

  devTools(id: string): void {
    const contents: WebContents | undefined = this.previews.get(id)?.view.webContents;
    if (!contents || !this.settings().devTools) throw new Error("turn on Developer mode in Settings › Browser first, then open a new preview tab");
    contents.openDevTools({ mode: "detach" });
  }

  /** The console of the previews of a thread (or every preview when it has none), for the agent's console tool. */
  consoles(thread: string | null): { url: string; lines: string[] }[] {
    const all = [...this.previews.values()].filter((preview) => !preview.view.webContents.isDestroyed());
    const own = thread ? all.filter((preview) => preview.thread === thread) : [];
    return (own.length ? own : all).map((preview) => ({ url: preview.view.webContents.getURL(), lines: [...preview.console] }));
  }

  close(id: string): void {
    this.placements.delete(id);
    const preview = this.previews.get(id);
    if (!preview) return;
    this.previews.delete(id);
    void this.syncProxy();
    this.asked.delete(id);
    if (!preview.window.isDestroyed()) preview.window.contentView.removeChildView(preview.view);
    if (!preview.view.webContents.isDestroyed()) preview.view.webContents.close();
  }

  /** Settings › Browser › Clear: cookies, storage and cache of every preview. */
  async clear(): Promise<void> {
    const ses = this.session();
    await ses.clearStorageData();
    await ses.clearCache();
  }
}
