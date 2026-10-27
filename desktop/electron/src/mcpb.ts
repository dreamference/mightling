// MCP bundles installed from a file (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16 B45). A bundle
// (`.mcpb`, the public MCP bundle format) is a zip with a `manifest.json` at its root that names a
// local MCP server: what it runs (`server.mcp_config`: command, args, env, with `${__dirname}` and
// `${user_config.*}` substituted), the settings it asks for (`user_config`) and who made it. A signed
// bundle carries a PKCS#7 block after the zip, between `MCPB_SIG_V1` (and a 4-byte little-endian
// length) and `MCPB_SIG_END`; this module only says whether one is there, it does not verify it.
//
// The main process reads the manifest for the window to show first, then unpacks the bundle into
// `$CODEX_HOME/mcpb/<name>/` with the checks a zip from anywhere needs: no absolute path, no `..`, no
// link, no encryption, only stored and deflated entries, at most ENTRY_LIMIT files and UNPACKED_LIMIT
// bytes (counted as inflated, each entry bounded by its declared size and checked by CRC), into a
// fresh folder renamed into place only once every entry is out. Nothing is fetched: a bundle runs
// with the runtimes the machine has, so it installs at `/airgapped on` too, except one whose command
// is a package runner (FETCHING_COMMANDS), which main.ts refuses at `on`.

import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import zlib from "node:zlib";

/** The largest `.mcpb` file read. */
export const BUNDLE_FILE_LIMIT = 256 * 1024 * 1024;
/** The most bytes a bundle unpacks to, and the most files it holds. */
export const UNPACKED_LIMIT = 1024 * 1024 * 1024;
export const ENTRY_LIMIT = 20_000;
/** The largest `manifest.json`. */
export const MANIFEST_LIMIT = 1024 * 1024;

/** A server name as config.toml keys it and a folder name under `$CODEX_HOME/mcpb/` (the window's `MCP_NAME`). */
export const BUNDLE_NAME = /^[A-Za-z0-9_-]{1,64}$/;

/** Commands that fetch what they run from the network (a package runner, a container pull). */
export const FETCHING_COMMANDS = ["npx", "pnpx", "bunx", "uvx", "uv", "pipx", "docker", "podman"];

const SIG_START = Buffer.from("MCPB_SIG_V1");
const SIG_END = Buffer.from("MCPB_SIG_END");
const EOCD = 0x06054b50;
const CENTRAL = 0x02014b50;
const LOCAL = 0x04034b50;

export interface ZipEntry {
  /** The entry's path inside the bundle, checked (`entryPath`), `/`-separated, no trailing `/`. */
  path: string;
  directory: boolean;
  method: number;
  crc: number;
  compressedSize: number;
  size: number;
  localOffset: number;
  /** The Unix permission bits, when the archive was made on Unix. */
  mode: number | null;
}

export interface Archive {
  entries: ZipEntry[];
  /** Whether a signature block follows the zip (not verified). */
  signed: boolean;
  /** Where the entries' bytes end: the central directory starts there. */
  dataEnd: number;
  unpackedBytes: number;
}

/**
 * A zip entry's name as a safe relative path, or an error: no backslash, NUL or control character,
 * not absolute (`/x`, `C:x`), no empty, `.` or `..` segment.
 */
export function entryPath(name: string): string {
  if (!name || /[\\\u0000-\u001f\u007f]/.test(name)) throw new Error(`the bundle holds an unsafe file name: ${JSON.stringify(name)}`);
  if (name.startsWith("/") || /^[A-Za-z]:/.test(name)) throw new Error(`the bundle holds an absolute path: ${name}`);
  const trimmed = name.endsWith("/") ? name.slice(0, -1) : name;
  const segments = trimmed.split("/");
  if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) throw new Error(`the bundle holds a path that leaves its folder: ${name}`);
  if (trimmed.length > 1024) throw new Error(`the bundle holds a path longer than 1024 characters`);
  return trimmed;
}

async function readAt(handle: fs.FileHandle, position: number, length: number): Promise<Buffer> {
  const buffer = Buffer.alloc(length);
  const { bytesRead } = await handle.read(buffer, 0, length, position);
  if (bytesRead !== length) throw new Error("the bundle is cut short");
  return buffer;
}

