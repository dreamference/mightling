// The Work window's preload (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3): one object,
// `window.electronBridge`, through `contextBridge`, as the Codex app's preload exposes. Outgoing
// messages go through `ipcRenderer.invoke` on one channel; incoming ones are re-dispatched as
// window `MessageEvent`s, so the same page could also run in a browser. Nothing else of Electron
// or Node reaches the page: `sandbox: true`, `contextIsolation: true`, `nodeIntegration: false`.

import { contextBridge, ipcRenderer } from "electron";

import { CHANNEL_CHUNK_ACK, CHANNEL_FOR_VIEW, CHANNEL_FROM_VIEW, Reassembler, chunked, isChunk } from "./api";

const incoming = new Reassembler();

ipcRenderer.on(CHANNEL_FOR_VIEW, (_event, data: unknown) => {
  let message = data;
  if (isChunk(data)) {
    ipcRenderer.send(CHANNEL_CHUNK_ACK, data.__chunk.id, data.__chunk.index);
    message = incoming.take(data);
    if (message === undefined) return;
  }
  window.dispatchEvent(new MessageEvent("message", { data: message, origin: "mightling-desktop" }));
});

contextBridge.exposeInMainWorld("electronBridge", {
  /** Sends one message to the main process and resolves with its answer; large ones go in pieces, each acknowledged before the next. */
  async sendMessageFromView(message: unknown): Promise<unknown> {
    let answer: unknown;
    for (const piece of chunked(message)) answer = await ipcRenderer.invoke(CHANNEL_FROM_VIEW, piece);
    return answer;
  },
});
contextBridge.exposeInMainWorld("mightlingWindowType", "electron");

// The system theme, read at preload time so the page can style its first paint.
// The document element may not exist yet when the preload runs (seen on macOS: "Cannot read
// properties of null (reading 'dataset')"), so then it is set as soon as the document is parsed.
const theme = window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
const applyTheme = () => {
  if (document.documentElement) document.documentElement.dataset.theme = theme;
};
if (document.documentElement) applyTheme();
else document.addEventListener("DOMContentLoaded", applyTheme, { once: true });
