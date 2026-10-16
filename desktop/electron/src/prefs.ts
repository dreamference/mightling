// The window's own preferences (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §1.6, §2.15): what
// the Codex app keeps beside the agent's settings rather than in them, such as what Enter does or
// whether a follow-up steers the running turn. One JSON file under the app's data folder, every key
// and value checked against the schema the page is built with (desktop/ui/src/prefs.json), so the
// page can neither invent a key nor store something of the wrong shape. Agent settings stay in
// `~/.mightling/config.toml`, written by the server (`config/value/write`). No Electron here.

import fs from "node:fs";
import path from "node:path";

import schemaFile from "../../ui/src/prefs.json";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export interface PrefSpec {
  default: Json;
  enum?: string[];
  type?: "string" | "number" | "boolean" | "strings" | "object";
  nullable?: boolean;
}

export const SCHEMA: Record<string, PrefSpec> = (schemaFile as { keys: Record<string, PrefSpec> }).keys;

/** The largest file this keeps; a preference is a setting, not a store. */
export const MAX_BYTES = 256 * 1024;

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** Why `value` is not a valid `key`, or null when it is. */
export function invalid(key: string, value: unknown, schema: Record<string, PrefSpec> = SCHEMA): string | null {
  const spec = Object.prototype.hasOwnProperty.call(schema, key) ? schema[key] : undefined;
  if (!spec) return `there is no preference named ${key}`;
  if (value === null) return spec.nullable || spec.default === null ? null : `${key} cannot be empty`;
  if (spec.enum) return typeof value === "string" && spec.enum.includes(value) ? null : `${key} must be one of ${spec.enum.join(", ")}`;
  switch (spec.type) {
    case "string":
      return typeof value === "string" && value.length <= 4096 ? null : `${key} must be a string`;
    case "number":
      return typeof value === "number" && Number.isFinite(value) ? null : `${key} must be a number`;
    case "boolean":
      return typeof value === "boolean" ? null : `${key} must be true or false`;
    case "strings":
      return Array.isArray(value) && value.length <= 500 && value.every((item) => typeof item === "string" && item.length <= 4096)
        ? null
        : `${key} must be a list of strings`;
    case "object":
      return isPlainObject(value) ? null : `${key} must be an object`;
    default:
      return `${key} has no type in the schema`;
  }
}

/** The defaults, overlaid with whatever valid values `stored` holds; anything else in it is ignored. */
export function withDefaults(stored: unknown, schema: Record<string, PrefSpec> = SCHEMA): Record<string, Json> {
  const result: Record<string, Json> = {};
  for (const [key, spec] of Object.entries(schema)) result[key] = spec.default;
  if (isPlainObject(stored)) {
    for (const [key, value] of Object.entries(stored)) {
      if (invalid(key, value, schema) === null) result[key] = value as Json;
    }
  }
  return result;
}

/** The preferences file: read whole, written whole (to a temporary file, then renamed). */
export class Prefs {
  private readonly file: string;
  private readonly schema: Record<string, PrefSpec>;

  constructor(folder: string, schema: Record<string, PrefSpec> = SCHEMA) {
    this.file = path.join(folder, "prefs.json");
    this.schema = schema;
  }

  read(): Record<string, Json> {
    try {
      const text = fs.readFileSync(this.file, "utf-8");
      return withDefaults(text.length <= MAX_BYTES ? JSON.parse(text) : null, this.schema);
    } catch {
      return withDefaults(null, this.schema);
    }
  }

  /** Writes `values` over the current preferences; refuses the whole write if any key or value is wrong. */
  write(values: unknown): Record<string, Json> {
    if (!isPlainObject(values)) throw new Error("preferences must be an object");
    for (const [key, value] of Object.entries(values)) {
      const reason = invalid(key, value, this.schema);
      if (reason) throw new Error(reason);
    }
    const next = { ...this.read(), ...(values as Record<string, Json>) };
    const text = JSON.stringify(next, null, 2);
    if (text.length > MAX_BYTES) throw new Error("the preferences would be too large");
    fs.mkdirSync(path.dirname(this.file), { recursive: true, mode: 0o700 });
    const temporary = `${this.file}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, text, { mode: 0o600 });
    fs.renameSync(temporary, this.file);
    return next;
  }
}
