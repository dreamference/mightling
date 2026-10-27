// The pull-request tab (DESKTOP_PARITY Phase 3 item 9): checks in words, and what goes to the composer.
import { describe, expect, it } from "vitest";

import { activityEntries, checkState, fixRequest, reviewRequest } from "./PullRequestsTab";

const pr = { number: 12, title: "Retry with backoff", headRefName: "retry", baseRefName: "main", author: { login: "ada" }, isDraft: false, reviewDecision: null, updatedAt: "", url: "https://example.com/pr/12" };

describe("the pull-request tab", () => {
  it("reads checks of either kind GitHub reports", () => {
    expect(checkState({ name: "tests", status: "COMPLETED", conclusion: "FAILURE" })).toEqual({ name: "tests", state: "failed" });
    expect(checkState({ name: "lint", status: "COMPLETED", conclusion: "SUCCESS" })).toEqual({ name: "lint", state: "passed" });
    expect(checkState({ context: "ci/legacy", state: "PENDING" })).toEqual({ name: "ci/legacy", state: "running" });
    expect(checkState({ name: "docs", status: "COMPLETED", conclusion: "SKIPPED" }).state).toBe("skipped");
  });

  it("asks the local model to review or fix, and never to push or comment", () => {
    expect(reviewRequest(pr)).toContain("gh pr diff 12");
    expect(reviewRequest(pr)).toContain("Do not push anything or comment on GitHub");
    expect(fixRequest(pr, "tests")).toContain('The check "tests" fails on pull request #12');
    expect(fixRequest(pr, "tests")).toContain("Do not push.");
  });
});

describe("a pull request's comments and reviews (DESKTOP §16 B32)", () => {
  it("lists comments and submitted reviews in the order they were made, in words", () => {
    const entries = activityEntries({
      comments: [{ author: { login: "ada" }, body: "Why 2 s?", createdAt: "2026-10-20T09:00:00Z" }, { author: null, body: "Bump.", createdAt: "2026-10-22T09:00:00Z" }],
      reviews: [
        { author: { login: "brin" }, body: "", state: "APPROVED", submittedAt: "2026-10-21T09:00:00Z" },
        { author: { login: "cy" }, body: "Needs a test.", state: "CHANGES_REQUESTED", submittedAt: "2026-10-19T09:00:00Z" },
        { author: { login: "me" }, body: "draft", state: "PENDING", submittedAt: null },
      ],
    });
    expect(entries.map((entry) => `${entry.who} ${entry.what}: ${entry.body}`)).toEqual(["cy requested changes: Needs a test.", "ada commented: Why 2 s?", "brin approved: ", "someone commented: Bump."]);
  });

  it("is empty when gh answered without them", () => {
    expect(activityEntries({})).toEqual([]);
    expect(activityEntries(null)).toEqual([]);
  });
});
