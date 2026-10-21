// Projects (DESKTOP_PARITY §4.5 item 13): folders with a primary, metadata kept whole, the context a
// project's thread is given, and that context kept out of what the user sees as their own words.
import { describe, expect, it } from "vitest";

import {
  INSTRUCTIONS_KEY, REMOVED_KEY, contextItem, contextStamp, isContextText, isRemoved, movedBefore, nameFromFolder, ordered, primaryFolder, projectContext,
  secondaryFolders, shownProjects, withMetadata, withPrimary, withoutContext,
} from "./projects";
import { userTexts } from "./composer";

const project = (fields: Partial<{ id: string; name: string; roots: { path: string }[]; metadata: Record<string, string>; position: number }> = {}) => ({
  id: "p1", name: "Launch", roots: [{ path: "/w/app" }, { path: "/w/docs" }], metadata: {}, position: 0, createdAt: 0, updatedAt: 0, recencyAt: null, ...fields,
});

describe("projects", () => {
  it("have a primary folder first, the others after it, and Make primary reorders them", () => {
    const p = project();
    expect(primaryFolder(p)).toBe("/w/app");
    expect(secondaryFolders(p)).toEqual(["/w/docs"]);
    expect(withPrimary(p.roots, "/w/docs")).toEqual([{ path: "/w/docs" }, { path: "/w/app" }]);
    expect(withPrimary(p.roots, "/nowhere")).toEqual(p.roots);
    expect(primaryFolder(project({ roots: [] }))).toBeNull();
    expect(nameFromFolder("/home/u/code/mightling/")).toBe("mightling");
  });

  it("keep every metadata key on a change (the server replaces the map whole), an empty value removing its key", () => {
    expect(withMetadata({ a: "1", [INSTRUCTIONS_KEY]: "old" }, { [INSTRUCTIONS_KEY]: "new" })).toEqual({ a: "1", [INSTRUCTIONS_KEY]: "new" });
    expect(withMetadata({ a: "1", [REMOVED_KEY]: "1" }, { [REMOVED_KEY]: null })).toEqual({ a: "1" });
    expect(withMetadata({}, { [INSTRUCTIONS_KEY]: "" })).toEqual({});
  });

  it("show the ones not removed, in the server's order", () => {
    const list = [project({ id: "b", position: 2 }), project({ id: "a", position: 1 }), project({ id: "r", position: 0, metadata: { [REMOVED_KEY]: "1" } })];
    expect(shownProjects(list).map((p) => p.id)).toEqual(["a", "b"]);
    expect(isRemoved(list[2])).toBe(true);
  });

  it("tell a thread their name, instructions, other folders and knowledge files, and nothing when there is nothing to tell", () => {
    const context = projectContext(project({ metadata: { [INSTRUCTIONS_KEY]: "Write for nurses, not doctors." } }), [{ name: "style.md", path: "/k/style.md", size: 10 }]);
    expect(context).toContain('the project "Launch"');
    expect(context).toContain("Write for nurses, not doctors.");
    expect(context).toContain("/w/docs");
    expect(context).toContain("- /k/style.md");
    expect(projectContext(project({ roots: [{ path: "/w/app" }] }), [])).toBeNull();
    // Told again only after it changed.
    expect(contextStamp(context)).toBe(contextStamp(context));
    expect(contextStamp(context)).not.toBe(contextStamp(`${context} `));
    expect(contextStamp(null)).toBe("");
  });

  it("keep the context out of the transcript, the thread's title and the composer's history", () => {
    const item = contextItem("This conversation belongs to the project \"Launch\".");
    expect(isContextText(item.text)).toBe(true);
    expect(isContextText("Fix the retry")).toBe(false);
    expect(withoutContext(`Fix the retry ${item.text}`)).toBe("Fix the retry");
    expect(withoutContext("Fix the retry")).toBe("Fix the retry");
    const view = { turns: [{ items: [{ type: "userMessage", content: [{ type: "text", text: "Fix the retry" }, item] }] }] };
    expect(userTexts(view as never)).toEqual(["Fix the retry"]);
  });

  it("keep a stable order: the user's dragged order first, the rest as they came, and a drag puts one just before another", () => {
    const keys = (list: string[]) => list;
    expect(ordered(["a", "b", "c"], (x) => x, [])).toEqual(["a", "b", "c"]);
    expect(ordered(["a", "b", "c"], (x) => x, ["c", "a"])).toEqual(["c", "a", "b"]);
    expect(movedBefore(keys(["a", "b", "c"]), [], "c", "a")).toEqual(["c", "a", "b"]);
    expect(movedBefore(keys(["a", "b", "c"]), ["c", "a", "b"], "a", "b")).toEqual(["c", "a", "b"]);
    expect(movedBefore(keys(["a", "b", "c"]), ["c", "a", "b"], "c", "b")).toEqual(["a", "c", "b"]);
    // A key no longer shown leaves the saved order.
    expect(movedBefore(keys(["a", "b"]), ["gone", "b", "a"], "a", "b")).toEqual(["a", "b"]);
  });
});

