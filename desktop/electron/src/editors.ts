// "Open in the chosen editor" (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.5, §2.6): the
// editors on this machine, found from their `.desktop` entries and on PATH, and the argument list
// that opens a file at a line in each. The page names an editor by id and a file inside a
// repository; the command and its arguments are this table's, never the page's. No Electron here.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { programOnPath } from "./platform";

export interface EditorKind {
  id: string;
  name: string;
  /** Executable names it is installed under. */
  bins: string[];
  /** The arguments that open `file` at `line`. */
  at: (file: string, line: number | null) => string[];
}

const colon = (file: string, line: number | null) => [line ? `${file}:${line}` : file];
const goto = (file: string, line: number | null) => (line ? ["--goto", `${file}:${line}`] : [file]);
const jetbrains = (file: string, line: number | null) => (line ? ["--line", String(line), file] : [file]);
const plus = (file: string, line: number | null) => (line ? [`+${line}`, file] : [file]);

export const KINDS: EditorKind[] = [
  { id: "code", name: "VS Code", bins: ["code"], at: goto },
  { id: "codium", name: "VSCodium", bins: ["codium", "vscodium"], at: goto },
  { id: "cursor", name: "Cursor", bins: ["cursor"], at: goto },
  { id: "zed", name: "Zed", bins: ["zed", "zeditor", "zedit"], at: colon },
  { id: "sublime", name: "Sublime Text", bins: ["subl", "sublime_text"], at: colon },
  { id: "pycharm", name: "PyCharm", bins: ["pycharm", "pycharm.sh", "pycharm-professional", "pycharm-community", "charm"], at: jetbrains },
  { id: "idea", name: "IntelliJ IDEA", bins: ["idea", "idea.sh", "intellij-idea-ultimate", "intellij-idea-community"], at: jetbrains },
  { id: "clion", name: "CLion", bins: ["clion", "clion.sh"], at: jetbrains },
  { id: "webstorm", name: "WebStorm", bins: ["webstorm", "webstorm.sh"], at: jetbrains },
  { id: "goland", name: "GoLand", bins: ["goland", "goland.sh"], at: jetbrains },
  { id: "rustrover", name: "RustRover", bins: ["rustrover", "rustrover.sh"], at: jetbrains },
  { id: "kate", name: "Kate", bins: ["kate"], at: jetbrains },
  { id: "gedit", name: "gedit", bins: ["gedit"], at: plus },
  { id: "gnome-text-editor", name: "Text Editor", bins: ["gnome-text-editor"], at: (file) => [file] },
];

export interface Editor {
  id: string;
  name: string;
  command: string;
}

/** The first word of a `.desktop` file's `Exec=` line: the program, without quotes or field codes. */
export function execProgram(exec: string): string | null {
  const match = exec.trim().match(/^"([^"]+)"|^(\S+)/);
  const program = match ? match[1] ?? match[2] : null;
  if (!program || program.startsWith("%")) return null;
  // `env VAR=x program …` and `flatpak run …` launchers: keep it simple, take the program after env's assignments.
  if (path.basename(program) === "env") {
    const rest = exec.trim().split(/\s+/).slice(1).filter((word) => !word.includes("="));
    return rest[0] ?? null;
  }
  return program;
}

/** Where `.desktop` entries live besides the user's own: the XDG data folders and snap's. */
export function systemDataDirs(): string[] {
  return [...(process.env.XDG_DATA_DIRS || "/usr/local/share:/usr/share").split(":").filter(Boolean), "/var/lib/snapd/desktop"];
}

/** A program by its absolute place on PATH, never a relative one: editors and terminals start in a repository (platform.ts). */
function onPath(bin: string): string | null {
  return programOnPath(bin, process.platform, process.env.PATH ?? "");
}

/** The editors installed here, each once, in the table's order. */
export function findEditors(home: string = os.homedir(), dataDirs: string[] = systemDataDirs()): Editor[] {
  const found = new Map<string, Editor>();
  const take = (kind: EditorKind, command: string) => {
    if (!found.has(kind.id)) found.set(kind.id, { id: kind.id, name: kind.name, command });
  };
  for (const dir of [path.join(home, ".local", "share", "applications"), ...dataDirs.map((data) => path.join(data, "applications"))]) {
    let names: string[];
    try {
      names = fs.readdirSync(dir).filter((name) => name.endsWith(".desktop"));
    } catch {
      continue;
    }
    for (const name of names) {
      let text: string;
      try {
        text = fs.readFileSync(path.join(dir, name), "utf8");
      } catch {
        continue;
      }
      const exec = text.match(/^Exec=(.*)$/m)?.[1];
      const program = exec ? execProgram(exec) : null;
      if (!program) continue;
      const kind = KINDS.find((entry) => entry.bins.includes(path.basename(program)));
      // A bare `Exec=code` is looked up as a bare name would be, by its absolute place on PATH.
      const command = path.isAbsolute(program) ? program : onPath(program);
      if (kind && command) take(kind, command);
    }
  }
  // JetBrains Toolbox's launcher scripts.
  const toolbox = path.join(home, ".local", "share", "JetBrains", "Toolbox", "scripts");
  for (const kind of KINDS) {
    for (const bin of kind.bins) {
      const script = path.join(toolbox, bin);
      if (fs.existsSync(script)) take(kind, script);
      const command = onPath(bin);
      if (command) take(kind, command);
    }
  }
  return KINDS.filter((kind) => found.has(kind.id)).map((kind) => found.get(kind.id) as Editor);
}

