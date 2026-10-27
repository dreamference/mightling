// The parity backlog's B2 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): a question from
// the agent answered in full. Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, startWork } from "./backlog-session";
import { received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B2: a question answered in full: descriptions shown, None of the above in the user's words, a secret hidden", async () => {
  const { page, home, close } = await open();
  try {
    await startWork(page, home, "Tidy the client with a question first.");
    const card = page.locator(".approval.questions");
    await expect(card).toContainText("Which part first?");
    await expect(card).toContainText("fetcher/client.py and its retry loop");
    const answer = card.getByRole("button", { name: "Answer" });
    await expect(answer).toBeDisabled();
    // "Other": a field for the user's words appears, and the answer waits for them.
    await card.getByRole("radio", { name: /None of the above/ }).check();
    await expect(answer).toBeDisabled();
    await card.getByRole("textbox", { name: "Your answer to Scope" }).fill("The logging first");
    // The secret is typed into a hidden field.
    const secret = card.getByLabel("Token", { exact: true });
    await expect(secret).toHaveAttribute("type", "password");
    await secret.fill("s3cret");
    await answer.click();
    await expect.poll(() => received(home).find((m) => m.id === "input-1")?.result).toEqual({
      answers: { scope: { answers: ["None of the above", "user_note: The logging first"] }, token: { answers: ["user_note: s3cret"] } },
    });
    await expect(card).toHaveCount(0);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
