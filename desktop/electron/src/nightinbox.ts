// Night Shift's results in the inbox, and its archive (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16 B41).
// A finished run is unread until it is opened in Scheduled or marked read: the window keeps the
// marks in its preferences (`nightSeen`, the time of the last Mark all as read, and `nightRead`, the
// runs opened since), and the main process counts the unread ones for the tray from the same files
// and preferences, so the tray says so with every window closed. `ling-admin night archive`
// (dreamference/night_shift/night_shift_archive.py) moves old runs to `night/archive/`, in the
// queue's own layout, which Scheduled searches here. Files on this machine only. No Electron here.

import path from "node:path";

import { readReport, readReports, readTasks, type NightTask } from "./night";

/** Statuses of a task that has run, as Scheduled's inbox lists them (desktop/ui/src/ScheduledView.tsx `FINISHED`). */
export const FINISHED = ["done", "no-change", "stalled", "failed", "cancelled", "blocked"];

/** Whether a finished run is unread: finished after the last Mark all as read, and not opened since. Pure. */
export function isUnread(task: Pick<NightTask, "id" | "status" | "at">, seen: unknown, read: unknown): boolean {
  if (!FINISHED.includes(task.status)) return false;
  if (Array.isArray(read) && read.includes(task.id)) return false;
  return typeof seen !== "string" || !seen || (task.at ?? "") > seen;
}

/** How many finished runs are unread. Pure. */
export function unreadRuns(tasks: Pick<NightTask, "id" | "status" | "at">[], seen: unknown, read: unknown): number {
  return tasks.filter((task) => isUnread(task, seen, read)).length;
}

/** The unread count for the tray, from `night/` and the window's preferences. */
export function nightUnread(nightDir: string, prefs: Record<string, unknown>): number {
  return unreadRuns(readTasks(nightDir), prefs.nightSeen, prefs.nightRead);
}

/** The archive's folder under `night/` (night_shift_archive.py `ARCHIVE_DIR_NAME`). */
export const ARCHIVE = "archive";

/** The most runs and reports one search answers with. */
const FOUND_RUNS = 50;
const FOUND_REPORTS = 20;
const QUERY_LIMIT = 200;

export interface ArchiveFound {
  tasks: NightTask[];
  reports: { name: string; lines: string[] }[];
}

/**
 * Searches the archive (DESKTOP §16 B41): runs whose task, id, branch, folder or status hold the
 * words, and reports holding them with the lines that do, newest first; with no words, the newest
 * runs and reports. Case is ignored; every word must appear.
 */
export function searchArchive(nightDir: string, query: unknown): ArchiveFound {
  if (typeof query !== "string" || query.length > QUERY_LIMIT) throw new Error("search the archive with a few words");
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const holds = (text: string) => words.every((word) => text.toLowerCase().includes(word));
  const dir = path.join(nightDir, ARCHIVE);
  const tasks = readTasks(dir)
    .filter((task) => holds([task.task, task.id, task.branch ?? "", task.repo, task.status].join("\n")))
    .slice(0, FOUND_RUNS);
  const reports: ArchiveFound["reports"] = [];
  for (const { name } of readReports(dir)) {
    if (reports.length >= FOUND_REPORTS) break;
    let text: string;
    try {
      text = readReport(dir, name);
    } catch {
      continue;
    }
    if (!words.length) {
      reports.push({ name, lines: [] });
      continue;
    }
    if (!holds(`${name}\n${text}`)) continue;
    const lines = text.split("\n").filter((line) => words.some((word) => line.toLowerCase().includes(word))).slice(0, 3).map((line) => line.trim().slice(0, 240));
    reports.push({ name, lines });
  }
  return { tasks, reports };
}
