// The preview tab (DESKTOP_PARITY §2.9): its view, its session's rules and its navigation guard,
// against stand-ins for Electron's view, session and window. The rules themselves are
// preview-rules.test.ts's; this checks that every way a page reaches out goes through them.
import { EventEmitter } from "node:events";
import { beforeEach, describe, expect, it, vi } from "vitest";

type Listener = (...args: any[]) => void;

const made = vi.hoisted(() => ({ views: [] as any[], session: null as any, nextId: 100 }));

vi.mock("electron", async () => {
  const { EventEmitter } = await import("node:events");
  class FakeContents extends EventEmitter {
    id = made.nextId++;
    url = "";
    destroyed = false;
    openHandler: ((details: { url: string }) => { action: string }) | null = null;
    loadURL = vi.fn(async (url: string) => {
      this.url = url;
    });
    reload = vi.fn();
    reloadIgnoringCache = vi.fn();
    focus = vi.fn();
    close = vi.fn(() => (this.destroyed = true));
    openDevTools = vi.fn();
    navigationHistory = { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() };
    setWindowOpenHandler(handler: (details: { url: string }) => { action: string }) {
      this.openHandler = handler;
    }
    isDestroyed() {
      return this.destroyed;
    }
    getURL() {
      return this.url;
    }
    getTitle() {
      return "";
    }
    isLoading() {
      return false;
    }
  }
  class WebContentsView {
    webContents = new FakeContents();
    visible: boolean | null = null;
    bounds: unknown = null;
    constructor(readonly options: { webPreferences: Record<string, unknown> }) {
      made.views.push(this);
    }
    setVisible(visible: boolean) {
      this.visible = visible;
    }
    setBounds(bounds: unknown) {
      this.bounds = bounds;
    }
  }
  const fromPartition = vi.fn((partition: string) => {
    if (made.session) return made.session;
    const ses = Object.assign(new EventEmitter(), {
      partition,
      permissionRequest: null as Listener | null,
      permissionCheck: null as Listener | null,
      spellcheck: true,
      beforeRequest: null as Listener | null,
      headersReceived: null as Listener | null,
      setPermissionRequestHandler(handler: Listener) {
        this.permissionRequest = handler;
      },
      setPermissionCheckHandler(handler: Listener) {
        this.permissionCheck = handler;
      },
      setSpellCheckerEnabled(on: boolean) {
        this.spellcheck = on;
      },
      webRequest: {
        onBeforeRequest: (_filter: unknown, handler: Listener) => (made.session.beforeRequest = handler),
        onHeadersReceived: (_filter: unknown, handler: Listener) => (made.session.headersReceived = handler),
      },
      clearStorageData: vi.fn(async () => {}),
      clearCache: vi.fn(async () => {}),
    });
    made.session = ses;
    return ses;
  });
  return { BrowserWindow: class {}, WebContentsView, session: { fromPartition } };
});

import { PREVIEW_PARTITION, Previews, type PreviewEvent, type PreviewSettings } from "./preview";

class FakeWindow extends EventEmitter {
  destroyed = false;
  contentView = { addChildView: vi.fn(), removeChildView: vi.fn() };
  isDestroyed() {
    return this.destroyed;
  }
}

