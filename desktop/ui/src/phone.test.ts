// Tests for the phone Work view, Telegram BackButton layer priority, and the drawer's Files section (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §10 G5, G12; specs/DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md §15).

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as bridge from "./bridge";
import { backTarget, DrawerFiles } from "./phone";
import { RpcClient } from "./rpc";

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
  delete (globalThis as { document?: unknown }).document;
});

describe("backTarget order (G5)", () => {
  it("closes layers from top-most to bottom-most and hides when nothing can be closed", () => {
    const allOpen = {
      outputTarget: { threadId: "t1", itemId: "cmd1" },
      reposOpen: true,
      dialogOpen: true,
      sheetOpen: true,
      drawer: true,
      threadSelected: true,
      narrow: true,
    };

    // 1. Output view (G8)
    expect(backTarget(allOpen)).toBe("output");

    // 2. Repos view (G4)
    const afterOutput = { ...allOpen, outputTarget: null };
    expect(backTarget(afterOutput)).toBe("repos");

    // 3. Open dialog that closes on Escape
    const afterRepos = { ...afterOutput, reposOpen: false };
    expect(backTarget(afterRepos)).toBe("dialog");

    // 4. Review pane or file tab sheet on a narrow screen
    const afterDialog = { ...afterRepos, dialogOpen: false };
    expect(backTarget(afterDialog)).toBe("sheet");
    expect(backTarget({ ...afterDialog, sheetOpen: undefined, tabsShown: true })).toBe("sheet");
    // On a wide screen, the side-by-side tab area is not a sheet, so drawer is next.
    expect(backTarget({ ...afterDialog, narrow: false })).toBe("drawer");

    // 5. Drawer
    const afterSheet = { ...afterDialog, sheetOpen: false };
    expect(backTarget(afterSheet)).toBe("drawer");

    // 6. Selected thread back to the thread list on a narrow screen
    const afterDrawer = { ...afterSheet, drawer: false };
    expect(backTarget(afterDrawer)).toBe("thread");
    expect(backTarget({ ...afterDrawer, threadSelected: undefined, selected: { id: "t1" } })).toBe("thread");
    // On a wide screen, a selected thread does not show the phone back button.
    expect(backTarget({ ...afterDrawer, narrow: false })).toBeNull();

    // Nothing left to close
    const noneOpen = { ...afterDrawer, threadSelected: false };
    expect(backTarget(noneOpen)).toBeNull();
  });
});

