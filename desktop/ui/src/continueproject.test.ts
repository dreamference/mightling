// Continue in a project (DESKTOP §16 B33): which projects are offered, and the fork into one against
// the scripted stand-in, including a copy the policy kept in the question's folder.

import { describe, expect, it } from "vitest";

import { continueInProject, continueTargets } from "./continueproject";
import { REMOVED_KEY } from "./projects";
import type { Project } from "./protocol/v2/Project";
import { ScriptedServer } from "./scripted-server";

const project = (id: string, name: string, roots: string[], position: number, metadata: Record<string, string> = {}): Project =>
  ({ id, name, roots: roots.map((path) => ({ path })), metadata, position, createdAt: 0, updatedAt: 0, recencyAt: null }) as Project;

const ASK = "/home/u/.mightling/ask";

describe("continue in a project", () => {
  it("offers the shown projects with a folder, in the sidebar's order, by their primary folder", () => {
    const projects = [
      project("p2", "Website", ["/work/site", "/work/shared"], 2),
      project("p1", "Fetcher", ["/work/fetcher"], 1),
      project("p3", "Reading", [], 3),
      project("p4", "Old", ["/work/old"], 0, { [REMOVED_KEY]: "1" }),
    ];
    expect(continueTargets(projects)).toEqual([
      { id: "p1", name: "Fetcher", folder: "/work/fetcher" },
      { id: "p2", name: "Website", folder: "/work/site" },
    ]);
    expect(continueTargets([])).toEqual([]);
  });

  it("forks the question into the primary folder and puts the copy in the project", async () => {
    const server = new ScriptedServer({
      "thread/fork": (params) => ({ thread: { id: "q1-copy", cwd: params.cwd, projectId: null } }),
      "thread/metadata/update": (params) => ({ thread: { id: params.threadId, cwd: "/work/fetcher", projectId: params.projectId } }),
    });
    const thread = await continueInProject(server.client, { id: "q1" }, { id: "p1", name: "Fetcher", folder: "/work/fetcher" }, "qwen3.8-27b", ASK);
    expect(thread).toMatchObject({ id: "q1-copy", cwd: "/work/fetcher", projectId: "p1" });
    expect(server.sent).toEqual([
      { method: "thread/fork", params: { threadId: "q1", model: "qwen3.8-27b", cwd: "/work/fetcher" } },
      { method: "thread/metadata/update", params: { threadId: "q1-copy", projectId: "p1" } },
    ]);
  });

  it("deletes a copy the policy kept in the question's folder, and says why", async () => {
    const server = new ScriptedServer({
      "thread/fork": () => ({ thread: { id: "q1-copy", cwd: `${ASK}/q-1a2b3c`, projectId: null } }),
      "thread/delete": () => ({}),
    });
    await expect(continueInProject(server.client, { id: "q1" }, { id: "p1", name: "Fetcher", folder: "/gone" }, null, ASK)).rejects.toThrow(/could not be continued in Fetcher: its folder \/gone is missing/);
    expect(server.methods()).toEqual(["thread/fork", "thread/delete"]);
  });
});
