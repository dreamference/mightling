// The bottom panel's tasks (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.6): a local
// environment's setup script when a worktree is made, and its actions when their button is pressed,
// run in the user's own shell in the project's folder, their output streamed to the page. These are
// the project's own scripts, started by the user, as the Codex app runs them in its terminal; main.ts
// refuses them at `/airgapped on`, since nothing sandboxes them. A task is its own process group, so
// Stop ends whatever it started. No Electron here.

import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";

export interface TaskEvent {
  id: string;
  kind: "started" | "output" | "exit";
  label?: string;
  cwd?: string;
  data?: string;
  code?: number | null;
}

export class Tasks {
  private readonly running = new Map<string, ChildProcess>();
  private next = 1;

  constructor(private readonly emit: (event: TaskEvent) => void, private readonly shell: string = process.env.SHELL || "/bin/bash") {}

  /** Starts `script` in `cwd`; resolves with the task's id at once, its output arriving as events. */
  run(label: unknown, script: unknown, cwd: unknown): string {
    if (typeof script !== "string" || !script.trim()) throw new Error("a task needs a script");
    if (typeof cwd !== "string" || !path.isAbsolute(cwd)) throw new Error("a task runs in an absolute folder");
    if (this.running.size >= 8) throw new Error("eight tasks are running already");
    const id = `task-${this.next++}`;
    const name = typeof label === "string" && label.trim() ? label.trim().slice(0, 80) : "Task";
    const child = spawn(this.shell, ["-l", "-c", script], { cwd, env: { ...process.env, TERM: "dumb", NO_COLOR: "1" }, detached: true, stdio: ["ignore", "pipe", "pipe"] });
    this.running.set(id, child);
    this.emit({ id, kind: "started", label: name, cwd });
    const out = (chunk: Buffer) => this.emit({ id, kind: "output", data: chunk.toString("utf8") });
    child.stdout?.on("data", out);
    child.stderr?.on("data", out);
    child.on("error", (error) => {
      this.emit({ id, kind: "output", data: `${error.message}\n` });
    });
    child.on("close", (code) => {
      this.running.delete(id);
      this.emit({ id, kind: "exit", code });
    });
    return id;
  }

  /** Ends a task and everything it started. */
  stop(id: unknown): void {
    const child = typeof id === "string" ? this.running.get(id) : undefined;
    if (!child?.pid) return;
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch {
      child.kill("SIGTERM");
    }
  }

  stopAll(): void {
    for (const id of [...this.running.keys()]) this.stop(id);
  }

  get count(): number {
    return this.running.size;
  }
}
