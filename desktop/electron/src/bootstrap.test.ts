// The single-instance lock (bootstrap.ts): a second `ling-app` hands its arguments to the running
// one and quits before it registers anything, applies a session rule or starts a window of its own.

import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ lock: true, log: [] as unknown[][] }));
vi.mock("electron", () => ({
  app: {
    requestSingleInstanceLock: (data?: unknown) => {
      calls.log.push(["lock", data]);
      return calls.lock;
    },
    quit: () => calls.log.push(["quit"]),
    setAsDefaultProtocolClient: (scheme: string) => calls.log.push(["scheme", scheme]),
  },
}));
vi.mock("./app-identity", () => ({ claimAppIdentity: () => calls.log.push(["identity"]) }));
vi.mock("./egress", () => ({ applySessionRules: () => calls.log.push(["session rules"]) }));
vi.mock("./main", () => ({ main: async (options: unknown) => void calls.log.push(["main", options]) }));

import appConfig from "../app.json";
import { bootstrap } from "./bootstrap";

const argv = process.argv;

beforeEach(() => {
  calls.lock = true;
  calls.log.length = 0;
  process.argv = argv;
  delete process.env.MIGHTLING_APP_AUDIT;
});

const names = () => calls.log.map((entry) => entry[0]);

describe("the single-instance lock", () => {
  it("lets a second instance only hand its arguments over and quit", async () => {
    calls.lock = false;
    process.argv = ["/opt/Mightling/ling-app", "--thread", "t-1"];
    await bootstrap();
    expect(calls.log).toEqual([["lock", { argv: ["--thread", "t-1"] }], ["quit"]]);
  });

  it("takes the lock first, then registers every scheme, the app's ID and the session rules before main", async () => {
    process.argv = ["/opt/Mightling/ling-app"];
    await bootstrap();
    expect(names()).toEqual(["lock", ...[appConfig.scheme, ...appConfig.legacySchemes].map(() => "scheme"), "identity", "session rules", "main"]);
    expect(calls.log.filter((entry) => entry[0] === "scheme").map((entry) => entry[1])).toEqual([appConfig.scheme, ...appConfig.legacySchemes]);
    expect(appConfig.legacySchemes.length).toBeGreaterThan(0);
    expect(names()).not.toContain("quit");
  });

  it("gives main what the command line asked for, without the executable", async () => {
    process.argv = ["/opt/Mightling/ling-app", "--work", "--cwd", "/srv/project"];
    process.env.MIGHTLING_APP_AUDIT = "30";
    await bootstrap();
    expect(calls.log.find((entry) => entry[0] === "main")?.[1]).toEqual({ work: { cwd: "/srv/project", thread: null }, audit: 30 });
    expect(calls.log[0]).toEqual(["lock", { argv: ["--work", "--cwd", "/srv/project"] }]);
  });
});
