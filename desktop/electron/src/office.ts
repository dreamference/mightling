// Office files previewed (DESKTOP §16 B31, DESKTOP_PARITY §9.5.9): a `.docx`, `.xlsx`, `.pptx`,
// `.odt` (and their kin) converted to a PDF by a LibreOffice installed on this machine, which the
// window then shows as it shows any PDF. LibreOffice runs headless, in a network namespace of its
// own (bubblewrap's `--unshare-net`: nothing it does reaches the network, at any `/airgapped`
// level), with the root read-only but for a /tmp of its own and a scratch folder, a profile of its own there, and a time
// limit. Without LibreOffice, or without bubblewrap (macOS, Windows), the tab says so instead.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/** The files a preview converts. */
export const OFFICE_EXTENSIONS: ReadonlySet<string> = new Set(["docx", "doc", "xlsx", "xls", "pptx", "ppt", "odt", "ods", "odp", "rtf"]);
/** The longest a conversion may take. */
export const OFFICE_TIMEOUT_MS = 60_000;
/** The largest file converted. */
export const OFFICE_MAX_BYTES = 50 * 1024 * 1024;
/** The largest PDF passed back to the page. */
export const OFFICE_MAX_PDF = 80 * 1024 * 1024;

export type OfficePreview = { pdf: string } | { unavailable: string };

function executable(file: string): boolean {
  try {
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}

/** The first of `names` on PATH, or null. */
export function onPath(names: string[], env: NodeJS.ProcessEnv = process.env): string | null {
  for (const dir of (env.PATH ?? "").split(path.delimiter).filter(Boolean)) {
    for (const name of names) if (executable(path.join(dir, name))) return path.join(dir, name);
  }
  return null;
}

/** LibreOffice's command: `MIGHTLING_SOFFICE` (tests), else `soffice` or `libreoffice` on PATH. */
export function findOffice(env: NodeJS.ProcessEnv = process.env): string | null {
  if (env.MIGHTLING_SOFFICE) return executable(env.MIGHTLING_SOFFICE) ? env.MIGHTLING_SOFFICE : null;
  return onPath(["soffice", "libreoffice"], env);
}

/** Whether `file` is one a preview converts, by its extension. */
export function isOfficeFile(file: string): boolean {
  const name = path.basename(file);
  const dot = name.lastIndexOf(".");
  return dot > 0 && OFFICE_EXTENSIONS.has(name.slice(dot + 1).toLowerCase());
}

/** The sandboxed conversion's command line: bubblewrap around LibreOffice, the network unshared. */
export function conversionCommand(bwrap: string, soffice: string, file: string, scratch: string): { command: string; args: string[] } {
  const profile = path.join(scratch, "profile");
  const tmp = path.join(scratch, "tmp");
  return {
    command: bwrap,
    args: [
      // LibreOffice writes under /tmp whatever TMPDIR says: a tmpfs of its own, then the scratch folder, the file and the command.
      "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
      "--bind", scratch, scratch, "--ro-bind", file, file, "--ro-bind", soffice, soffice,
      "--unshare-net", "--unshare-ipc", "--unshare-pid", "--die-with-parent", "--new-session",
      "--setenv", "TMPDIR", tmp, "--setenv", "HOME", scratch,
      soffice, `-env:UserInstallation=file://${profile}`, "--headless", "--norestore", "--nolockcheck",
      "--convert-to", "pdf", "--outdir", path.join(scratch, "out"), file,
    ],
  };
}

/** Converts `file` to a PDF (base64), or says why there is none. */
export async function officePreview(file: unknown, env: NodeJS.ProcessEnv = process.env, platform: NodeJS.Platform = process.platform): Promise<OfficePreview> {
  if (typeof file !== "string" || !path.isAbsolute(file) || !isOfficeFile(file)) throw new Error("not an office file");
  const stat = await fs.promises.stat(file).catch(() => null);
  if (!stat?.isFile()) throw new Error("no such file");
  if (stat.size > OFFICE_MAX_BYTES) return { unavailable: "This file is too large to preview here; open it in an office suite." };
  const soffice = findOffice(env);
  if (!soffice) return { unavailable: "Previewing office files needs LibreOffice on this machine (the libreoffice package); it is not installed." };
  const bwrap = platform === "linux" ? onPath(["bwrap"], env) : null;
  if (!bwrap) return { unavailable: "Previewing office files runs LibreOffice with no network, which needs bubblewrap on Linux; open the file in an office suite instead." };
  const scratch = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mightling-office-"));
  try {
    for (const dir of ["profile", "tmp", "out"]) await fs.promises.mkdir(path.join(scratch, dir));
    const { command, args } = conversionCommand(bwrap, soffice, file, scratch);
    const code = await new Promise<number | null>((resolve, reject) => {
      const child = spawn(command, args, { stdio: "ignore", env: { PATH: env.PATH ?? "/usr/bin:/bin", LANG: env.LANG ?? "C.UTF-8" } });
      const timer = setTimeout(() => {
        child.kill("SIGKILL");
        reject(new Error(`LibreOffice took longer than ${OFFICE_TIMEOUT_MS / 1000} s`));
      }, OFFICE_TIMEOUT_MS);
      child.on("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.on("exit", (exitCode) => {
        clearTimeout(timer);
        resolve(exitCode);
      });
    });
    const base = path.basename(file).replace(/\.[^.]+$/, "");
    const pdf = path.join(scratch, "out", `${base}.pdf`);
    const made = await fs.promises.stat(pdf).catch(() => null);
    if (!made?.isFile()) return { unavailable: `LibreOffice could not convert this file${code ? ` (exit code ${code})` : ""}.` };
    if (made.size > OFFICE_MAX_PDF) return { unavailable: "The converted file is too large to show here." };
    return { pdf: (await fs.promises.readFile(pdf)).toString("base64") };
  } finally {
    await fs.promises.rm(scratch, { recursive: true, force: true });
  }
}