/** Where the zip ends and whether a signature block follows it. */
async function zipExtent(handle: fs.FileHandle, size: number): Promise<{ zipEnd: number; signed: boolean }> {
  if (size < SIG_END.length) return { zipEnd: size, signed: false };
  const tailLength = Math.min(size, 1024 * 1024);
  const tail = await readAt(handle, size - tailLength, tailLength);
  if (!tail.subarray(tailLength - SIG_END.length).equals(SIG_END)) return { zipEnd: size, signed: false };
  const at = tail.lastIndexOf(SIG_START);
  if (at < 0 || at + SIG_START.length + 4 > tailLength) throw new Error("the signature block at the end of the bundle is damaged");
  const length = tail.readUInt32LE(at + SIG_START.length);
  if (at + SIG_START.length + 4 + length + SIG_END.length !== tailLength) throw new Error("the signature block at the end of the bundle is damaged");
  return { zipEnd: size - tailLength + at, signed: true };
}

/** Reads a bundle's central directory and checks every entry; nothing is inflated yet. */
export async function readArchive(file: string): Promise<Archive> {
  const handle = await fs.open(file, "r");
  try {
    const stats = await handle.stat();
    if (!stats.isFile()) throw new Error(`${file} is not a file`);
    if (stats.size > BUNDLE_FILE_LIMIT) throw new Error(`the bundle is larger than ${BUNDLE_FILE_LIMIT / (1024 * 1024)} MB`);
    const { zipEnd, signed } = await zipExtent(handle, stats.size);
    if (zipEnd < 22) throw new Error("this is not an MCP bundle: it is not a zip");
    const tailLength = Math.min(zipEnd, 22 + 0xffff);
    const tail = await readAt(handle, zipEnd - tailLength, tailLength);
    let eocd = -1;
    for (let at = tailLength - 22; at >= 0; at--) {
      if (tail.readUInt32LE(at) === EOCD && at + 22 + tail.readUInt16LE(at + 20) === tailLength) {
        eocd = at;
        break;
      }
    }
    if (eocd < 0) throw new Error("this is not an MCP bundle: it is not a zip");
    const entriesOnDisk = tail.readUInt16LE(eocd + 8);
    const count = tail.readUInt16LE(eocd + 10);
    const directorySize = tail.readUInt32LE(eocd + 12);
    const directoryOffset = tail.readUInt32LE(eocd + 16);
    if (count === 0xffff || directorySize === 0xffffffff || directoryOffset === 0xffffffff) throw new Error("the bundle is a ZIP64 archive, which is not read here");
    if (tail.readUInt16LE(eocd + 4) !== 0 || tail.readUInt16LE(eocd + 6) !== 0 || entriesOnDisk !== count) throw new Error("the bundle is a zip split over several files");
    if (count > ENTRY_LIMIT) throw new Error(`the bundle holds more than ${ENTRY_LIMIT} files`);
    const eocdOffset = zipEnd - tailLength + eocd;
    if (directoryOffset + directorySize > eocdOffset) throw new Error("the bundle's zip directory is damaged");
    const directory = await readAt(handle, directoryOffset, directorySize);

    const entries: ZipEntry[] = [];
    const seen = new Set<string>();
    let unpackedBytes = 0;
    let at = 0;
    for (let index = 0; index < count; index++) {
      if (at + 46 > directory.length || directory.readUInt32LE(at) !== CENTRAL) throw new Error("the bundle's zip directory is damaged");
      const madeBy = directory.readUInt16LE(at + 4) >> 8;
      const flags = directory.readUInt16LE(at + 8);
      const method = directory.readUInt16LE(at + 10);
      const crc = directory.readUInt32LE(at + 16);
      const compressedSize = directory.readUInt32LE(at + 20);
      const size = directory.readUInt32LE(at + 24);
      const nameLength = directory.readUInt16LE(at + 28);
      const extraLength = directory.readUInt16LE(at + 30);
      const commentLength = directory.readUInt16LE(at + 32);
      const external = directory.readUInt32LE(at + 38);
      const localOffset = directory.readUInt32LE(at + 42);
      if (at + 46 + nameLength > directory.length) throw new Error("the bundle's zip directory is damaged");
      const name = directory.subarray(at + 46, at + 46 + nameLength).toString("utf8");
      at += 46 + nameLength + extraLength + commentLength;

      if (name.includes("�")) throw new Error("the bundle holds a file name that is not UTF-8");
      const entry = entryPath(name);
      if (flags & 0x1) throw new Error(`the bundle's ${entry} is encrypted`);
      if (method !== 0 && method !== 8) throw new Error(`the bundle's ${entry} is compressed in a way not read here (method ${method})`);
      if (compressedSize === 0xffffffff || size === 0xffffffff || localOffset === 0xffffffff) throw new Error("the bundle is a ZIP64 archive, which is not read here");
      if (localOffset >= directoryOffset) throw new Error("the bundle's zip directory is damaged");
      const mode = madeBy === 3 ? external >>> 16 : null;
      const kind = mode === null ? 0 : mode & 0o170000;
      if (kind === 0o120000) throw new Error(`the bundle's ${entry} is a link, which a bundle may not hold`);
      const directoryEntry = name.endsWith("/") || kind === 0o040000 || (mode === null && (external & 0x10) !== 0);
      if (kind !== 0 && kind !== 0o100000 && kind !== 0o040000) throw new Error(`the bundle's ${entry} is not a file or a folder`);
      const key = entry.toLowerCase();
      if (seen.has(key)) throw new Error(`the bundle holds ${entry} twice`);
      seen.add(key);
      if (!directoryEntry) unpackedBytes += size;
      if (unpackedBytes > UNPACKED_LIMIT) throw new Error(`the bundle unpacks to more than ${UNPACKED_LIMIT / (1024 * 1024)} MB`);
      entries.push({ path: entry, directory: directoryEntry, method, crc, compressedSize, size, localOffset, mode: mode === null ? null : mode & 0o777 });
    }
    return { entries, signed, dataEnd: directoryOffset, unpackedBytes };
  } finally {
    await handle.close();
  }
}

