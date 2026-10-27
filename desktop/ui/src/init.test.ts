// `/init`: an AGENTS.md for a repository (DESKTOP §16 B38): the turn's message, and where the command is offered.
import { describe, expect, it } from "vitest";

import { matchCommands, parseSlash } from "./composer";
import { AGENTS_FILE, agentsPath, initPrompt } from "./init";

describe("/init", () => {
  it("asks for a short AGENTS.md at the root, from the repository's commands, layout and conventions", () => {
    const prompt = initPrompt("none");
    expect(prompt.startsWith("Write a short AGENTS.md for this repository")).toBe(true);
    for (const words of ["root", "test", "lint", "format", "Layout", "Conventions", "Do not guess", "under about 60 lines", "Change no file but AGENTS.md."]) expect(prompt).toContain(words);
    // Even when the window found none, the root may have one: the agent checks and never replaces it.
    expect(prompt).toContain("If there is already an AGENTS.md at the root, do not replace it");
  });

  it("with one already there, proposes changes to it as edits to review in the diff", () => {
    const prompt = initPrompt("present");
    expect(prompt.startsWith("This repository already has an AGENTS.md")).toBe(true);
    expect(prompt).toContain("propose changes to it");
    expect(prompt).toContain("change only what is wrong, out of date or missing, as small edits to the file");
    expect(prompt).toContain("reviewed in the diff and undone");
    expect(prompt).not.toContain("Write a short");
  });

  it("passes on what was typed after the command as the user's note", () => {
    expect(initPrompt("none", "  mention the GPU tests ")).toMatch(/\n\nThe user adds: mention the GPU tests$/);
    expect(initPrompt("none", "   ")).not.toContain("The user adds");
  });

  it("reads the folder's AGENTS.md to choose", () => {
    expect(AGENTS_FILE).toBe("AGENTS.md");
    expect(agentsPath("/src/fetcher")).toBe("/src/fetcher/AGENTS.md");
    expect(agentsPath("/src/fetcher/")).toBe("/src/fetcher/AGENTS.md");
  });

  it("is in Work's / menu, not Ask's, and takes words", () => {
    expect(matchCommands("ini", { view: "work", hasThread: false })[0].name).toBe("init");
    expect(matchCommands("ini", { view: "ask", hasThread: true }).map((command) => command.name)).not.toContain("init");
    expect(parseSlash("/init mention the GPU tests")).toMatchObject({ command: { name: "init" }, args: "mention the GPU tests" });
  });
});
