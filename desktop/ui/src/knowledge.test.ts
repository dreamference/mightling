// A project's knowledge searched through ling-docs, and its page in Ask (DESKTOP_PARITY_CLAUDE §4.7).
import { describe, expect, it } from "vitest";

import { knowledgeReach } from "./ProjectPage";
import { knowledgeCollection, projectContext } from "./projects";
import type { Project } from "./protocol/v2/Project";

const project = { name: "Adapters", roots: [], metadata: { "mightling.instructions": "Cite the spec." } } as unknown as Project;
const files = [{ name: "checklist.md", path: "/h/.mightling/projects/p-1/knowledge/checklist.md", size: 70 }];

describe("a project's knowledge", () => {
  it("is a collection named after the project, as ling-docs names may be", () => {
    expect(knowledgeCollection("p-1A2b")).toBe("project-p-1a2b");
    expect(knowledgeCollection("0199f3c2-7b1e-7a10-9a5b-3f1c2d4e5f60")).toHaveLength(40);
    expect(knowledgeCollection("a.b c")).toBe("project-a-b-c");
  });

  it("tells the agent to search it only when it is indexed", () => {
    expect(projectContext(project, files)).not.toContain("docs_search");
    expect(projectContext(project, files, "project-p-1")).toContain('docs_search with collection "project-p-1"');
    expect(projectContext(project, [], "project-p-1")).not.toContain("docs_search");
  });

  it("says how the agent reaches it", () => {
    const counts = (indexed: number, pending: number) => ({ counts: { indexed, pending } as never });
    expect(knowledgeReach(0, null)).toBe("none");
    expect(knowledgeReach(2, null)).toBe("files");
    expect(knowledgeReach(2, counts(0, 2))).toBe("indexing");
    expect(knowledgeReach(2, counts(2, 0))).toBe("searchable");
  });
});
