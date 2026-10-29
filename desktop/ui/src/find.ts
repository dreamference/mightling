// Find in thread (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1; Ctrl+F, Ctrl+G, Shift+F3):
// the matches in the shown thread's text, marked with the CSS Custom Highlight API (nothing in the
// page's DOM is changed, so a streaming turn is not disturbed), the current one scrolled to.
// Works the same in the app and in a browser on `ling web`.

/** The offsets of every case-insensitive occurrence of `query` in `text`. */
export function occurrences(text: string, query: string): number[] {
  if (!query) return [];
  // The text itself is searched, not a lowered copy: lower case can change a length ("İ" becomes two
  // code units), which would move every offset after it. Without the `u` flag, case is compared per
  // code unit, so a match is always `query.length` long.
  const pattern = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  return Array.from(text.matchAll(pattern), (match) => match.index);
}

/** Every match of `query` under `root`, as ranges, in document order. */
export function findRanges(root: Element, query: string): Range[] {
  const ranges: Range[] = [];
  if (!query.trim()) return ranges;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent ?? "";
    for (const at of occurrences(text, query)) {
      const range = document.createRange();
      range.setStart(node, at);
      range.setEnd(node, at + query.length);
      ranges.push(range);
    }
    if (ranges.length > 2000) break;
  }
  return ranges;
}

const supported = () => typeof CSS !== "undefined" && "highlights" in CSS && typeof Highlight !== "undefined";

/** Marks `ranges`, the `current` one apart, and scrolls it into view; clears with no ranges. */
export function mark(ranges: Range[], current: number): void {
  if (!supported()) return;
  CSS.highlights.delete("find");
  CSS.highlights.delete("find-current");
  if (ranges.length === 0) return;
  CSS.highlights.set("find", new Highlight(...ranges));
  const now = ranges[current];
  if (now) {
    CSS.highlights.set("find-current", new Highlight(now));
    const box = now.getBoundingClientRect();
    const element = now.startContainer.parentElement;
    if (element && (box.top < 80 || box.bottom > window.innerHeight - 120)) element.scrollIntoView({ block: "center" });
  }
}
