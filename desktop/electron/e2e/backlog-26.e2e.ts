// The parity backlog's B26 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): recommended MCP servers.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-26
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";
import { received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B26: the Add server form offers well-known local MCP servers, each filling the form in and marked as needing the network", async () => {
  const { app, page, home, close } = await open();
  try {
    await app.evaluate(({ app: electronApp }) => {
      electronApp.emit("second-instance", {}, ["ling-app", "ling://skills"], "/", { argv: ["ling://skills"] });
    });
    const tools = page.getByRole("dialog", { name: "Skills and tools" });
    await tools.getByRole("tab", { name: "MCP servers" }).or(tools.getByRole("button", { name: "MCP servers" })).first().click();
    await tools.getByRole("button", { name: "Add an MCP server" }).click();
    const recommended = tools.getByRole("list", { name: "Recommended MCP servers" });
    await expect(recommended).toContainText("Needs the network");
    await recommended.getByRole("button", { name: "Git", exact: true }).click();
    await expect(tools.getByLabel("Server name")).toHaveValue("git");
    await expect(tools.getByLabel("Server command or URL")).toHaveValue("uvx");
    await expect(tools.getByLabel("Server arguments")).toHaveValue(/mcp-server-git/);
    // Nothing was written until Add.
    expect(received(home).some((m) => m.method === "config/value/write" && String(m.params?.keyPath).startsWith("mcp_servers."))).toBe(false);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
