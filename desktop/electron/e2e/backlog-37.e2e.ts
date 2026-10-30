// The parity backlog's B37 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): a side chat that is not kept.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-37
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";
import { projectFolders, received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

const text = (line: { params?: Record<string, unknown> }) => String(((line.params?.input as { text?: string }[] | undefined) ?? [])[0]?.text ?? "");

test("B37: Ctrl+Alt+S opens an unkept side chat beside the thread, its first message carrying the recap; Keep as a thread saves a copy", async () => {
  const { page, home, close } = await open();
  try {
    const { fetcher } = projectFolders(home);
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Fix the flaky retry test" }).click();
    await expect(page.locator(".bar .title")).toHaveText("Fix the flaky retry test");
    // The recap is of the turns the thread has loaded: they arrive after the title does.
    await expect(page.getByText("The loop swallowed the last")).toBeVisible();
    await page.keyboard.press("Control+Alt+s");
    const pane = page.getByRole("complementary", { name: "Side chat" });
    await expect(pane).toContainText("its 2 latest messages");
    // Ephemeral, in the thread's folder read-only, no prompt (so no Ask folder of its own).
    const startedSide = () => received(home).filter((line) => line.method === "thread/start" && line.params?.ephemeral === true && line.params?.cwd === fetcher);
    await expect.poll(() => startedSide().length).toBe(1);
    expect(startedSide()[0].params).toMatchObject({ sandbox: "read-only", approvalPolicy: "never" });
    expect(startedSide()[0].params).not.toHaveProperty("prompt");

    // The first message carries the recap; the pane shows the words alone.
    const box = pane.getByRole("textbox", { name: "Side chat message" });
    await box.fill("What is left?");
    await box.press("Enter");
    await expect(pane).toContainText("Left: the docs. The retry fix is in.");
    await expect(pane.locator(".side-chat-message.user")).toHaveText(["What is left?"]);
    const sideTurns = () => received(home).filter((line) => line.method === "turn/start" && String(line.params?.threadId).startsWith("e") && /^(What is left|Only the docs)\?/.test(text(line)));
    expect(text(sideTurns()[0])).toContain("User: The retry test fails one run in ten");
    expect(text(sideTurns()[0])).toContain('Context: this was asked in the conversation "Fix the flaky retry test"');
    await box.fill("Only the docs?");
    await box.press("Enter");
    await expect(pane).toContainText("Yes, only the docs.");
    expect(text(sideTurns()[1])).toBe("Only the docs?");
    // Not in the sidebar, and the thread in front is still the thread.
    await expect(page.locator(".thread", { hasText: "What is left" })).toHaveCount(0);
    await expect(page.locator(".bar .title")).toHaveText("Fix the flaky retry test");

    // Closed, it is gone: the server lets go of it.
    const sideId = String(sideTurns()[0].params?.threadId);
    await pane.getByRole("button", { name: "Close the side chat" }).click();
    await expect(pane).toHaveCount(0);
    await expect.poll(() => received(home).some((line) => line.method === "thread/unsubscribe" && line.params?.threadId === sideId)).toBe(true);

    // Opened again and kept: a saved thread in the same folder, its first message what the side chat said.
    await page.keyboard.press("Control+Alt+s");
    await expect(pane).toBeVisible();
    await pane.getByRole("textbox", { name: "Side chat message" }).fill("What is left?");
    await pane.getByRole("textbox", { name: "Side chat message" }).press("Enter");
    await expect(pane).toContainText("Left: the docs.");
    await pane.getByRole("button", { name: "Keep as a thread" }).click();
    await expect(pane).toHaveCount(0);
    await expect.poll(() => received(home).some((line) => line.method === "thread/start" && !line.params?.ephemeral && line.params?.cwd === fetcher)).toBe(true);
    const kept = received(home).filter((line) => line.method === "turn/start" && String(line.params?.threadId).startsWith("n")).pop();
    expect(text(kept!)).toContain('side chat beside the conversation "Fix the flaky retry test"');
    expect(text(kept!)).toContain("Agent: Left: the docs.");
    expect(received(home).find((line) => line.method === "thread/name/set" && line.params?.threadId === kept!.params?.threadId)?.params?.name).toBe("Side chat: Fix the flaky retry test");
    await expect(page.locator(".bar .title")).toHaveText("Side chat: Fix the flaky retry test");
    await expect(page.locator(".thread", { hasText: "Side chat: Fix the flaky retry test" })).toHaveCount(1);

    // Today's saved recap stays on the thread's menu.
    const row = page.locator(".thread-row", { hasText: "Fix the flaky retry test" }).filter({ hasNotText: "Side chat" }).first();
    await row.hover();
    await row.getByRole("button", { name: "More" }).click();
    await page.getByRole("menuitem", { name: "Recap in a saved copy" }).click();
    await expect.poll(() => received(home).some((line) => line.method === "thread/fork" && line.params?.threadId === "w1")).toBe(true);
  } finally {
    await close();
  }
});
