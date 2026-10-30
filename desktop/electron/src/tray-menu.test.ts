// The tray's menu: Quick Entry's "New question…" after Ask and Work, only when the box is wired (PARITY_CLAUDE §4.2).
import { describe, expect, it, vi } from "vitest";

type Item = { label?: string; role?: string; type?: string; click?: () => void };
const menus: Item[][] = [];

vi.mock("electron", () => ({
  BrowserWindow: class {},
  Menu: { buildFromTemplate: (template: Item[]) => (menus.push(template), template) },
  Notification: class {},
  Tray: class {
    setToolTip(): void {}
    setContextMenu(): void {}
    isDestroyed(): boolean {
      return false;
    }
  },
  app: {},
  nativeImage: { createFromPath: () => ({ isEmpty: () => false, resize: () => ({}) }) },
  powerSaveBlocker: {},
}));

import { installTray } from "./shell";

const labels = (menu: Item[]) => menu.map((item) => item.label ?? item.role ?? item.type);

describe("the tray's menu", () => {
  it("offers New question… after Ask and Work, and its click opens the box", () => {
    const quickQuestion = vi.fn();
    installTray("/icons", { openAsk: vi.fn(), openWork: vi.fn(), quickQuestion });
    const menu = menus.at(-1)!;
    expect(labels(menu)).toEqual(["Ask", "Work", "New question…", "separator", "quit"]);
    menu.find((item) => item.label === "New question…")!.click!();
    expect(quickQuestion).toHaveBeenCalledOnce();
  });

  it("has no New question… when Quick Entry is not wired", () => {
    installTray("/icons", { openAsk: vi.fn(), openWork: vi.fn() });
    expect(labels(menus.at(-1)!)).toEqual(["Ask", "Work", "separator", "quit"]);
  });
});