/** One entry's bytes, inflated to at most its declared size and checked against its CRC. */
async function entryData(handle: fs.FileHandle, archive: Archive, entry: ZipEntry): Promise<Buffer> {
  const header = await readAt(handle, entry.localOffset, 30);
  if (header.readUInt32LE(0) !== LOCAL) throw new Error(`the bundle's ${entry.path} is damaged`);
  const start = entry.localOffset + 30 + header.readUInt16LE(26) + header.readUInt16LE(28);
  if (start + entry.compressedSize > archive.dataEnd) throw new Error(`the bundle's ${entry.path} is damaged`);
  const raw = entry.compressedSize ? await readAt(handle, start, entry.compressedSize) : Buffer.alloc(0);
  let data: Buffer;
  if (entry.method === 0) data = raw;
  else {
    try {
      data = zlib.inflateRawSync(raw, { maxOutputLength: Math.max(1, entry.size) });
    } catch {
      throw new Error(`the bundle's ${entry.path} does not inflate to the size it declares`);
    }
  }
  if (data.length !== entry.size) throw new Error(`the bundle's ${entry.path} does not inflate to the size it declares`);
  if (zlib.crc32(data) >>> 0 !== entry.crc >>> 0) throw new Error(`the bundle's ${entry.path} fails its checksum`);
  return data;
}

/** The bytes of one entry of a bundle (the manifest), at most `limit`. */
export async function readEntry(file: string, archive: Archive, name: string, limit: number): Promise<Buffer | null> {
  const entry = archive.entries.find((candidate) => candidate.path === name && !candidate.directory);
  if (!entry) return null;
  if (entry.size > limit) throw new Error(`the bundle's ${name} is larger than ${limit} bytes`);
  const handle = await fs.open(file, "r");
  try {
    return await entryData(handle, archive, entry);
  } finally {
    await handle.close();
  }
}

/** A setting the bundle asks for (`user_config.<key>`). */
export interface BundleSetting {
  key: string;
  type: "string" | "number" | "boolean" | "directory" | "file";
  title: string;
  description: string;
  required: boolean;
  sensitive: boolean;
  multiple: boolean;
  default: string | number | boolean | string[] | null;
  min: number | null;
  max: number | null;
}

