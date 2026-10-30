// Every window of the app (DESKTOP_ELECTRON §13), a full one, a pop-out or a detached pane, is made
// with the same preload, renderer sandbox and navigation lock, and loads the bundled page alone.
import { beforeEach, describe, expect, it, vi } from "vitest";

const made: { options: Record<string, unknown>; handlers: Record<string, (event: { preventDefault(): void }, url?: string) => void>; open: ((details: { url: string }) => unknown) | null; loaded: string | null }[] = [];

vi.mock("electron", () => {
  class BrowserWindow {
    webContents: Record<string, unknown>;
    constructor(options: Record<string, unknown>) {
      const entry = { options, handlers: {} as Record<string, (event: { preventDefault(): void }, url?: string) => void>, open: null as ((details: { url: string }) => unknown) | null, loaded: null as string | null };
      made.push(entry);
      this.webContents = {
        on: (name: string, handler: (event: { preventDefault(): void }, url?: string) => void) => (entry.handlers[name] = handler),
        setWindowOpenHandler: (handler: (details: { url: string }) => unknown) => (entry.open = handler),
      };
      this.loadURL = (url: string) => {
        entry.loaded = url;
        return Promise.resolve();
      };
    }
    loadURL: (url: string) => Promise<void>;
  }
  return { BrowserWindow, app: { isPackaged: true, getPath: () => "/tmp/ling-user-data" }, shell: { openExternal: vi.fn() }, protocol: { registerSchemesAsPrivileged: vi.fn() } };
});
vi.mock("./window-state", () => ({ place: vi.fn(), track: vi.fn() }));

import { shell } from "electron";
import { WORK_URL } from "./app-protocol";
import { openWork, pageUrl, staysOnPage } from "./work";

beforeEach(() => {
  made.length = 0;
});

describe("the app's windows", () => {
  it("each load the bundled page, with what they show in the fragment", () => {
    expect(pageUrl({ view: "ask" })).toBe(WORK_URL);
    expect(pageUrl({ view: "work" })).toBe(`${WORK_URL}#work`);
    expect(pageUrl({ popout: "t 1" })).toBe(`${WORK_URL}#popout=t%201`);
    expect(pageUrl({ pane: { kind: "terminal", thread: "t1" } })).toBe(`${WORK_URL}#pane=terminal&thread=t1`);
    expect(pageUrl({ pane: { kind: "review", thread: "a/b" } })).toBe(`${WORK_URL}#pane=review&thread=a%2Fb`);
  });

  it("go nowhere but the bundled page", () => {
    expect(staysOnPage(WORK_URL)).toBe(true);
    expect(staysOnPage(`${WORK_URL}#work`)).toBe(true);
    for (const url of ["https://example.com", "app://-/other.html", `${WORK_URL}.evil`, "app://evil/index.html", "file:///etc/passwd", "ling://threads/x"]) {
      expect(staysOnPage(url), url).toBe(false);
    }
  });

  it("are all made the same way, whatever they show", () => {
    const kinds = [{ view: "ask" as const }, { view: "work" as const }, { popout: "t1" }, { pane: { kind: "terminal" as const, thread: "t1" } }, { pane: { kind: "review" as const, thread: "t1" } }];
    for (const kind of kinds) openWork({ preload: "/p/preload.cjs", show: false, theme: "dark", ...kind });
    expect(made).toHaveLength(kinds.length);
    for (const entry of made) {
      expect(entry.options.webPreferences).toEqual({
        preload: "/p/preload.cjs", additionalArguments: ["--mightling-theme=dark"], sandbox: true, contextIsolation: true, nodeIntegration: false, webviewTag: false, spellcheck: true, devTools: false,
      });
      expect(entry.loaded?.startsWith(WORK_URL)).toBe(true);
      // Navigation away, a redirect and an embedded guest are stopped; a new window is never opened in the app.
      for (const name of ["will-navigate", "will-redirect"]) {
        const stopped = vi.fn();
        entry.handlers[name]({ preventDefault: stopped }, "https://example.com/");
        expect(stopped, name).toHaveBeenCalled();
        const kept = vi.fn();
        entry.handlers[name]({ preventDefault: kept }, `${WORK_URL}#work`);
        expect(kept, name).not.toHaveBeenCalled();
      }
      const guest = vi.fn();
      entry.handlers["will-attach-webview"]({ preventDefault: guest });
      expect(guest).toHaveBeenCalled();
      expect(entry.open?.({ url: "app://-/index.html" })).toEqual({ action: "deny" });
      expect(entry.open?.({ url: "https://example.com" })).toEqual({ action: "deny" });
    }
  });

  it("never opens a window nor hands a window.open address to the system's browser (links go through link/open, which checks the air gap)", () => {
    openWork({ preload: "/p/preload.cjs", show: false, theme: "light", view: "work" });
    const openExternal = vi.mocked(shell.openExternal);
    openExternal.mockClear();
    for (const url of ["https://example.com/a b", "https://user:secret@example.com/", "file:///etc/passwd", "javascript:alert(1)"]) {
      expect(made[0].open?.({ url }), url).toEqual({ action: "deny" });
    }
    expect(openExternal).not.toHaveBeenCalled();
  });
});
