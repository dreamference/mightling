// MCP bundles installed from a file (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16 B45): what Skills and
// tools shows of a bundle's manifest before installing it, the settings form's values, and the
// `config/batchWrite` that adds the bundle's server to config.toml (`mcp_servers.<name>`) or takes it
// out. The main process reads and unpacks the bundle (desktop/electron/src/mcpb.ts). Pure: no DOM.

import type { BundleServer, BundleSetting } from "./bridge";
import type { ConfigBatchWriteParams } from "./protocol/v2/ConfigBatchWriteParams";
import type { JsonValue } from "./protocol/serde_json/JsonValue";

/** The write that adds a bundle's server, replacing any entry of that name, reloaded at once. */
export function bundleWrite(name: string, server: BundleServer): ConfigBatchWriteParams {
  return { edits: [{ keyPath: `mcp_servers.${name}`, value: server as unknown as JsonValue, mergeStrategy: "replace" }], filePath: null, expectedVersion: null, reloadUserConfig: true };
}

/** The write that takes a bundle's server out of config.toml. */
export function bundleRemoval(name: string): ConfigBatchWriteParams {
  return { edits: [{ keyPath: `mcp_servers.${name}`, value: null, mergeStrategy: "replace" }], filePath: null, expectedVersion: null, reloadUserConfig: true };
}

/** What the form holds: text per setting (several values one per line), a tick for an on/off one. */
export type BundleForm = Record<string, string | boolean>;

/** The form as it opens: each setting's default. */
export function initialForm(settings: BundleSetting[]): BundleForm {
  return Object.fromEntries(settings.map((setting) => {
    const value = setting.default;
    if (setting.type === "boolean") return [setting.key, value === true];
    return [setting.key, Array.isArray(value) ? value.join("\n") : value === null ? "" : String(value)];
  }));
}

/** The form's values as the main process checks them: numbers as numbers, several values as a list; an empty field left out. */
export function formValues(settings: BundleSetting[], form: BundleForm): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const setting of settings) {
    const raw = form[setting.key];
    if (setting.type === "boolean") {
      values[setting.key] = raw === true;
      continue;
    }
    const text = typeof raw === "string" ? raw.trim() : "";
    if (!text) continue;
    if (setting.multiple) values[setting.key] = text.split("\n").map((line) => line.trim()).filter(Boolean);
    else values[setting.key] = setting.type === "number" ? Number(text) : text;
  }
  return values;
}

/** The required settings still empty, by title. */
export function missingSettings(settings: BundleSetting[], form: BundleForm): string[] {
  const values = formValues(settings, form);
  return settings.filter((setting) => setting.required && setting.type !== "boolean" && values[setting.key] === undefined).map((setting) => setting.title);
}

/** A command and its arguments as one line, an argument with a space or a quote quoted. */
export function commandLine(command: string, args: string[]): string {
  const quote = (word: string) => (word === "" || /[\s"'\\]/.test(word) ? JSON.stringify(word) : word);
  return [command, ...args].map(quote).join(" ");
}

/** Whether config.toml (as `config/read` answered) has a server of this name already. */
export function configuredServer(config: unknown, name: string): boolean {
  const servers = typeof config === "object" && config !== null ? (config as { mcp_servers?: unknown }).mcp_servers : null;
  return typeof servers === "object" && servers !== null && Object.prototype.hasOwnProperty.call(servers, name);
}

/** A size in bytes, in words. */
export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
