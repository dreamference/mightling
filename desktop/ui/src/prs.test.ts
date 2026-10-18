// The pull-request tab (DESKTOP_PARITY Phase 3 item 9): checks in words, and what goes to the composer.
import { describe, expect, it } from "vitest";

import { checkState, fixRequest, reviewRequest } from "./PullRequestsTab";

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
