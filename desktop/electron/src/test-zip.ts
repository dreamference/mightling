// Crafted zips for the tests of the MCP bundle reader (mcpb.ts) and its end-to-end test: a small
// writer that lets a test set what a hostile archive would (names, modes, declared sizes, the method,
// a signature block after it). Not a test itself, and never part of the app.

import zlib from "node:zlib";

export interface FixtureEntry {
  name: string;
  data?: Buffer | string;
  /** Deflate the data (the default) or store it. */
  deflate?: boolean;
  /** Unix mode bits, file type included (0o100644 a file, 0o120777 a link, 0o040755 a folder). */
  mode?: number;
  /** Overrides, to craft a lie: the size declared, the method, the flags. */
  size?: number;
  method?: number;
  flags?: number;
}

/** A zip of `entries`, and a signature block after it when `signature` is given. */
export function makeZip(entries: FixtureEntry[], signature?: Buffer): Buffer {
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  for (const entry of entries) {
    const data = Buffer.isBuffer(entry.data) ? entry.data : Buffer.from(entry.data ?? "");
    const deflate = entry.deflate !== false && !entry.name.endsWith("/");
    const body = deflate ? zlib.deflateRawSync(data) : data;
    const method = entry.method ?? (deflate ? 8 : 0);
    const crc = zlib.crc32(data) >>> 0;
    const name = Buffer.from(entry.name, "utf8");
    const size = entry.size ?? data.length;
    const flags = (entry.flags ?? 0) | 0x800;
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(flags, 6);
    local.writeUInt16LE(method, 8);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(size, 22);
    local.writeUInt16LE(name.length, 26);
    locals.push(local, name, body);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE((3 << 8) | 20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(flags, 8);
    central.writeUInt16LE(method, 10);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(body.length, 20);
    central.writeUInt32LE(size, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt32LE(((entry.mode ?? (entry.name.endsWith("/") ? 0o040755 : 0o100644)) << 16) >>> 0, 38);
    central.writeUInt32LE(offset, 42);
    centrals.push(central, name);
    offset += 30 + name.length + body.length;
  }
  const directory = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  const zip = Buffer.concat([...locals, directory, end]);
  if (!signature) return zip;
  const length = Buffer.alloc(4);
  length.writeUInt32LE(signature.length, 0);
  return Buffer.concat([zip, Buffer.from("MCPB_SIG_V1"), length, signature, Buffer.from("MCPB_SIG_END")]);
}

/** A bundle's manifest as JSON text, with `extra` merged over a small node server's. */
export function manifestText(extra: Record<string, unknown> = {}): string {
  return JSON.stringify({
    manifest_version: "0.3",
    name: "notes-search",
    display_name: "Notes search",
    version: "1.2.0",
    description: "Searches a folder of notes",
    author: { name: "Example Tools" },
    server: { type: "node", entry_point: "server/index.js", mcp_config: { command: "node", args: ["${__dirname}/server/index.js", "--root", "${user_config.root}"], env: { NOTES_TOKEN: "${user_config.token}" } } },
    user_config: {
      root: { type: "directory", title: "Notes folder", description: "Where the notes are", required: true, default: "${HOME}/notes" },
      token: { type: "string", title: "Token", sensitive: true },
      limit: { type: "number", title: "Results", default: 10, min: 1, max: 50 },
    },
    ...extra,
  });
}
