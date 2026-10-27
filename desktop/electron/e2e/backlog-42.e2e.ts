// The parity backlog's B42 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): Scheduled's Repeat
// on chosen weekdays, every N nights and on a day of the month, through `ling-admin night every add`.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-42
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";
import { runs } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B42: a task repeats on chosen weekdays, every N nights or on a day of the month", async () => {
  const { page, home, close } = await open();
  const added = () => runs(home).filter((args) => args[0] === "night" && args[1] === "every" && args[2] === "add");
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("button", { name: /Scheduled/ }).click();
    const view = page.getByRole("dialog", { name: "Scheduled" });
    const repeat = view.getByRole("button", { name: "Repeat", exact: true });

    // On chosen days: Monday, Wednesday and Friday are offered; Wednesday off, Saturday on.
    await view.getByRole("textbox", { name: "Task" }).fill("Audit the dependencies");
    await view.getByRole("combobox", { name: "Repeat" }).selectOption("days");
    const days = view.getByRole("group", { name: "Days" });
    await expect(days.getByRole("checkbox", { name: "Mon" })).toBeChecked();
    await days.getByRole("checkbox", { name: "Wed" }).uncheck();
    await days.getByRole("checkbox", { name: "Sat" }).check();
    await repeat.click();
    await expect.poll(() => added().pop()).toEqual(["night", "every", "add", "--days", "mon,fri,sat", "--", "Audit the dependencies"]);
    await expect(view.locator("tr", { hasText: "Audit the dependencies" })).toContainText("every Monday, Friday, Saturday");

    // No day chosen: nothing to repeat on.
    await view.getByRole("textbox", { name: "Task" }).fill("Never");
    for (const day of ["Mon", "Fri", "Sat"]) await days.getByRole("checkbox", { name: day }).uncheck();
    await expect(repeat).toBeDisabled();

    // Every 3 nights.
    await view.getByRole("textbox", { name: "Task" }).fill("Sync the migrations");
    await view.getByRole("combobox", { name: "Repeat" }).selectOption("every");
    await view.getByRole("spinbutton", { name: "Nights between runs" }).fill("3");
    await repeat.click();
    await expect.poll(() => added().pop()).toEqual(["night", "every", "add", "--every", "3", "--", "Sync the migrations"]);
    await expect(view.locator("tr", { hasText: "Sync the migrations" })).toContainText("every 3 nights");

    // A number out of range is refused by the main process; ling-admin is not run.
    await view.getByRole("textbox", { name: "Task" }).fill("Too often");
    await view.getByRole("spinbutton", { name: "Nights between runs" }).fill("0");
    await repeat.click();
    await expect(page.getByText(/the nights between runs is a whole number from 1 to 365/)).toBeVisible();
    expect(added().some((args) => args.includes("Too often"))).toBe(false);

    // On the 31st of each month (the last day in shorter months).
    await view.getByRole("textbox", { name: "Task" }).fill("Rotate the keys");
    await view.getByRole("combobox", { name: "Repeat" }).selectOption("monthly");
    await view.getByRole("spinbutton", { name: "Day of the month" }).fill("31");
    await repeat.click();
    await expect.poll(() => added().pop()).toEqual(["night", "every", "add", "--monthly", "31", "--", "Rotate the keys"]);
    const monthly = view.locator("tr", { hasText: "Rotate the keys" });
    await expect(monthly).toContainText("on day 31 of each month");

    // Stop takes it out of Repeating.
    await monthly.getByRole("button", { name: "Stop" }).click();
    await expect(monthly).toHaveCount(0);
    await expect(view.locator("tr", { hasText: "Sync the migrations" })).toHaveCount(1);
  } finally {
    await close();
  }
});
