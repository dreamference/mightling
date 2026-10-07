import { describe, expect, it } from "vitest";

import { parseStatus } from "./airgapped";

describe("the air-gap status line", () => {
  it("is read from ling airgapped's first line", () => {
    expect(parseStatus("Airgapped: on (this session)\n  off  …\n")).toEqual({ level: "on", source: "this session" });
    expect(parseStatus("Airgapped: off (default)")).toEqual({ level: "off", source: "default" });
    expect(parseStatus("Usage: ling airgapped [default <off|on>]")).toBeNull();
  });
});
