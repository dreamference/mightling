// The Quick Entry box and the pet are the app's page too (`#quick`, `#pet`) on the same preload and
// channel: each is made sandboxed, without Node, opens no window and navigates nowhere, and shows
// itself without taking the focus from the app the user is in.
import { beforeEach, describe, expect, it, vi } from "vitest";

type Handler = (event: { preventDefault(): void }, url?: string) => void;
const made: { options: Record<string, unknown>; handlers: Record<string, Handler>; once: Record<string, () => void>; open: ((details: { url: string }) => unknown) | null; loaded: string | null; calls: string[]; position: number[] | null; bounds: Record<string, number> | null }[] = [];

vi.mock("electron", () => {
  class BrowserWindow {
    webContents: Record<string, unknown>;
    entry: (typeof made)[number];
    constructor(options: Record<string, unknown>) {
      const entry: (typeof made)[number] = { options, handlers: {}, once: {}, open: null, loaded: null, calls: [], position: null, bounds: null };
      made.push(entry);
      this.entry = entry;
      this.webContents = {
        on: (name: string, handler: Handler) => (entry.handlers[name] = handler),
        setWindowOpenHandler: (handler: (details: { url: string }) => unknown) => (entry.open = handler),
      };
    }
    loadURL(url: string) {
      this.entry.loaded = url;
      return Promise.resolve();
    }
    setAlwaysOnTop(on: boolean, level: string) {
      this.entry.calls.push(`top:${on}:${level}`);
    }
    once(name: string, handler: () => void) {
      this.entry.once[name] = handler;
    }
    show() {
      this.entry.calls.push("show");
    }
    showInactive() {
      this.entry.calls.push("showInactive");
    }
    setPosition(x: number, y: number) {
      this.entry.position = [x, y];
    }
    getBounds() {
      return { x: 1000, y: 700, width: 104, height: 104 };
    }
    setBounds(bounds: Record<string, number>) {
      this.entry.bounds = bounds;
    }
  }
  const workArea = { x: 1920, y: 0, width: 2560, height: 1440 };
  const screen = {
    getAllDisplays: () => [{ workArea }],
    getPrimaryDisplay: () => ({ workArea }),
    getCursorScreenPoint: () => ({ x: 2000, y: 100 }),
    getDisplayNearestPoint: () => ({ workArea }),
  };
  return { BrowserWindow, app: { isPackaged: true, getPath: () => "/tmp/ling-user-data" }, screen, shell: { openExternal: vi.fn() }, protocol: { registerSchemesAsPrivileged: vi.fn() } };
});

import { shell } from "electron";
import { WORK_URL } from "./app-protocol";
import { BUBBLE, PET_SIZES, fitPet, openPet } from "./pet";
import { QUICK_SIZE, openQuick, placeQuick } from "./quick";

beforeEach(() => {
  made.length = 0;
  vi.mocked(shell.openExternal).mockClear();
});

/** What every small window must hold: the renderer locked down, no window opened, no navigation, nothing handed to the browser. */
function locked(entry: (typeof made)[number], fragment: string): void {
  expect(entry.loaded).toBe(`${WORK_URL}#${fragment}`);
  expect(entry.options.webPreferences).toMatchObject({ preload: "/p/preload.cjs", sandbox: true, contextIsolation: true, nodeIntegration: false, devTools: false });
  expect(entry.options.webPreferences).not.toHaveProperty("webviewTag", true);
  expect(entry.options.show).toBe(false);
  for (const url of ["https://example.com/", "app://-/index.html", `${WORK_URL}#work`, "file:///etc/passwd", "javascript:alert(1)"]) {
    expect(entry.open?.({ url }), url).toEqual({ action: "deny" });
    // Even the app's own page: the box and the pet never become a full window by navigating.
    const stopped = vi.fn();
    entry.handlers["will-navigate"]({ preventDefault: stopped }, url);
    expect(stopped, url).toHaveBeenCalled();
  }
  expect(shell.openExternal).not.toHaveBeenCalled();
}

describe("the Quick Entry box's window", () => {
  it("is the app's page at #quick, sandboxed, opening nothing and going nowhere", () => {
    openQuick("/p/preload.cjs", "dark");
    expect(made).toHaveLength(1);
    locked(made[0], "quick");
    expect(made[0].options.webPreferences).toMatchObject({ additionalArguments: ["--mightling-theme=dark"] });
    expect(made[0].options).toMatchObject({ ...QUICK_SIZE, frame: false, alwaysOnTop: true, skipTaskbar: true, backgroundColor: "#212121" });
    expect(made[0].calls).toEqual(["top:true:floating"]);
  });

  it("sits centred in the upper third of the screen the pointer is on", () => {
    const window = openQuick("/p/preload.cjs", "light");
    expect(made[0].options.backgroundColor).toBe("#ffffff");
    placeQuick(window);
    expect(made[0].position).toEqual([1920 + (2560 - QUICK_SIZE.width) / 2, 1440 / 4]);
  });
});

describe("the pet's window", () => {
  it("is the app's page at #pet, sandboxed, opening nothing and going nowhere", () => {
    openPet("/p/preload.cjs", "medium", false, "light");
    expect(made).toHaveLength(1);
    locked(made[0], "pet");
    expect(made[0].options).toMatchObject({ width: PET_SIZES.medium, height: PET_SIZES.medium, transparent: true, alwaysOnTop: true, skipTaskbar: true });
    // Hidden until the setting shows it.
    expect(made[0].once["ready-to-show"]).toBeUndefined();
  });

  it("shows itself without taking the focus when asked to", () => {
    openPet("/p/preload.cjs", "small", true, "dark");
    made[0].once["ready-to-show"]();
    expect(made[0].calls).toEqual(["top:true:floating", "showInactive"]);
  });

  it("grows for its bubble from the corner it sits in, and back", () => {
    const window = openPet("/p/preload.cjs", "medium", false, "dark");
    fitPet(window, "medium", true);
    expect(made[0].bounds).toEqual({ x: 1104 - BUBBLE.width, y: 804 - (PET_SIZES.medium + BUBBLE.height), width: BUBBLE.width, height: PET_SIZES.medium + BUBBLE.height });
    fitPet(window, "medium", false);
    expect(made[0].bounds).toEqual({ x: 1000, y: 700, width: 104, height: 104 });
  });
});
