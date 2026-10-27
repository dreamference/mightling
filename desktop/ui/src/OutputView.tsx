// Full-screen read-only command output view (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §10 G8).
// Reads a single `commandExecution` item's command line and `aggregatedOutput` from the store by thread and item id
// so streaming output updates live, with case-insensitive search (highlighting matches and jumping next/previous),
// clipboard copy, and Close (also on Escape). Monospace, wraps long lines, and never writes to stdin.

import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import type { ThreadItem } from "./protocol/v2/ThreadItem";

export interface OutputMatch {
  start: number;
  end: number;
}

export type CommandExecutionItem = Extract<ThreadItem, { type: "commandExecution" }>;

export interface ThreadTurnsEntry {
  turns: readonly { items: readonly ThreadItem[] }[];
}

export interface ClipboardWriter {
  writeText: (text: string) => Promise<void>;
}

/**
 * Find all non-overlapping case-insensitive occurrences of `query` in `text`.
 * An empty query (`query.length === 0`) or empty text returns no matches.
 */
export function findMatches(text: string, query: string): OutputMatch[] {
  if (!text || query.length === 0) return [];
  const haystack = text.toLowerCase();
  const needle = query.toLowerCase();
  const step = needle.length;
  if (step === 0) return [];
  const matches: OutputMatch[] = [];
  let from = 0;
  while (from <= haystack.length - step) {
    const idx = haystack.indexOf(needle, from);
    if (idx === -1) break;
    matches.push({ start: idx, end: idx + step });
    from = idx + step;
  }
  return matches;
}

/** Step the active match index forward (`+1`) or backward (`-1`), wrapping at the ends. */
export function stepMatchIndex(current: number, total: number, delta: 1 | -1): number {
  if (total <= 0) return 0;
  return (((current + delta) % total) + total) % total;
}

/** Format the search counter shown beside the search box (for example `"3 of 17"`). */
export function formatMatchCount(activeIndex: number, total: number, query: string): string {
  if (query.length === 0) return "";
  if (total <= 0) return "0 of 0";
  const clamped = Math.min(Math.max(0, activeIndex), total - 1);
  return `${clamped + 1} of ${total}`;
}

/** Read a `commandExecution` item live from the store by `threadId` and `itemId`. */
export function findCommandItem(
  threads: Record<string, ThreadTurnsEntry>,
  threadId: string,
  itemId: string,
): CommandExecutionItem | null {
  const thread = threads[threadId];
  if (!thread) return null;
  for (const turn of thread.turns) {
    for (const item of turn.items) {
      if (item.type === "commandExecution" && item.id === itemId) {
        return item;
      }
    }
  }
  return null;
}

/** Copy output text with `navigator.clipboard.writeText`, returning `"Copied"` or the failure message. */
export async function copyOutputText(
  text: string,
  clipboard: ClipboardWriter | null | undefined = typeof navigator !== "undefined" ? navigator.clipboard : undefined,
): Promise<string> {
  if (!clipboard || typeof clipboard.writeText !== "function") {
    return "Copy failed: clipboard unavailable";
  }
  try {
    await clipboard.writeText(text);
    return "Copied";
  } catch (error) {
    const detail = error instanceof Error && error.message ? error.message : String(error);
    return detail ? `Copy failed: ${detail}` : "Copy failed";
  }
}

export interface OutputViewProps {
  threads: Record<string, ThreadTurnsEntry>;
  threadId: string;
  itemId: string;
  onClose: () => void;
  clipboard?: ClipboardWriter | null;
  initialQuery?: string;
  initialActiveMatch?: number;
  initialCopyStatus?: string | null;
}

export function OutputView({
  threads,
  threadId,
  itemId,
  onClose,
  clipboard,
  initialQuery = "",
  initialActiveMatch = 0,
  initialCopyStatus = null,
}: OutputViewProps) {
  const item = findCommandItem(threads, threadId, itemId);
  const command = item?.command ?? "";
  const output = item?.aggregatedOutput ?? "";
  const status = item?.status ?? "completed";

  const [query, setQuery] = useState(initialQuery);
  const [activeMatch, setActiveMatch] = useState(initialActiveMatch);
  const [copyStatus, setCopyStatus] = useState<string | null>(initialCopyStatus);
  const logRef = useRef<HTMLPreElement | null>(null);

  const matches = useMemo(() => findMatches(output, query), [output, query]);
  const currentIndex = matches.length === 0 ? 0 : Math.min(Math.max(0, activeMatch), matches.length - 1);
  const countLabel = formatMatchCount(currentIndex, matches.length, query);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    const container = logRef.current;
    if (!container || matches.length === 0) return;
    const target = container.querySelector(`[data-match-index="${currentIndex}"]`) as {
      scrollIntoView?: (options?: ScrollIntoViewOptions) => void;
    } | null;
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ block: "nearest" });
    }
  }, [currentIndex, matches.length, query]);

  const jump = (delta: 1 | -1) => {
    if (matches.length === 0) return;
    setActiveMatch((prev) => stepMatchIndex(Math.min(prev, matches.length - 1), matches.length, delta));
  };

  const handleCopy = () => {
    void copyOutputText(output, clipboard).then((result) => {
      setCopyStatus(result);
    });
  };

  const renderedOutput = useMemo((): ReactNode => {
    if (!output) return "(no output)";
    if (matches.length === 0) return output;
    const pieces: ReactNode[] = [];
    let cursor = 0;
    for (let i = 0; i < matches.length; i++) {
      const match = matches[i];
      if (match.start > cursor) {
        pieces.push(output.slice(cursor, match.start));
      }
      pieces.push(
        <mark
          key={`${match.start}-${match.end}-${i}`}
          data-match-index={i}
          className={i === currentIndex ? "output-match current" : "output-match"}
        >
          {output.slice(match.start, match.end)}
        </mark>,
      );
      cursor = match.end;
    }
    if (cursor < output.length) {
      pieces.push(output.slice(cursor));
    }
    return pieces;
  }, [output, matches, currentIndex]);

  return (
    <div
      className="output-view"
      role="dialog"
      aria-modal="true"
      aria-label="Command output"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <header className="output-view-bar">
        <div className="output-view-command" title={command}>
          <span className={`pill ${status}`}>{status}</span>
          <code>{command}</code>
        </div>
        <div className="output-view-controls">
          <div className="output-view-search" role="search">
            <input
              type="search"
              aria-label="Search command output"
              placeholder="Search output…"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActiveMatch(0);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  jump(event.shiftKey ? -1 : 1);
                }
              }}
            />
            {query.length > 0 && (
              <span className="output-view-count" aria-live="polite">
                {countLabel}
              </span>
            )}
            <button
              type="button"
              className="ghost"
              onClick={() => jump(-1)}
              disabled={matches.length === 0}
              aria-label="Previous match"
              title="Previous match (Shift+Enter)"
            >
              Previous
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => jump(1)}
              disabled={matches.length === 0}
              aria-label="Next match"
              title="Next match (Enter)"
            >
              Next
            </button>
          </div>
          <button type="button" className="ghost output-view-copy" onClick={handleCopy}>
            Copy
          </button>
          {copyStatus && (
            <span className="output-view-copy-status" role="status" aria-live="polite">
              {copyStatus}
            </span>
          )}
          <button type="button" className="ghost output-view-close" onClick={onClose}>
            Close
          </button>
        </div>
      </header>
      <pre ref={logRef} className="output-view-log">
        {renderedOutput}
      </pre>
    </div>
  );
}
