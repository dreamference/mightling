// The preview tab's rules (DESKTOP_PARITY §2.9, Phase 3 item 4): this machine, the LAN and files
// load; the internet asks or follows Settings › Browser; airgapped on keeps it to this machine.
import { describe, expect, it } from "vitest";

import { addressOf, decide, reachOf } from "./preview-rules";
import { consoleLine } from "./preview-rules";

const none = new Set<string>();

describe("the preview tab's rules", () => {
  it("knows this machine, the LAN and the internet by the host alone", () => {
    expect(reachOf("localhost")).toBe("loopback");
    expect(reachOf("app.localhost")).toBe("loopback");
    expect(reachOf("127.0.0.1")).toBe("loopback");
    expect(reachOf("[::1]")).toBe("loopback");
    expect(reachOf("192.168.1.20")).toBe("lan");
    expect(reachOf("10.0.0.5")).toBe("lan");
    expect(reachOf("172.20.1.1")).toBe("lan");
    expect(reachOf("172.32.1.1")).toBe("internet");
    expect(reachOf("gx10-1a2b")).toBe("lan");
    expect(reachOf("gx10-1a2b.local")).toBe("lan");
    expect(reachOf("example.com")).toBe("internet");
    expect(reachOf("8.8.8.8")).toBe("internet");
  });

  it("loads this machine, the LAN and files; asks for the internet; follows the user's choices", () => {
    expect(decide("http://localhost:5173/", {}, none, false).verdict).toBe("allow");
    expect(decide("http://gx10-1a2b.local:8080/", {}, none, false).verdict).toBe("allow");
    expect(decide("file:///home/u/site/index.html", {}, none, false).verdict).toBe("allow");
    expect(decide("https://example.com/a", {}, none, false)).toMatchObject({ verdict: "ask", site: "example.com" });
    expect(decide("https://example.com/a", { "example.com": "allow" }, none, false).verdict).toBe("allow");
    expect(decide("https://example.com/a", { "example.com": "block" }, new Set(["example.com"]), false).verdict).toBe("block");
    expect(decide("https://cdn.example.com/a.js", { "example.com": "allow" }, none, false).verdict).toBe("ask");
    expect(decide("https://example.com/a", {}, new Set(["example.com"]), false).verdict).toBe("allow");
    expect(decide("ftp://example.com/", {}, none, false).verdict).toBe("block");
    expect(decide("chrome://settings", {}, none, false).verdict).toBe("block");
  });

  it("keeps to this machine at airgapped on, whatever was allowed", () => {
    expect(decide("http://127.0.0.1:3000/", {}, none, true).verdict).toBe("allow");
    expect(decide("file:///tmp/a.html", {}, none, true).verdict).toBe("allow");
    expect(decide("http://192.168.1.20/", {}, none, true).verdict).toBe("block");
    expect(decide("https://example.com/", { "example.com": "allow" }, new Set(["example.com"]), true).verdict).toBe("block");
  });

  it("reads the address bar: a local host gets http, a site https, a path is a file", () => {
    expect(addressOf("localhost:5173")).toBe("http://localhost:5173");
    expect(addressOf("gx10-1a2b.local/docs")).toBe("http://gx10-1a2b.local/docs");
    expect(addressOf("example.com")).toBe("https://example.com");
    expect(addressOf("/home/u/site/index.html")).toBe("file:///home/u/site/index.html");
    expect(addressOf("C:\\site\\index.html")).toBe("file:///C:/site/index.html");
    expect(addressOf("nas.lan:5000")).toBe("http://nas.lan:5000");
    expect(addressOf("https://example.com/x")).toBe("https://example.com/x");
    expect(addressOf("  ")).toBeNull();
  });
});

describe("a preview's console, for the agent's console tool", () => {
  it("is one line per message, its level and where it came from, in both of Electron's event forms", () => {
    expect(consoleLine({ level: "error", message: "Uncaught TypeError: x is undefined", lineNumber: 12, sourceId: "http://127.0.0.1:5173/src/main.js" })).toBe("[error] Uncaught TypeError: x is undefined (main.js:12)");
    expect(consoleLine({}, 2, "deprecated API", 3, "http://127.0.0.1/app.js")).toBe("[warning] deprecated API (app.js:3)");
    expect(consoleLine({ level: "info", message: "x".repeat(5000) }).length).toBe(2000);
  });
});

