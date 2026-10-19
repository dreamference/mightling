// The window's keyboard shortcuts (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.19), read from
// keymap.json. A handler names its action with `keyAction("<id>")`, which keymap.test.ts finds in
// the sources, so the table and the code cannot drift apart. Pure: no DOM beyond the event's fields.

import keymapFile from "./keymap.json";

export type KeyContext = "global" | "composer" | "approval" | "terminal" | "file" | "preview";

export interface KeyAction {
  id: string;
  group: string;
  label: string;
  keys: string[];
  context: KeyContext;
  status: "built" | "planned";
}

export const KEYMAP: KeyAction[] = (keymapFile as { actions: KeyAction[] }).actions;

const BY_ID = new Map(KEYMAP.map((action) => [action.id, action]));

/**
 * On a Mac the table's `Ctrl` is the Command key (⌘) and its `Super` the Control key, as in the
 * Codex app and every Mac program: Cmd+P searches files there, not Control+P. Two kinds of binding
 * keep the literal Control key on a Mac, as Mac programs do: those on Tab (Control+Tab cycles) and
 * the terminal's own (Control+L clears it). The table and the user's bindings stay one, written for
 * Linux and Windows.
 */
// The browser's own word (Chromium on a Mac says "Macintosh; Intel Mac OS X"); Node, which runs the
// tests, says "Node.js/…" on every platform, so the tests read the table one way unless they ask.
let mac = typeof navigator !== "undefined" && /Macintosh|Mac OS X/.test(navigator.userAgent ?? "");

/** Sets whether the keys are a Mac's (the window decides at start; tests set both). */
export function setMacKeys(value: boolean): void {
  mac = value;
}

/** Whether `Ctrl` in a binding on `key` in `context` means ⌘ here. */
function commandIsCtrl(key: string, context?: KeyContext): boolean {
  return mac && key !== "Tab" && context !== "terminal";
}

/** Bindings the user chose in Settings › Keyboard shortcuts (kept in the window's preferences). */
let overrides: Record<string, string[]> = {};

/** Applies the user's bindings; an id the table does not have is ignored. */
export function setOverrides(chosen: Record<string, unknown>): void {
  const next: Record<string, string[]> = {};
  for (const [id, keys] of Object.entries(chosen ?? {})) {
    if (BY_ID.has(id) && Array.isArray(keys) && keys.every((key) => typeof key === "string")) next[id] = keys as string[];
  }
  overrides = next;
}

/** The action `id` with the user's bindings, for a handler to match against; throws for an id the table does not have. */
export function keyAction(id: string): KeyAction {
  const action = BY_ID.get(id);
  if (!action) throw new Error(`no keyboard action named ${id}`);
  return overrides[id] ? { ...action, keys: overrides[id] } : action;
}

/** Every action with the user's bindings. */
export function effectiveKeymap(): KeyAction[] {
  return KEYMAP.map((action) => keyAction(action.id));
}

/** The binding a key event spells, as the table writes them (`Ctrl+Shift+K`); null for a lone modifier. */
export function chordOf(event: KeyLike): string | null {
  if (["Control", "Shift", "Alt", "Meta", "OS"].includes(event.key)) return null;
  const key = event.key.length === 1 ? event.key.toUpperCase() : event.key === "Esc" ? "Escape" : event.key;
  const swap = commandIsCtrl(key);
  const ctrl = swap ? event.metaKey : event.ctrlKey;
  const meta = swap ? event.ctrlKey : event.metaKey;
  const mods = [ctrl ? "Ctrl" : "", event.altKey ? "Alt" : "", event.shiftKey ? "Shift" : "", meta ? "Super" : ""].filter(Boolean);
  return [...mods, key === " " ? "Space" : key].join("+");
}

/** The fields of a keyboard event a binding is matched against. */
export interface KeyLike {
  key: string;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  metaKey: boolean;
}

