// A command's approval card and the web host's approval strip (DESKTOP §16 B8, specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §6.1, §10 G7).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ApprovalStrip, approvalStripRows, scrollApprovalIntoView } from "./ApprovalStrip";
import { commandChoices, FILE_CHOICES, phoneChoices, prefixLabel } from "./approvals";

const labels = (params: unknown, airgappedOn = false) => commandChoices(params, airgappedOn).map((choice) => choice.label);

describe("a command's approval", () => {
  it("offers the four plain choices when nothing more is proposed", () => {
    expect(labels({ command: "ls" })).toEqual(["Allow", "Allow for this session", "Decline", "Stop the turn"]);
  });

  it("offers to always allow the proposed prefix, answered with the amendment", () => {
    const choices = commandChoices({ command: "cargo test -p x", proposedExecpolicyAmendment: ["cargo", "test"] }, false);
    expect(choices.map((choice) => choice.label)).toEqual(["Allow", "Allow for this session", "Always allow cargo test …", "Decline", "Stop the turn"]);
    expect(choices[2].decision).toEqual({ acceptWithExecpolicyAmendment: { execpolicy_amendment: ["cargo", "test"] } });
    expect(labels({ proposedExecpolicyAmendment: [] })).not.toContain("Always allow  …");
  });

  it("offers a host to allow or block, never one to allow at airgapped on", () => {
    const params = { proposedNetworkPolicyAmendments: [{ host: "pypi.org", action: "allow" }, { host: "tracker.example", action: "deny" }, { host: "", action: "allow" }] };
    expect(labels(params)).toEqual(["Allow", "Allow for this session", "Always allow pypi.org", "Always block tracker.example", "Decline", "Stop the turn"]);
    expect(labels(params, true)).toEqual(["Allow", "Allow for this session", "Always block tracker.example", "Decline", "Stop the turn"]);
    expect(commandChoices(params, false)[2].decision).toEqual({ applyNetworkPolicyAmendment: { network_policy_amendment: { host: "pypi.org", action: "allow" } } });
  });

  it("offers only what the server lists when it lists the decisions", () => {
    const params = { proposedExecpolicyAmendment: ["rm"], availableDecisions: ["accept", { acceptWithExecpolicyAmendment: { execpolicy_amendment: ["npm", "run"] } }, "cancel"] };
    expect(labels(params)).toEqual(["Allow", "Always allow npm run …", "Stop the turn"]);
  });

  it("quotes a word with a space and shortens a long prefix", () => {
    expect(prefixLabel(["git", "commit", "-m", "a b"])).toBe('git commit -m "a b"');
    expect(prefixLabel(["x".repeat(80)]).length).toBe(60);
  });

  it("drops session-wide and policy-amendment choices for a chat-repos checkout on a phone", () => {
    const withRules = commandChoices(
      {
        command: "cargo test",
        proposedExecpolicyAmendment: ["cargo", "test"],
        proposedNetworkPolicyAmendments: [
          { host: "pypi.org", action: "allow" },
          { host: "tracker.example", action: "deny" },
        ],
      },
      false,
    );
    expect(phoneChoices(withRules).map((choice) => choice.label)).toEqual(["Allow", "Decline", "Stop the turn"]);
    expect(phoneChoices(withRules).map((choice) => choice.decision)).toEqual(["accept", "decline", "cancel"]);
    expect(phoneChoices(FILE_CHOICES).map((choice) => choice.label)).toEqual(["Allow", "Decline", "Stop the turn"]);
    expect(phoneChoices(FILE_CHOICES).map((choice) => choice.decision)).toEqual(["accept", "decline", "cancel"]);
  });
});

describe("the Waiting for you approval strip (G7)", () => {
  const threads = {
    "thread-1": { id: "thread-1", name: "Fix login", preview: "First turn" },
    "thread-2": { id: "thread-2", name: null, preview: "Add tests for repo picker" },
  };

  it("computes one row per waiting thread in request order with counts and fallback labels", () => {
    const rows = approvalStripRows(
      [{ threadId: "thread-1" }, { threadId: "thread-2" }, { threadId: "thread-1" }, { threadId: "thread-3" }],
      threads,
    );
    expect(rows).toEqual([
      { threadId: "thread-1", label: "Fix login", count: 2 },
      { threadId: "thread-2", label: "Add tests for repo picker", count: 1 },
      { threadId: "thread-3", label: "thread-3", count: 1 },
    ]);
  });

  it("includes the deep-linked thread when approvalRequested is true before its request arrives", () => {
    const rows = approvalStripRows([], threads, { approvalRequested: true, selectedThreadId: "thread-2" });
    expect(rows).toEqual([{ threadId: "thread-2", label: "Add tests for repo picker", count: 1 }]);
  });

  it("renders Waiting for you in the web host when requests wait and disappears when nothing waits", () => {
    const emptyHtml = renderToStaticMarkup(
      createElement(ApprovalStrip, {
        host: "web",
        approvalRequested: false,
        requests: [],
        threads,
        openThread: () => {},
      }),
    );
    expect(emptyHtml).toBe("");

    const waitingHtml = renderToStaticMarkup(
      createElement(ApprovalStrip, {
        host: "web",
        approvalRequested: false,
        requests: [{ threadId: "thread-1" }, { threadId: "thread-2" }],
        threads,
        openThread: () => {},
      }),
    );
    expect(waitingHtml).toContain("Waiting for you");
    expect(waitingHtml).toContain("2 approvals waiting");
    expect(waitingHtml).toContain("Fix login");
    expect(waitingHtml).toContain("Add tests for repo picker");
  });

  it("stays hidden in the desktop host unless approvalRequested is set", () => {
    const desktopIdle = renderToStaticMarkup(
      createElement(ApprovalStrip, {
        host: "electron",
        approvalRequested: false,
        requests: [{ threadId: "thread-1" }],
        threads,
        openThread: () => {},
      }),
    );
    expect(desktopIdle).toBe("");

    const desktopDeepLinked = renderToStaticMarkup(
      createElement(ApprovalStrip, {
        host: "electron",
        approvalRequested: true,
        requests: [],
        threads,
        selectedThreadId: "thread-1",
        openThread: () => {},
      }),
    );
    expect(desktopDeepLinked).toContain("Waiting for you");
    expect(desktopDeepLinked).toContain("1 approval waiting");
    expect(desktopDeepLinked).toContain("Fix login");
  });

  it("scrolls the approval card into view and focuses no button (a stray Enter must not approve)", () => {
    const focus = vi.fn();
    const scrollIntoView = vi.fn();
    const fakeDoc = {
      querySelector: (selector: string) =>
        selector === ".approval"
          ? {
              scrollIntoView,
              querySelector: (inner: string) => (inner === "button" ? { focus } : null),
            }
          : null,
    };
    expect(scrollApprovalIntoView(fakeDoc)).toBe(true);
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "center" });
    expect(focus).not.toHaveBeenCalled();
    expect(scrollApprovalIntoView({ querySelector: () => null })).toBe(false);
  });
});