/** The stdio server config.toml gets (`mcp_servers.<name>`: Codex's command, args, env, cwd). */
export interface BundleServer {
  command: string;
  args: string[];
  env?: Record<string, string>;
  cwd: string;
}

/** What the window shows before anything is unpacked. */
export interface BundleSummary {
  /** The folder under `$CODEX_HOME/mcpb/` and the server's name in config.toml. */
  name: string;
  /** Where it is unpacked. */
  dir: string;
  displayName: string;
  version: string;
  description: string;
  author: string;
  serverType: string;
  /** What it runs, with the folder and the user's folders substituted and the settings left as `${user_config.<key>}`. */
  command: string;
  args: string[];
  env: Record<string, string>;
  settings: BundleSetting[];
  signed: boolean;
  files: number;
  unpackedBytes: number;
  /** The command when it fetches what it runs (a package runner), else null. */
  fetches: string | null;
  /** Why it cannot be installed here, else null. */
  refusal: string | null;
  /** Whether a bundle of this name is installed already. */
  installed: boolean;
}

/** The user's folders a manifest may name (`${HOME}`, `${DESKTOP}`, `${DOCUMENTS}`, `${DOWNLOADS}`). */
export interface Places {
  home: string;
  desktop: string;
  documents: string;
  downloads: string;
}

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown): string => (typeof value === "string" ? value : "");

/** A manifest's `name` made into a server and folder name: letters, digits, `-` and `_`, at most 64. */
export function bundleName(name: string): string {
  const cleaned = name.trim().replace(/[^A-Za-z0-9_-]+/g, "-").replace(/-{2,}/g, "-").replace(/^[-_]+|[-_]+$/g, "").slice(0, 64);
  if (!BUNDLE_NAME.test(cleaned)) throw new Error(`the bundle's name ${JSON.stringify(name)} has nothing usable as a server name`);
  return cleaned;
}

/** The manifest's settings, in its order, defaults substituted. */
export function bundleSettings(manifest: Json, places: Places): BundleSetting[] {
  const config = isObject(manifest.user_config) ? manifest.user_config : {};
  const types = ["string", "number", "boolean", "directory", "file"];
  return Object.entries(config).filter(([key, value]) => /^[A-Za-z0-9_.-]{1,64}$/.test(key) && isObject(value)).map(([key, raw]) => {
    const value = raw as Json;
    const type = (types.includes(text(value.type)) ? value.type : "string") as BundleSetting["type"];
    const fill = (entry: unknown) => (typeof entry === "string" ? substitute(entry, { dir: "", ...places }, null) : entry);
    const fallback = value.default;
    const initial = Array.isArray(fallback) ? fallback.filter((entry) => typeof entry === "string").map((entry) => fill(entry) as string)
      : typeof fallback === "string" || typeof fallback === "number" || typeof fallback === "boolean" ? fill(fallback) as string | number | boolean : null;
    return {
      key, type,
      title: text(value.title) || key,
      description: text(value.description),
      required: value.required === true,
      sensitive: value.sensitive === true,
      multiple: value.multiple === true && (type === "directory" || type === "file"),
      default: initial,
      min: typeof value.min === "number" ? value.min : null,
      max: typeof value.max === "number" ? value.max : null,
    };
  });
}

/**
 * Substitutes the variables a bundle's `mcp_config` may use: `${__dirname}` (the bundle's folder),
 * `${HOME}`, `${DESKTOP}`, `${DOCUMENTS}`, `${DOWNLOADS}`, `${pathSeparator}` and `${/}`, and
 * `${user_config.<key>}` (several values joined by `,` inside a longer string; left as written when
 * `values` is null, for showing). An unknown variable is left as written.
 */
export function substitute(input: string, places: Places & { dir: string }, values: Record<string, unknown> | null): string {
  return input.replace(/\$\{([^}]+)\}/g, (whole, variable: string) => {
    const known: Record<string, string> = {
      __dirname: places.dir, HOME: places.home, DESKTOP: places.desktop, DOCUMENTS: places.documents, DOWNLOADS: places.downloads, pathSeparator: path.sep, "/": path.sep,
    };
    if (variable in known) return known[variable];
    if (!variable.startsWith("user_config.") || values === null) return whole;
    const value = values[variable.slice("user_config.".length)];
    if (Array.isArray(value)) return value.map(String).join(",");
    return value === undefined || value === null ? "" : String(value);
  });
}

