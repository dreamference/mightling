// Following the conversation as it grows (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.2), as
// the vendor's app does after its fix of 26.928: a thread opens at its latest message, new output
// keeps the view at the bottom only while the reader is there, and reading further up (or a Find
// match above) is never pulled back down. Pure.

/** How close to the end still counts as "at the bottom", in pixels: a line of slack for rounding and a partly shown line. */
export const NEAR_BOTTOM_PX = 48;

/** The scroll geometry of an element, as the DOM gives it. */
export interface ScrollBox {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/** Whether the reader is at (or within `slack` of) the end of the conversation. */
export function nearBottom(box: ScrollBox, slack: number = NEAR_BOTTOM_PX): boolean {
  return box.scrollHeight - box.scrollTop - box.clientHeight <= slack;
}
