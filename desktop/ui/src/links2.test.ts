// `ling://new?originUrl=…`: a remote matched however it is written (DESKTOP §16 B19).
import { describe, expect, it } from "vitest";

import { remoteKey, sameRemote } from "./links2";

describe("remotes", () => {
  it("are the same repository whether https or ssh, with or without .git or a user", () => {
    expect(remoteKey("https://GitHub.com/acme/app.git")).toBe("github.com/acme/app");
    expect(remoteKey("git@github.com:acme/app.git")).toBe("github.com/acme/app");
    expect(sameRemote("ssh://git@github.com:22/acme/app", "https://github.com/acme/app/")).toBe(true);
    expect(sameRemote("https://github.com/acme/app", "https://github.com/acme/other")).toBe(false);
    expect(sameRemote("not a remote", "not a remote")).toBe(false);
  });
});
