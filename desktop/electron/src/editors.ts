// "Open in the chosen editor" (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.5, §2.6): the
// editors on this machine, found from their `.desktop` entries and on PATH, and the argument list
// that opens a file at a line in each. The page names an editor by id and a file inside a
// repository; the command and its arguments are this table's, never the page's. No Electron here.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

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

function onPath(bin: string): string | null {
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (!dir) continue;
    const candidate = path.join(dir, bin);
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      // Not here.
    }
  }
  return null;
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
      if (kind) take(kind, program);
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

/** What Windows runs when it is opened (ShellExecute), beside `PATHEXT`'s own list. */
const WINDOWS_RUNS = new Set([
  ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".msc", ".scr", ".pif",
  ".cpl", ".lnk", ".url", ".reg", ".hta", ".jar", ".inf", ".scf", ".appref-ms", ".application", ".appx", ".appxbundle", ".msix",
  ".msixbundle", ".appinstaller", ".settingcontent-ms", ".library-ms", ".search-ms", ".diagcab", ".gadget", ".xll",
]);
/** What a Mac launches, installs or runs in Terminal when it is opened. */
const MAC_RUNS = new Set([".app", ".command", ".tool", ".terminal", ".pkg", ".mpkg", ".workflow", ".action", ".prefpane", ".saver", ".fileloc", ".inetloc", ".webloc"]);
/** What a Linux desktop launches when it is opened. */
const LINUX_RUNS = new Set([".desktop", ".appimage"]);

/**
 * Why the system would run `file` rather than open it (null when it would open it): the system's
 * choice for a file is `shell.openPath`, which on Windows starts a `.bat` or an `.exe`, on a Mac a
 * `.app` or a `.command`, and on Linux a `.desktop` entry or an executable. A path the page names
 * comes from an answer or a file the agent wrote, so the user's "open" must never become "run".
 */
export async function runsWhenOpened(file: string, platform: NodeJS.Platform = process.platform, pathext: string = process.env.PATHEXT ?? ""): Promise<string | null> {
  const name = await fs.promises.realpath(file).catch(() => file);
  const stat = await fs.promises.stat(name).catch(() => null);
  const ext = (candidate: string) => path.extname(candidate).toLowerCase();
  const names = [file, name].map((candidate) => (platform === "win32" ? path.win32.basename(candidate) : path.basename(candidate)));
  const why = `${names[0]} would run, not open: choose an editor in the review pane to open it`;
  if (platform === "win32") {
    // Windows drops a name's trailing dots and spaces (`run.bat.` is `run.bat`); a stream (`a.txt:b`) is never a document.
    if (names.some((candidate) => candidate.includes(":"))) return why;
    const runs = new Set([...WINDOWS_RUNS, ...pathext.split(";").map((entry) => entry.trim().toLowerCase()).filter((entry) => entry.startsWith("."))]);
    return names.some((candidate) => runs.has(ext(candidate.replace(/[. ]+$/, "")))) ? why : null;
  }
  const runs = platform === "darwin" ? MAC_RUNS : LINUX_RUNS;
  if (names.some((candidate) => runs.has(ext(candidate)))) return why;
  // An executable is run (a Mac opens it in Terminal; a file manager may launch it); a folder opens in the file manager.
  if (stat?.isFile() && (stat.mode & 0o111) !== 0) return why;
  return null;
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
