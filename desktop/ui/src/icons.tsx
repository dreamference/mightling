// The UI's icons: outlines from Lucide 0.460.0 (https://lucide.dev, ISC licence, the notice in
// icons.LICENSE.txt), copied as path data so they are inline SVG in the bundle with nothing loaded
// at run time (DESKTOP_PARITY §3.4). They take the text colour (`currentColor`) and are hidden
// from assistive technology: the button around each one carries the label.

import type { ReactElement } from "react";

const PATHS = {
  archive: <><rect width="20" height="5" x="2" y="3" rx="1" /><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" /><path d="M10 12h4" /></>,
  arrowUp: <><path d="m5 12 7-7 7 7" /><path d="M12 19V5" /></>,
  check: <path d="M20 6 9 17l-5-5" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  chevronLeft: <path d="m15 18-6-6 6-6" />,
  chevronRight: <path d="m9 18 6-6-6-6" />,
  clock: <><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></>,
  copy: <><rect width="14" height="14" x="8" y="8" rx="2" ry="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></>,
  fileDiff: <><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" /><path d="M9 10h6" /><path d="M12 13V7" /><path d="M9 17h6" /></>,
  gitFork: <><circle cx="12" cy="18" r="3" /><circle cx="6" cy="6" r="3" /><circle cx="18" cy="6" r="3" /><path d="M18 9v2c0 .6-.4 1-1 1H7c-.6 0-1-.4-1-1V9" /><path d="M12 12v3" /></>,
  info: <><circle cx="12" cy="12" r="10" /><path d="M12 16v-4" /><path d="M12 8h.01" /></>,
  folder: <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />,
  panelLeft: <><rect width="18" height="18" x="3" y="3" rx="2" /><path d="M9 3v18" /></>,
  paperclip: <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />,
  pencil: <><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" /><path d="m15 5 4 4" /></>,
  pin: <><path d="M12 17v5" /><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" /></>,
  more: <><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /><circle cx="5" cy="12" r="1" /></>,
  externalLink: <><path d="M15 3h6v6" /><path d="M10 14 21 3" /><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /></>,
  plus: <><path d="M5 12h14" /><path d="M12 5v14" /></>,
  search: <><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></>,
  settings: <><path d="M20 7h-9" /><path d="M14 17H5" /><circle cx="17" cy="17" r="3" /><circle cx="7" cy="7" r="3" /></>,
  square: <rect width="18" height="18" x="3" y="3" rx="2" />,
  squarePen: <><path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z" /></>,
  terminal: <><polyline points="4 17 10 11 4 5" /><line x1="12" x2="20" y1="19" y2="19" /></>,
  undo: <><path d="M9 14 4 9l5-5" /><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11" /></>,
  x: <><path d="M18 6 6 18" /><path d="m6 6 12 12" /></>,
} satisfies Record<string, ReactElement>;

export type IconName = keyof typeof PATHS;

/** One icon, `size` px square (16 in rows, 20 in the bar: §3.4). */
export function Icon({ name, size = 16, strokeWidth = 2 }: { name: IconName; size?: number; strokeWidth?: number }) {
  return (
    <svg className={`icon-svg icon-${name}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {PATHS[name]}
    </svg>
  );
}

/**
 * Mightling's own mark, the faceted ling of images/mightling-logo-*.svg without the wordmark, for
 * the empty state (§3.7). Its colours are tokens (styles.css `--mark-*`).
 */
export function MightlingMark({ size = 64 }: { size?: number }) {
  return (
    <svg className="mark" width={size * 1.6} height={size} viewBox="24 10 230 125" aria-hidden="true" focusable="false">
      <polygon points="96,64 52,14 78,16 134,56" fill="var(--mark-body)" />
      <polygon points="96,64 78,16 134,56" fill="var(--mark-facet-1)" />
      <polygon points="28,96 70,70 130,56 176,44 196,52 196,92 182,100 140,118 90,122 44,112" fill="var(--mark-body)" />
      <polygon points="130,56 176,44 168,86" fill="var(--mark-facet-2)" />
      <polygon points="70,70 130,56 118,92" fill="var(--mark-facet-3)" />
      <polygon points="178,99 140,116 92,120 58,110 102,103 148,95" fill="var(--mark-light)" />
      <polygon points="196,52 196,92 172,86 174,54" fill="var(--mark-light)" />
      <polygon points="184,58 189,63 184,68 179,63" fill="var(--mark-eye)" />
      <polygon points="196,52 250,74 196,72" fill="var(--mark-beak-1)" />
      <polygon points="196,72 250,74 196,92" fill="#e4572e" />
      <polygon points="98,121 112,131 118,119" fill="var(--mark-beak-1)" />
    </svg>
  );
}
