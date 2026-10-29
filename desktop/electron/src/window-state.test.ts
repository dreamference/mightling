import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({ screen: {} }));

import { MAX_RESTORED, WindowSession, cascade, clamped, savedWindows } from "./window-state";

describe("restored bounds", () => {
  const area = { x: 0, y: 0, width: 1920, height: 1080 };
  it("stay on the display and above the minimum size", () => {
    expect(clamped({ x: 100, y: 100, width: 800, height: 600 }, area, { width: 480, height: 600 })).toEqual({ x: 100, y: 100, width: 800, height: 600 });
    // Off the right edge: pulled back in.
    expect(clamped({ x: 1800, y: 900, width: 800, height: 600 }, area, { width: 480, height: 600 })).toEqual({ x: 1120, y: 480, width: 800, height: 600 });
    // Bigger than the display: shrunk to it; smaller than the minimum: grown to it.
    expect(clamped({ x: 0, y: 0, width: 4000, height: 100 }, area, { width: 480, height: 600 })).toEqual({ x: 0, y: 0, width: 1920, height: 600 });
    expect(clamped(undefined, area, { width: 480, height: 600 })).toBeUndefined();
    // A display smaller than the minimum: the window starts at its corner, its title bar on it.
    expect(clamped({ x: 900, y: 700, width: 800, height: 700 }, { x: 1920, y: 0, width: 1024, height: 500 }, { width: 480, height: 600 })).toEqual({ x: 1920, y: 0, width: 800, height: 600 });
  });
});

describe("the session of full windows", () => {
  const scratch = () => fs.mkdtempSync(path.join(os.tmpdir(), "ling-window-session-"));
  const at = (x: number) => ({ x, y: 10, width: 800, height: 600 });

  it("reads only well-formed windows, at most eight", () => {
    expect(savedWindows(null)).toEqual([]);
    expect(savedWindows({ windows: [{ view: "work", bounds: at(1) }, { view: "chat" }, { view: "ask", bounds: { x: "1" } }, 7, { view: "ask", maximized: true }] }))
      .toEqual([{ view: "work", bounds: at(1) }, { view: "ask" }, { view: "ask", maximized: true }]);
    expect(savedWindows({ windows: Array.from({ length: 12 }, (_, index) => ({ view: "ask", bounds: at(index) })) })).toHaveLength(MAX_RESTORED);
  });

  it("keeps the windows open at a quit, least recently focused first, and forgets one closed while others stay", () => {
    const dir = scratch();
    const file = path.join(dir, "windows.json");
    const session = new WindowSession(file);
    session.add("1", { view: "ask", bounds: at(1) });
    session.add("2", { view: "work", bounds: at(2) });
    session.add("3", { view: "work" });
    session.update("3", { bounds: at(3), maximized: true });
    session.focused("1");
    session.closing("2", false);
    expect(session.snapshot()).toEqual([{ view: "work", bounds: at(3), maximized: true }, { view: "ask", bounds: at(1) }]);
    session.freeze();
    session.closing("3", false);
    session.closing("1", false);
    expect(new WindowSession(file).restored()).toEqual([{ view: "work", bounds: at(3), maximized: true }, { view: "ask", bounds: at(1) }]);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("keeps the last window closed, until another opens", () => {
    const dir = scratch();
    const file = path.join(dir, "windows.json");
    const session = new WindowSession(file);
    session.add("1", { view: "work", bounds: at(1) });
    session.closing("1", true);
    session.flush();
    expect(new WindowSession(file).restored()).toEqual([{ view: "work", bounds: at(1) }]);
    session.add("2", { view: "ask" });
    session.flush();
    expect(new WindowSession(file).restored()).toEqual([{ view: "ask" }]);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("starts from the single window of earlier versions when there is no session yet", () => {
    const dir = scratch();
    fs.writeFileSync(path.join(dir, "window-state-work.json"), JSON.stringify({ bounds: at(5), maximized: true }));
    expect(new WindowSession(path.join(dir, "windows.json"), path.join(dir, "window-state-work.json")).restored()).toEqual([{ view: "ask", bounds: at(5), maximized: true }]);
    expect(new WindowSession(path.join(dir, "windows.json"), path.join(dir, "nothing.json")).restored()).toEqual([]);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("opens a new window down and to the right of the one it came from, inside the work area", () => {
    const area = { x: 0, y: 0, width: 1920, height: 1080 };
    expect(cascade({ x: 100, y: 100, width: 800, height: 600 }, area)).toEqual({ x: 132, y: 132, width: 800, height: 600 });
    expect(cascade({ x: 1100, y: 470, width: 800, height: 600 }, area)).toEqual({ x: 0, y: 0, width: 800, height: 600 });
  });
});
