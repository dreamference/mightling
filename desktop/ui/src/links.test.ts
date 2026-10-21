// A link in an answer (DESKTOP_PARITY_CLAUDE §2.4): the dialog names the site apart from the address.
import { describe, expect, it } from "vitest";

import { siteOf } from "./LinkDialog";

describe("the link dialog", () => {
  it("shows the site an address names, its port included", () => {
    expect(siteOf("https://developer.nvidia.com/blog/nvfp4/")).toBe("developer.nvidia.com");
    expect(siteOf("http://127.0.0.1:8080/x")).toBe("127.0.0.1:8080");
    expect(siteOf("not a url")).toBe("not a url");
  });
});
