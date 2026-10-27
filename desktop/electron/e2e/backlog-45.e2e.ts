// The parity backlog's B45 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): MCP bundles (.mcpb) installed from a file.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-45
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { makeZip, manifestText } from "../src/test-zip";
import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";
import { received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B45: a bundle's manifest is shown first, Install unpacks it and adds its server through config/batchWrite (airgapped on too), Remove takes it out", async () => {
  const { app, page, home, close } = await open({
    prepare: (scratch) => {
      fs.writeFileSync(path.join(scratch, "notes.mcpb"), makeZip([
        { name: "manifest.json", data: manifestText() },
        { name: "server/index.js", data: "console.log('notes')\n" },
      ]));
      // Signed, and run by a package runner: shown, never installed at airgapped on.
      fs.writeFileSync(path.join(scratch, "runner.mcpb"), makeZip([
        { name: "manifest.json", data: manifestText({ name: "runner-tool", display_name: "Runner tool", user_config: {}, server: { type: "node", mcp_config: { command: "npx", args: ["-y", "some-server"] } } }) },
      ], Buffer.from("a PKCS#7 block")));
    },
  });
  try {
    // Nothing here fetches, so it installs with airgapped on.
    fs.writeFileSync(path.join(home, "airgapped-on"), "");
    const dir = path.join(home, ".mightling", "mcpb", "notes-search");
    const choose = (file: string) => app.evaluate(({ dialog }, chosen) => {
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: [chosen] })) as typeof dialog.showOpenDialog;
    }, path.join(home, file));
    const writes = () => received(home).filter((line) => line.method === "config/batchWrite").map((line) => line.params);

    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("button", { name: "Skills and tools" }).click();
    const view = page.getByRole("dialog", { name: "Skills and tools" });
    await view.getByRole("button", { name: "MCP servers" }).click();
    await choose("notes.mcpb");
    await view.getByRole("button", { name: "Install from a file…" }).click();

    // The manifest first: name, author, the command with the bundle's folder in it, the settings, unsigned.
    const card = view.getByRole("region", { name: "Bundle to install" });
    await expect(card.getByRole("heading")).toHaveText("Notes search 1.2.0");
    await expect(card).toContainText("By Example Tools.");
    await expect(card.locator(".mcp-bundle-command")).toHaveText(`node ${dir}/server/index.js --root \${user_config.root}`);
    await expect(card).toContainText("NOTES_TOKEN=${user_config.token}");
    await expect(card).toContainText("Not signed.");
    await expect(card.getByRole("textbox", { name: "Notes folder" })).toHaveValue(path.join(home, "notes"));
    await expect(card.getByLabel("Token")).toHaveAttribute("type", "password");
    expect(fs.existsSync(dir)).toBe(false);
    expect(writes()).toEqual([]);

    await card.getByLabel("Token").fill("s3cret");
    await card.getByRole("button", { name: "Install", exact: true }).click();
    await expect.poll(writes).toEqual([{
      edits: [{ keyPath: "mcp_servers.notes-search", mergeStrategy: "replace",
        value: { command: "node", args: [`${dir}/server/index.js`, "--root", path.join(home, "notes")], env: { NOTES_TOKEN: "s3cret" }, cwd: dir } }],
      filePath: null, expectedVersion: null, reloadUserConfig: true,
    }]);
    expect(fs.readFileSync(path.join(dir, "server", "index.js"), "utf8")).toBe("console.log('notes')\n");
    const installed = view.getByRole("table", { name: "Installed bundles" });
    await expect(installed).toContainText("Notes search");
    await expect(card).toHaveCount(0);

    // A signed bundle run by a package runner: the signature is reported, not checked; at airgapped on the main process refuses it.
    await choose("runner.mcpb");
    await view.getByRole("button", { name: "Install from a file…" }).click();
    await expect(card).toContainText("A signature is attached; Mightling does not check it");
    await expect(card).toContainText("This bundle runs npx, which fetches what it runs from the network");
    const token = await page.evaluate(() => (window as unknown as { electronBridge: { sendMessageFromView(message: object): Promise<{ token: string }> } }).electronBridge
      .sendMessageFromView({ type: "mcpb/pick" }).then((picked) => picked.token));
    const refused = await page.evaluate((sent) => (window as unknown as { electronBridge: { sendMessageFromView(message: object): Promise<unknown> } }).electronBridge
      .sendMessageFromView(sent).then(() => "ran", (error: unknown) => String(error)), { type: "mcpb/install", token, values: {}, replace: false });
    expect(refused).toContain("airgapped is on");
    expect(fs.existsSync(path.join(home, ".mightling", "mcpb", "runner-tool"))).toBe(false);
    await card.getByRole("button", { name: "Cancel" }).click();

    // Remove: the server leaves config.toml, the folder is deleted.
    page.once("dialog", (dialog) => void dialog.accept());
    await installed.getByRole("button", { name: "Remove Notes search" }).click();
    await expect.poll(() => writes().length).toBe(2);
    expect(writes()[1]).toEqual({ edits: [{ keyPath: "mcp_servers.notes-search", value: null, mergeStrategy: "replace" }], filePath: null, expectedVersion: null, reloadUserConfig: true });
    await expect.poll(() => fs.existsSync(dir)).toBe(false);
    await expect(installed).toHaveCount(0);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
