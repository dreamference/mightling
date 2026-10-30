// Several repositories in the review pane (DESKTOP §16 B32): the selector's labels, and Last turn over
// All repos or one, its files placed by the repository whose top level holds them. Also the
// repository service on `ling web` (CHAT_REPOS §10 G4, G6, G9, G13).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as bridge from "./bridge";
import { parseDiff } from "./diff";
import { GitBar } from "./GitBar";
import { ReposView } from "./ReposView";
import { ReviewPane, reviewScopesFor } from "./ReviewPane";
import { WorktreeHeader } from "./WorktreeControls";
import {
  CHAT_ID_KEY,
  CHAT_WORK_PROMPT,
  beginGitAction,
  browserChatId,
  chatRepos,
  checkoutThreadForCwd,
  executeGitAction,
  filesIn,
  formatAnonymityRefusal,
  fullAccessConfirmationPhrase,
  matchesFullAccessConfirmation,
  openRepoForWork,
  recordCheckout,
  recordRepos,
  repoName,
  repoOf,
  repoOption,
  resetChatRepos,
  safePrUrl,
  setChatReposFetcher,
  sortAndFilterRepos,
  turnBase,
  turnLines,
  type ChatRepoState,
  type ChatRepoSummary,
} from "./repos";

const file = (path: string, added: number, removed: number) =>
  `diff --git a/${path} b/${path}\n--- a/${path}\n+++ b/${path}\n@@ -1,${removed} +1,${added} @@\n${"-old\n".repeat(removed)}${"+new\n".repeat(added)}`;

const cwd = "/home/u/src/shop";
const repos = ["/home/u/src/shop/api", "/home/u/src/shop/web", "/home/u/src/shop/web/vendor/lib"];
const turn = parseDiff(file("api/server.py", 4, 1) + file("web/app.ts", 2, 2) + file("web/vendor/lib/x.c", 1, 0) + file("notes.md", 3, 0));

describe("several repositories", () => {
  it("labels each repository with its lines changed", () => {
    expect(repoName("/home/u/src/shop/api/")).toBe("api");
    expect(repoName("C:\\src\\shop\\web")).toBe("web");
    expect(repoOption("api", { added: 9, removed: 3 })).toBe("api +9 −3");
    expect(repoOption("api", undefined)).toBe("api");
  });

  it("places a turn's file in the innermost repository holding it, by the thread's folder", () => {
    expect(repoOf("api/server.py", cwd, repos)).toBe(repos[0]);
    expect(repoOf("web/vendor/lib/x.c", cwd, repos)).toBe(repos[2]);
    expect(repoOf("notes.md", cwd, repos)).toBeNull();
    expect(repoOf("/home/u/src/shop/web/app.ts", null, repos)).toBe(repos[1]);
    expect(repoOf("web2/app.ts", cwd, repos)).toBeNull();
    // A path out of the thread's folder, and a Windows share's.
    expect(repoOf("../web/app.ts", "/srv/shop/api", ["/srv/shop/web"])).toBe("/srv/shop/web");
    expect(repoOf("./app.ts", "/srv/shop/web", ["/srv/shop/web"])).toBe("/srv/shop/web");
    expect(repoOf("\\\\nas\\src\\web\\app.ts", "C:\\work", ["\\\\nas\\src\\web"])).toBe("\\\\nas\\src\\web");
    expect(repoOf("app.ts", null, repos)).toBeNull();
  });

  it("starts Last turn's paths where the server does: the folder above several repositories, else the repository holding it", () => {
    expect(turnBase(cwd, repos, repos[0])).toBe(cwd);
    expect(turnBase("/home/u/src/shop/api/server", ["/home/u/src/shop/api"], "/home/u/src/shop/api")).toBe("/home/u/src/shop/api");
    expect(turnBase("/home/u/src/shop/api", ["/home/u/src/shop/api"], "/home/u/src/shop/api")).toBe("/home/u/src/shop/api");
    expect(turnBase("/home/u/notes", [], null)).toBe("/home/u/notes");
  });

  it("counts the turn per repository and in all of them, and shows All repos or one", () => {
    const { total, byRepo } = turnLines(turn, cwd, repos);
    expect(total).toEqual({ added: 10, removed: 3 });
    expect(byRepo.get(repos[0])).toEqual({ added: 4, removed: 1 });
    expect(byRepo.get(repos[1])).toEqual({ added: 2, removed: 2 });
    expect(byRepo.get(repos[2])).toEqual({ added: 1, removed: 0 });
    expect(filesIn(turn, cwd, repos, null).map((entry) => entry.path)).toEqual(["api/server.py", "web/app.ts", "web/vendor/lib/x.c", "notes.md"]);
    expect(filesIn(turn, cwd, repos, repos[1]).map((entry) => entry.path)).toEqual(["web/app.ts"]);
  });
});

