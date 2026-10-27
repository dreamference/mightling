// The parity backlog's B16 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): rules per MCP tool.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-16
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";
import { received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B16: each MCP tool can be always allowed, asked each time, or turned off, written as the keys ling reads", async () => {
  const { page, home, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("button", { name: "Skills and tools" }).click();
    const view = page.getByRole("dialog", { name: "Skills and tools" });
    await view.getByRole("button", { name: "MCP servers" }).click();
    await view.getByRole("button", { name: "3 tools" }).click();
    const tools = view.getByRole("table", { name: "ling-code's tools" });
    await expect(tools.locator("code")).toHaveText(["callers", "def", "refs"]);
    await tools.getByRole("combobox", { name: "When refs runs" }).selectOption("approve");
    await expect.poll(() => received(home).filter((m) => m.method === "config/batchWrite").map((m) => m.params)).toContainEqual({
      edits: [{ keyPath: "mcp_servers.ling-code.tools.refs.approval_mode", value: "approve", mergeStrategy: "replace" }], reloadUserConfig: true,
    });
    await tools.getByRole("combobox", { name: "When callers runs" }).selectOption("off");
    await expect.poll(() => received(home).filter((m) => m.method === "config/batchWrite").length).toBe(2);
    expect(received(home).filter((m) => m.method === "config/batchWrite")[1].params).toEqual({
      edits: [{ keyPath: "mcp_servers.ling-code.tools.callers.approval_mode", value: null, mergeStrategy: "replace" }, { keyPath: "mcp_servers.ling-code.disabled_tools", value: ["callers"], mergeStrategy: "replace" }],
      reloadUserConfig: true,
    });
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
