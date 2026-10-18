// The tab area beside the thread (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1, §2.8): the
// review tab and file tabs, one list per window, opened, focused and closed here so the rules
// (one tab per file, the neighbour takes the focus, a closed tab reopens where it was) are tested
// apart from the page.

export type WorkspaceTab =
  | { key: "review"; kind: "review" }
  /** A file read through `fs/readFile`; `line` is where it was asked to open (go to line), 1-based. */
  | { key: string; kind: "file"; path: string; line: number | null }
  /** A page in the preview tab (Phase 3 item 4); `url` is where it was opened, its title once known. */
  | { key: string; kind: "preview"; id: string; url: string; title: string | null };

export const REVIEW_TAB: WorkspaceTab = { key: "review", kind: "review" };

export const fileTabKey = (path: string) => `file:${path}`;

export function fileTab(path: string, line: number | null = null): WorkspaceTab {
  return { key: fileTabKey(path), kind: "file", path, line };
}

/** The tabs with `tab` in them: a tab already open (the same file) is updated in place, a new one goes last. */
export function withTab(tabs: WorkspaceTab[], tab: WorkspaceTab): WorkspaceTab[] {
  const at = tabs.findIndex((entry) => entry.key === tab.key);
  if (at < 0) return [...tabs, tab];
  return tabs.map((entry, index) => (index === at ? tab : entry));
}

/** The tabs without `key`, and the tab that has the focus afterwards (its right neighbour, else its left). */
export function withoutTab(tabs: WorkspaceTab[], key: string, active: string | null): { tabs: WorkspaceTab[]; active: string | null } {
  const at = tabs.findIndex((entry) => entry.key === key);
  if (at < 0) return { tabs, active };
  const rest = tabs.filter((entry) => entry.key !== key);
  if (active !== key) return { tabs: rest, active };
  const next = rest[at] ?? rest[at - 1] ?? null;
  return { tabs: rest, active: next ? next.key : null };
}

/** The tab next to the active one, for Ctrl+Tab in the tab area; wraps around. */
export function stepTab(tabs: WorkspaceTab[], active: string | null, step: 1 | -1): string | null {
  if (tabs.length === 0) return null;
  const at = tabs.findIndex((entry) => entry.key === active);
  return tabs[(Math.max(at, 0) + step + tabs.length) % tabs.length].key;
}

let nextPreview = 1;

export function previewTab(url: string): WorkspaceTab {
  const id = `p${Date.now().toString(36)}${nextPreview++}`;
  return { key: `preview:${id}`, kind: "preview", id, url, title: null };
}

/** What a tab is called in the strip: the file's name, the page's title (or host), or Review. */
export function tabLabel(tab: WorkspaceTab): string {
  if (tab.kind === "review") return "Review";
  if (tab.kind === "preview") {
    if (tab.title) return tab.title;
    try {
      return tab.url ? new URL(tab.url).host || "Preview" : "New preview";
    } catch {
      return "Preview";
    }
  }
  return baseName(tab.path);
}

export function baseName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

/** How a closed tab is remembered for Ctrl+Shift+T (shell.ts `ClosedSurfaces`). */
export function surfaceOf(tab: WorkspaceTab): "review" | `file:${string}` | `preview:${string}` {
  if (tab.kind === "review") return "review";
  if (tab.kind === "file") return `file:${tab.path}`;
  return `preview:${tab.url}`;
}
