// The review pane's diff reader (DESKTOP_PARITY §2.5): files, hunks and line numbers; one hunk as a
// patch; the split view's rows; the comments sent to the composer.
import { describe, expect, it } from "vitest";

import { commentsMessage, filePatch, fingerprint, hunkPatch, parseDiff, splitRows, underDirectory } from "./diff";

const GIT = `diff --git a/a.txt b/a.txt
index 1111111..2222222 100644
--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 line 1
-line 2
+line 2 changed
 line 3
@@ -34,3 +34,4 @@
 line 34
 line 35
+line 35b
 line 36
diff --git a/new.txt b/new.txt
new file mode 100644
index 0000000..ce01362
--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+hello
\\ No newline at end of file
`;

describe("the diff reader", () => {
  it("reads git's diff: files, their status, hunks and both line numbers", () => {
    const files = parseDiff(GIT);
    expect(files.map((file) => [file.path, file.status, file.added, file.removed, file.hunks.length])).toEqual([
      ["a.txt", "modified", 2, 1, 2],
      ["new.txt", "added", 1, 0, 1],
    ]);
    const [first] = files[0].hunks;
    expect(first.lines.map((line) => [line.kind, line.oldNo, line.newNo])).toEqual([
      ["context", 1, 1], ["del", 2, null], ["add", null, 2], ["context", 3, 3],
    ]);
    expect(files[0].hunks[1].lines[2]).toEqual({ kind: "add", text: "line 35b", oldNo: null, newNo: 36 });
    expect(files[1].hunks[0].lines.map((line) => line.kind)).toEqual(["add", "note"]);
  });

  it("reads a diff without `diff --git` lines, and a removed line that looks like a header", () => {
    const plain = `--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n--- not a header\n+++ not one either\n same\n--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-a\n+b\n`;
    const files = parseDiff(plain);
    expect(files.map((file) => file.path)).toEqual(["x.py", "y.py"]);
    expect(files[0].hunks[0].lines.map((line) => `${line.kind}:${line.text}`)).toEqual(["del:-- not a header", "add:++ not one either", "context:same"]);
  });

  it("makes a patch of one hunk with the file's header, for git apply", () => {
    const [file] = parseDiff(GIT);
    expect(hunkPatch(file, 1)).toBe(`diff --git a/a.txt b/a.txt
index 1111111..2222222 100644
--- a/a.txt
+++ b/a.txt
@@ -34,3 +34,4 @@
 line 34
 line 35
+line 35b
 line 36
`);
    expect(filePatch(parseDiff(GIT)[1])).toContain("new file mode 100644\n");
    expect(filePatch(parseDiff(GIT)[1])).toContain("+hello\n\\ No newline at end of file\n");
  });

  it("pairs removals with their replacements in the split view", () => {
    const [file] = parseDiff(GIT);
    expect(splitRows(file.hunks[0]).map((row) => [row.left?.text ?? null, row.right?.text ?? null])).toEqual([
      ["line 1", "line 1"], ["line 2", "line 2 changed"], ["line 3", "line 3"],
    ]);
  });

  it("marks a file viewed until its changes change, and sends comments to the composer", () => {
    const [file] = parseDiff(GIT);
    expect(fingerprint(file)).toBe(fingerprint(parseDiff(GIT)[0]));
    expect(fingerprint(file)).not.toBe(fingerprint(parseDiff(GIT.replace("line 35b", "line 35c"))[0]));
    expect(commentsMessage([
      { path: "a.txt", line: 2, side: "new", quote: "line 2 changed", text: "Say why it changed." },
      { path: "a.txt", line: 2, side: "old", quote: "line 2", text: "Keep this one." },
    ])).toBe("Please address these review comments:\n- a.txt:2 `line 2 changed`: Say why it changed.\n- a.txt:2 (removed line) `line 2`: Keep this one.");
    expect(commentsMessage([])).toBe("");
  });
});

describe("a turn's diff under a folder of the repository", () => {
  it("moves every path under the folder, and nothing else", () => {
    const diff = "diff --git a/greet.py b/greet.py\n--- a/greet.py\n+++ b/greet.py\n@@ -1 +1 @@\n-print('Hello ')\n+print('Hi, ')\n";
    expect(underDirectory(diff, "app")).toBe("diff --git a/app/greet.py b/app/greet.py\n--- a/app/greet.py\n+++ b/app/greet.py\n@@ -1 +1 @@\n-print('Hello ')\n+print('Hi, ')\n");
    expect(underDirectory(diff, "src\\app\\")).toContain("+++ b/src/app/greet.py");
    expect(underDirectory(diff, "")).toBe(diff);
    // A new file keeps /dev/null.
    expect(underDirectory("--- /dev/null\n+++ b/new.txt\n", "app")).toBe("--- /dev/null\n+++ b/app/new.txt\n");
  });
});
