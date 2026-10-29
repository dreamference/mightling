// The preview tab's comments on an area, several sent together, and its address history (DESKTOP §16 B29).
import { describe, expect, it } from "vitest";

import { HISTORY_KEPT, addressOf, commentLine, remembered } from "./PreviewTab";

describe("preview comments and history (DESKTOP §16 B29)", () => {
  it("names an element by its selector and text, and an area by its size and place", () => {
    expect(commentLine({ selector: "h1", text: "The hero", url: "http://localhost:5173/" }, " Bigger "))
      .toBe('On the page http://localhost:5173/, the element `h1` ("The hero"): Bigger');
    expect(commentLine({ selector: "main > section", text: "", url: "http://localhost:5173/", area: { x: 10, y: 20, width: 300, height: 120 } }, "Too much space"))
      .toBe("On the page http://localhost:5173/, the area of 300 × 120 px at (10, 20), around `main > section`: Too much space");
  });

  it("keeps the tab's addresses newest first, each once, capped", () => {
    expect(remembered(["b", "a"], "a")).toEqual(["a", "b"]);
    const long = Array.from({ length: HISTORY_KEPT }, (_, index) => `u${index}`);
    expect(remembered(long, "new")).toHaveLength(HISTORY_KEPT);
    expect(remembered(long, "new")[0]).toBe("new");
  });
});

describe("the address bar, read as the app reads it (preview-rules.ts addressOf)", () => {
  it("gives a LAN name, a link-local or Tailscale address http, a site https", () => {
    expect(addressOf("nas.lan:5000")).toBe("http://nas.lan:5000");
    expect(addressOf("printer.home.arpa")).toBe("http://printer.home.arpa");
    expect(addressOf("169.254.1.2/setup")).toBe("http://169.254.1.2/setup");
    expect(addressOf("100.101.102.103:8080")).toBe("http://100.101.102.103:8080");
    expect(addressOf("192.168.1.20")).toBe("http://192.168.1.20");
    expect(addressOf("example.com")).toBe("https://example.com");
    expect(addressOf("8.8.8.8")).toBe("https://8.8.8.8");
  });

  it("reads a Windows path as a file", () => {
    expect(addressOf("C:\\site\\index.html")).toBe("file:///C:/site/index.html");
    expect(addressOf("/home/u/site/index.html")).toBe("file:///home/u/site/index.html");
  });
});
