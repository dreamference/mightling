// A repeating Night Shift task's cadence, checked before `ling-admin night every add` runs
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16 B42; dreamference/night_shift/night_shift_recurring.py).

export const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

/** The cadences a repeating task can have, as `ling-admin night every list` names them. */
export const CADENCES = ["daily", "weekly", "days", "every", "monthly"] as const;
export type Cadence = (typeof CADENCES)[number];

/** A cadence's parameter, as the window sends it: the weekly day, the chosen days, the nights between runs, or the day of the month. */
export interface CadenceOptions {
  weekday?: string | null;
  days?: string[] | string | null;
  every?: number | string | null;
  day?: number | string | null;
}

const WEEKDAY_SET = new Set<string>(WEEKDAYS);

/** The chosen weekdays (`"mon,wed,fri"` or a list of them), lower case, each once, in the order given. */
export function parseDays(value: unknown): string[] {
  const parts = typeof value === "string" ? value.split(",") : Array.isArray(value) && value.every((day) => typeof day === "string") ? (value as string[]) : null;
  const days = (parts ?? []).map((day) => day.trim().toLowerCase()).filter(Boolean);
  if (!days.length || days.some((day) => !WEEKDAY_SET.has(day))) throw new Error("choose the days: mon, tue, wed, thu, fri, sat, sun");
  return [...new Set(days)];
}

/** A whole number from `low` to `high`, given as a number or as its digits; anything else is refused. */
export function wholeNumber(value: unknown, low: number, high: number, what: string): number {
  const number = typeof value === "number" ? value : typeof value === "string" && /^\s*\d{1,4}\s*$/.test(value) ? Number(value) : NaN;
  if (!Number.isInteger(number) || number < low || number > high) throw new Error(`${what} is a whole number from ${low} to ${high}`);
  return number;
}

/**
 * The cadence flags of `ling-admin night every add` for a cadence the window sent, each value checked here.
 *
 * @param cadence `daily`, `weekly`, `days`, `every` or `monthly`.
 * @param options The cadence's parameter (`weekday`, `days`, `every` or `day`).
 * @returns The flags, e.g. `["--days", "mon,wed,fri"]`.
 */
export function cadenceArgs(cadence: unknown, options: unknown): string[] {
  const given = options && typeof options === "object" && !Array.isArray(options) ? (options as CadenceOptions) : {};
  switch (cadence) {
    case "daily":
      return ["--daily"];
    case "weekly":
      if (typeof given.weekday !== "string" || !WEEKDAY_SET.has(given.weekday)) throw new Error("a weekly task names its day: mon..sun");
      return ["--weekly", given.weekday];
    case "days":
      return ["--days", parseDays(given.days).join(",")];
    case "every":
      return ["--every", String(wholeNumber(given.every, 1, 365, "the nights between runs"))];
    case "monthly":
      return ["--monthly", String(wholeNumber(given.day, 1, 31, "the day of the month"))];
    default:
      throw new Error("repeat every night, every week, on chosen days, every N nights or on a day of the month");
  }
}