/** Why a server type cannot run here without fetching, or null. */
function typeRefusal(server: Json): string | null {
  if (server.type === "uv") return "this bundle asks the app to fetch its Python packages with uv; a bundle installed here must carry what it runs";
  if (!["node", "python", "binary", undefined].includes(server.type as string | undefined)) return `this bundle's server type ${JSON.stringify(server.type)} is not one this app runs`;
  if (!isObject(server.mcp_config) && !text(server.entry_point)) return "this bundle names neither a command nor an entry point";
  return null;
}

/** The command and environment a manifest's server runs on this platform, before substitution. */
function launch(server: Json, platform: NodeJS.Platform): { command: string; args: unknown[]; env: Json } {
  const config = isObject(server.mcp_config) ? server.mcp_config : null;
  const entry = text(server.entry_point);
  let command = config ? text(config.command) : server.type === "node" ? "node" : server.type === "python" ? (platform === "win32" ? "python" : "python3") : `\${__dirname}/${entry}`;
  let args: unknown[] = config ? (Array.isArray(config.args) ? config.args : []) : server.type === "binary" ? [] : [`\${__dirname}/${entry}`];
  let env: Json = config && isObject(config.env) ? config.env : {};
  const override = config && isObject(config.platform_overrides) ? config.platform_overrides[platform] : null;
  if (isObject(override)) {
    if (typeof override.command === "string") command = override.command;
    if (Array.isArray(override.args)) args = override.args;
    if (isObject(override.env)) env = { ...env, ...override.env };
  }
  return { command, args, env };
}

/**
 * The server config.toml gets for a bundle unpacked into `dir`: its command (a relative path made
 * absolute in the folder, `.exe` added on Windows for a binary), its arguments (a whole-argument
 * `${user_config.<key>}` with several values becomes one argument each), its environment, and the
 * folder as its working directory. `values` null leaves the settings as written (for showing).
 */
export function bundleServer(manifest: Json, dir: string, places: Places, values: Record<string, unknown> | null, platform: NodeJS.Platform = process.platform): BundleServer {
  const server = isObject(manifest.server) ? manifest.server : {};
  const where = { dir, ...places };
  const plan = launch(server, platform);
  let command = substitute(plan.command, where, values);
  if (/[\\/]/.test(command) && !path.isAbsolute(command)) command = path.join(dir, command);
  if (platform === "win32" && server.type === "binary" && !path.extname(command)) command += ".exe";
  const args = plan.args.filter((arg): arg is string | number | boolean => ["string", "number", "boolean"].includes(typeof arg)).flatMap((arg) => {
    const whole = typeof arg === "string" ? arg.match(/^\$\{user_config\.([^}]+)\}$/) : null;
    const value = whole && values ? values[whole[1]] : undefined;
    if (Array.isArray(value)) return value.map(String);
    return [substitute(String(arg), where, values)];
  });
  const env = Object.fromEntries(Object.entries(plan.env).filter(([key, value]) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(key) && ["string", "number", "boolean"].includes(typeof value))
    .map(([key, value]) => [key, substitute(String(value), where, values)]));
  return Object.keys(env).length ? { command, args, env, cwd: dir } : { command, args, cwd: dir };
}

/** The command's own name when it fetches what it runs (FETCHING_COMMANDS), else null. */
export function fetchingCommand(command: string): string | null {
  const base = path.basename(command.replace(/\\/g, "/")).replace(/\.(exe|cmd|bat)$/i, "").toLowerCase();
  return FETCHING_COMMANDS.includes(base) ? base : null;
}

/** The folder bundles are unpacked into. */
export const bundlesFolder = (codexHome: string) => path.join(codexHome, "mcpb");

async function exists(place: string): Promise<boolean> {
  return fs.lstat(place).then(() => true, () => false);
}

