// The parity backlog's B34 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): MCP servers and plugins in full.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-34
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";
import { received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B34: Add server with environment, folder, headers and a token's variable; Remove asks first; startup status; plugin details before install, search, Installed on/off, a marketplace removed", async () => {
  const { page, home, close } = await open();
  try {
    const sent = (method: string) => received(home).filter((line) => line.method === method).map((line) => line.params as Record<string, unknown>);
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("button", { name: "Skills and tools" }).click();
    const view = page.getByRole("dialog", { name: "Skills and tools" });

    // A command with arguments, environment and a working folder: only the fields filled are written.
    await view.getByRole("button", { name: "MCP servers" }).click();
    await view.getByRole("button", { name: "Add an MCP server" }).click();
    await view.getByRole("textbox", { name: "Server name" }).fill("notes");
    await view.getByRole("textbox", { name: "Server command or URL" }).fill("node");
    await view.getByRole("textbox", { name: "Server arguments" }).fill("server.js --stdio");
    await view.getByRole("textbox", { name: "Environment variables" }).fill("NOTES_DIR=/srv/notes\nLOG_LEVEL=info");
    await view.getByRole("textbox", { name: "Working folder" }).fill("app");
    await expect(view.getByRole("status")).toContainText("full path");
    await expect(view.getByRole("button", { name: "Add", exact: true })).toBeDisabled();
    await view.getByRole("textbox", { name: "Working folder" }).fill("/srv/app");
    await view.getByRole("button", { name: "Add", exact: true }).click();
    await expect.poll(() => sent("config/value/write").find((params) => params.keyPath === "mcp_servers.notes")?.value).toEqual({
      command: "node", args: ["server.js", "--stdio"], env: { NOTES_DIR: "/srv/notes", LOG_LEVEL: "info" }, cwd: "/srv/app",
    });

    // A URL with headers and the bearer token's variable: a token pasted there is refused, its name is written.
    await view.getByRole("button", { name: "Add an MCP server" }).click();
    await view.getByRole("textbox", { name: "Server name" }).fill("tracker");
    await view.getByRole("combobox", { name: "Server kind" }).selectOption("url");
    await view.getByRole("textbox", { name: "Server command or URL" }).fill("https://mcp.example/mcp");
    await view.getByRole("textbox", { name: "HTTP headers" }).fill("X-Team: core");
    await view.getByRole("textbox", { name: "Bearer-token variable" }).fill("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig");
    await expect(view.getByRole("status")).toContainText("not the token");
    await expect(view.getByRole("button", { name: "Add", exact: true })).toBeDisabled();
    await view.getByRole("textbox", { name: "Bearer-token variable" }).fill("$TRACKER_TOKEN");
    await view.getByRole("button", { name: "Add", exact: true }).click();
    await expect.poll(() => sent("config/value/write").find((params) => params.keyPath === "mcp_servers.tracker")?.value).toEqual({
      url: "https://mcp.example/mcp", http_headers: { "X-Team": "core" }, bearer_token_env_var: "TRACKER_TOKEN",
    });
    expect(JSON.stringify(sent("config/value/write"))).not.toContain("eyJhbGciOiJIUzI1NiJ9");

    // Remove: offered for the servers added here (in config.toml), asks first; dismissed, nothing is written.
    const notes = view.locator("tr", { hasText: "notes" });
    await expect(notes).toContainText("starts");
    let asked = "";
    page.once("dialog", (dialog) => { asked = dialog.message(); void dialog.dismiss(); });
    await notes.getByRole("button", { name: "Remove notes" }).click();
    expect(asked).toContain("Remove the MCP server notes?");
    expect(sent("config/batchWrite").some((params) => JSON.stringify(params).includes("mcp_servers.notes"))).toBe(false);
    page.once("dialog", (dialog) => void dialog.accept());
    await notes.getByRole("button", { name: "Remove notes" }).click();
    await expect.poll(() => sent("config/batchWrite").find((params) => JSON.stringify(params).includes("mcp_servers.notes"))?.edits).toEqual([
      { keyPath: "mcp_servers.notes", value: null, mergeStrategy: "replace" },
    ]);
    await expect(view.locator("tr", { hasText: "notes" })).toHaveCount(0);
    // ling-code is not in config.toml here: no Remove to offer.
    await expect(view.getByRole("button", { name: "Remove ling-code" })).toHaveCount(0);

    // Startup status: what a thread reports (here after Read the servers again), in words.
    await view.getByRole("button", { name: "Read the servers again" }).click();
    await expect(view.locator("tr", { hasText: "ling-code" }).first()).toContainText("failed to start: exited with status 1");

    // Plugins: search, the Installed row and its on/off.
    await view.getByRole("button", { name: "Plugins" }).click();
    const installed = view.getByRole("region", { name: "Installed plugins" });
    await expect(installed).toContainText("format");
    await installed.getByRole("button", { name: "Turn off format" }).click();
    await expect.poll(() => sent("config/batchWrite").find((params) => JSON.stringify(params).includes("format@team"))?.edits).toEqual([
      { keyPath: "plugins", value: { "format@team": { enabled: false } }, mergeStrategy: "upsert" },
    ]);
    const search = view.getByRole("searchbox", { name: "Search plugins" });
    await search.fill("nothing like it");
    await expect(view).toContainText("No plugin matches the search.");
    await search.fill("style");
    await expect(view.locator("tr", { hasText: "lint" })).toHaveCount(0);
    await expect(installed).toContainText("format");
    await search.fill("");

    // Install shows the details (plugin/read) first; only the details' Install installs.
    await view.locator("tr", { hasText: "lint" }).getByRole("button", { name: "Install" }).click();
    const details = view.getByRole("region", { name: "Plugin details" });
    await expect(details).toContainText("Runs the team's checks before a commit.");
    await expect(details).toContainText("$lint-check");
    await expect(details).toContainText("lint-server");
    await expect(details).toContainText("postToolUse");
    expect(sent("plugin/read")).toEqual([{ marketplacePath: "/srv/team/marketplace.json", pluginName: "lint" }]);
    expect(sent("plugin/install")).toEqual([]);
    await details.getByRole("button", { name: "Install" }).click();
    await expect.poll(() => sent("plugin/install")).toEqual([{ marketplacePath: "/srv/team/marketplace.json", pluginName: "lint" }]);
    await expect(details).toHaveCount(0);

    // A marketplace removed, after a confirmation.
    page.once("dialog", (dialog) => void dialog.accept());
    await view.getByRole("button", { name: "Remove the marketplace Team tools" }).click();
    await expect.poll(() => sent("marketplace/remove")).toEqual([{ marketplaceName: "Team tools" }]);
  } finally {
    await close();
  }
});
