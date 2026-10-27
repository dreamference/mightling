// The "Waiting for you" approval strip at the top of the Work view (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §6.1, §10 G7).
// Shown when `approvalRequested` is set from a deep link or (in the web host) when any server request in `state.requests`
// is waiting; names how many approvals wait and offers one button per waiting thread that opens the thread and scrolls its
// approval card into view.

import type { Host } from "./bridge";
import type { Thread } from "./protocol/v2/Thread";
import type { PendingRequest, ThreadView } from "./store";

export interface ApprovalStripRow {
  threadId: string;
  label: string;
  count: number;
}

export interface ApprovalStripOptions {
  approvalRequested?: boolean;
  selectedThreadId?: string | null;
}

export type ThreadSummaryEntry =
  | Pick<Thread, "id" | "name" | "preview">
  | Pick<ThreadView, "thread">;

function unwrapThreadSummary(entry: ThreadSummaryEntry | undefined): Pick<Thread, "id" | "name" | "preview"> | null {
  if (!entry) return null;
  return "thread" in entry ? entry.thread : entry;
}

/**
 * Compute one row per waiting thread from pending server requests and known threads, in request order.
 * When `approvalRequested` is true for `selectedThreadId` before its request arrives in `requests`,
 * include a row for `selectedThreadId` so the user can jump to it immediately.
 */
export function approvalStripRows(
  requests: readonly Pick<PendingRequest, "threadId">[],
  threads: Record<string, ThreadSummaryEntry>,
  options?: ApprovalStripOptions,
): ApprovalStripRow[] {
  const byThread = new Map<string, ApprovalStripRow>();
  for (const req of requests) {
    const threadId = req.threadId ?? options?.selectedThreadId ?? null;
    if (!threadId) continue;
    const existing = byThread.get(threadId);
    if (existing) {
      existing.count += 1;
    } else {
      const thread = unwrapThreadSummary(threads[threadId]);
      const label = (thread?.name ?? "").trim() || (thread?.preview ?? "").trim() || threadId;
      byThread.set(threadId, { threadId, label, count: 1 });
    }
  }
  if (options?.approvalRequested && options.selectedThreadId && !byThread.has(options.selectedThreadId)) {
    const threadId = options.selectedThreadId;
    const thread = unwrapThreadSummary(threads[threadId]);
    const label = (thread?.name ?? "").trim() || (thread?.preview ?? "").trim() || threadId;
    byThread.set(threadId, { threadId, label, count: 1 });
  }
  return Array.from(byThread.values());
}

/** Scroll the active thread's approval card into view. No button takes the focus: a stray Enter must not approve. */
export function scrollApprovalIntoView(root?: { querySelector?: (selector: string) => unknown } | null): boolean {
  const scope = root ?? (typeof document !== "undefined" ? document : null);
  if (!scope || typeof scope.querySelector !== "function") return false;
  const card = scope.querySelector(".approval") as {
    scrollIntoView?: (arg?: boolean | ScrollIntoViewOptions) => void;
  } | null;
  if (!card) return false;
  if (typeof card.scrollIntoView === "function") {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  return true;
}

export interface ApprovalStripProps {
  host: Host;
  approvalRequested: boolean;
  requests: readonly Pick<PendingRequest, "threadId">[];
  threads: Record<string, ThreadSummaryEntry>;
  selectedThreadId?: string | null;
  openThread: (threadId: string) => unknown;
  doc?: { querySelector?: (selector: string) => unknown } | null;
}

export function ApprovalStrip({
  host,
  approvalRequested,
  requests,
  threads,
  selectedThreadId = null,
  openThread,
  doc,
}: ApprovalStripProps) {
  const shouldShow = approvalRequested || (host === "web" && requests.length > 0);
  if (!shouldShow) return null;

  const rows = approvalStripRows(requests, threads, { approvalRequested, selectedThreadId });
  const totalWaiting = requests.length > 0 ? requests.length : rows.reduce((sum, row) => sum + row.count, 0) || (approvalRequested ? 1 : 0);
  if (totalWaiting === 0) return null;

  const waitingText = totalWaiting === 1 ? "1 approval waiting" : `${totalWaiting} approvals waiting`;

  const handleSelectThread = (threadId: string) => {
    const result = openThread(threadId);
    scrollApprovalIntoView(doc);
    if (result && typeof (result as PromiseLike<unknown>).then === "function") {
      void (result as PromiseLike<unknown>).then(
        () => {
          scrollApprovalIntoView(doc);
        },
        () => {},
      );
    }
  };

  return (
    <div className="approval-strip" role="status" aria-live="polite">
      <div className="approval-strip-summary">
        <strong className="approval-strip-title">Waiting for you</strong>
        <span className="approval-strip-count">{waitingText}</span>
      </div>
      {rows.length > 0 && (
        <div className="approval-strip-threads">
          {rows.map((row) => (
            <button
              key={row.threadId}
              type="button"
              className="approval-strip-thread"
              onClick={() => handleSelectThread(row.threadId)}
              title={`Open ${row.label} and jump to its approval card`}
            >
              <span className="approval-strip-thread-label">{row.label}</span>
              {row.count > 1 && <span className="approval-strip-thread-badge">{row.count}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