/** Reads a bundle and says what it is: the manifest, the command, the settings, signed or not. */
export async function readBundle(file: string, codexHome: string, places: Places, platform: NodeJS.Platform = process.platform): Promise<{ archive: Archive; manifest: Json; summary: BundleSummary }> {
  const archive = await readArchive(file);
  const bytes = await readEntry(file, archive, "manifest.json", MANIFEST_LIMIT);
  if (!bytes) throw new Error("this is not an MCP bundle: it has no manifest.json");
  let manifest: unknown;
  try {
    manifest = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("the bundle's manifest.json is not JSON");
  }
  if (!isObject(manifest) || !text(manifest.name) || !isObject(manifest.server)) throw new Error("the bundle's manifest.json names no server");
  const name = bundleName(text(manifest.name));
  const dir = path.join(bundlesFolder(codexHome), name);
  const server = manifest.server;
  const author = isObject(manifest.author) ? text(manifest.author.name) : text(manifest.author);
  const shown = bundleServer(manifest, dir, places, null, platform);
  const platforms = isObject(manifest.compatibility) && Array.isArray(manifest.compatibility.platforms) ? manifest.compatibility.platforms.map(String) : null;
  const refusal = typeRefusal(server) ?? (platforms && !platforms.includes(platform) ? `this bundle runs only on ${platforms.join(", ")}` : null);
  return {
    archive, manifest,
    summary: {
      name,
      dir,
      displayName: text(manifest.display_name) || text(manifest.name),
      version: text(manifest.version),
      description: text(manifest.description),
      author,
      serverType: text(server.type) || "unknown",
      command: shown.command,
      args: shown.args,
      env: shown.env ?? {},
      settings: bundleSettings(manifest, places),
      signed: archive.signed,
      files: archive.entries.filter((entry) => !entry.directory).length,
      unpackedBytes: archive.unpackedBytes,
      fetches: fetchingCommand(shown.command),
      refusal,
      installed: await exists(dir),
    },
  };
}

/** The settings the user filled in, checked against what the bundle asks for; defaults fill the rest. */
export function settingValues(settings: BundleSetting[], given: Record<string, unknown>): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const setting of settings) {
    let value = given[setting.key];
    const empty = value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0);
    if (empty) value = setting.default ?? undefined;
    if (value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0)) {
      if (setting.required) throw new Error(`${setting.title} is required`);
      continue;
    }
    if (setting.type === "number") {
      const number = typeof value === "number" ? value : Number(value);
      if (!Number.isFinite(number)) throw new Error(`${setting.title} must be a number`);
      if ((setting.min !== null && number < setting.min) || (setting.max !== null && number > setting.max)) throw new Error(`${setting.title} must be between ${setting.min ?? "-∞"} and ${setting.max ?? "∞"}`);
      values[setting.key] = number;
    } else if (setting.type === "boolean") {
      if (typeof value !== "boolean") throw new Error(`${setting.title} must be on or off`);
      values[setting.key] = value;
    } else if (setting.multiple) {
      const list = Array.isArray(value) ? value : [value];
      if (!list.every((entry) => typeof entry === "string")) throw new Error(`${setting.title} must be text`);
      values[setting.key] = list;
    } else {
      if (typeof value !== "string") throw new Error(`${setting.title} must be text`);
      values[setting.key] = value;
    }
  }
  return values;
}

/**
 * Unpacks a bundle read before (`expected` is the name shown) into `$CODEX_HOME/mcpb/<name>/` and
 * returns the server config.toml gets. A bundle of that name already there is replaced only when
 * `replace` says so; the old one is removed only once the new one is in place.
 */
