import { describe, expect, it, vi } from "vitest";

// The preview session's request rules, as Electron would call them: one handler for every request.
const proxies = vi.hoisted(() => ({ set: [] as Array<{ mode: string; pacScript?: string }>, closed: 0 }));
const rules = vi.hoisted(() => ({ beforeRequest: null as null | ((details: { url: string; webContentsId?: number; resourceType?: string; frame?: { origin: string } | null }, callback: (answer: { cancel: boolean }) => void) => void) }));

vi.mock("electron", () => {
  const ses = {
    setPermissionRequestHandler: () => {},
    setPermissionCheckHandler: () => {},
    setSpellCheckerEnabled: () => {},
    setProxy: async (config: { mode: string; pacScript?: string }) => { proxies.set.push(config); },
    closeAllConnections: async () => { proxies.closed += 1; },
    on: () => {},
    webRequest: {
      onBeforeRequest: (_filter: unknown, handler: typeof rules.beforeRequest) => { rules.beforeRequest = handler; },
      onHeadersReceived: () => {},
    },
  };
  class WebContentsView {
    webContents = {
      id: 7,
      reloads: 0,
      policies: [] as string[],
      setWebRTCIPHandlingPolicy(policy: string) { this.policies.push(policy); },
      on: () => {},
      setWindowOpenHandler: () => {},
      isDestroyed: () => false,
      loadURL: async () => {},
      reload() { this.reloads += 1; },
      getURL: () => "http://localhost:5173/",
      getTitle: () => "",
      isLoading: () => false,
      navigationHistory: { canGoBack: () => false, canGoForward: () => false },
    };
    setVisible() {}
    setBounds() {}
  }
  return { BrowserWindow: class {}, WebContentsView, session: { fromPartition: () => ses } };
});

import { AIR_GAP_PAC, AIR_GAP_PROXY, Previews } from "./preview";

const asked = (url: string) => new Promise<boolean>((resolve) => rules.beforeRequest!({ url, webContentsId: 7 }, ({ cancel }) => resolve(cancel)));

describe("a preview at /airgapped", () => {
  it("refuses a page's own fetches, sockets and images once the thread turns on, not only its next navigation", async () => {
    let on = false;
    const previews = new Previews(() => {}, () => ({ sites: { "example.com": "allow" }, devTools: false }), async () => on);
    let view: { webContents: { reloads: number } } | null = null;
    const window = { contentView: { addChildView: (added: typeof view) => { view = added; } }, on: () => {} };
    await previews.open(window as never, "tab", "http://localhost:5173/", "thread-1");
    expect(await asked("https://example.com/api")).toBe(false);
    on = true;
    await previews.recheck();
    // What the page already had open closes: it loads again, at on.
    expect(view!.webContents.reloads).toBe(1);
    expect(await asked("https://example.com/api")).toBe(true);
    expect(await asked("wss://example.com/socket")).toBe(true);
    expect(await asked("http://localhost:5173/app.js")).toBe(false);
  });
});

describe("a preview and WebRTC", () => {
  it("keeps a page's peer connections off UDP, which no request rule sees", async () => {
    const previews = new Previews(() => {}, () => ({ sites: {}, devTools: false }), async () => true);
    let view: { webContents: { policies: string[] } } | null = null;
    const window = { contentView: { addChildView: (added: typeof view) => { view = added; } }, on: () => {} };
    await previews.open(window as never, "rtc", "http://localhost:5173/", "thread-1");
    expect(view!.webContents.policies).toEqual(["disable_non_proxied_udp"]);
  });
});

describe("an internet page and this machine", () => {
  it("refuses a page's own requests from the internet to this machine or the LAN, not the tab's navigations", async () => {
    const previews = new Previews(() => {}, () => ({ sites: { "example.com": "allow" }, devTools: false }), async () => false);
    const window = { contentView: { addChildView: () => {} }, on: () => {} };
    await previews.open(window as never, "lna", "http://localhost:5173/", null);
    const from = (url: string, resourceType: string, origin: string | null) =>
      new Promise<boolean>((resolve) => rules.beforeRequest!({ url, webContentsId: 7, resourceType, frame: origin === null ? null : { origin } }, ({ cancel }) => resolve(cancel)));
    expect(await from("http://127.0.0.1:8000/v1/models", "xhr", "https://example.com")).toBe(true);
    expect(await from("ws://localhost:18000/", "webSocket", "https://example.com")).toBe(true);
    expect(await from("http://192.168.1.1/admin", "subFrame", "https://example.com")).toBe(true);
    // An opaque frame (sandboxed, data:) counts as the tab's page, here this machine's.
    expect(await from("http://127.0.0.1:8000/v1/models", "xhr", "null")).toBe(false);
    expect(await from("http://127.0.0.1:8000/v1/models", "xhr", "http://localhost:5173")).toBe(false);
    expect(await from("http://127.0.0.1:8000/v1/models", "script", null)).toBe(false);
    // The tab itself may still go there: a navigation the user sees.
    expect(await from("http://localhost:5173/", "mainFrame", "https://example.com")).toBe(false);
    expect(await from("https://example.com/api", "xhr", "https://example.com")).toBe(false);
  });
});

describe("a preview's proxy at /airgapped", () => {
  it("is dead for everything but this machine while any tab is at on, and the system's again when none is", async () => {
    proxies.set.length = 0;
    proxies.closed = 0;
    let on = true;
    const previews = new Previews(() => {}, () => ({ sites: {}, devTools: false }), async () => on);
    const window = { contentView: { addChildView: () => {} }, on: () => {} };
    await previews.open(window as never, "proxy", "http://localhost:5173/", "thread-1");
    expect(proxies.set.map((config) => config.mode)).toEqual(["pac_script"]);
    expect(proxies.closed).toBe(1);
    on = false;
    await previews.recheck();
    expect(proxies.set.map((config) => config.mode)).toEqual(["pac_script", "system"]);
    expect(proxies.closed).toBe(1);
  });

  it("has a script that lets loopback through and fails everything else", () => {
    const find = new Function("shExpMatch", "isInNet", `${AIR_GAP_PAC}; return FindProxyForURL;`)(() => false, () => false) as (url: string, host: string) => string;
    expect(find("http://localhost:5173/", "localhost")).toBe("DIRECT");
    expect(find("http://127.0.0.1:5173/", "127.0.0.1")).toBe("DIRECT");
    expect(() => find("https://example.com/", "example.com")).toThrow();
    expect(AIR_GAP_PROXY.pacScript.startsWith("data:application/x-ns-proxy-autoconfig,")).toBe(true);
  });
});
