// A repeating Night Shift task's cadence in Scheduled (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16 B42):
// every night, every week on one day, on chosen weekdays, every N nights, or on a day of the month.

import type { RepeatingTask } from "./bridge";

/** The weekdays, as `ling-admin night every` names them, and their names. */
export const WEEKDAYS = [["mon", "Monday"], ["tue", "Tuesday"], ["wed", "Wednesday"], ["thu", "Thursday"], ["fri", "Friday"], ["sat", "Saturday"], ["sun", "Sunday"]] as const;

export type Cadence = RepeatingTask["cadence"];

const dayName = (day: string | null) => WEEKDAYS.find(([value]) => value === day)?.[1] ?? day ?? "";

/** The chosen days in the week's order, each once: what a day's checkbox in Repeat toggles. */
export function toggleDay(days: string[], day: string): string[] {
  const chosen = days.includes(day) ? days.filter((other) => other !== day) : [...days, day];
  return WEEKDAYS.map(([value]) => value).filter((value) => chosen.includes(value));
}

/** A repeating task's cadence in words, for Scheduled's Repeating list. */
export function cadenceWords(entry: Pick<RepeatingTask, "cadence" | "weekday" | "days" | "every" | "day">): string {
  switch (entry.cadence) {
    case "weekly":
      return `every ${dayName(entry.weekday)}`;
    case "days":
      return `every ${(entry.days ?? []).map(dayName).join(", ")}`;
    case "every":
      return entry.every === 1 ? "every night" : `every ${entry.every} nights`;
    case "monthly":
      return `on day ${entry.day} of each month`;
    default:
      return "every night";
  }
}
