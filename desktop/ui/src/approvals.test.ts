// A command's approval card: the plain choices, and a rule for later when the server proposes one (DESKTOP §16 B8).
import { describe, expect, it } from "vitest";

import { commandChoices, prefixLabel } from "./approvals";

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
});
