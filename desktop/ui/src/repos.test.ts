// Several repositories in the review pane (DESKTOP §16 B32): the selector's labels, and Last turn over
// All repos or one, its files placed by the repository whose top level holds them.
import { describe, expect, it } from "vitest";

import { parseDiff } from "./diff";
import { filesIn, repoName, repoOf, repoOption, turnBase, turnLines } from "./repos";

const file = (path: string, added: number, removed: number) =>
  `diff --git a/${path} b/${path}\n--- a/${path}\n+++ b/${path}\n@@ -1,${removed} +1,${added} @@\n${"-old\n".repeat(removed)}${"+new\n".repeat(added)}`;

const cwd = "/home/u/src/shop";
const repos = ["/home/u/src/shop/api", "/home/u/src/shop/web", "/home/u/src/shop/web/vendor/lib"];
const turn = parseDiff(file("api/server.py", 4, 1) + file("web/app.ts", 2, 2) + file("web/vendor/lib/x.c", 1, 0) + file("notes.md", 3, 0));

describe("several repositories", () => {
  it("labels each repository with its lines changed", () => {
    expect(repoName("/home/u/src/shop/api/")).toBe("api");
    expect(repoName("C:\\src\\shop\\web")).toBe("web");
    expect(repoOption("api", { added: 9, removed: 3 })).toBe("api +9 −3");
    expect(repoOption("api", undefined)).toBe("api");
  });

  it("places a turn's file in the innermost repository holding it, by the thread's folder", () => {
    expect(repoOf("api/server.py", cwd, repos)).toBe(repos[0]);
    expect(repoOf("web/vendor/lib/x.c", cwd, repos)).toBe(repos[2]);
    expect(repoOf("notes.md", cwd, repos)).toBeNull();
    expect(repoOf("/home/u/src/shop/web/app.ts", null, repos)).toBe(repos[1]);
    expect(repoOf("web2/app.ts", cwd, repos)).toBeNull();
    expect(repoOf("app.ts", null, repos)).toBeNull();
  });

  it("starts Last turn's paths where the server does: the folder above several repositories, else the repository holding it", () => {
    expect(turnBase(cwd, repos, repos[0])).toBe(cwd);
    expect(turnBase("/home/u/src/shop/api/server", ["/home/u/src/shop/api"], "/home/u/src/shop/api")).toBe("/home/u/src/shop/api");
    expect(turnBase("/home/u/src/shop/api", ["/home/u/src/shop/api"], "/home/u/src/shop/api")).toBe("/home/u/src/shop/api");
    expect(turnBase("/home/u/notes", [], null)).toBe("/home/u/notes");
  });

  it("counts the turn per repository and in all of them, and shows All repos or one", () => {
    const { total, byRepo } = turnLines(turn, cwd, repos);
    expect(total).toEqual({ added: 10, removed: 3 });
    expect(byRepo.get(repos[0])).toEqual({ added: 4, removed: 1 });
    expect(byRepo.get(repos[1])).toEqual({ added: 2, removed: 2 });
    expect(byRepo.get(repos[2])).toEqual({ added: 1, removed: 0 });
    expect(filesIn(turn, cwd, repos, null).map((entry) => entry.path)).toEqual(["api/server.py", "web/app.ts", "web/vendor/lib/x.c", "notes.md"]);
    expect(filesIn(turn, cwd, repos, repos[1]).map((entry) => entry.path)).toEqual(["web/app.ts"]);
  });
});
