// The apps a test launches, and how they end. Playwright's worker waits at teardown for every
// Electron it launched to quit; one still running held up the full run's teardown past its timeout
// while each file alone passed. An app Playwright closes within about a second of its agent's
// server starting never exits (its windows close, the main process stays until killed: seen every
// time on Linux, and only under Playwright; the same quit from a SIGTERM, without Playwright's
// debugger attached, exits at once), and app.e2e.ts's second test closes in that window. So every
// launch is recorded, a close is bounded and ends in a kill, and an `afterEach` closes whatever a
// test left open.
import { _electron as electron, type ElectronApplication } from "@playwright/test";

const open = new Set<ElectronApplication>();

/** `electron.launch`, recorded so that `closeLeftovers` can end it. */
export async function launch(options: Parameters<typeof electron.launch>[0]): Promise<ElectronApplication> {
  const app = await electron.launch(options);
  open.add(app);
  return app;
}

/** Quits the app, and kills it when it has not quit within `ms`. */
export async function closeApp(app: ElectronApplication, ms = 5_000): Promise<void> {
  open.delete(app);
  const child = app.process();
  const closed = app.close().then(
    () => true,
    () => false,
  );
  let timer: NodeJS.Timeout | undefined;
  const quit = await Promise.race([closed, new Promise<boolean>((resolve) => (timer = setTimeout(() => resolve(false), ms)))]);
  clearTimeout(timer);
  if (!quit && child.exitCode === null && child.signalCode === null) {
    child.kill("SIGKILL");
    await Promise.race([closed, new Promise((resolve) => setTimeout(resolve, 5_000))]);
  }
}

/** Ends every app a test launched and did not close (for `test.afterEach`). */
export async function closeLeftovers(): Promise<void> {
  await Promise.all([...open].map((app) => closeApp(app)));
}
