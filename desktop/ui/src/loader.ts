// Threads are viewed with `thread/read` and resumed only when something needs the thread loaded: a
// message sent, a turn steered or stopped, a compaction, a review, the goal or the queue changed.
// `thread/resume` writes to the thread's rollout file, whose modification time is the `updatedAt`
// that `thread/list` reports (codex's thread_summary.rs), so merely opening a thread made the sidebar
// say it was active "now", and after a restart the time stayed wrong.

/** The requests that need their thread loaded in the server (resumed) before they are sent. */
export const NEEDS_LOADED: ReadonlySet<string> = new Set([
  "turn/start", "turn/steer", "turn/interrupt", "thread/compact/start", "review/start",
  "thread/goal/set", "thread/goal/clear", "thread/queue/add", "thread/queue/start", "thread/queue/delete",
  "thread/revert", "thread/memoryMode/set", "thread/settings/update",
]);

/** Which threads the server has loaded for this window, and the resume that loads one once. */
export class ThreadLoader {
  private readonly loaded = new Set<string>();
  private readonly loading = new Map<string, Promise<void>>();

  constructor(private readonly resume: (threadId: string) => Promise<unknown>) {}

  isLoaded(threadId: string): boolean {
    return this.loaded.has(threadId);
  }

  /** A thread this window started or resumed: loaded already. */
  markLoaded(threadId: string): void {
    this.loaded.add(threadId);
  }

  /** Resumes `threadId` unless it is loaded (one resume however many requests wait on it). */
  ensure(threadId: string): Promise<void> {
    if (this.loaded.has(threadId)) return Promise.resolve();
    let pending = this.loading.get(threadId);
    if (!pending) {
      pending = this.resume(threadId).then(
        () => {
          this.loaded.add(threadId);
          this.loading.delete(threadId);
        },
        (error: unknown) => {
          this.loading.delete(threadId);
          throw error;
        },
      );
      this.loading.set(threadId, pending);
    }
    return pending;
  }

  /** What a request needs first: its thread loaded, or nothing. */
  before(method: string, params: unknown): Promise<void> | undefined {
    const threadId = (params as { threadId?: unknown } | null)?.threadId;
    return NEEDS_LOADED.has(method) && typeof threadId === "string" && !this.loaded.has(threadId) ? this.ensure(threadId) : undefined;
  }

  /** An answer that loaded a thread: a start, a fork or a resume (however the request was made). */
  after(method: string, result: unknown): void {
    const threadId = (result as { thread?: { id?: unknown } } | null)?.thread?.id;
    if ((method === "thread/start" || method === "thread/fork" || method === "thread/resume") && typeof threadId === "string") this.loaded.add(threadId);
  }

  /** A new server has nothing loaded. */
  reset(): void {
    this.loaded.clear();
    this.loading.clear();
  }
}
