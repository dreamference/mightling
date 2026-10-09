// What crosses between Work's page and the main process, over the one channel the Codex app
// uses: `window.electronBridge.sendMessageFromView(message)` out, window `MessageEvent`s in
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3). `desktop/ui/src/bridge.ts` holds the page's
// copy of these shapes.

/** The channel names, as the Codex app names them: `<app>_desktop:message-from-view` and `-for-view`. */
export const CHANNEL_FROM_VIEW = "mightling_desktop:message-from-view";
export const CHANNEL_FOR_VIEW = "mightling_desktop:message-for-view";
export const CHANNEL_CHUNK_ACK = "mightling_desktop:chunk-ack";

/** Payloads bigger than this cross in pieces, each acknowledged before the next (the Codex app does the same). */
export const CHUNK_BYTES = 1 << 20;

/** What the page sends. `work/send` carries one protocol message for `ling app-server`. */
export type FromView =
  | { type: "work/start" }
  | { type: "work/send"; message: unknown }
  | { type: "work/stop" }
  | { type: "work/target" }
  | { type: "work/airgapped"; thread: string | null }
  /** An attachment for an Ask thread, written into its folder (ask.ts); the bytes base64. */
  | { type: "ask/upload"; thread: string; name: string; kind: "image" | "file"; data: string }
  /** The microphone's recording, for text (voice.ts); the bytes base64. */
  | { type: "voice/transcribe"; mime: string; data: string }
  | { type: "context-menu"; x: number; y: number; editable: boolean; selection: string }
  | { type: "window/minimize" }
  | { type: "window/maximize" }
  | { type: "window/close" };

/** What the page hears, re-dispatched by the preload as `MessageEvent`s whose `data` is one of these. */
export type ForView =
  | { channel: "work://message"; payload: unknown }
  | { channel: "work://stderr"; payload: string }
  | { channel: "work://protocol-error"; payload: string }
  | { channel: "work://exit"; payload: number | null }
  | { channel: "theme"; payload: "light" | "dark" }
  /** Show Ask or Work: the menu, a second instance or a link asked for one. */
  | { channel: "view"; payload: "ask" | "work" };

export interface Started {
  served_model: string | null;
  started: boolean;
  /** The folder Ask threads' scratch folders live in, canonical, as `ling web` answers it too. */
  ask_root: string | null;
}

export interface Airgapped {
  level: "off" | "on";
  source: string;
}

/** A piece of a payload too big for one message. */
export interface Chunk {
  __chunk: { id: string; index: number; total: number; part: string };
}

export const isChunk = (value: unknown): value is Chunk =>
  typeof value === "object" && value !== null && "__chunk" in value;

/** Splits a message into chunks when it is larger than `CHUNK_BYTES`; otherwise returns it alone. */
export function chunked(message: unknown, limit = CHUNK_BYTES): unknown[] {
  const text = JSON.stringify(message);
  if (text.length <= limit) return [message];
  const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const total = Math.ceil(text.length / limit);
  return Array.from({ length: total }, (_, index) => ({ __chunk: { id, index, total, part: text.slice(index * limit, (index + 1) * limit) } }));
}

/** Reassembles chunks; returns the whole message once the last piece is in, else `undefined`. */
export class Reassembler {
  private readonly parts = new Map<string, string[]>();

  take(chunk: Chunk): unknown {
    const { id, index, total, part } = chunk.__chunk;
    const parts = this.parts.get(id) ?? new Array<string>(total);
    parts[index] = part;
    this.parts.set(id, parts);
    for (let i = 0; i < total; i++) if (parts[i] === undefined) return undefined;
    this.parts.delete(id);
    return JSON.parse(parts.join(""));
  }
}
