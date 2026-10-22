// Threads from other devices (specs/DREAMFERENCE_MIGHTLING_SYNC.md, Phase 1): the page's side of the
// node's sync hub, over a stand-in `fetch`.
import { describe, expect, it } from "vitest";

import { ago, byDevice, elsewhere, folderName, hub, type SyncedThread } from "./sync";

const thread = (id: string, owner: string, updated: number, archived = false): SyncedThread => ({
  thread: id, owner, owner_name: `${owner}'s`, title: `t ${id}`, archived, cwd: "/home/u/app", source: "vscode", created: 1, updated, fork_of: null,
});

function stand_in(answers: Record<string, [number, unknown]>) {
  const sent: { url: string; init?: RequestInit }[] = [];
  const fetcher = async (url: string, init?: RequestInit) => {
    sent.push({ url, init });
    const [status, body] = answers[url.split("?")[0]] ?? [404, { error: "Not found." }];
    return new Response(JSON.stringify(body), { status });
  };
  return { sent, api: hub(fetcher) };
}

describe("threads from other devices", () => {
  it("leaves this machine's own threads out, groups the rest by device and puts the newest first", () => {
    const threads = [thread("a", "lap", 10), thread("b", "node", 30), thread("c", "desk", 20), thread("d", "lap", 40), thread("e", "lap", 50, true)];
    const groups = byDevice(threads, "node", false);
    expect(groups.map((group) => [group.name, group.threads.map((t) => t.thread)])).toEqual([["lap's", ["d", "a"]], ["desk's", ["c"]]]);
    expect(byDevice(threads, "node", true).map((group) => group.threads.map((t) => t.thread))).toEqual([["e"]]);
    expect(elsewhere(threads, "node")).toBe(3);
    expect(elsewhere(threads, null)).toBe(4);
  });

  it("is nothing while the hub is off: its routes answer 404", async () => {
    const { api } = stand_in({});
    expect(await api.status()).toBeNull();
    const { api: on } = stand_in({ "/api/sync/status": [200, { enabled: true, cursor: 3, this_device: null, caller: { device: "ph", agent: false, scope: "watch" } }] });
    expect((await on.status())?.caller.scope).toBe("watch");
  });

  it("archives and deletes through the hub, and says why the hub refused", async () => {
    const { api, sent } = stand_in({
      "/api/sync/threads/x/meta": [200, {}],
      "/api/sync/threads/x/delete": [403, { error: "this device may watch threads, not delete them" }],
      "/api/sync/manifest": [200, { cursor: 9 }],
    });
    await api.archive("x", true);
    expect(sent[0].init?.method).toBe("POST");
    expect(JSON.parse(String(sent[0].init?.body))).toEqual({ archived: true });
    await expect(api.remove("x")).rejects.toThrow("may watch threads, not delete them");
    expect(await api.changed(4)).toBe(9);
    expect(sent[2].url).toBe("/api/sync/manifest?since=4&wait=25");
  });

  it("names times and folders briefly", () => {
    expect(ago(1000, 1030)).toBe("just now");
    expect(ago(1000, 1000 + 180)).toBe("3 min ago");
    expect(ago(0, 7200)).toBe("2 h ago");
    expect(ago(0, 3 * 86400)).toBe("3 days ago");
    expect(folderName("/home/u/app/")).toBe("app");
    expect(folderName(null)).toBe("");
  });
});
