// The window's own tools (the owner's decision 5 of DESKTOP_PARITY §9.5, 2026-10-21): the agent may
// read, whenever it wants, the output of the thread's terminal tabs and the console of the preview
// tabs. A new Work thread names them in `thread/start`'s `windowTools`; the policy (policy.json)
// writes their definitions as `dynamicTools`, and the server asks the window with `item/tool/call`.
// The answer is bounded and obvious secrets are masked; nothing leaves the machine, so they work at
// `/airgapped on` too. Pure.

export const TERMINAL_TOOL = "mightling_terminal_output";
export const CONSOLE_TOOL = "mightling_preview_console";

/** Lines of each tab by default and at most, and the characters of a whole answer at most. */
export const DEFAULT_LINES = 120;
export const MAX_LINES = 400;
export const MAX_ANSWER = 48 * 1024;

/** The tools a new thread gets: the console only where there is a preview tab (the desktop app). */
export function windowToolNames(desktop: boolean): string[] {
  return desktop ? [TERMINAL_TOOL, CONSOLE_TOOL] : [TERMINAL_TOOL];
}

const MASK = "[masked]";

/** Things that look like secrets, masked: API keys and tokens of the common shapes, bearer tokens, JWTs, private keys, and the values of `password=`/`token:`-like settings. */
export function maskSecrets(text: string): string {
  return text
    .replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)/g, `${MASK} (a private key)`)
    .replace(/\b(sk|pk|rk)-(?:live-|test-|proj-)?[A-Za-z0-9_-]{16,}/g, MASK)
    .replace(/\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}/g, MASK)
    .replace(/\bglpat-[A-Za-z0-9_-]{16,}/g, MASK)
    .replace(/\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g, MASK)
    .replace(/\bxox[abprs]-[A-Za-z0-9-]{10,}/g, MASK)
    .replace(/\bhf_[A-Za-z0-9]{20,}/g, MASK)
    .replace(/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g, MASK)
    .replace(/\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{12,}/gi, (_, kind: string) => `${kind} ${MASK}`)
    .replace(/\b([A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|auth)[A-Za-z0-9_.-]*)(\s*[:=]\s*)("[^"\n]*"|'[^'\n]*'|[^\s"',;]+)/gi, (_, key: string, sep: string) => `${key}${sep}${MASK}`);
}

/** How many lines a call asks for, within the bounds. */
export function linesAsked(args: unknown): number {
  const asked = typeof args === "object" && args !== null && "lines" in args ? Number((args as { lines: unknown }).lines) : DEFAULT_LINES;
  return Number.isFinite(asked) ? Math.max(1, Math.min(MAX_LINES, Math.floor(asked))) : DEFAULT_LINES;
}

/** The last `count` lines of a text. */
export function lastLines(text: string, count: number): string {
  return text.split("\n").slice(-count).join("\n").trim();
}

/** An answer cut to the limit, keeping its end (the newest output), and masked. */
export function bounded(text: string): string {
  const masked = maskSecrets(text);
  return masked.length > MAX_ANSWER ? `[earlier output cut]\n${masked.slice(masked.length - MAX_ANSWER)}` : masked;
}

export interface TerminalTab {
  title: string;
  cwd: string;
  running: boolean;
  output: string;
}

/** The terminal tool's answer: each of the thread's tabs, its folder and its last lines. */
export function terminalAnswer(tabs: TerminalTab[], lines: number): string {
  if (tabs.length === 0) return "No terminal tab is open for this thread in the Mightling app.";
  return bounded(tabs.map((tab) => `## Terminal "${tab.title}" in ${tab.cwd} (${tab.running ? "running" : "ended"})\n${lastLines(tab.output, lines) || "(no output yet)"}`).join("\n\n"));
}

export interface ConsoleTab {
  url: string;
  lines: string[];
}

/** The console tool's answer: each preview tab, its address and its last messages. */
export function consoleAnswer(tabs: ConsoleTab[], lines: number): string {
  if (tabs.length === 0) return "No preview tab is open in the Mightling app.";
  return bounded(tabs.map((tab) => `## Preview of ${tab.url || "(no page yet)"}\n${tab.lines.slice(-lines).join("\n") || "(the console is empty)"}`).join("\n\n"));
}

/** A window tool's call, in words, for the thread's transcript. */
export function toolLabel(tool: string): string | null {
  if (tool === TERMINAL_TOOL) return "Read the terminal";
  if (tool === CONSOLE_TOOL) return "Read the preview's console";
  return null;
}
