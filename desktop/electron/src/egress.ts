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

/** The app's own page: the only origin that may ask for anything. */
const APP_ORIGIN = "app://-";

const fromApp = (url: string | undefined) => url === APP_ORIGIN || (url ?? "").startsWith(`${APP_ORIGIN}/`);

/**
 * Whether a permission request is granted: writing to the clipboard (a copy button), and the
 * microphone alone, for the app's own page (the composer's dictation button,
 * specs/DREAMFERENCE_MIGHTLING_ASK.md §7). Never the camera, the screen or anything else.
 */
export function grantsRequest(permission: string, details: { mediaTypes?: string[]; requestingUrl?: string; securityOrigin?: string }): boolean {
  if (permission === "clipboard-sanitized-write") return true;
  if (permission !== "media") return false;
  const types = details.mediaTypes ?? [];
  return types.length > 0 && types.every((type) => type === "audio") && fromApp(details.requestingUrl ?? details.securityOrigin);
}

/** The same rule for the checks Chromium makes before asking (`mediaType` is one kind at a time). */
export function grantsCheck(permission: string, origin: string, details: { mediaType?: string }): boolean {
  if (permission === "clipboard-sanitized-write") return true;
  return permission === "media" && details.mediaType === "audio" && fromApp(origin);
}

/**
 * Session rules, once the app is ready: no permission a page asks for is granted but writing to
 * the clipboard and, for the app's own page, the microphone (`grantsRequest`), and spellcheck
 * downloads nothing (Electron on Linux would fetch Hunspell dictionaries from a Google CDN: the
 * download URL is pointed at the app's own scheme, where no dictionary exists).
 */
export function applySessionRules(): void {
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((_contents, permission, callback, details) =>
    callback(grantsRequest(permission, details as { mediaTypes?: string[]; requestingUrl?: string; securityOrigin?: string })));
  ses.setPermissionCheckHandler((_contents, permission, origin, details) => grantsCheck(permission, origin, details));
  try {
    ses.setSpellCheckerDictionaryDownloadURL("app://-/dictionaries/");
  } catch {
    ses.setSpellCheckerEnabled(false);
  }
}
