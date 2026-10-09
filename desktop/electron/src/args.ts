// How `ling app` asked for the window: `--work`, `--cwd <folder>` and `--thread <id>` open it on
// Work (the launcher passes them); nothing opens it on Ask, as before. A `mightling://` link (the
// scheme the app registers) opens Work too: `mightling://thread/<id>` or `mightling://work?cwd=<folder>`.

import app from "../app.json";

export interface WorkTarget {
  cwd: string | null;
  thread: string | null;
}

/** The Work target in `args`, or `null` when they open Ask. Chromium's own switches are ignored. */
export function workTarget(args: readonly string[]): WorkTarget | null {
  const valueOf = (flag: string): string | null => {
    const at = args.indexOf(flag);
    return at >= 0 && at + 1 < args.length ? args[at + 1] : null;
  };
  const link = args.find((arg) => arg.startsWith(`${app.scheme}://`));
  if (link) return linkTarget(link);
  const target = { cwd: valueOf("--cwd"), thread: valueOf("--thread") };
  return args.includes("--work") || target.cwd !== null || target.thread !== null ? target : null;
}

/** `mightling://thread/<id>` and `mightling://work?cwd=<folder>`; anything else opens Work plainly. */
export function linkTarget(link: string): WorkTarget {
  try {
    const url = new URL(link);
    if (url.hostname === "thread") {
      const id = decodeURIComponent(url.pathname.replace(/^\/+/, ""));
      return { cwd: null, thread: /^[A-Za-z0-9-]+$/.test(id) ? id : null };
    }
    return { cwd: url.searchParams.get("cwd"), thread: null };
  } catch {
    return { cwd: null, thread: null };
  }
}

/** Whether this process was asked to run the audit's session (`MIGHTLING_APP_AUDIT=<seconds>`): hidden windows, then quit. */
export function auditSeconds(env: NodeJS.ProcessEnv = process.env): number | null {
  const value = Number(env.MIGHTLING_APP_AUDIT);
  return Number.isFinite(value) && value > 0 ? value : null;
}