describe("bridge.telegram (G5)", () => {
  it("is a no-op in Electron and in an ordinary browser tab", () => {
    const sent: unknown[] = [];
    const listeners: ((event: MessageEvent) => void)[] = [];
    (globalThis as { document?: unknown }).document = {
      documentElement: { dataset: { host: "telegram" } },
    };
    (globalThis as { window?: unknown }).window = {
      mightlingWindowType: "electron",
      electronBridge: {
        sendMessageFromView: async (msg: unknown) => {
          sent.push(msg);
          return null;
        },
      },
      addEventListener: (_type: string, fn: (event: MessageEvent) => void) => {
        listeners.push(fn);
      },
      removeEventListener: () => {},
    };

    expect(bridge.host()).toBe("electron");
    expect(bridge.telegram.available()).toBe(false);
    bridge.telegram.setBackButton(true);
    const onBack = vi.fn();
    const unsubscribe = bridge.telegram.onBack(onBack);
    unsubscribe();
    expect(sent).toEqual([]);
    expect(listeners).toEqual([]);
    expect(onBack).not.toHaveBeenCalled();

    // Ordinary browser tab (no data-host="telegram")
    (globalThis as { document?: unknown }).document = {
      documentElement: { dataset: {} },
    };
    (globalThis as { window?: unknown }).window = {
      mightlingWindowType: "web",
      electronBridge: {
        sendMessageFromView: async (msg: unknown) => {
          sent.push(msg);
          return null;
        },
      },
      addEventListener: (_type: string, fn: (event: MessageEvent) => void) => {
        listeners.push(fn);
      },
      removeEventListener: () => {},
    };
    expect(bridge.host()).toBe("web");
    expect(bridge.telegram.available()).toBe(false);
    bridge.telegram.setBackButton(true);
    bridge.telegram.onBack(onBack)();
    expect(sent).toEqual([]);
    expect(listeners).toEqual([]);
  });

  it("sends back-button visibility and receives telegramBack events when data-host is telegram", () => {
    const sent: unknown[] = [];
    const listeners = new Set<(event: MessageEvent) => void>();
    (globalThis as { document?: unknown }).document = {
      documentElement: { dataset: { host: "telegram" } },
    };
    (globalThis as { window?: unknown }).window = {
      mightlingWindowType: "web",
      electronBridge: {
        sendMessageFromView: async (msg: unknown) => {
          sent.push(msg);
          return null;
        },
      },
      addEventListener: (type: string, fn: (event: MessageEvent) => void) => {
        if (type === "message") listeners.add(fn);
      },
      removeEventListener: (type: string, fn: (event: MessageEvent) => void) => {
        if (type === "message") listeners.delete(fn);
      },
    };

    expect(bridge.telegram.available()).toBe(true);
    bridge.telegram.setBackButton(true);
    bridge.telegram.setBackButton(false);
    expect(sent).toEqual([
      { type: "telegram/back-button", visible: true },
      { type: "telegram/back-button", visible: false },
    ]);

    let pressed = 0;
    const unsubscribe = bridge.telegram.onBack(() => {
      pressed += 1;
    });
    for (const fn of listeners) {
      fn({ data: { channel: "theme", payload: "dark" } } as MessageEvent);
      fn({ data: { channel: "telegramBack", payload: null } } as MessageEvent);
    }
    expect(pressed).toBe(1);

    unsubscribe();
    expect(listeners.size).toBe(0);
  });
});

describe("DrawerFiles section in the drawer (G12)", () => {
  it("renders the Files section with FileTree for a Work thread on the web host only", () => {
    (globalThis as { window?: unknown }).window = {
      mightlingWindowType: "web",
      addEventListener: () => {},
      removeEventListener: () => {},
    };
    const client = new RpcClient({ send: async () => {} });
    const listen = () => () => {};
    const checkoutCwd = "/home/u/.mightling/chat-repos/org/alpha-service/web-01234567";

    const html = renderToStaticMarkup(
      createElement(DrawerFiles, {
        open: true,
        view: "work",
        cwd: checkoutCwd,
        client,
        listen,
        selected: `${checkoutCwd}/src/index.ts`,
        onOpen: () => {},
        onClose: () => {},
      }),
    );
    expect(html).toContain('class="project drawer-files"');
    expect(html).toContain(">Files</h2>");
    expect(html).toContain('class="file-tree"');
    expect(html).toContain(`title="${checkoutCwd}"`);
    expect(html).toContain(">web-01234567</span>");

    // Hidden when no Work thread is selected, when in Ask, when the drawer is closed, or in Electron.
    expect(
      renderToStaticMarkup(
        createElement(DrawerFiles, {
          open: true,
          view: "work",
          cwd: null,
          client,
          listen,
          selected: null,
          onOpen: () => {},
          onClose: () => {},
        }),
      ),
    ).toBe("");
    expect(
      renderToStaticMarkup(
        createElement(DrawerFiles, {
          open: true,
          view: "ask",
          cwd: checkoutCwd,
          client,
          listen,
          selected: null,
          onOpen: () => {},
          onClose: () => {},
        }),
      ),
    ).toBe("");
    expect(
      renderToStaticMarkup(
        createElement(DrawerFiles, {
          open: false,
          view: "work",
          cwd: checkoutCwd,
          client,
          listen,
          selected: null,
          onOpen: () => {},
          onClose: () => {},
        }),
      ),
    ).toBe("");
    expect(
      renderToStaticMarkup(
        createElement(DrawerFiles, {
          host: "electron",
          open: true,
          view: "work",
          cwd: checkoutCwd,
          client,
          listen,
          selected: null,
          onOpen: () => {},
          onClose: () => {},
        }),
      ),
    ).toBe("");
  });
});
