// The parity backlog's B33 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): an Ask question
// continued in a project, through the main process's policy: the fork reaches the server in the
// project's primary folder with the sandbox an Ask folder gets; into a folder that is gone it stays in
// the question's folder, and the window deletes that copy and says why.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-33
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";
import { projectFolders, received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

// What reached the stand-in, past the policy; the model the window names is not this item's.
const sent = (home: string, method: string) => received(home).filter((line) => line.method === method).map(({ params }) => {
  const { model: _model, ...rest } = params ?? {};
  return rest;
});

test("B33: Continue in a project forks an Ask question into the project's folder as a Work thread, and nowhere else", async () => {
  // The question's folder linked by its thread id, as the app links one it started: the policy knows it is an Ask thread.
  const { page, home, close } = await open({
    prepare: (home) => {
      const askRoot = path.join(home, ".mightling", "ask");
      fs.symlinkSync("q-1a2b3c", path.join(askRoot, "q1"));
      fs.mkdirSync(path.join(home, "gone"));
    },
  });
  const { fetcher } = projectFolders(home);
  const question = fs.realpathSync(path.join(home, ".mightling", "ask", "q-1a2b3c"));
  try {
    // Two projects: one whose folder is then removed.
    await page.getByRole("button", { name: "Work", exact: true }).click();
    for (const [name, folder] of [["Launch", fetcher], ["Gone", path.join(home, "gone")]]) {
      await page.getByRole("button", { name: "New project" }).click();
      const dialog = page.getByRole("dialog", { name: "New project" });
      await dialog.getByRole("textbox", { name: "Project name" }).fill(name);
      // The dialog may start with the folder in view; each project here has only its own.
      const removable = dialog.getByRole("button", { name: /^Remove / });
      while (await removable.count()) await removable.first().click();
      await dialog.getByRole("textbox", { name: "Folder to add" }).fill(folder);
      await dialog.getByRole("textbox", { name: "Folder to add" }).press("Enter");
      await dialog.getByRole("button", { name: "Create project" }).click();
      await expect(page.locator(".server-project h2", { hasText: name })).toBeVisible();
    }
    fs.rmSync(path.join(home, "gone"), { recursive: true });

    // From the question's own bar: into the folder that is gone, the copy stays in the question's folder; deleted, and said.
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await page.locator(".sidebar .thread-row", { hasText: "GB10" }).locator(".thread").click();
    await page.getByRole("button", { name: "Continue in a project" }).click();
    const picker = page.getByRole("dialog", { name: "Continue in a project" });
    await expect(picker.getByRole("button", { name: "Continue in Launch" })).toContainText(fetcher);
    await picker.getByRole("button", { name: "Continue in Gone" }).click();
    await expect(page.locator(".notice", { hasText: "could not be continued in Gone" })).toBeVisible();
    await expect.poll(() => sent(home, "thread/fork")).toEqual([{ threadId: "q1", cwd: question, sandbox: "workspace-write" }]);
    await expect.poll(() => sent(home, "thread/delete")).toEqual([{ threadId: "q1-fork" }]);
    expect(sent(home, "thread/metadata/update")).toEqual([]);
    await expect(picker).toHaveCount(0);

    // From the row's menu: into Launch's folder, a Work thread there, in the project, opened in Work.
    const row = page.locator(".sidebar .thread-row", { hasText: "GB10" }).first();
    await row.hover();
    await row.getByRole("button", { name: "More" }).click();
    await page.getByRole("menuitem", { name: "Continue in a project…" }).click();
    await picker.getByRole("button", { name: "Continue in Launch" }).click();
    await expect.poll(() => sent(home, "thread/fork").length).toBe(2);
    expect(sent(home, "thread/fork")[1]).toEqual({ threadId: "q1", cwd: fetcher, sandbox: "workspace-write" });
    await expect.poll(() => sent(home, "thread/metadata/update")).toEqual([{ threadId: "q1-fork", projectId: "proj-1" }]);
    await expect(page.locator("header .cwd")).toHaveText(fetcher);
    await expect(page.locator(".server-project", { hasText: "Launch" }).locator('.thread-row[data-thread-id="q1-fork"]')).toBeVisible();

    // The question itself stays in Ask.
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.locator(".sidebar .thread-row", { hasText: "GB10" })).toHaveCount(1);
  } finally {
    await close();
  }
});
