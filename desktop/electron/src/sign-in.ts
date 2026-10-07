// Signs the Chat window in to the web UI from the main process, once per window
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3, decision 12).
//
// The web UI's admin password is random per install and kept in
// `~/.config/dreamference/chat-admin.json` (mode 0600, `{email, password}`), written by
// `ling-admin chat configure` on the node. Only this process reads it: the password is posted to
// `/api/auth/login` over the Chat window's own session, and the page receives nothing but the
// session cookie the server sets. No script is injected into the page and no preload exists for
// Chat, so nothing in Onyx's page can read the credential.
//
// The attempt is made once per window, and only when `/api/me` answers 401 or 403: a window that
// is signed in already is left alone, and a password that no longer works (changed by the owner)
// gets the ordinary login page, not a second try. A machine without the file (a client) shows the
// login page.

import fs from "node:fs";
import path from "node:path";

export const CREDENTIALS_FILE = path.join(".config", "dreamference", "chat-admin.json");

/** The cookie fastapi-users sets on a successful login, which is the whole of the session. */
export const AUTH_COOKIE = "fastapiusersauth";

export interface Credentials {
  email: string;
  password: string;
}

/** `~/.config/dreamference/chat-admin.json` under `home`. */
export function credentialsFile(home: string | undefined = process.env.HOME): string | null {
  return home ? path.join(home, CREDENTIALS_FILE) : null;
}

/** The credentials, or `null` when the file is missing, unreadable or not `{email, password}` strings. */
export function readCredentials(file: string | null): Credentials | null {
  if (!file) return null;
  let value: unknown;
  try {
    value = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  const { email, password } = value as Record<string, unknown>;
  if (typeof email !== "string" || typeof password !== "string" || email === "" || password === "") return null;
  return { email, password };
}

/** What the sign-in needs from the network, over the Chat window's session (`server.ts`-free, so it can be tested). */
export interface Http {
  /** `GET <url>`'s status. Throws when the server cannot be reached. */
  status(url: string): Promise<number>;
  /** `POST <url>` with a form body: its status and its `Set-Cookie` headers. */
  postForm(url: string, body: string): Promise<{ status: number; setCookie: string[] }>;
  /** Stores the session cookie when the response's own `Set-Cookie` did not land in the jar. */
  ensureCookie(origin: string, cookie: ParsedCookie): Promise<void>;
}

export type Outcome = "signed-in" | "signed-in-already" | "no-credentials" | "refused" | "unreachable" | "tried-before";

/** One window's sign-in: at most one login attempt over the window's life. */
export class WindowSignIn {
  private tried = false;
  private running: Promise<Outcome> | null = null;

  constructor(
    private readonly origin: string,
    private readonly credentials: () => Credentials | null,
    private readonly http: Http,
  ) {}

  /** Signs the window in if it has no session and has not been tried; concurrent calls share one attempt. */
  run(): Promise<Outcome> {
    if (this.tried) return Promise.resolve("tried-before");
    this.running ??= this.attempt().finally(() => (this.running = null));
    return this.running;
  }

  private async attempt(): Promise<Outcome> {
    let me: number;
    try {
      me = await this.http.status(`${this.origin}/api/me`);
    } catch {
      return "unreachable"; // Not counted as the attempt: nothing was tried.
    }
    if (me !== 401 && me !== 403) return "signed-in-already";
    // Counted before the login, so a wrong password is tried once per window, not once per page.
    this.tried = true;
    const credentials = this.credentials();
    if (!credentials) return "no-credentials";
    const body = new URLSearchParams({ username: credentials.email, password: credentials.password }).toString();
    let login: { status: number; setCookie: string[] };
    try {
      login = await this.http.postForm(`${this.origin}/api/auth/login`, body);
    } catch {
      return "unreachable";
    }
    if (login.status < 200 || login.status >= 300) return "refused";
    const cookie = login.setCookie.map(parseSetCookie).find((parsed) => parsed?.name === AUTH_COOKIE);
    if (cookie) await this.http.ensureCookie(this.origin, cookie);
    return "signed-in";
  }
}

export interface ParsedCookie {
  name: string;
  value: string;
  path: string;
  httpOnly: boolean;
  secure: boolean;
  sameSite: "unspecified" | "no_restriction" | "lax" | "strict";
  /** Seconds since the epoch, or undefined for a session cookie. */
  expirationDate?: number;
}

/** One `Set-Cookie` header, as `session.cookies.set` wants it. */
export function parseSetCookie(header: string, now: number = Date.now()): ParsedCookie | null {
  const [pair, ...attributes] = header.split(";").map((part) => part.trim());
  const equals = pair?.indexOf("=") ?? -1;
  if (!pair || equals <= 0) return null;
  const cookie: ParsedCookie = {
    name: pair.slice(0, equals),
    value: pair.slice(equals + 1),
    path: "/",
    httpOnly: false,
    secure: false,
    sameSite: "lax",
  };
  for (const attribute of attributes) {
    const [key, ...rest] = attribute.split("=");
    const value = rest.join("=");
    switch (key.toLowerCase()) {
      case "path":
        cookie.path = value || "/";
        break;
      case "httponly":
        cookie.httpOnly = true;
        break;
      case "secure":
        cookie.secure = true;
        break;
      case "samesite":
        cookie.sameSite = value.toLowerCase() === "strict" ? "strict" : value.toLowerCase() === "none" ? "no_restriction" : "lax";
        break;
      case "max-age": {
        const seconds = Number(value);
        if (Number.isFinite(seconds)) cookie.expirationDate = Math.floor(now / 1000) + seconds;
        break;
      }
      case "expires": {
        const when = Date.parse(value);
        if (!Number.isNaN(when) && cookie.expirationDate === undefined) cookie.expirationDate = Math.floor(when / 1000);
        break;
      }
    }
  }
  return cookie;
}
