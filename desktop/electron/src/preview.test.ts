import { describe, expect, it, vi } from "vitest";

// The preview session's request rules, as Electron would call them: one handler for every request.
const rules = vi.hoisted(() => ({ beforeRequest: null as null | ((details: { url: string; webContentsId?: number }, callback: (answer: { cancel: boolean }) => void) => void) }));

vi.mock("electron", () => {
  const ses = {
    setPermissionRequestHandler: () => {},
    setPermissionCheckHandler: () => {},
    setSpellCheckerEnabled: () => {},
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

import { Previews } from "./preview";

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