function setHost(kind: "electron" | "web") {
  if (kind === "electron") {
    (globalThis as { window?: unknown }).window = {
      mightlingWindowType: "electron",
      electronBridge: {
        sendMessageFromView: async () => null,
      },
      addEventListener: () => {},
      removeEventListener: () => {},
    };
  } else {
    (globalThis as { window?: unknown }).window = {
      addEventListener: () => {},
      removeEventListener: () => {},
    };
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SAMPLE_REPOS: ChatRepoSummary[] = [
  {
    repo: "org/older-lib",
    default_branch: "main",
    pushed_at: "2026-10-10T08:00:00Z",
    private: false,
    archived: true,
    mirror: false,
    read_only: true,
    checkouts: [],
  },
  {
    repo: "org/alpha-service",
    default_branch: "main",
    pushed_at: "2026-10-25T19:30:00Z",
    private: true,
    archived: false,
    mirror: false,
    read_only: false,
    checkouts: ["01234567", "89abcdef"],
  },
  {
    repo: "org/mirror-docs",
    default_branch: "main",
    pushed_at: "2026-10-18T12:00:00Z",
    private: false,
    archived: false,
    mirror: true,
    read_only: true,
    checkouts: ["fedcba98"],
  },
];

function sampleState(overrides: Partial<ChatRepoState> = {}): ChatRepoState {
  return {
    repo: "org/alpha-service",
    branch: "phone/01234567",
    base: "main",
    ahead: 2,
    behind: 1,
    uncommitted: [{ path: "src/index.ts", added: 5, removed: 2 }],
    read_only: false,
    private: true,
    budget: { used: 12000, limit: 50000, resets_at: 1793145600 },
    warnings: [],
    ...overrides,
  };
}

afterEach(() => {
  resetChatRepos();
  delete (globalThis as { window?: unknown }).window;
});

describe("G4: Repos view and opening a checkout", () => {
  it("orders repositories most-recent first, filters by search, renders marks, and preselects open=repo:<org>/<repo>", async () => {
    setHost("web");
    const calls: string[] = [];
    const api = chatRepos(async (url) => {
      calls.push(url);
      return jsonResponse(SAMPLE_REPOS);
    });
    const listed = await api.list();
    expect(calls).toEqual(["/api/repos"]);
    expect(listed.forgeOff).toBe(false);

    const ordered = sortAndFilterRepos(listed.repos);
    expect(ordered.map((entry) => entry.repo)).toEqual([
      "org/alpha-service",
      "org/mirror-docs",
      "org/older-lib",
    ]);
    expect(sortAndFilterRepos(listed.repos, "mirror").map((entry) => entry.repo)).toEqual(["org/mirror-docs"]);

    const html = renderToStaticMarkup(
      createElement(ReposView, {
        initialRepos: listed.repos,
        selectedRepo: "org/mirror-docs",
      }),
    );
    const alphaPos = html.indexOf("org/alpha-service");
    const mirrorPos = html.indexOf("org/mirror-docs");
    const olderPos = html.indexOf("org/older-lib");
    expect(alphaPos).toBeGreaterThan(-1);
    expect(alphaPos).toBeLessThan(mirrorPos);
    expect(mirrorPos).toBeLessThan(olderPos);

    expect(html).toContain("🔒");
    expect(html).toContain("archived (read-only)");
    expect(html).toContain("mirror");
    expect(html).toContain("2 open checkouts");
    expect(html).toContain("1 open checkout");
    expect(html).toContain("0 open checkouts");
    expect(html).toContain('aria-selected="true" data-repo="org/mirror-docs"');

    const filteredHtml = renderToStaticMarkup(
      createElement(ReposView, {
        initialRepos: listed.repos,
        initialSearch: "older",
      }),
    );
    expect(filteredHtml).toContain("org/older-lib");
    expect(filteredHtml).not.toContain("org/alpha-service");
  });

  it("shows the forge-off message on 404 from /api/repos and offers nothing else", async () => {
    setHost("web");
    const api = chatRepos(async () =>
      jsonResponse({ error: "The forge is off (`ling-admin forge on`)." }, 404),
    );
    const listed = await api.list();
    expect(listed.forgeOff).toBe(true);

    const html = renderToStaticMarkup(
      createElement(ReposView, {
        initialForgeOff: listed.forgeOff,
        initialRepos: listed.repos,
      }),
    );
    expect(html).toContain("ling-admin forge on");
    expect(html).not.toContain("Search repositories");
    expect(html).not.toContain('role="listbox"');
  });

  it("calls POST /api/repos/open and starts a chat-work thread in the answered cwd", async () => {
    setHost("web");
    const store = new Map<string, string>();
    const storage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
    };
    const chatId = browserChatId(storage);
    expect(chatId).toMatch(/^web-[0-9a-f]{16}$/);
    expect(browserChatId(storage)).toBe(chatId);
    expect(store.get(CHAT_ID_KEY)).toBe(chatId);

    const posted: { url: string; body: unknown }[] = [];
    const checkoutCwd = "/home/u/.mightling/chat-repos/org/alpha-service/web-01234567";
    const api = chatRepos(async (url, init) => {
      posted.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
      return jsonResponse({
        thread: "01234567",
        cwd: checkoutCwd,
        branch: "phone/01234567",
        read_only: false,
      });
    });

    const rpcCalls: { method: string; params: Record<string, unknown> }[] = [];
    const result = await openRepoForWork("org/alpha-service", {
      api,
      chat: chatId,
      servedModel: "qwen3.8-27b-nvfp4-dflash2",
      existingThreads: [],
      request: async (method, params) => {
        rpcCalls.push({ method, params });
        return { thread: { id: "thread-1", cwd: checkoutCwd } };
      },
    });

    expect(posted).toEqual([
      {
        url: "/api/repos/open",
        body: { repo: "org/alpha-service", bridge: "web", chat: chatId },
      },
    ]);
    expect(rpcCalls).toEqual([
      {
        method: "thread/start",
        params: {
          cwd: checkoutCwd,
          prompt: CHAT_WORK_PROMPT,
          model: "qwen3.8-27b-nvfp4-dflash2",
        },
      },
    ]);
    expect(result.resumed).toBe(false);
    expect(result.opened.cwd).toBe(checkoutCwd);
    expect(bridge.git.available(checkoutCwd)).toBe(true);
  });
});

describe("G6: Diffs and review scopes for chat-repos threads", () => {
  it("offers uncommitted, branch and turn only for a chat-repos thread in the web host, and never Stage, Unstage or Revert", () => {
    const checkoutCwd = "/home/u/.mightling/chat-repos/org/alpha-service/web-01234567";
    recordCheckout({
      thread: "01234567",
      cwd: checkoutCwd,
      repo: "org/alpha-service",
      branch: "phone/01234567",
      read_only: false,
    });

    setHost("web");
    expect(reviewScopesFor("web", bridge.git.available(checkoutCwd)).map((s) => s.id)).toEqual([
      "uncommitted",
      "branch",
      "turn",
    ]);
    expect(reviewScopesFor("web", bridge.git.available("/home/u/src/shop")).map((s) => s.id)).toEqual(["turn"]);

    const webRepoPane = renderToStaticMarkup(
      createElement(ReviewPane, {
        cwd: checkoutCwd,
        threadId: "thread-1",
        turnDiff: file("src/index.ts", 2, 1),
        airgapOn: false,
        layout: "unified",
        setLayout: () => {},
        editor: null,
        setEditor: () => {},
        refreshKey: 0,
        onComments: () => {},
        onRevertTurn: () => {},
        generateMessage: async () => "",
        forcePushDefault: false,
        notice: () => {},
        onOpenFile: null,
        initialScope: "uncommitted",
        initialDiff: file("src/index.ts", 2, 1),
      }),
    );
    expect(webRepoPane).toContain("Uncommitted");
    expect(webRepoPane).toContain("Branch");
    expect(webRepoPane).toContain("Last turn");
    expect(webRepoPane).not.toContain("Unstaged");
    expect(webRepoPane).not.toContain("Staged");
    expect(webRepoPane).not.toContain(">Stage<");
    expect(webRepoPane).not.toContain(">Unstage<");
    expect(webRepoPane).not.toContain(">Revert<");
    expect(webRepoPane).not.toContain("commit-box");

    const ordinaryWebPane = renderToStaticMarkup(
      createElement(ReviewPane, {
        cwd: "/home/u/src/shop",
        threadId: "thread-2",
        turnDiff: file("api/server.py", 1, 0),
        airgapOn: false,
        layout: "unified",
        setLayout: () => {},
        editor: null,
        setEditor: () => {},
        refreshKey: 0,
        onComments: () => {},
        onRevertTurn: () => {},
        generateMessage: async () => "",
        forcePushDefault: false,
        notice: () => {},
        onOpenFile: null,
      }),
    );
    expect(ordinaryWebPane).toContain("Last turn");
    expect(ordinaryWebPane).not.toContain("Uncommitted");
    expect(ordinaryWebPane).not.toContain("Branch");

    setHost("electron");
    expect(reviewScopesFor("electron", bridge.git.available("/home/u/src/shop")).map((s) => s.id)).toEqual([
      "unstaged",
      "staged",
      "commit",
      "branch",
      "turn",
    ]);
  });

  it("reads uncommitted and branch diffs and status from /api/repos/diff and /api/repos/state", async () => {
    setHost("web");
    const checkoutCwd = "/home/u/.mightling/chat-repos/org/alpha-service/web-01234567";
    recordCheckout({
      thread: "01234567",
      cwd: checkoutCwd,
      repo: "org/alpha-service",
      branch: "phone/01234567",
      read_only: false,
    });
    const requests: string[] = [];
    setChatReposFetcher(async (url) => {
      requests.push(url);
      if (url.startsWith("/api/repos/state?")) {
        return jsonResponse(sampleState());
      }
      if (url === "/api/repos/diff?thread=01234567&scope=uncommitted") {
        return jsonResponse([{ path: "src/index.ts", added: 2, removed: 1 }]);
      }
      if (url === "/api/repos/diff?thread=01234567&scope=uncommitted&path=src%2Findex.ts") {
        return jsonResponse({ path: "src/index.ts", diff: file("src/index.ts", 2, 1) });
      }
      if (url === "/api/repos/diff?thread=01234567&scope=branch") {
        return jsonResponse([{ path: "src/index.ts", added: 3, removed: 0, diff: file("src/index.ts", 3, 0) }]);
      }
      return jsonResponse({}, 404);
    });

    const status = await bridge.git.status(checkoutCwd);
    expect(status.branch).toBe("phone/01234567");
    expect(status.ahead).toBe(2);
    expect(status.behind).toBe(1);
    expect(status.files.map((f) => f.path)).toEqual(["src/index.ts"]);

    const uncommitted = await bridge.git.diff(checkoutCwd, "uncommitted");
    expect(uncommitted.against).toBeNull();
    expect(uncommitted.diff).toContain("diff --git a/src/index.ts b/src/index.ts");

    const branchDiff = await bridge.git.diff(checkoutCwd, "branch");
    expect(branchDiff.against).toBe("main");
    expect(branchDiff.diff).toContain("diff --git a/src/index.ts b/src/index.ts");

    expect(requests).toEqual([
      "/api/repos/state?thread=01234567",
      "/api/repos/diff?thread=01234567&scope=uncommitted",
      "/api/repos/diff?thread=01234567&scope=uncommitted&path=src%2Findex.ts",
      "/api/repos/diff?thread=01234567&scope=branch",
      "/api/repos/state?thread=01234567",
    ]);
  });
});

describe("G9: Git bar, single-use confirm nonces, and refusals", () => {
  it("renders the git bar only for a chat-repos thread in the web host, and disables actions when read-only", () => {
    const checkoutCwd = "/home/u/.mightling/chat-repos/org/alpha-service/web-01234567";
    recordCheckout({
      thread: "01234567",
      cwd: checkoutCwd,
      repo: "org/alpha-service",
      branch: "phone/01234567",
      read_only: false,
    });

    setHost("electron");
    expect(
      renderToStaticMarkup(createElement(GitBar, { thread: "01234567", cwd: checkoutCwd, state: sampleState() })),
    ).toBe("");

    setHost("web");
    expect(
      renderToStaticMarkup(
        createElement(GitBar, { thread: "01234567", cwd: "/home/u/src/shop", state: sampleState() }),
      ),
    ).toBe("");

    const activeHtml = renderToStaticMarkup(
      createElement(GitBar, {
        thread: "01234567",
        cwd: checkoutCwd,
        state: sampleState({ allow_main: true }),
      }),
    );
    expect(activeHtml).toContain("phone/01234567");
    expect(activeHtml).toContain("↑2 ↓1");
    expect(activeHtml).toContain(">Commit<");
    expect(activeHtml).toContain(">Push<");
    expect(activeHtml).toContain(">Update<");
    expect(activeHtml).toContain(">Merge<");
    expect(activeHtml).toContain(">PR<");
    expect(activeHtml).toContain(">Work on main<");
    expect(activeHtml).not.toContain(">Abort<");

    const hiddenMainHtml = renderToStaticMarkup(
      createElement(GitBar, {
        thread: "01234567",
        cwd: checkoutCwd,
        state: sampleState({ allow_main: false }),
      }),
    );
    expect(hiddenMainHtml).not.toContain(">Work on main<");

    const readOnlyHtml = renderToStaticMarkup(
      createElement(GitBar, {
        thread: "01234567",
        cwd: checkoutCwd,
        state: sampleState({ branch: null, read_only: true }),
      }),
    );
    expect(readOnlyHtml).toContain("This repository is read-only on the forge");
    expect(readOnlyHtml).toContain('disabled=""');
  });

  it("fetches a confirm nonce with the right intent and posts it for commit, push, update, abort, merge, pr and main", async () => {
    const log: { method: string; url: string; body: unknown }[] = [];
    let counter = 0;
    const api = chatRepos(async (url, init) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      log.push({ method, url, body });

      if (url.startsWith("/api/repos/state?")) {
        const parsed = new URL(url, "http://127.0.0.1");
        const intent = parsed.searchParams.get("intent");
        counter += 1;
        return jsonResponse(sampleState({ confirm: `nonce-${intent}-${counter}` }));
      }
      if (url === "/api/repos/commit") {
        return jsonResponse({
          commit: "9f8e7d6c5b4a3210",
          files: [{ path: "src/index.ts", added: 5, removed: 2 }],
          added: 5,
          removed: 2,
        });
      }
      if (url === "/api/repos/push") {
        return jsonResponse({ pushed: 2, branch: "phone/01234567", anonymity_check: "passed" });
      }
      if (url === "/api/repos/update") {
        if ((body as { abort?: boolean })?.abort) {
          return jsonResponse({ aborted: true });
        }
        return jsonResponse({ conflicts: ["src/index.ts", "README.md"] }, 409);
      }
      if (url === "/api/repos/merge") {
        return jsonResponse({ merged: true, branch: "phone/01234567", into: "main" });
      }
      if (url === "/api/repos/pr") {
        return jsonResponse({ number: 17, url: "/forge/org/alpha-service/pulls/17" });
      }
      if (url === "/api/repos/main") {
        return jsonResponse({ repo: "org/alpha-service", branch: "main" });
      }
      return jsonResponse({}, 404);
    });

    // 1. Commit
    const commitStep = await beginGitAction(api, "01234567", "commit", "Fix header layout");
    expect(commitStep.pending.intent).toBe("commit");
    expect(commitStep.pending.confirm).toBe("nonce-commit-1");
    const commitOut = await executeGitAction(api, "01234567", commitStep.pending);
    expect(commitOut.text).toBe("Committed 9f8e7d6 (1 file, +5 −2)");

    // 2. Push
    const pushStep = await beginGitAction(api, "01234567", "push");
    expect(pushStep.pending.intent).toBe("push");
    const pushOut = await executeGitAction(api, "01234567", pushStep.pending);
    expect(pushOut.text).toBe("Pushed 2 commits to phone/01234567");

    // 3. Update (with conflicts -> enables Abort)
    const updateStep = await beginGitAction(api, "01234567", "update");
    expect(updateStep.pending.intent).toBe("update");
    const updateOut = await executeGitAction(api, "01234567", updateStep.pending);
    expect(updateOut.text).toBe("Conflicts: src/index.ts, README.md");
    expect(updateOut.mergeInProgress).toBe(true);

    // 4. Abort (uses intent=update and posts abort: true)
    const abortStep = await beginGitAction(api, "01234567", "abort");
    expect(abortStep.pending.intent).toBe("update");
    const abortOut = await executeGitAction(api, "01234567", abortStep.pending);
    expect(abortOut.text).toBe("Merge aborted");
    expect(abortOut.mergeInProgress).toBe(false);

    // 5. Merge
    const mergeStep = await beginGitAction(api, "01234567", "merge");
    expect(mergeStep.pending.intent).toBe("merge");
    const mergeOut = await executeGitAction(api, "01234567", mergeStep.pending);
    expect(mergeOut.text).toBe("Merged phone/01234567 into main");

    // 6. PR (opens the /forge/... link in a new tab)
    const openedUrls: string[] = [];
    const prStep = await beginGitAction(api, "01234567", "pr");
    expect(prStep.pending.intent).toBe("pr");
    const prOut = await executeGitAction(api, "01234567", prStep.pending, {
      openTab: (url) => openedUrls.push(url),
    });
    expect(prOut.text).toBe("Pull request #17");
    expect(prOut.prUrl).toBe("/forge/org/alpha-service/pulls/17");
    expect(openedUrls).toEqual(["/forge/org/alpha-service/pulls/17"]);

    // 7. Work on main
    const mainStep = await beginGitAction(api, "01234567", "main");
    expect(mainStep.pending.intent).toBe("main");
    const mainOut = await executeGitAction(api, "01234567", mainStep.pending);
    expect(mainOut.text).toBe("Working on main");

    expect(log).toEqual([
      { method: "GET", url: "/api/repos/state?thread=01234567&intent=commit", body: null },
      {
        method: "POST",
        url: "/api/repos/commit",
        body: { thread: "01234567", message: "Fix header layout", confirm: "nonce-commit-1" },
      },
      { method: "GET", url: "/api/repos/state?thread=01234567&intent=push", body: null },
      {
        method: "POST",
        url: "/api/repos/push",
        body: { thread: "01234567", confirm: "nonce-push-2" },
      },
      { method: "GET", url: "/api/repos/state?thread=01234567&intent=update", body: null },
      {
        method: "POST",
        url: "/api/repos/update",
        body: { thread: "01234567", confirm: "nonce-update-3" },
      },
      { method: "GET", url: "/api/repos/state?thread=01234567&intent=update", body: null },
      {
        method: "POST",
        url: "/api/repos/update",
        body: { thread: "01234567", confirm: "nonce-update-4", abort: true },
      },
      { method: "GET", url: "/api/repos/state?thread=01234567&intent=merge", body: null },
      {
        method: "POST",
        url: "/api/repos/merge",
        body: { thread: "01234567", confirm: "nonce-merge-5" },
      },
      { method: "GET", url: "/api/repos/state?thread=01234567&intent=pr", body: null },
      {
        method: "POST",
        url: "/api/repos/pr",
        body: { thread: "01234567", confirm: "nonce-pr-6" },
      },
      { method: "GET", url: "/api/repos/state?thread=01234567&intent=main", body: null },
      {
        method: "POST",
        url: "/api/repos/main",
        body: { thread: "01234567", confirm: "nonce-main-7" },
      },
    ]);
  });

  it("shows only file, line and entry on an anonymity refusal and nothing else from the answer", async () => {
    const api = chatRepos(async (url) => {
      if (url.startsWith("/api/repos/state?")) {
        return jsonResponse(sampleState({ confirm: "nonce-push-99" }));
      }
      return jsonResponse(
        {
          refused: "anonymity",
          error: "never-show-this-raw-error-text",
          findings: [
            {
              file: "src/config.ts",
              line: 42,
              entry: 2,
              matched: "sensitive-token-value",
              snippet: "const token = 'sensitive-token-value'",
            },
          ],
        },
        422,
      );
    });

    const step = await beginGitAction(api, "01234567", "push");
    const outcome = await executeGitAction(api, "01234567", step.pending);
    expect(outcome.text).toBe("Push refused by the anonymity check: src/config.ts:42 (entry 2)");
    expect(outcome.text).not.toContain("sensitive-token-value");
    expect(outcome.text).not.toContain("never-show-this-raw-error-text");
    expect(formatAnonymityRefusal([{ file: "README.md", line: 7, entry: 1 }])).toBe(
      "Push refused by the anonymity check: README.md:7 (entry 1)",
    );
  });

  it("says the anonymity check could not run, and nothing from the answer, when it names no findings", async () => {
    const api = chatRepos(async (url) => {
      if (url.startsWith("/api/repos/state?")) return jsonResponse(sampleState({ confirm: "nonce-push-98" }));
      return jsonResponse({ refused: "anonymity", error: "raw-server-text" }, 422);
    });
    const step = await beginGitAction(api, "01234567", "push");
    const outcome = await executeGitAction(api, "01234567", step.pending);
    expect(outcome.text).toBe("Push refused: the anonymity check could not run");
  });

  it("opens a pull request's link only on the forge's path or http(s)", async () => {
    expect(safePrUrl("/forge/org/widget/pulls/1")).toBe(true);
    expect(safePrUrl("https://forge.example/org/widget/pulls/1")).toBe(true);
    expect(safePrUrl("javascript:alert(1)")).toBe(false);
    expect(safePrUrl("data:text/html,x")).toBe(false);
    const opened: string[] = [];
    const api = chatRepos(async (url) => {
      if (url.startsWith("/api/repos/state?")) return jsonResponse(sampleState({ confirm: "nonce-pr-3" }));
      return jsonResponse({ number: 3, url: "javascript:alert(1)" });
    });
    const step = await beginGitAction(api, "01234567", "pr");
    const outcome = await executeGitAction(api, "01234567", step.pending, { openTab: (url) => opened.push(url) });
    expect(opened).toEqual([]);
    expect(outcome).toEqual({ text: "Pull request #3" });
  });

  it("opens the pull request in a new tab in a browser, and never calls window.open in the app (which denies it)", async () => {
    const api = chatRepos(async (url) => {
      if (url.startsWith("/api/repos/state?")) return jsonResponse(sampleState({ confirm: "nonce-pr-1" }));
      return jsonResponse({ number: 3, url: "https://forge.example/org/widget/pulls/3" });
    });
    try {
      for (const [type, calls] of [["web", 1], ["electron", 0]] as const) {
        const open = vi.fn();
        vi.stubGlobal("window", { open, mightlingWindowType: type });
        const step = await beginGitAction(api, "01234567", "pr");
        await executeGitAction(api, "01234567", step.pending);
        expect(open, type).toHaveBeenCalledTimes(calls);
      }
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows a file whose diff could not be read by its header only, never invented lines", async () => {
    const api = chatRepos(async (url) => {
      if (url.includes("path=")) return jsonResponse({ error: "boom" }, 500);
      return jsonResponse([{ path: "a.txt", added: 2, removed: 1 }]);
    });
    const text = await api.diff("01234567", "uncommitted");
    expect(text).toBe("diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n");
  });
});

describe("G13: WorktreeHeader on a chat-repos thread", () => {
  it("shows repository, branch, main badge, 🔒 when private, budget left, and warnings in the web host", () => {
    setHost("web");
    const branchHtml = renderToStaticMarkup(
      createElement(WorktreeHeader, {
        link: undefined,
        branch: "phone/01234567",
        canHandOff: false,
        inGit: true,
        environment: null,
        onCreateBranch: () => {},
        branchPrefix: "ling/",
        onHandOff: () => {},
        onOpen: () => {},
        onAction: () => {},
        chatRepo: sampleState({
          repo: "org/alpha-service",
          branch: "phone/01234567",
          private: true,
          budget: { used: 12000, limit: 50000, resets_at: 1793145600 },
          warnings: ["Another thread (89abcdef) also has an open checkout of org/alpha-service."],
        }),
      }),
    );
    expect(branchHtml).toContain("org/alpha-service");
    expect(branchHtml).toContain("phone/01234567");
    expect(branchHtml).toContain("🔒");
    expect(branchHtml).toContain("38,000 tokens left");
    expect(branchHtml).toContain("Another thread (89abcdef) also has an open checkout of org/alpha-service.");
    expect(branchHtml).not.toContain('data-badge="main"');

    const mainHtml = renderToStaticMarkup(
      createElement(WorktreeHeader, {
        link: undefined,
        branch: "main",
        canHandOff: false,
        inGit: true,
        environment: null,
        onCreateBranch: () => {},
        branchPrefix: "ling/",
        onHandOff: () => {},
        onOpen: () => {},
        onAction: () => {},
        chatRepo: sampleState({
          repo: "org/public-tool",
          branch: "main",
          private: false,
        }),
      }),
    );
    expect(mainHtml).toContain("org/public-tool");
    expect(mainHtml).toContain('data-badge="main"');
    expect(mainHtml).not.toContain("🔒");

    // In the Electron host, WorktreeHeader stays the desktop header even if chatRepo is passed.
    setHost("electron");
    const desktopHtml = renderToStaticMarkup(
      createElement(WorktreeHeader, {
        link: undefined,
        branch: "main",
        canHandOff: true,
        inGit: true,
        environment: null,
        onCreateBranch: () => {},
        branchPrefix: "ling/",
        onHandOff: () => {},
        onOpen: () => {},
        onAction: () => {},
        chatRepo: sampleState(),
      }),
    );
    expect(desktopHtml).toContain("local · main");
    expect(desktopHtml).toContain("Hand off");
    expect(desktopHtml).not.toContain("tokens left");
  });
});

describe("G7: Full Access typed confirmation and sandbox chip", () => {
  it("renders Workspace or Full Access chip, hides button with disabled title when off, and enables Confirm only on exact FULL ACCESS <org>/<repo>", () => {
    const checkoutCwd = "/home/u/.mightling/chat-repos/org/alpha-service/web-01234567";
    recordCheckout({
      thread: "01234567",
      cwd: checkoutCwd,
      repo: "org/alpha-service",
      branch: "phone/01234567",
      read_only: false,
    });
    setHost("web");

    expect(fullAccessConfirmationPhrase("org/alpha-service")).toBe("FULL ACCESS org/alpha-service");
    expect(matchesFullAccessConfirmation("FULL ACCESS org/alpha-service", "org/alpha-service")).toBe(true);
    expect(matchesFullAccessConfirmation("full access org/alpha-service", "org/alpha-service")).toBe(false);
    expect(matchesFullAccessConfirmation("FULL ACCESS org/alpha-service ", "org/alpha-service")).toBe(false);
    expect(matchesFullAccessConfirmation("FULL ACCESS org/other", "org/alpha-service")).toBe(false);

    // 1. Not allowed: Workspace chip with title "Full Access is off for phone threads" and no Full Access button.
    const disallowedHtml = renderToStaticMarkup(
      createElement(GitBar, {
        thread: "01234567",
        cwd: checkoutCwd,
        state: sampleState({ full_access_allowed: false, full_access: false }),
      }),
    );
    expect(disallowedHtml).toContain(">Workspace<");
    expect(disallowedHtml).toContain('title="Full Access is off for phone threads"');
    expect(disallowedHtml).not.toContain("Full Access…");
    expect(disallowedHtml).not.toContain("Back to Workspace");

    // 2. Allowed and not granted: Workspace chip and "Full Access…" button.
    const allowedHtml = renderToStaticMarkup(
      createElement(GitBar, {
        thread: "01234567",
        cwd: checkoutCwd,
        state: sampleState({ full_access_allowed: true, full_access: false }),
      }),
    );
    expect(allowedHtml).toContain(">Workspace<");
    expect(allowedHtml).toContain(">Full Access…<");
    expect(allowedHtml).not.toContain("Back to Workspace");

    // 3. Pending full-access confirmation with partial/wrong text: Confirm button is disabled.
    const mismatchPendingHtml = renderToStaticMarkup(
      createElement(GitBar, {
        thread: "01234567",
        cwd: checkoutCwd,
        state: sampleState({ full_access_allowed: true, full_access: false }),
        initialPending: {
          action: "full-access",
          intent: "access",
          confirm: "nonce-access-1",
          message: "FULL ACCESS org/alpha",
        },
      }),
    );
    expect(mismatchPendingHtml).toContain("Type FULL ACCESS org/alpha-service to allow Full Access:");
    expect(mismatchPendingHtml).toContain('class="primary" disabled=""');

    // 4. Pending full-access confirmation with exact text: Confirm button is enabled.
    const matchedPendingHtml = renderToStaticMarkup(
      createElement(GitBar, {
        thread: "01234567",
        cwd: checkoutCwd,
        state: sampleState({ full_access_allowed: true, full_access: false }),
        initialPending: {
          action: "full-access",
          intent: "access",
          confirm: "nonce-access-1",
          message: "FULL ACCESS org/alpha-service",
        },
      }),
    );
    expect(matchedPendingHtml).toContain('<button class="primary">Confirm</button>');

    // 5. Granted: Full Access chip and "Back to Workspace" button.
    const grantedHtml = renderToStaticMarkup(
      createElement(GitBar, {
        thread: "01234567",
        cwd: checkoutCwd,
        state: sampleState({ full_access_allowed: true, full_access: true }),
      }),
    );
    expect(grantedHtml).toContain(">Full Access<");
    expect(grantedHtml).toContain(">Back to Workspace<");
    expect(grantedHtml).not.toContain("Full Access…");
  });

  it("performs the two-step nonce and POST /api/repos/access flow for full access, workspace reset, and server refusals", async () => {
    const log: { method: string; url: string; body: unknown }[] = [];
    let counter = 0;
    let shouldRefuseAirgap = false;
    const api = chatRepos(async (url, init) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      log.push({ method, url, body });

      if (url.startsWith("/api/repos/state?")) {
        const parsed = new URL(url, "http://127.0.0.1");
        const intent = parsed.searchParams.get("intent");
        counter += 1;
        return jsonResponse(
          sampleState({
            full_access_allowed: true,
            full_access: false,
            confirm: intent ? `nonce-${intent}-${counter}` : undefined,
          }),
        );
      }
      if (url === "/api/repos/access") {
        if (shouldRefuseAirgap) {
          return jsonResponse(
            {
              error:
                "Full Access runs commands with no sandbox, and the sandbox is what takes their network away, so the two are incompatible",
            },
            403,
          );
        }
        const req = body as { level?: string; typed?: string };
        if (req.level === "full" && req.typed !== "FULL ACCESS org/alpha-service") {
          return jsonResponse({ error: "type FULL ACCESS org/alpha-service exactly" }, 400);
        }
        return jsonResponse({ level: req.level });
      }
      return jsonResponse({}, 404);
    });

    // 1. Grant Full Access (GET state?intent=access -> POST /api/repos/access with level="full" and typed).
    const fullStep = await beginGitAction(api, "01234567", "full-access", "FULL ACCESS org/alpha-service");
    expect(fullStep.pending.intent).toBe("access");
    expect(fullStep.pending.confirm).toBe("nonce-access-1");
    const fullOut = await executeGitAction(api, "01234567", fullStep.pending);
    expect(fullOut.text).toBe("Full Access");

    // 2. Back to Workspace needs no nonce (it must work with the forge down): POST level="workspace" only.
    const wsOut = await executeGitAction(api, "01234567", { action: "workspace-access", intent: "access", confirm: "", message: "" });
    expect(wsOut.text).toBe("Workspace");

    // 3. Server refusal surfaces only the server's error sentence inline.
    shouldRefuseAirgap = true;
    const refusedStep = await beginGitAction(api, "01234567", "full-access", "FULL ACCESS org/alpha-service");
    const refusedOut = await executeGitAction(api, "01234567", refusedStep.pending);
    expect(refusedOut.text).toBe(
      "Full Access runs commands with no sandbox, and the sandbox is what takes their network away, so the two are incompatible",
    );

    expect(log).toEqual([
      { method: "GET", url: "/api/repos/state?thread=01234567&intent=access", body: null },
      {
        method: "POST",
        url: "/api/repos/access",
        body: {
          thread: "01234567",
          level: "full",
          typed: "FULL ACCESS org/alpha-service",
          confirm: "nonce-access-1",
        },
      },
      {
        method: "POST",
        url: "/api/repos/access",
        body: {
          thread: "01234567",
          level: "workspace",
        },
      },
      { method: "GET", url: "/api/repos/state?thread=01234567&intent=access", body: null },
      {
        method: "POST",
        url: "/api/repos/access",
        body: {
          thread: "01234567",
          level: "full",
          typed: "FULL ACCESS org/alpha-service",
          confirm: "nonce-access-2",
        },
      },
    ]);
  });
});

