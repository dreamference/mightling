// Opening an answer's link (DESKTOP_PARITY_CLAUDE §2.4): only a plain web address reaches the browser.
import { describe, expect, it } from "vitest";

import { URL_MAX, webUrl } from "./links";

describe("an answer's link", () => {
  it("is opened only as a plain http or https address", () => {
    expect(webUrl("https://www.nvidia.com/en-us/")).toBe("https://www.nvidia.com/en-us/");
    expect(webUrl("HTTP://Example.COM")).toBe("http://example.com/");
    for (const refused of ["javascript:alert(1)", "file:///etc/passwd", "ling://quick", "https://bank.example@evil.example/", "https://:pw@example.com/", "/relative", "", null, 7]) {
      expect(webUrl(refused)).toBeNull();
    }
    expect(webUrl(`https://example.com/${"a".repeat(URL_MAX)}`)).toBeNull();
  });
});
