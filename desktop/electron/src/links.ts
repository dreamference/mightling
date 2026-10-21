// A web address from an answer, opened in the system's browser (DESKTOP_PARITY_CLAUDE §2.4, decided
// 2026-10-21): the page shows the full address and asks first; this process checks the address again
// (the page's rule, desktop/ui/src/markdown.ts `webLink`) and refuses at `/airgapped on` (main.ts
// `open/url`), as it refuses the forge's page. No Electron here.

/** The longest address opened (the page's WEB_LINK_MAX). */
export const URL_MAX = 2048;

/** The address to open, written out in full, or null: http or https with a host, no user name or password, at most URL_MAX characters. */
export function webUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value || value.length > URL_MAX) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if ((url.protocol !== "http:" && url.protocol !== "https:") || !url.hostname || url.username || url.password) return null;
  return url.href;
}
