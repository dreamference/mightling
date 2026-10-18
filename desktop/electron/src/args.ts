// How `ling app` asked for the window: `--work`, `--cwd <folder>` and `--thread <id>` open it on
// Work (the launcher passes them); nothing opens it on Ask, as before. A `ling://` link
// (DESKTOP_PARITY §2.20; the app registers `ling` and, for links made before, `mightling`) opens
// what it names: `ling://threads/<id>`, `ling://threads/new`, `ling://new?prompt=…&path=…`,
// `ling://settings`, `ling://skills`, `ling://night`, `ling://plugins/<id>`; and the older
// `mightling://thread/<id>` and `mightling://work?cwd=<folder>`.

import app from "../app.json";

export interface WorkTarget {
  cwd: string | null;
  thread: string | null;
  /** Text for the composer of a new thread (`ling://new?prompt=…`). */
  prompt?: string | null;
  /** A page of the window a link names (`ling://settings`, `ling://skills`, …), or `new` for a new thread. */
  page?: "new" | "settings" | "skills" | "night" | "plugins" | "pets" | "ask";
  /** The plugin `ling://plugins/<id>` names. */
  plugin?: string;
}

const SCHEMES = [app.scheme, ...app.legacySchemes];

/** The Work target in `args`, or `null` when they open Ask. Chromium's own switches are ignored. */
export function workTarget(args: readonly string[]): WorkTarget | null {
  const valueOf = (flag: string): string | null => {
    const at = args.indexOf(flag);
    return at >= 0 && at + 1 < args.length ? args[at + 1] : null;
  };
  const link = args.find((arg) => SCHEMES.some((scheme) => arg.startsWith(`${scheme}://`)));
  if (link) return linkTarget(link);
  const target = { cwd: valueOf("--cwd"), thread: valueOf("--thread") };
  return args.includes("--work") || target.cwd !== null || target.thread !== null ? target : null;
}

const threadId = (value: string) => (/^[A-Za-z0-9-]+$/.test(value) ? value : null);

/** A link's target; anything it does not name opens Work plainly. */
export function linkTarget(link: string): WorkTarget {
  try {
    const url = new URL(link);
    const rest = decodeURIComponent(url.pathname.replace(/^\/+/, ""));
    switch (url.hostname) {
      case "thread":
      case "threads":
        if (rest === "new") return { cwd: null, thread: null, page: "new" };
        return { cwd: null, thread: threadId(rest) };
      case "new":
        return { cwd: url.searchParams.get("path"), thread: null, prompt: url.searchParams.get("prompt"), page: "new" };
      case "settings":
      case "skills":
      case "night":
        return { cwd: null, thread: null, page: url.hostname };
      // `ling://pets` and the vendor's `pets/install` shape: Settings › Pets (a pet is not installed from a link).
      case "pets":
        return { cwd: null, thread: null, page: "pets" };
      case "plugins":
        return { cwd: null, thread: null, page: "plugins", ...(rest ? { plugin: rest.slice(0, 200) } : {}) };
      default:
        return { cwd: url.searchParams.get("cwd"), thread: null };
    }
  } catch {
    return { cwd: null, thread: null };
  }
}

/**
 * The page the audit's session opens in a preview tab (`MIGHTLING_APP_AUDIT_PREVIEW`, DESKTOP_PARITY
 * §5): an http URL on loopback only, the audit's own page, which reaches for the internet so the
 * trace shows the preview's rules hold. Null otherwise.
 */
export function auditPreview(env: NodeJS.ProcessEnv = process.env): string | null {
  const value = env.MIGHTLING_APP_AUDIT_PREVIEW;
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" && (url.hostname === "127.0.0.1" || url.hostname === "localhost") ? url.toString() : null;
  } catch {
    return null;
  }
}

/** Whether this process was asked to run the audit's session (`MIGHTLING_APP_AUDIT=<seconds>`): hidden windows, then quit. */
export function auditSeconds(env: NodeJS.ProcessEnv = process.env): number | null {
  const value = Number(env.MIGHTLING_APP_AUDIT);
  return Number.isFinite(value) && value > 0 ? value : null;
}