interface Chord {
  ctrl: boolean;
  shift: boolean;
  alt: boolean;
  meta: boolean;
  key: string;
}

/** Parses one chord such as `Ctrl+Shift+P`, `Ctrl+=` or `Super+Shift+D`. */
export function parseChord(text: string): Chord {
  const parts = text.split("+");
  // `Ctrl++` would end in two empty parts; no binding here uses it.
  const key = parts.pop() ?? "";
  const mods = new Set(parts.map((part) => part.toLowerCase()));
  return { ctrl: mods.has("ctrl"), shift: mods.has("shift"), alt: mods.has("alt"), meta: mods.has("super") || mods.has("meta"), key };
}

/** The key's name as a binding spells it: letters upper case, the rest as the event names them. */
function keyName(event: KeyLike): string {
  if (event.key.length === 1) return event.key.toUpperCase();
  return event.key === "Esc" ? "Escape" : event.key;
}

/** Shifted punctuation: with Shift held the event reports `?` for `/`, `>` for `.`, `+` for `=`. */
const UNSHIFTED: Record<string, string> = { "?": "/", ">": ".", "+": "=", "_": "-", "{": "[", "}": "]", "~": "`", ")": "0", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9" };

/** Whether `event` is the chord `binding` (one chord; a sequence's chords are matched one by one), in `context`. */
export function matchesChord(event: KeyLike, binding: string, context?: KeyContext): boolean {
  const chord = parseChord(binding);
  const swap = commandIsCtrl(chord.key, context);
  const ctrl = swap ? event.metaKey : event.ctrlKey;
  const meta = swap ? event.ctrlKey : event.metaKey;
  if (chord.ctrl !== ctrl || chord.alt !== event.altKey || chord.meta !== meta) return false;
  const target = chord.key.length === 1 ? chord.key.toUpperCase() : chord.key;
  if (keyName(event) === target) return chord.shift === event.shiftKey;
  // Shifted punctuation (`Ctrl+Shift+.` arrives as `>`; `Ctrl+=` is typed as `Ctrl++` on many layouts).
  return event.shiftKey && UNSHIFTED[event.key] === chord.key;
}

/** Whether `event` triggers `action` (its single-chord bindings; sequences are handled by their owner). */
export function matches(event: KeyLike, action: KeyAction): boolean {
  return action.keys.some((binding) => !binding.includes(" ") && matchesChord(event, binding, action.context));
}

/** Two actions in one context sharing a binding: the keymap's one rule. */
export function conflicts(actions: KeyAction[] = KEYMAP): string[] {
  const seen = new Map<string, string>();
  const found: string[] = [];
  for (const action of actions) {
    for (const binding of action.keys) {
      const slot = `${action.context}:${binding.toLowerCase()}`;
      const other = seen.get(slot);
      if (other && other !== action.id) found.push(`${binding} in ${action.context}: ${other} and ${action.id}`);
      else seen.set(slot, action.id);
    }
  }
  return found;
}

/** A binding as this machine's keyboard names it: `Cmd+Shift+M` and `Option` on a Mac, as written elsewhere. */
export function keyLabel(binding: string, context?: KeyContext): string {
  if (!mac) return binding;
  return binding.split(" ").map((chord) => {
    const parts = chord.split("+");
    const key = parts[parts.length - 1];
    const swap = commandIsCtrl(key, context);
    return parts.map((part, index) => {
      if (index === parts.length - 1) return part;
      if (part === "Ctrl") return swap ? "Cmd" : "Ctrl";
      if (part === "Super") return swap ? "Ctrl" : "Cmd";
      return part === "Alt" ? "Option" : part;
    }).join("+");
  }).join(" ");
}

/** The label a menu or tooltip shows for an action's first binding, e.g. "Ctrl+Shift+M" (Cmd+Shift+M on a Mac). */
export function shortcutLabel(id: string): string {
  const action = keyAction(id);
  return action.keys[0] ? keyLabel(action.keys[0], action.context) : "";
}
