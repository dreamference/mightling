// The pet (DESKTOP_PARITY Phase 3 item 6): what it shows, followed from the server's messages.
import { describe, expect, it } from "vitest";

import { PetStatus, petImage } from "./pet";

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
});
