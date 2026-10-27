// The artifacts viewer (DESKTOP_PARITY Phase 3 item 3): which files show rendered, how a
// document's references resolve, and what opens by itself when the agent creates it.
import { describe, expect, it } from "vitest";

import { artifactKind, createdArtifacts, hasSource, imageType, isRemote, localReference, parseTable, tableSeparator } from "./artifacts";
import { renderDocument } from "./markdown";

describe("the artifacts viewer", () => {
  it("knows Markdown, images, PDFs, HTML and LaTeX by their names", () => {
    expect(artifactKind("/w/README.md")).toBe("markdown");
    expect(artifactKind("/w/hero.WEBP")).toBe("image");
    expect(artifactKind("/w/report.pdf")).toBe("pdf");
    expect(artifactKind("/w/index.html")).toBe("html");
    expect(artifactKind("/w/paper.tex")).toBe("latex");
    expect(artifactKind("/w/main.py")).toBeNull();
    expect(imageType("/w/logo.svg")).toBe("image/svg+xml");
    expect(hasSource("/w/logo.svg")).toBe(true);
    expect(hasSource("/w/logo.png")).toBe(false);
  });

  it("resolves a document's local references and refuses remote ones", () => {
    expect(localReference("/w/docs/guide.md", "img/a.png")).toBe("/w/docs/img/a.png");
    expect(localReference("/w/docs/guide.md", "../README.md#install")).toBe("/w/README.md");
    expect(localReference("/w/docs/guide.md", "file:///etc/hosts")).toBe("/etc/hosts");
    expect(localReference("/w/docs/guide.md", "a%20b.png")).toBe("/w/docs/a b.png");
    expect(localReference("/w/docs/guide.md", "https://example.com/x.png")).toBeNull();
    expect(localReference("/w/docs/guide.md", "javascript:alert(1)")).toBeNull();
    expect(localReference("/w/docs/guide.md", "#part")).toBeNull();
    expect(isRemote("data:image/png;base64,xx")).toBe(true);
  });

  it("renders a document's images and links as references the viewer resolves, never as loads", () => {
    const html = renderDocument("![chart](img/c.png) [guide](docs/guide.md) <script>x()</script>");
    expect(html).toContain('<img data-ref="img/c.png" alt="chart">');
    expect(html).toContain('data-href="docs/guide.md"');
    expect(html).not.toContain("<script>");
    expect(html).not.toMatch(/\ssrc=|\shref=/);
  });

  it("opens what a finished turn created: documents and images, not code, not edits", () => {
    const item = { type: "fileChange", status: "completed", changes: [
      { path: "/w/report.pdf", kind: { type: "add" } },
      { path: "notes.md", kind: { type: "add" } },
      { path: "/w/main.py", kind: { type: "add" } },
      { path: "/w/README.md", kind: { type: "update" } },
    ] };
    expect(createdArtifacts(item, "/w")).toEqual(["/w/report.pdf", "/w/notes.md"]);
    expect(createdArtifacts({ ...item, status: "inProgress" }, "/w")).toEqual([]);
  });
});

describe("CSV and TSV tabs (DESKTOP §16 B27)", () => {
  it("are tables, with a Source view", () => {
    expect(artifactKind("/p/data.csv")).toBe("table");
    expect(artifactKind("/p/data.TSV")).toBe("table");
    expect(hasSource("/p/data.csv")).toBe(true);
  });

  it("read quoted cells, doubled quotes, line breaks in a cell and either line ending", () => {
    const text = 'name,note\r\n"Smith, J.","said ""hi""\nthen left"\nLee,\n';
    expect(parseTable(text, ",")).toEqual({ rows: [["name", "note"], ["Smith, J.", 'said "hi"\nthen left'], ["Lee", ""]], totalRows: 3, totalColumns: 2 });
  });

  it("pick the separator: a tab for .tsv, a semicolon when a .csv's first line has more of them", () => {
    expect(tableSeparator("a.tsv", "a,b\tc")).toBe("\t");
    expect(tableSeparator("a.csv", "a;b;c,d\n1;2;3")).toBe(";");
    expect(tableSeparator("a.csv", "a,b\n")).toBe(",");
  });

  it("cap what is drawn and count the rest", () => {
    const text = Array.from({ length: 30 }, (_, row) => Array.from({ length: 8 }, (_, column) => `${row}.${column}`).join(",")).join("\n");
    const table = parseTable(text, ",", 10, 3);
    expect(table.rows).toHaveLength(10);
    expect(table.rows[0]).toEqual(["0.0", "0.1", "0.2"]);
    expect(table.totalRows).toBe(30);
    expect(table.totalColumns).toBe(8);
  });
});
