// File tabs and the file tree (DESKTOP_PARITY Phase 3 item 1): the tab list's rules, and how a file
// the server read is decoded, named and highlighted.
import { describe, expect, it } from "vitest";

import { decodeFile, highlighted, inGitInternals, joinPath, languageOf, lineCount, parentOf, relativeTo, sizeLabel, treeEntries } from "./files";
import { folderError } from "./FileTree";
import { REVIEW_TAB, fileTab, stepTab, tabLabel, withTab, withoutTab } from "./tabs";

const b64 = (text: string) => btoa(String.fromCharCode(...new TextEncoder().encode(text)));

describe("tabs", () => {
  it("opens one tab per file, a second open moving it to the line asked", () => {
    let tabs = withTab([], REVIEW_TAB);
    tabs = withTab(tabs, fileTab("/w/a.py"));
    tabs = withTab(tabs, fileTab("/w/b.py"));
    tabs = withTab(tabs, fileTab("/w/a.py", 12));
    expect(tabs.map(tabLabel)).toEqual(["Review", "a.py", "b.py"]);
    expect(tabs[1]).toMatchObject({ kind: "file", line: 12 });
  });

  it("gives the focus to the right neighbour of a closed tab, else the left", () => {
    const tabs = [REVIEW_TAB, fileTab("/w/a.py"), fileTab("/w/b.py")];
    expect(withoutTab(tabs, "file:/w/a.py", "file:/w/a.py").active).toBe("file:/w/b.py");
    expect(withoutTab(tabs, "file:/w/b.py", "file:/w/b.py").active).toBe("file:/w/a.py");
    expect(withoutTab(tabs, "file:/w/b.py", "review")).toMatchObject({ active: "review" });
    expect(withoutTab([REVIEW_TAB], "review", "review")).toEqual({ tabs: [], active: null });
    expect(stepTab(tabs, "file:/w/b.py", 1)).toBe("review");
    expect(stepTab(tabs, "review", -1)).toBe("file:/w/b.py");
  });
});

describe("files", () => {
  it("lists folders first, then files, by name ignoring case, without .git", () => {
    const entries = [
      { fileName: "b.py", isDirectory: false, isFile: true },
      { fileName: ".git", isDirectory: true, isFile: false },
      { fileName: "Src", isDirectory: true, isFile: false },
      { fileName: "A.md", isDirectory: false, isFile: true },
      { fileName: "docs", isDirectory: true, isFile: false },
    ];
    expect(treeEntries(entries).map((entry) => entry.fileName)).toEqual(["docs", "Src", "A.md", "b.py"]);
  });

  it("decodes text, and calls a file with a NUL byte or bad UTF-8 binary", () => {
    expect(decodeFile(b64("héllo\n"))).toEqual({ text: "héllo\n", bytes: 7 });
    expect(decodeFile(btoa("\x89PNG\r\n\x1a\n\x00\x00"))).toEqual({ text: null, bytes: 10 });
    expect(decodeFile(btoa("\xff\xfe\xfa")).text).toBeNull();
  });

  it("finds the language from the name, and highlights with everything escaped", () => {
    expect(languageOf("/w/fetcher/client.py")).toBe("python");
    expect(languageOf("/w/Dockerfile")).toBe("dockerfile");
    expect(languageOf("/w/Cargo.toml")).toBe("ini");
    expect(languageOf("/w/notes.unknownext")).toBeNull();
    expect(highlighted("def f():\n    return '<b>'\n", "python")).toContain('<span class="hljs-keyword">def</span>');
    expect(highlighted("<script>alert(1)</script>", null)).toBe("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(highlighted("<img src=x onerror=alert(1)>", "python")).not.toContain("<img");
  });

  it("joins, splits and shortens paths the way the tree and tabs show them", () => {
    expect(joinPath("/w/", "a.py")).toBe("/w/a.py");
    expect(parentOf("/w/src/a.py")).toBe("/w/src");
    expect(parentOf("/a.py")).toBe("/");
    expect(relativeTo("/w", "/w/src/a.py")).toBe("src/a.py");
    expect(relativeTo("/w", "/elsewhere/a.py")).toBe("/elsewhere/a.py");
    expect(lineCount("a\nb\n")).toBe(2);
    expect(lineCount("a\nb")).toBe(2);
    expect(sizeLabel(812)).toBe("812 B");
    expect(sizeLabel(14_540)).toBe("14.2 KB");
  });
});

describe("search files and go to line", () => {
  it("reads a line after the name, or alone for the file in front", async () => {
    const { parseFileQuery, lineWithin } = await import("./files");
    expect(parseFileQuery("hero.ts")).toEqual({ text: "hero.ts", line: null });
    expect(parseFileQuery("src/hero.ts:12")).toEqual({ text: "src/hero.ts", line: 12 });
    expect(parseFileQuery("hero.ts:12:5")).toEqual({ text: "hero.ts", line: 12 });
    expect(parseFileQuery(":40")).toEqual({ text: "", line: 40 });
    expect(parseFileQuery(":0")).toEqual({ text: "", line: null });
    expect(lineWithin("500", 68)).toBe(68);
    expect(lineWithin(":3", 68)).toBe(3);
    expect(lineWithin("x", 68)).toBeNull();
  });
});

describe("search files", () => {
  it("leaves out what is inside .git, and nothing else", () => {
    for (const internal of [".git/HEAD", ".git/refs/heads/main", ".git/hooks/pre-commit.sample", "sub/.git/config", ".git\\worktrees\\a\\HEAD", ".git"]) expect(inGitInternals(internal)).toBe(true);
    for (const file of [".gitignore", ".github/workflows/ci.yml", "src/git.ts", "docs/.gitkeep", "a.git/b"]) expect(inGitInternals(file)).toBe(false);
  });
});

describe("a folder the tree cannot read", () => {
  it("says what macOS's access prompt means, and passes anything else on", () => {
    expect(folderError("Interrupted system call (os error 4)")).toMatch(/macOS is asking/);
    expect(folderError("Operation not permitted (os error 1)")).toMatch(/Files and Folders/);
    expect(folderError("No such file or directory (os error 2)")).toBe("No such file or directory (os error 2)");
  });
});
