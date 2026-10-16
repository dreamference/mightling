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