describe("which folder is a repository checkout, and what a refusal says", () => {
  it("takes a folder for a checkout only by its recorded path, the service's own layout, or a listed id at its end", () => {
    resetChatRepos();
    expect(checkoutThreadForCwd("/srv/chat-repos/org/widget/web-ABCDEF01")).toBe("abcdef01");
    expect(checkoutThreadForCwd("C:\\data\\chat-repos\\org\\widget\\telegram-0123abcd")).toBe("0123abcd");
    for (const cwd of [null, undefined, "", "/srv/chat-repos/widget/web-abcdef01", "/srv/chat-repos/org/widget/sms-abcdef01", "/srv/chat-repos/org/widget/web-abcdef0", "/home/u/src/0123abcd", "/home/u/src/proj-0123abcd"]) {
      expect(checkoutThreadForCwd(cwd), String(cwd)).toBeNull();
    }
    recordRepos([{ repo: "org/widget", checkouts: ["0123abcd"] } as unknown as ChatRepoSummary]);
    expect(checkoutThreadForCwd("/home/u/src/0123abcd")).toBe("0123abcd");
    expect(checkoutThreadForCwd("/home/u/src/proj-0123abcd")).toBe("0123abcd");
    expect(checkoutThreadForCwd("/home/u/src/proj0123abcd")).toBeNull();
    expect(checkoutThreadForCwd("/home/u/src/0123abcd/sub")).toBeNull();
    resetChatRepos();
    expect(checkoutThreadForCwd("/home/u/src/0123abcd")).toBeNull();
  });

  it("opens no pull request link it cannot read as an address", () => {
    for (const url of ["", "not a url", "//evil.example/pulls/1", "forge/org/widget", "file:///etc/passwd", "ftp://forge.example/x"]) {
      expect(safePrUrl(url), url).toBe(false);
    }
  });

  it("says a refusal by the server's error, else its reason, else what was refused, and an anonymity finding by file and entry alone", async () => {
    const pushWith = async (answer: Response) => {
      const api = chatRepos(async (url) => (url.startsWith("/api/repos/state?") ? jsonResponse(sampleState({ confirm: "nonce-push-7" })) : answer));
      const step = await beginGitAction(api, "01234567", "push");
      return (await executeGitAction(api, "01234567", step.pending)).text;
    };
    expect(await pushWith(jsonResponse({ error: "the branch moved", reason: "ignored" }, 409))).toBe("the branch moved");
    expect(await pushWith(jsonResponse({ reason: "main is protected" }, 403))).toBe("main is protected");
    expect(await pushWith(jsonResponse({}, 500))).toBe("Push refused");
    expect(await pushWith(new Response("", { status: 502 }))).toBe("Push refused");
    expect(await pushWith(new Response("Bad gateway", { status: 502 }))).toBe("Bad gateway");
    expect(await pushWith(jsonResponse({ findings: [{ file: "a.ts", secret: "s3cr3t" }] }, 422))).toBe("Push refused by the anonymity check: a.ts (entry 0)");
  });
});