describe("a preview tab", () => {
  let events: { window: unknown; event: PreviewEvent }[];
  let settings: PreviewSettings;
  let airgapped: Set<string>;
  let previews: Previews;
  let window: FakeWindow;

  beforeEach(() => {
    made.views.length = 0;
    made.session = null;
    events = [];
    settings = { sites: {}, devTools: false };
    airgapped = new Set();
    previews = new Previews((target, event) => events.push({ window: target, event }), () => settings, async (thread) => thread !== null && airgapped.has(thread));
    window = new FakeWindow();
  });

  const open = async (id: string, url: string, thread: string | null = null) => {
    await previews.open(window as never, id, url, thread);
    return made.views.at(-1);
  };
  const told = (kind: PreviewEvent["kind"]) => events.filter((entry) => entry.event.kind === kind).map((entry) => entry.event);
  /** Fires one of the page's navigation events at the guard; answers whether it was stopped. */
  const navigate = (view: any, name: string, url: string) => {
    const preventDefault = vi.fn();
    view.webContents.emit(name, { url, preventDefault });
    return preventDefault.mock.calls.length > 0;
  };

  it("is a sandboxed view on a session of its own, hidden until placed, that grants no permission", async () => {
    const view = await open("p1", "http://localhost:5173/");
    expect(view.options.webPreferences).toEqual({
      partition: PREVIEW_PARTITION, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, spellcheck: false, devTools: false,
    });
    expect(view.options.webPreferences).not.toHaveProperty("preload");
    expect(view.visible).toBe(false);
    expect(window.contentView.addChildView).toHaveBeenCalledWith(view);
    expect(view.webContents.loadURL).toHaveBeenCalledWith("http://localhost:5173/");

    const ses = made.session;
    expect(ses.partition).toBe(PREVIEW_PARTITION);
    expect(ses.spellcheck).toBe(false);
    for (const permission of ["media", "geolocation", "notifications", "clipboard-read", "clipboard-sanitized-write"]) {
      const callback = vi.fn();
      ses.permissionRequest(view.webContents, permission, callback);
      expect(callback, permission).toHaveBeenCalledWith(false);
    }
    expect(ses.permissionCheck(view.webContents, "media")).toBe(false);
    // Every response says no to DNS prefetching, whatever the server sent.
    const headers = vi.fn();
    ses.headersReceived({ responseHeaders: { "Content-Type": ["text/html"], "X-DNS-Prefetch-Control": ["on"] } }, headers);
    expect(headers).toHaveBeenCalledWith({ responseHeaders: { "Content-Type": ["text/html"], "X-DNS-Prefetch-Control": ["off"] } });

    // A second tab shares the session, set up once.
    await open("p2", "http://localhost:3000/");
    expect(made.session).toBe(ses);
  });

  it("goes nowhere the rules refuse, by link, redirect or frame, and asks once per site", async () => {
    const view = await open("p1", "http://localhost:5173/");
    for (const name of ["will-navigate", "will-redirect", "will-frame-navigate"]) {
      expect(navigate(view, name, "http://127.0.0.1:8080/next"), name).toBe(false);
      expect(navigate(view, name, "https://example.com/"), name).toBe(true);
    }
    // One question for example.com, however often in a row the page tries it.
    expect(told("ask")).toEqual([{ id: "p1", kind: "ask", site: "example.com", reason: "example.com is on the internet" }]);
    for (const name of ["will-navigate", "will-redirect", "will-frame-navigate"]) expect(navigate(view, name, "chrome://settings"), name).toBe(true);
    expect(told("blocked")).toHaveLength(1);
    expect(events.every((entry) => entry.window === window)).toBe(true);

    settings.sites = { "example.com": "block" };
    expect(navigate(view, "will-navigate", "https://example.com/")).toBe(true);
    expect(told("blocked").at(-1)).toMatchObject({ site: "example.com", reason: "example.com is blocked in Settings › Browser" });
    settings.sites = { "example.com": "allow" };
    expect(navigate(view, "will-navigate", "https://example.com/")).toBe(false);
  });

  it("opens a page's new window in the same tab only when the rules allow it, and never a window of its own", async () => {
    const view = await open("p1", "http://localhost:5173/");
    const contents = view.webContents;
    expect(contents.openHandler({ url: "http://localhost:5173/docs" })).toEqual({ action: "deny" });
    expect(contents.loadURL).toHaveBeenLastCalledWith("http://localhost:5173/docs");
    contents.loadURL.mockClear();
    expect(contents.openHandler({ url: "https://ads.example.net/" })).toEqual({ action: "deny" });
    expect(contents.openHandler({ url: "javascript:alert(1)" })).toEqual({ action: "deny" });
    expect(contents.loadURL).not.toHaveBeenCalled();
    expect(told("ask")).toEqual([expect.objectContaining({ site: "ads.example.net" })]);
    expect(told("blocked")).toHaveLength(1);
  });

  it("lets a page's new window reach a file or a data: page only where Chromium would let the page itself", async () => {
    // A link to a file or a data: page from a web page is refused by Chromium in the page; a pop-up
    // the main process loaded into the tab would not be (a browser-initiated load), so it is refused here.
    settings.sites = { "example.com": "allow" };
    const view = await open("p1", "https://example.com/");
    const contents = view.webContents;
    contents.loadURL.mockClear();
    for (const target of ["file:///etc/passwd", "data:text/html,<h1>Sign in</h1>"]) {
      expect(contents.openHandler({ url: target }), target).toEqual({ action: "deny" });
    }
    expect(contents.loadURL).not.toHaveBeenCalled();
    expect(told("blocked")).toEqual([expect.objectContaining({ site: "", reason: "a page may not open a file or a data: page from here" })]);
    // A file the user opened may open the files beside it.
    const local = await open("p2", "file:///srv/site/index.html");
    local.webContents.openHandler({ url: "file:///srv/site/next.html" });
    expect(local.webContents.loadURL).toHaveBeenLastCalledWith("file:///srv/site/next.html");
  });

  it("refuses the internet and the local network for a thread at /airgapped on, this machine still allowed", async () => {
    airgapped.add("t-on");
    settings.sites = { "example.com": "allow" };
    const view = await open("p1", "https://example.com/", "t-on");
    expect(view.webContents.loadURL).not.toHaveBeenCalled();
    expect(told("blocked")).toEqual([expect.objectContaining({ site: "example.com", reason: "airgapped is on: only this machine is reachable" })]);
    expect(navigate(view, "will-navigate", "http://192.168.1.20/")).toBe(true);
    expect(navigate(view, "will-navigate", "http://localhost:8000/")).toBe(false);
    // The same tab in a thread at off: the level is asked again on every navigation the page asks for.
    await previews.navigate("p1", "https://example.com/", "t-off");
    expect(view.webContents.loadURL).toHaveBeenCalledWith("https://example.com/");
  });

  it("decides every request of the session: a preview's by the rules, anyone else's refused", async () => {
    const view = await open("p1", "http://localhost:5173/");
    const decide = (details: Record<string, unknown>) => {
      const callback = vi.fn();
      made.session.beforeRequest(details, callback);
      return callback.mock.calls[0][0];
    };
    expect(decide({ webContentsId: view.webContents.id, url: "http://localhost:5173/app.js" })).toEqual({ cancel: false });
    expect(decide({ webContentsId: view.webContents.id, url: "https://cdn.example.org/lib.js" })).toEqual({ cancel: true });
    expect(told("ask")).toEqual([expect.objectContaining({ site: "cdn.example.org" })]);
    // A service worker or a stray: no preview to answer to.
    expect(decide({ url: "http://localhost:5173/sw.js" })).toEqual({ cancel: true });
    expect(decide({ webContentsId: 9999, url: "http://localhost:5173/" })).toEqual({ cancel: true });
    expect(decide({ webContentsId: 9999, url: "devtools://devtools/bundled/inspector.html" })).toEqual({ cancel: false });

    // Allow once: the site passes for this run of the app, and the page loads again.
    previews.allowOnce("p1", "cdn.example.org");
    expect(view.webContents.reload).toHaveBeenCalled();
    expect(decide({ webContentsId: view.webContents.id, url: "https://cdn.example.org/lib.js" })).toEqual({ cancel: false });
    // A closed tab's contents get no request through.
    previews.close("p1");
    expect(decide({ webContentsId: view.webContents.id, url: "http://localhost:5173/app.js" })).toEqual({ cancel: true });
  });

  it("asks where to save a download from an allowed site, and refuses one from anywhere else", async () => {
    const view = await open("p1", "http://localhost:5173/");
    const download = (url: string, contents: { id: number }) => {
      const item = { getURL: () => url, setSaveDialogOptions: vi.fn() };
      const event = { preventDefault: vi.fn() };
      made.session.emit("will-download", event, item, contents);
      return { refused: event.preventDefault.mock.calls.length > 0, asked: item.setSaveDialogOptions.mock.calls.length > 0 };
    };
    expect(download("http://localhost:5173/build.zip", view.webContents)).toEqual({ refused: false, asked: true });
    expect(told("download")).toEqual([{ id: "p1", kind: "download", url: "http://localhost:5173/build.zip", site: "localhost" }]);
    expect(download("https://example.com/tool.sh", view.webContents)).toEqual({ refused: true, asked: false });
    expect(told("blocked").at(-1)).toMatchObject({ site: "example.com", reason: "A download was refused: example.com is on the internet" });
    expect(download("http://localhost:5173/x.zip", { id: 9999 })).toEqual({ refused: true, asked: false });
  });

  it("opens developer tools only in Developer mode", async () => {
    const view = await open("p1", "http://localhost:5173/");
    expect(() => previews.devTools("p1")).toThrow("Developer mode");
    expect(() => previews.devTools("nope")).toThrow("Developer mode");
    settings.devTools = true;
    previews.devTools("p1");
    expect(view.webContents.openDevTools).toHaveBeenCalledWith({ mode: "detach" });
  });

  it("gives the agent the console of its own thread's previews, the last lines only", async () => {
    const mine = await open("p1", "http://localhost:5173/", "t1");
    await open("p2", "http://localhost:3000/", "t2");
    for (let line = 0; line < 450; line++) mine.webContents.emit("console-message", { level: "info", message: `line ${line}`, lineNumber: 1, sourceId: "app.js" });
    const consoles = previews.consoles("t1");
    expect(consoles).toHaveLength(1);
    expect(consoles[0].url).toBe("http://localhost:5173/");
    expect(consoles[0].lines).toHaveLength(400);
    expect(consoles[0].lines.at(-1)).toContain("line 449");
    // A thread with no preview of its own sees every one.
    expect(previews.consoles("t3")).toHaveLength(2);
    expect(previews.consoles(null)).toHaveLength(2);
  });

  it("is placed where the page says, even when it said so before the view existed, and goes with its window", async () => {
    previews.place("p1", { x: 10.4, y: 20.6, width: -5, height: 300 }, true);
    const view = await open("p1", "http://localhost:5173/");
    expect(view.bounds).toEqual({ x: 10, y: 21, width: 0, height: 300 });
    expect(view.visible).toBe(true);
    previews.place("p1", { x: 0, y: 0, width: 100, height: 100 }, false);
    expect(view.visible).toBe(false);

    previews.close("p1");
    expect(window.contentView.removeChildView).toHaveBeenCalledWith(view);
    expect(view.webContents.close).toHaveBeenCalled();
    // Gone: nothing answers to its id any more.
    previews.reload("p1");
    expect(view.webContents.reload).not.toHaveBeenCalled();
    expect(await previews.pick("p1")).toBeNull();

    const other = await open("p2", "http://localhost:3000/");
    window.emit("closed");
    expect(other.webContents.close).toHaveBeenCalled();
    expect(previews.consoles(null)).toEqual([]);
  });

  it("clears the preview session's storage and cache, and nothing of the app's", async () => {
    await previews.clear();
    expect(made.session.partition).toBe(PREVIEW_PARTITION);
    expect(made.session.clearStorageData).toHaveBeenCalled();
    expect(made.session.clearCache).toHaveBeenCalled();
  });
});
