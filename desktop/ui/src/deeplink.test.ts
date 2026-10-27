import { describe, expect, it } from "vitest";
import {
  applyOpenTarget,
  consumeOpenTarget,
  parseOpenTarget,
  validOpenThreadId,
  validRepoName,
} from "./bridge";

describe("Mini App and web query deep links (CHAT_REPOS §12.4 G3)", () => {
  it("validates <org>/<repo> with the same rules as chat_repos.rs", () => {
    expect(validRepoName("acme/widgets")).toBe(true);
    expect(validRepoName("org_1/repo-2.js")).toBe(true);
    expect(validRepoName("")).toBe(false);
    expect(validRepoName("acme")).toBe(false);
    expect(validRepoName("acme/widgets/extra")).toBe(false);
    expect(validRepoName(".hidden/repo")).toBe(false);
    expect(validRepoName("acme/-flag")).toBe(false);
    expect(validRepoName("acme/..")).toBe(false);
    expect(validRepoName(`acme/${"a".repeat(65)}`)).toBe(false);
  });

  it("validates 8-char lowercase hex chat-repos thread ids and uuid-like app-server thread ids", () => {
    expect(validOpenThreadId("0123abcd")).toBe(true);
    expect(validOpenThreadId("01a0f1ae-2da4-7942-99ae-c3151dc137cc")).toBe(true);
    expect(validOpenThreadId("0123ABCD")).toBe(false);
    expect(validOpenThreadId("0123abc")).toBe(false);
    expect(validOpenThreadId("-01a0f1ae-2da4")).toBe(false);
    expect(validOpenThreadId("01a0f1ae-2da4-")).toBe(false);
    expect(validOpenThreadId(`${"a".repeat(32)}-${"b".repeat(32)}`)).toBe(false);
  });

  it("parses ?open=repos, ?open=repo:<org>/<repo>, and ?open=thread:<id> with optional &approval=1", () => {
    expect(parseOpenTarget("?open=repos")).toEqual({ kind: "repos", approval: false });
    expect(parseOpenTarget("?open=repos&approval=1")).toEqual({ kind: "repos", approval: true });
    expect(parseOpenTarget("?open=repo:acme/widgets")).toEqual({
      kind: "repo",
      repo: "acme/widgets",
      approval: false,
    });
    expect(parseOpenTarget("?open=repo:acme/widgets&approval=1")).toEqual({
      kind: "repo",
      repo: "acme/widgets",
      approval: true,
    });
    expect(parseOpenTarget("?open=thread:0123abcd&approval=1")).toEqual({
      kind: "thread",
      threadId: "0123abcd",
      approval: true,
    });
    expect(parseOpenTarget("?open=repo:../secret")).toBeNull();
    expect(parseOpenTarget("?open=thread:not_valid")).toBeNull();
    expect(parseOpenTarget("?open=other")).toBeNull();
    expect(parseOpenTarget("")).toBeNull();
  });

  it("consumes open and approval from the query string with replaceState and leaves ordinary tabs untouched", () => {
    const replaced: (string | URL | null | undefined)[] = [];
    const hist = {
      replaceState(_data: unknown, _unused: string, url?: string | URL | null) {
        replaced.push(url);
      },
    };
    expect(consumeOpenTarget({ pathname: "/", search: "", hash: "#work" }, hist)).toBeNull();
    expect(replaced).toEqual([]);

    const target = consumeOpenTarget(
      { pathname: "/", search: "?open=repo:acme/widgets&approval=1", hash: "" },
      hist,
    );
    expect(target).toEqual({ kind: "repo", repo: "acme/widgets", approval: true });
    expect(replaced).toEqual(["/"]);

    consumeOpenTarget(
      { pathname: "/", search: "?open=repos", hash: "#tgWebAppData=query_id%3D1" },
      hist,
    );
    expect(replaced).toEqual(["/", "/"]);

    consumeOpenTarget(
      { pathname: "/", search: "?open=repos", hash: "#work" },
      hist,
    );
    expect(replaced).toEqual(["/", "/", "/#work"]);
  });

  it("routes parsed targets to openRepos, openWorkThread, and the G7 approval flag", () => {
    const calls: { repos: (string | null)[]; threads: string[]; approvals: boolean[] } = {
      repos: [],
      threads: [],
      approvals: [],
    };
    const actions = {
      openRepos: (repo: string | null) => calls.repos.push(repo),
      openWorkThread: (threadId: string) => calls.threads.push(threadId),
      setApprovalRequested: (requested: boolean) => calls.approvals.push(requested),
    };

    applyOpenTarget(parseOpenTarget("?open=repos"), actions);
    applyOpenTarget(parseOpenTarget("?open=repo:acme/widgets&approval=1"), actions);
    applyOpenTarget(parseOpenTarget("?open=thread:0123abcd&approval=1"), actions);
    applyOpenTarget(null, actions);

    expect(calls.repos).toEqual([null, "acme/widgets"]);
    expect(calls.threads).toEqual(["0123abcd"]);
    expect(calls.approvals).toEqual([true, true]);
  });
});
