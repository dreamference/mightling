// Subagent marks (DESKTOP_PARITY §4.5 item 37, DESKTOP §16 B21): one colour and one glyph per
// subagent, from its thread id, the same wherever it shows: the subagents panel, the stream's
// subagent items and the sidebar's row of the thread that started it.

/** The glyphs, distinct in shape so a mark is told apart without its colour. */
export const GLYPHS = ["●", "▲", "■", "◆", "★", "✚", "⬟", "⬢"] as const;
/** Hues spread round the wheel; with the glyphs, 64 marks before one repeats. */
export const HUES = [12, 48, 96, 160, 200, 240, 280, 320] as const;

/** A small stable hash of a thread id (FNV-1a). */
function hash(text: string): number {
  let value = 0x811c9dc5;
  for (let index = 0; index < text.length; index++) {
    value ^= text.charCodeAt(index);
    value = Math.imul(value, 0x01000193) >>> 0;
  }
  return value;
}

/** A subagent's mark: its glyph and hue, the same for the same id everywhere. */
export function agentMark(id: string): { glyph: string; hue: number } {
  const value = hash(id);
  return { glyph: GLYPHS[value % GLYPHS.length], hue: HUES[Math.floor(value / GLYPHS.length) % HUES.length] };
}

export function AgentMark({ id, label }: { id: string; label?: string }) {
  const mark = agentMark(id);
  return (
    <span className="agent-mark" style={{ color: `hsl(${mark.hue} 70% 52%)` }} title={label} aria-hidden={label ? undefined : true} aria-label={label} role={label ? "img" : undefined}>
      {mark.glyph}
    </span>
  );
}
