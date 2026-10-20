// An MCP server's question (DESKTOP_PARITY §4.5 item 14): the schema read as fields, the answer typed.
import { describe, expect, it } from "vitest";

import { elicitationAnswer, elicitationContent, elicitationFields, shownElicitation } from "./elicitation";

const schema = {
  type: "object",
  properties: {
    repo: { type: "string", title: "Repository", description: "owner/name" },
    count: { type: "integer", title: "How many", default: 3 },
    ratio: { type: "number" },
    dryRun: { type: "boolean", title: "Dry run", default: true },
    level: { type: "string", oneOf: [{ const: "low", title: "Low" }, { const: "high", title: "High" }] },
    old: { type: "string", enum: ["a", "b"], enumNames: ["Alpha", "Beta"] },
    tags: { type: "array", items: { anyOf: [{ const: "x", title: "X" }, { const: "y", title: "Y" }] } },
    nested: { type: "object" },
  },
  required: ["repo", "level"],
};

describe("an MCP server's question", () => {
  it("reads the schema's properties as fields, in order, with their kinds, choices and defaults", () => {
    const fields = elicitationFields(schema);
    expect(fields.map((field) => [field.name, field.kind, field.required])).toEqual([
      ["repo", "string", true], ["count", "integer", false], ["ratio", "number", false], ["dryRun", "boolean", false],
      ["level", "choice", true], ["old", "choice", false], ["tags", "choices", false],
    ]);
    expect(fields[0]).toMatchObject({ label: "Repository", description: "owner/name", initial: "" });
    expect(fields[1].initial).toBe("3");
    expect(fields[3].initial).toBe("true");
    expect(fields[4].options).toEqual([{ value: "low", label: "Low" }, { value: "high", label: "High" }]);
    expect(fields[5].options).toEqual([{ value: "a", label: "Alpha" }, { value: "b", label: "Beta" }]);
    expect(elicitationFields(null)).toEqual([]);
    expect(elicitationFields({ type: "object" })).toEqual([]);
  });

  it("sends each field as its type, leaves empty optional ones out, and names what is missing or wrong", () => {
    const fields = elicitationFields(schema);
    expect(elicitationContent(fields, { repo: " acme/app ", level: "high", ratio: "0.5", tags: ["y"] })).toEqual({
      content: { repo: "acme/app", count: 3, ratio: 0.5, dryRun: true, level: "high", tags: ["y"] },
      problems: [],
    });
    expect(elicitationContent(fields, { repo: "", level: "", count: "2.5" }).problems).toEqual(["Repository is required.", "How many must be a whole number.", "level is required."]);
    expect(elicitationContent(fields, { repo: "a/b", level: "low", dryRun: "false" }).content.dryRun).toBe(false);
  });

  it("answers accept with the content, decline and cancel without", () => {
    expect(elicitationAnswer("accept", { repo: "a/b" })).toEqual({ action: "accept", content: { repo: "a/b" }, _meta: null });
    expect(elicitationAnswer("accept")).toEqual({ action: "accept", content: {}, _meta: null });
    expect(elicitationAnswer("decline", { repo: "a/b" })).toEqual({ action: "decline", content: null, _meta: null });
    expect(elicitationAnswer("cancel")).toEqual({ action: "cancel", content: null, _meta: null });
  });

  it("shows every mode but the vendor's identity check", () => {
    expect(shownElicitation({ mode: "form" })).toBe(true);
    expect(shownElicitation({ mode: "url" })).toBe(true);
    expect(shownElicitation({ mode: "openai/userVerification" })).toBe(false);
  });
});
