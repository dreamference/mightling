// The main process's reads of the user's folders never hold up the main thread (userfs.ts): a
// folder whose reads never answer (a macOS access prompt nobody has answered yet) leaves the event
// loop free, so IPC replies keep coming, and the call itself gives up after its time limit.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { environments } from "./environments";
import { repositories } from "./git";
import * as userFs from "./userfs";

const never = <T>() => new Promise<T>(() => {});

afterEach(() => userFs.setUserFs(null));

/** Resolves with how many 10 ms ticks the event loop ran while `work` was pending. */
async function ticksDuring<T>(work: Promise<T>): Promise<{ result: T | Error; ticks: number }> {
  let ticks = 0;
  const timer = setInterval(() => ticks++, 10);
  try {
    return { result: await work.catch((error: Error) => error), ticks };
  } finally {
    clearInterval(timer);
  }
}

describe("reads of the user's folders", () => {
  it("leave the event loop running while a folder does not answer, and give up after the limit", async () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-userfs-"));
    fs.mkdirSync(path.join(home, "work-demo", "app"), { recursive: true });
    // The walk below a folder that is not a repository: readdir never answers.
    userFs.setUserFs({ readdir: never }, 300);
    const walk = await ticksDuring(repositories(path.join(home, "work-demo")));
    expect(walk.result).toEqual([]);
    expect(walk.ticks).toBeGreaterThan(5);

    // A folder whose very stat never answers: the check refuses in time, with the reason.
    userFs.setUserFs({ stat: never }, 200);
    const checked = await ticksDuring(userFs.isDirectory(home));
    expect(checked.result).toBeInstanceOf(userFs.UserFsTimeout);
    expect(String(checked.result)).toMatch(/did not answer within .*folder-access prompt/);
    expect(checked.ticks).toBeGreaterThan(5);
    const read = await ticksDuring(environments(home));
    expect(read.result).toBeInstanceOf(userFs.UserFsTimeout);

    // The reads that timed out are still with the file system: main.ts quits hard while any is.
    expect(userFs.pendingCalls()).toBeGreaterThan(0);

    // With the real calls, the same folder reads at once.
    userFs.setUserFs(null);
    expect(await userFs.isDirectory(home)).toBe(true);
    expect(await userFs.exists(path.join(home, "missing"))).toBe(false);
    expect(await userFs.samePath(path.join(home, "work-demo", ".."), home)).toBe(true);
    fs.rmSync(home, { recursive: true, force: true });
  });
});
