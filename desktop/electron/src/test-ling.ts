// Stand-ins for `ling` in the main process's tests: a Node script, made runnable the way the
// platform runs things: with a shebang and the executable bit on Linux and macOS, through a `.cmd`
// next to it on Windows (which has neither). Not a test itself.

import fs from "node:fs";
import path from "node:path";

/** Writes `source` (JavaScript for Node) as the runnable `name` in `dir`; returns what to run. */
export function nodeScript(dir: string, name: string, source: string): string {
  if (process.platform === "win32") {
    fs.writeFileSync(path.join(dir, `${name}.js`), source);
    const runner = path.join(dir, `${name}.cmd`);
    fs.writeFileSync(runner, `@"${process.execPath}" "%~dp0${name}.js" %*\r\n`);
    return runner;
  }
  const file = path.join(dir, name);
  fs.writeFileSync(file, `#!/usr/bin/env node\n${source}`);
  fs.chmodSync(file, 0o755);
  return file;
}
