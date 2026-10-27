// Review findings on their lines (DESKTOP §16 B12).
import { describe, expect, it } from "vitest";

import { parseDiff } from "./diff";
import { anchorLine, findingMessage, latestFindings, parseFindings, repoPath } from "./findings";

const REVIEW = `The change is close; two things.

Full review comments:

- Retry swallows the last error — /w/app/fetcher/client.py:14-16
  The loop returns None after the last attempt.
  Re-raise instead.

- [x] Unused import — /w/app/fetcher/util.py:3-3
  \`os\` is never used.`;

const DIFF = `diff --git a/fetcher/client.py b/fetcher/client.py
--- a/fetcher/client.py
+++ b/fetcher/client.py
@@ -12,4 +12,5 @@ def fetch(url):
     for attempt in range(attempts):
-        pass
+        try:
+            return get(url)
+        except ConnectionError:
             continue
`;

describe("review findings", () => {
  it("are read back from the review's text with their bodies", () => {
    const found = parseFindings(REVIEW);
    expect(found).toEqual([
      { title: "Retry swallows the last error", body: "The loop returns None after the last attempt.\nRe-raise instead.", path: "/w/app/fetcher/client.py", start: 14, end: 16 },
      { title: "Unused import", body: "`os` is never used.", path: "/w/app/fetcher/util.py", start: 3, end: 3 },
    ]);
    expect(parseFindings("Looks good to me.")).toEqual([]);
  });

  it("sit on the first line of their range the diff shows, or nowhere", () => {
    const [file] = parseDiff(DIFF);
    const [onClient, onUtil] = parseFindings(REVIEW);
    expect(repoPath(onClient, "/w/app/")).toBe("fetcher/client.py");
    expect(repoPath(onClient, "/elsewhere")).toBeNull();
    expect(anchorLine(file, onClient)).toBe(14);
    expect(anchorLine(file, { ...onUtil, path: onClient.path, start: 40, end: 41 })).toBeNull();
  });

  it("go to the composer as a request, and the latest review's are the ones shown", () => {
    const [onClient] = parseFindings(REVIEW);
    expect(findingMessage(onClient, "fetcher/client.py")).toBe("Please fix this review finding (fetcher/client.py:14-16): Retry swallows the last error\n\nThe loop returns None after the last attempt.\nRe-raise instead.");
    const turns = [{ items: [{ type: "exitedReviewMode", review: REVIEW }] }, { items: [{ type: "agentMessage" }] }, { items: [{ type: "exitedReviewMode", review: "- Only one — /w/a.py:1-2" }] }];
    expect(latestFindings(turns).map((f) => f.title)).toEqual(["Only one"]);
    expect(latestFindings([])).toEqual([]);
  });
});
