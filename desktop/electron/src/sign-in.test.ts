// The Chat window's sign-in: the old in-page script's behaviour (once per window, only without a
// session, no second try), now in the main process with the per-install credential.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { AUTH_COOKIE, WindowSignIn, credentialsFile, parseSetCookie, readCredentials, type Http, type ParsedCookie } from "./sign-in";

const ORIGIN = "http://localhost:3000";
const CREDENTIALS = { email: "admin@example.test", password: "s3cret-random" };

function fakeHttp(me: number, login: number, setCookie: string[] = [`${AUTH_COOKIE}=tok; HttpOnly; Max-Age=3600; Path=/; SameSite=lax`]) {
  const calls: { method: string; url: string; body?: string }[] = [];
  const cookies: ParsedCookie[] = [];
  const http: Http = {
    status: async (url) => {
      calls.push({ method: "GET", url });
      return me;
    },
    postForm: async (url, body) => {
      calls.push({ method: "POST", url, body });
      return { status: login, setCookie };
    },
    ensureCookie: async (_origin, cookie) => {
      cookies.push(cookie);
    },
  };
  return { http, calls, cookies };
}

describe("the Chat window's sign-in", () => {
  it("signs a window without a session in once, with the per-install account", async () => {
    const { http, calls, cookies } = fakeHttp(403, 204);
    const signIn = new WindowSignIn(ORIGIN, () => CREDENTIALS, http);
    expect(await signIn.run()).toBe("signed-in");
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([`GET ${ORIGIN}/api/me`, `POST ${ORIGIN}/api/auth/login`]);
    expect(calls[1].body).toBe("username=admin%40example.test&password=s3cret-random");
    expect(cookies.map((cookie) => [cookie.name, cookie.value, cookie.httpOnly])).toEqual([[AUTH_COOKIE, "tok", true]]);
    // Never twice in one window.
    expect(await signIn.run()).toBe("tried-before");
    expect(calls).toHaveLength(2);
  });

  it("gives a refused password the login page and no second attempt", async () => {
    const { http, calls } = fakeHttp(401, 400);
    const signIn = new WindowSignIn(ORIGIN, () => CREDENTIALS, http);
    expect(await signIn.run()).toBe("refused");
    expect(await signIn.run()).toBe("tried-before");
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
  });

  it("leaves a signed-in window alone, and does not count that as the attempt", async () => {
    const { http, calls } = fakeHttp(200, 204);
    const signIn = new WindowSignIn(ORIGIN, () => CREDENTIALS, http);
    expect(await signIn.run()).toBe("signed-in-already");
    expect(await signIn.run()).toBe("signed-in-already");
    expect(calls.every((call) => call.method === "GET")).toBe(true);
  });

  it("shows the login page on a machine without the file (a client)", async () => {
    const { http, calls } = fakeHttp(403, 204);
    const signIn = new WindowSignIn(ORIGIN, () => null, http);
    expect(await signIn.run()).toBe("no-credentials");
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
  });

  it("shares one attempt between concurrent calls", async () => {
    const { http, calls } = fakeHttp(403, 204);
    const signIn = new WindowSignIn(ORIGIN, () => CREDENTIALS, http);
    const outcomes = await Promise.all([signIn.run(), signIn.run()]);
    expect(outcomes).toEqual(["signed-in", "signed-in"]);
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
  });

  it("does not spend the attempt while the server is unreachable", async () => {
    const calls: string[] = [];
    let up = false;
    const http: Http = {
      status: async (url) => {
        calls.push(url);
        if (!up) throw new Error("ECONNREFUSED");
        return 403;
      },
      postForm: async () => ({ status: 204, setCookie: [] }),
      ensureCookie: async () => {},
    };
    const signIn = new WindowSignIn(ORIGIN, () => CREDENTIALS, http);
    expect(await signIn.run()).toBe("unreachable");
    up = true;
    expect(await signIn.run()).toBe("signed-in");
  });
});

describe("the credentials file", () => {
  it("is read only when it holds an email and a password", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "chat-admin-"));
    const file = path.join(dir, "chat-admin.json");
    try {
      expect(readCredentials(file)).toBeNull();
      fs.writeFileSync(file, JSON.stringify(CREDENTIALS), { mode: 0o600 });
      expect(readCredentials(file)).toEqual(CREDENTIALS);
      fs.writeFileSync(file, JSON.stringify({ email: "a@b", password: "" }));
      expect(readCredentials(file)).toBeNull();
      fs.writeFileSync(file, "not json");
      expect(readCredentials(file)).toBeNull();
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
    expect(credentialsFile("/home/u")).toBe("/home/u/.config/dreamference/chat-admin.json");
    expect(credentialsFile("")).toBeNull();
  });
});

describe("Set-Cookie", () => {
  it("is parsed into what session.cookies.set takes", () => {
    const now = Date.UTC(2026, 9, 7);
    expect(parseSetCookie(`${AUTH_COOKIE}=a=b; HttpOnly; Max-Age=60; Path=/; SameSite=lax`, now)).toEqual({
      name: AUTH_COOKIE,
      value: "a=b",
      path: "/",
      httpOnly: true,
      secure: false,
      sameSite: "lax",
      expirationDate: now / 1000 + 60,
    });
    expect(parseSetCookie("garbage")).toBeNull();
  });
});
