// Diagrams in answers (DESKTOP_PARITY_CLAUDE gap 9, DESKTOP_PARITY §2.4, DESKTOP §16 B15): a fenced
// ```mermaid block in a message is drawn as a diagram by Mermaid, bundled with the app (no CDN, no
// network) and loaded the first time a diagram appears. Strict: labels are sanitised text, no HTML
// labels, no click handlers, nothing from the answer runs as script. A block that does not parse (one
// still streaming, or a mistake) stays as its source. The source is a click away under the diagram.

/** Mermaid's settings: nothing in a diagram may act or reach out. */
export function mermaidConfig(dark: boolean): Record<string, unknown> {
  return {
    startOnLoad: false,
    securityLevel: "strict",
    theme: dark ? "dark" : "default",
    htmlLabels: false,
    flowchart: { htmlLabels: false },
    maxTextSize: 50_000,
    fontFamily: "inherit",
  };
}

/** The longest diagram source drawn; a longer one stays as text. */
export const DIAGRAM_CAP = 20_000;

type MermaidApi = { initialize: (config: Record<string, unknown>) => void; render: (id: string, text: string) => Promise<{ svg: string }> };

let loading: Promise<MermaidApi> | null = null;
let configuredDark: boolean | null = null;
let counter = 0;

function loadMermaid(): Promise<MermaidApi> {
  loading ??= import("mermaid").then((module) => module.default as unknown as MermaidApi);
  return loading;
}

const isDark = () => document.documentElement.dataset.theme === "dark" || (document.documentElement.dataset.theme !== "light" && window.matchMedia?.("(prefers-color-scheme: dark)").matches === true);

/**
 * Draws every ```mermaid block under `root` not drawn yet. Each block is replaced by a figure holding
 * the diagram and, folded, its source; a block that fails is marked so it is not tried again until
 * its text changes (a streamed message re-renders its HTML, which brings a fresh block).
 */
export async function drawDiagrams(root: HTMLElement | null): Promise<number> {
  if (!root) return 0;
  const blocks = Array.from(root.querySelectorAll<HTMLElement>(".markdown pre > code.language-mermaid:not([data-diagram])"));
  if (!blocks.length) return 0;
  const mermaid = await loadMermaid();
  const dark = isDark();
  if (configuredDark !== dark) {
    mermaid.initialize(mermaidConfig(dark));
    configuredDark = dark;
  }
  let drawn = 0;
  for (const code of blocks) {
    const source = code.textContent ?? "";
    code.dataset.diagram = "trying";
    if (!source.trim() || source.length > DIAGRAM_CAP) {
      code.dataset.diagram = "skipped";
      continue;
    }
    // Taken before the render: another message's diagrams may be drawn meanwhile and move the counter.
    const id = `mightling-diagram-${++counter}`;
    try {
      const { svg } = await mermaid.render(id, source);
      const pre = code.parentElement;
      if (!pre || !pre.isConnected) continue;
      const figure = document.createElement("figure");
      figure.className = "diagram";
      figure.setAttribute("role", "img");
      figure.setAttribute("aria-label", "A diagram");
      const picture = document.createElement("div");
      picture.className = "diagram-picture";
      // Mermaid's strict mode returns sanitised SVG: no script, no event handlers, no foreign HTML.
      picture.innerHTML = svg;
      const details = document.createElement("details");
      details.className = "diagram-source";
      const summary = document.createElement("summary");
      summary.textContent = "Source";
      const kept = pre.cloneNode(true) as HTMLElement;
      (kept.querySelector("code") as HTMLElement).dataset.diagram = "source";
      details.append(summary, kept);
      figure.append(picture, details);
      pre.replaceWith(figure);
      drawn++;
    } catch {
      code.dataset.diagram = "failed";
      // Mermaid leaves its error drawing in the page on a failed render; take it away.
      document.getElementById(`d${id}`)?.remove();
    }
  }
  return drawn;
}
