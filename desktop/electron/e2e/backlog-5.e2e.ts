// The parity backlog's B5 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): Night Shift's status, and a task queued on a node or with a number of attempts.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-5
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";
import { runs } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B5: Scheduled says whether Night Shift is on and queues a task on a node or with a number of attempts", async () => {
  const { page, home, close } = await open({ prepare: (home) => {
    const units = path.join(home, ".config", "systemd", "user");
    fs.mkdirSync(units, { recursive: true });
    fs.writeFileSync(path.join(units, "mightling-night.timer"), "[Unit]\n# Night Shift window: 01:00-07:00\n[Timer]\nOnCalendar=*-*-* 01:00:00\n");
  } });
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    // From no thread: a task queued from a thread carries its latest messages (tonight.ts), not wanted here.
    await page.getByRole("button", { name: /Scheduled/ }).click();
    const view = page.getByRole("dialog", { name: "Scheduled" });
    await expect(view.getByRole("status")).toHaveText("Night Shift is on: it works through the queue every night, 01:00-07:00.");
    // Best of 3, queued here.
    await view.getByRole("textbox", { name: "Task" }).fill("Make the retry test table-driven");
    await view.getByRole("combobox", { name: "Attempts" }).selectOption("3");
    await view.getByRole("button", { name: "Queue" }).click();
    await expect(view.locator(".notice")).toContainText("Queued");
    // On a node: the attempts are not offered, since a node works a task once.
    await view.getByRole("textbox", { name: "Task" }).fill("Bump the lock file");
    await view.getByRole("textbox", { name: "Node" }).fill("second-node");
    await expect(view.getByRole("combobox", { name: "Attempts" })).toBeDisabled();
    await view.getByRole("button", { name: "Queue" }).click();
    await expect.poll(() => runs(home).filter((args) => args[0] === "night" && args[1] === "add").map((args) => args.join(" "))).toEqual([
      "night add --best-of 3 Make the retry test table-driven", "night add --on second-node Bump the lock file",
    ]);
    // An address is refused before anything runs.
    await view.getByRole("textbox", { name: "Task" }).fill("Anything");
    await view.getByRole("textbox", { name: "Node" }).fill("10.0.0.7");
    await view.getByRole("button", { name: "Queue" }).click();
    await expect(page.getByText(/not an address/)).toBeVisible();
    expect(runs(home).filter((args) => args[0] === "night" && args[1] === "add")).toHaveLength(2);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
