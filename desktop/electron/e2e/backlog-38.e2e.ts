// The parity backlog's B38 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): `/init` writes an AGENTS.md for a repository.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-38
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";
import { projectFolders, received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

const text = (line: { params?: Record<string, unknown> }) => String(((line.params?.input as { text?: string }[] | undefined) ?? [])[0]?.text ?? "");

test("B38: /init writes AGENTS.md as the turn's change, and with one there proposes edits to it", async () => {
  const { page, home, close } = await open();
  try {
    const { fetcher } = projectFolders(home);
    const agents = path.join(fetcher, "AGENTS.md");
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("textbox", { name: "Project folder" }).fill(fetcher);
    const composer = page.locator(".composer textarea");
    // In Work's / menu.
    await composer.fill("/ini");
    await expect(page.getByRole("listbox", { name: "Commands" }).locator("li", { hasText: "AGENTS.md" })).toBeVisible();
    // Typed out in full, Enter runs it.
    await composer.fill("/init");
    await composer.press("Enter");

    // A new thread in the folder, its turn Mightling's prompt, under the thread's permissions; the file arrives as the turn's change.
    const inits = () => received(home).filter((line) => line.method === "turn/start" && /AGENTS\.md/.test(text(line)));
    await expect.poll(() => inits().length).toBe(1);
    expect(text(inits()[0])).toMatch(/^Write a short AGENTS\.md for this repository/);
    expect(String(inits()[0].params?.threadId)).toMatch(/^n/);
    const started = received(home).find((line) => line.method === "thread/start" && line.params?.cwd === fetcher);
    expect(started).toBeTruthy();
    expect(inits()[0].params?.permissions).toEqual(started?.params?.permissions);
    const added = page.locator(".item.files details", { hasText: "AGENTS.md" });
    await expect(added.first()).toContainText("Added");
    await expect(page.getByText("Done: the retry counter is in fetcher/client.py.")).toHaveCount(1);
    expect(fs.existsSync(agents)).toBe(true);

    // Again in the same thread, now that it has one: the other prompt, the user's note after it, and an edit, not a new file.
    await composer.fill("/init mention the GPU tests");
    await composer.press("Enter");
    await expect.poll(() => inits().length).toBe(2);
    expect(text(inits()[1])).toMatch(/^This repository already has an AGENTS\.md/);
    expect(text(inits()[1])).toMatch(/The user adds: mention the GPU tests$/);
    expect(inits()[1].params?.threadId).toBe(inits()[0].params?.threadId);
    await expect(page.locator(".item.files details", { hasText: "AGENTS.md" }).nth(1)).toContainText("Edited");

    // Not in Ask's / menu.
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await composer.fill("/ini");
    await expect(page.getByRole("listbox", { name: "Commands" }).locator("li", { hasText: "AGENTS.md" })).toHaveCount(0);
  } finally {
    await close();
  }
});
