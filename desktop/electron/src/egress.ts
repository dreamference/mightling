// What keeps Chromium quiet on the network (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3,
// decision 6). The Codex app applies the first three behind a switch; here they are always on,
// because "nothing leaves your machine" is the product's claim and `ling-admin audit egress --app`
// checks it. Called before `app.whenReady()`.

import { app, session } from "electron";

/** Chromium switches applied at start. */
export const SWITCHES: ReadonlyArray<[string, string?]> = [
  ["disable-background-networking"],
  ["disable-component-update"],
  // AimEnabled, AutofillServerCommunication and NetworkTimeServiceQuerying are the Codex app's
  // list; MediaRouter is Chromium's Cast discovery, which sends mDNS and SSDP on its own.
  ["disable-features", "AimEnabled,AutofillServerCommunication,NetworkTimeServiceQuerying,MediaRouter"],
];

export function applySwitches(): void {
  for (const [name, value] of SWITCHES) {
    if (value === undefined) app.commandLine.appendSwitch(name);
    else app.commandLine.appendSwitch(name, value);
  }
}

/**
 * Session rules, once the app is ready: no permission a page asks for is granted but writing to
 * the clipboard (the Ask window is text only; the microphone went with the Onyx window), and
 * spellcheck downloads nothing
 * (Electron on Linux would fetch Hunspell dictionaries from a Google CDN: the download URL is
 * pointed at the app's own scheme, where no dictionary exists).
 */
export function applySessionRules(): void {
  const ses = session.defaultSession;
  // Writing to the clipboard (a copy button) is the one thing a page may do.
  ses.setPermissionRequestHandler((_contents, permission, callback) => callback(permission === "clipboard-sanitized-write"));
  ses.setPermissionCheckHandler((_contents, permission) => permission === "clipboard-sanitized-write");
  try {
    ses.setSpellCheckerDictionaryDownloadURL("app://-/dictionaries/");
  } catch {
    ses.setSpellCheckerEnabled(false);
  }
}