export async function installBundle(file: string, codexHome: string, places: Places, options: { expected: string; values: Record<string, unknown>; replace: boolean; platform?: NodeJS.Platform }): Promise<{ name: string; dir: string; server: BundleServer }> {
  const platform = options.platform ?? process.platform;
  const { archive, manifest, summary } = await readBundle(file, codexHome, places, platform);
  if (summary.name !== options.expected) throw new Error("the file changed since it was read: choose it again");
  if (summary.refusal) throw new Error(summary.refusal);
  const values = settingValues(summary.settings, options.values);
  const root = bundlesFolder(codexHome);
  const dir = path.join(root, summary.name);
  if (summary.installed && !options.replace) throw new Error(`a bundle named ${summary.name} is installed already: remove it first, or replace it`);
  await fs.mkdir(root, { recursive: true });
  const partial = path.join(root, `.${summary.name}.${randomUUID()}.partial`);
  await fs.mkdir(partial);
  try {
    await unpack(file, archive, partial);
    const server = bundleServer(manifest, dir, places, values, platform);
    // A command inside the bundle must be runnable, whatever the archive kept of its mode.
    const inside = path.relative(dir, server.command);
    if (platform !== "win32" && inside && !inside.startsWith("..") && !path.isAbsolute(inside)) {
      const command = path.join(partial, inside);
      const stats = await fs.lstat(command).catch(() => null);
      if (stats?.isFile()) await fs.chmod(command, (stats.mode & 0o777) | 0o755);
    }
    if (await exists(dir)) {
      const old = path.join(root, `.${summary.name}.${randomUUID()}.old`);
      await fs.rename(dir, old);
      await fs.rename(partial, dir);
      await fs.rm(old, { recursive: true, force: true });
    } else await fs.rename(partial, dir);
    return { name: summary.name, dir, server };
  } catch (error) {
    await fs.rm(partial, { recursive: true, force: true });
    throw error;
  }
}

/** Writes every entry of a checked archive under `into` (a fresh, empty folder). */
export async function unpack(file: string, archive: Archive, into: string): Promise<void> {
  const handle = await fs.open(file, "r");
  let written = 0;
  try {
    for (const entry of archive.entries) {
      const target = path.join(into, ...entry.path.split("/"));
      const inside = path.relative(into, target);
      if (!inside || inside.startsWith("..") || path.isAbsolute(inside)) throw new Error(`the bundle holds a path that leaves its folder: ${entry.path}`);
      if (entry.directory) {
        await fs.mkdir(target, { recursive: true });
        continue;
      }
      await fs.mkdir(path.dirname(target), { recursive: true });
      const data = await entryData(handle, archive, entry);
      written += data.length;
      if (written > UNPACKED_LIMIT) throw new Error(`the bundle unpacks to more than ${UNPACKED_LIMIT / (1024 * 1024)} MB`);
      const mode = entry.mode !== null && entry.mode & 0o111 ? 0o755 : 0o644;
      await fs.writeFile(target, data, { flag: "wx", mode });
    }
  } finally {
    await handle.close();
  }
}

/** Removes an installed bundle's folder; nothing when it is not there. */
export async function removeBundle(codexHome: string, name: string): Promise<void> {
  if (!BUNDLE_NAME.test(name)) throw new Error(`not a bundle name: ${name}`);
  const dir = path.join(bundlesFolder(codexHome), name);
  const stats = await fs.lstat(dir).catch(() => null);
  if (!stats) return;
  if (!stats.isDirectory()) throw new Error(`${dir} is not a bundle's folder`);
  await fs.rm(dir, { recursive: true, force: true });
}

/** An installed bundle, as Skills and tools lists it. */
export interface InstalledBundle {
  name: string;
  displayName: string;
  version: string;
  author: string;
  dir: string;
}

/** The bundles under `$CODEX_HOME/mcpb/`, by name. */
export async function listBundles(codexHome: string): Promise<InstalledBundle[]> {
  const root = bundlesFolder(codexHome);
  const entries = await fs.readdir(root, { withFileTypes: true }).catch(() => []);
  const found: InstalledBundle[] = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || !BUNDLE_NAME.test(entry.name)) continue;
    const dir = path.join(root, entry.name);
    let manifest: Json = {};
    try {
      const file = path.join(dir, "manifest.json");
      const stats = await fs.lstat(file);
      if (stats.isFile() && stats.size <= MANIFEST_LIMIT) {
        const parsed: unknown = JSON.parse(await fs.readFile(file, "utf8"));
        if (isObject(parsed)) manifest = parsed;
      }
    } catch {
      // Listed by its folder's name alone.
    }
    found.push({
      name: entry.name,
      displayName: text(manifest.display_name) || text(manifest.name) || entry.name,
      version: text(manifest.version),
      author: isObject(manifest.author) ? text(manifest.author.name) : text(manifest.author),
      dir,
    });
  }
  return found.sort((a, b) => a.name.localeCompare(b.name));
}
