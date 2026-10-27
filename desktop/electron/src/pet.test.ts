// The pet (DESKTOP_PARITY Phase 3 item 6): what it shows, followed from the server's messages.
import { describe, expect, it } from "vitest";

import { PetStatus, petBounds, petImage } from "./pet";

describe("the pet's state", () => {
  it("follows turns, requests for the user, and what finished since it was last looked at", () => {
    const status = new PetStatus();
    expect(status.observe({ method: "turn/started", params: { threadId: "a", turn: {} } })).toBe(true);
    status.observe({ method: "turn/started", params: { threadId: "b", turn: {} } });
    expect(status.counts()).toEqual({ working: 2, waiting: 0, finished: 0 });
    expect(status.observe({ id: 7, method: "item/commandExecution/requestApproval", params: { threadId: "b" } })).toBe(true);
    expect(status.focus()).toBe("b");
    expect(status.answered(7)).toBe(true);
    status.observe({ id: 8, method: "item/fileChange/requestApproval", params: { threadId: "a" } });
    status.observe({ method: "serverRequest/resolved", params: { threadId: "a", requestId: 8 } });
    expect(status.counts().waiting).toBe(0);
    status.observe({ id: 9, method: "item/tool/requestUserInput", params: { threadId: "a" } });
    status.observe({ method: "turn/completed", params: { threadId: "a", turn: {} } });
    expect(status.counts()).toEqual({ working: 1, waiting: 0, finished: 1 });
    status.seen();
    expect(status.counts().finished).toBe(0);
    expect(status.observe({ method: "item/agentMessage/delta", params: { threadId: "b" } })).toBe(false);
    expect(status.observe("not a message")).toBe(false);
  });

  it("takes only a picture file it can show, small enough", () => {
    expect(petImage("relative.png")).toBeNull();
    expect(petImage("/nowhere/pet.png")).toBeNull();
    expect(petImage("/etc/hostname")).toBeNull();
    expect(petImage(null)).toBeNull();
  });

  it("lists the threads that need the user, by name: waiting here, then blocked (DESKTOP §16 B18)", () => {
    const status = new PetStatus();
    status.observe({ id: "w1␟1", result: { data: [{ id: "a", name: null, preview: "Fix the flaky test" }, { id: "b", name: "Release notes", preview: "x" }, { id: "c", name: "Bump deps", preview: "" }] } });
    status.observe({ id: 4, method: "item/commandExecution/requestApproval", params: { threadId: "a" } });
    status.observe({ method: "thread/status/changed", params: { threadId: "a", status: { type: "active", activeFlags: ["waitingOnApproval"] } } });
    // Waiting on an answer no request here can give: blocked.
    expect(status.observe({ method: "thread/status/changed", params: { threadId: "b", status: { type: "active", activeFlags: ["waitingOnUserInput"] } } })).toBe(true);
    status.observe({ method: "thread/status/changed", params: { threadId: "c", status: { type: "systemError" } } });
    expect(status.needs()).toEqual([
      { id: "a", title: "Fix the flaky test", why: "waiting" },
      { id: "b", title: "Release notes", why: "blocked" },
      { id: "c", title: "Bump deps", why: "blocked" },
    ]);
    expect(status.listed("b")).toBe(true);
    expect(status.listed("z")).toBe(false);
    // Back to idle: no longer listed.
    status.observe({ method: "thread/status/changed", params: { threadId: "b", status: { type: "idle" } } });
    expect(status.needs().map((need) => need.id)).toEqual(["a", "c"]);
  });

  it("opens where it was left, on a display still attached, else bottom right (DESKTOP §16 B18)", () => {
    const primary = { x: 0, y: 0, width: 1920, height: 1080 };
    const second = { x: 1920, y: 0, width: 1280, height: 1024 };
    expect(petBounds(104, null, [primary], primary)).toEqual({ x: 1792, y: 952 });
    expect(petBounds(104, { right: 600, bottom: 500 }, [primary, second], primary)).toEqual({ x: 496, y: 396 });
    expect(petBounds(104, { right: 2500, bottom: 300 }, [primary, second], primary)).toEqual({ x: 2396, y: 196 });
    // The second display is gone: back to the primary's corner.
    expect(petBounds(104, { right: 2500, bottom: 300 }, [primary], primary)).toEqual({ x: 1792, y: 952 });
    expect(petBounds(104, { right: "x" }, [primary], primary)).toEqual({ x: 1792, y: 952 });
  });
});