/** The command line that opens `file` at `line` in `editor`. */
export function openCommand(editor: Editor, file: string, line: number | null): { command: string; args: string[] } {
  const kind = KINDS.find((entry) => entry.id === editor.id);
  if (!kind) throw new Error(`no editor named ${editor.id}`);
  const at = line !== null && Number.isInteger(line) && line > 0 ? line : null;
  return { command: editor.command, args: kind.at(file, at) };
}

/**
 * The only file types the app hands to the system's own app (`shell.openPath`): documents, on
 * every platform. The same kinds `ling web` shows or downloads at `/docs/`
 * (ling-rs/web/src/documents.rs), plus JSON, logs and images. Left out on purpose: HTML and SVG
 * (script in a browser; `ling web` never serves HTML inline), RTF and the legacy and macro Office
 * formats, and everything a system runs, mounts or installs.
 */
export const DOCUMENT_TYPES: ReadonlySet<string> = new Set([
  ".txt", ".md", ".markdown", ".rst", ".org", ".tex", ".log", ".csv", ".tsv", ".json",
  ".pdf",
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".avif",
  ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
]);

/**
 * Why `target` is not opened with the system's app (null when it is). A path the page names comes
 * from an answer or a file the agent wrote, and the system's choice for a file runs, mounts or
 * installs far more types than anyone can list (a `.chm`, an `.iso`, a `.docm`, a `.dmg`, a
 * `.mobileconfig`, whatever an installed app registers), so only `DOCUMENT_TYPES` open: a regular
 * file, under its own name and behind any link, with no executable bit. A folder (`kind`
 * "folder") opens in the file manager unless a Mac would launch it as a bundle (`Tool.app`).
 */
export async function whyNotOpened(target: string, kind: "document" | "folder" = "document", platform: NodeJS.Platform = process.platform, pathext: string = process.env.PATHEXT ?? ""): Promise<string | null> {
  const real = await fs.promises.realpath(target).catch(() => null);
  const stat = real === null ? null : await fs.promises.stat(real).catch(() => null);
  const names = [target, real ?? target].map((candidate) => (platform === "win32" ? path.win32.basename(candidate) : path.basename(candidate)));
  if (!stat) return `${names[0]} is not there`;
  // Windows drops a name's trailing dots and spaces (`run.bat.` is `run.bat`).
  const ext = (candidate: string) => path.extname(platform === "win32" ? candidate.replace(/[. ]+$/, "") : candidate).toLowerCase();
  if (kind === "folder") {
    if (!stat.isDirectory()) return `${names[0]} is not a folder`;
    return platform === "darwin" && names.some((candidate) => ext(candidate) !== "") ? `${names[0]} is a bundle a Mac would launch, not a folder: shown in its folder instead` : null;
  }
  const why = `${names[0]} is not a document type the system opens from here (text, PDF, an image, an Office file without macros): shown in its folder instead; choose an editor in the review pane to open it`;
  if (!stat.isFile()) return why;
  // A stream (`a.txt:b`) is never a document; PATHEXT names what Windows runs, even an allowed type.
  if (platform === "win32" && names.some((candidate) => candidate.includes(":"))) return why;
  const runs = platform === "win32" ? new Set(pathext.split(";").map((entry) => entry.trim().toLowerCase())) : new Set<string>();
  if (!names.every((candidate) => DOCUMENT_TYPES.has(ext(candidate)) && !runs.has(ext(candidate)))) return why;
  // An executable is run (a Mac opens it in Terminal; a file manager may launch it).
  if (platform !== "win32" && (stat.mode & 0o111) !== 0) return why;
  return null;
}

/** The part of Electron's `shell` that opens or shows a path. */
export interface SystemShell {
  openPath(target: string): Promise<string>;
  showItemInFolder(target: string): void;
}

/**
 * Opens `target` with the system's app when `whyNotOpened` allows it; otherwise shows it in its
 * folder and throws why, so the page tells the user it was shown, not opened. A path that is not
 * there is neither.
 */
export async function openOrReveal(target: string, system: SystemShell, kind: "document" | "folder" = "document", platform: NodeJS.Platform = process.platform, pathext: string = process.env.PATHEXT ?? ""): Promise<void> {
  const why = await whyNotOpened(target, kind, platform, pathext);
  if (why === null) {
    const failed = await system.openPath(target);
    if (failed) throw new Error(failed);
    return;
  }
  if (await fs.promises.lstat(target).then(() => true, () => false)) system.showItemInFolder(target);
  throw new Error(why);
}

/** Terminal emulators and how each is told where to start; the first found is used. */
export const TERMINALS: { bin: string; args: (dir: string) => string[] }[] = [
  { bin: "ptyxis", args: (dir) => ["--new-window", "--working-directory", dir] },
  { bin: "gnome-terminal", args: (dir) => [`--working-directory=${dir}`] },
  { bin: "kgx", args: (dir) => [`--working-directory=${dir}`] },
  { bin: "konsole", args: (dir) => ["--workdir", dir] },
  { bin: "xfce4-terminal", args: (dir) => [`--working-directory=${dir}`] },
  { bin: "tilix", args: (dir) => ["-w", dir] },
  { bin: "alacritty", args: (dir) => ["--working-directory", dir] },
  { bin: "kitty", args: (dir) => ["--directory", dir] },
  { bin: "wezterm", args: (dir) => ["start", "--cwd", dir] },
  { bin: "foot", args: (dir) => ["-D", dir] },
  { bin: "x-terminal-emulator", args: () => [] },
  { bin: "xterm", args: () => [] },
];

/** The command that opens a terminal in `dir`, or null when none is installed. */
export function terminalCommand(dir: string): { command: string; args: string[] } | null {
  for (const terminal of TERMINALS) {
    const command = onPath(terminal.bin);
    if (command) return { command, args: terminal.args(dir) };
  }
  return null;
}
