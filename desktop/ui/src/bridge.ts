// The Work window's end of the app's main process (desktop/electron/src/main.ts): one object the
// preload exposes, `window.electronBridge.sendMessageFromView(message)`, and window `MessageEvent`s
// for what the main process sends — the Codex app's shape, so this page could also run in a
// browser against another transport. Nothing else in the UI touches Electron.

import type { Transport } from "./rpc";

export interface Started {
  served_model: string | null;
  started: boolean;
}

export interface WorkTarget {
  cwd: string | null;
  thread: string | null;
}

export interface Airgapped {
  level: "off" | "on";
  source: string;
}

/** What the preload exposes (desktop/electron/src/preload.ts). */
interface ElectronBridge {
  sendMessageFromView(message: unknown): Promise<unknown>;
}

declare global {
  interface Window {
    electronBridge?: ElectronBridge;
    mightlingWindowType?: string;
  }
}

function send<T>(message: object): Promise<T> {
  const bridge = window.electronBridge;
  if (!bridge) return Promise.reject(new Error("this page is not inside Mightling's desktop app"));
  return bridge.sendMessageFromView(message) as Promise<T>;
}

export const bridgeTransport: Transport = {
  send: (message: object) => send<void>({ type: "work/send", message }),
};

export const startServer = () => send<Started>({ type: "work/start" });
export const stopServer = () => send<void>({ type: "work/stop" });
export const openChat = () => send<void>({ type: "work/open-chat" });
export const workTarget = () => send<WorkTarget>({ type: "work/target" });
export const airgapped = (thread: string | null) => send<Airgapped>({ type: "work/airgapped", thread });
export const contextMenu = (x: number, y: number, editable: boolean, selection: string) =>
  send<void>({ type: "context-menu", x, y, editable, selection });
export const windowControl = (action: "minimize" | "maximize" | "close") => send<void>({ type: `window/${action}` });

export interface BridgeEvents {
  message: (message: unknown) => void;
  stderr: (line: string) => void;
  protocolError: (line: string) => void;
  exit: (code: number | null) => void;
  /** The system theme changed; the page may restyle. */
  theme?: (theme: "light" | "dark") => void;
}

/** Subscribes to the bridge's events; resolves with a function that unsubscribes them all. */
export async function listenBridge(events: BridgeEvents): Promise<() => void> {
  const onMessage = (event: MessageEvent) => {
    const data = event.data as { channel?: string; payload?: unknown } | null;
    if (!data || typeof data !== "object" || typeof data.channel !== "string") return;
    switch (data.channel) {
      case "work://message":
        return events.message(data.payload);
      case "work://stderr":
        return events.stderr(String(data.payload));
      case "work://protocol-error":
        return events.protocolError(String(data.payload));
      case "work://exit":
        return events.exit(typeof data.payload === "number" ? data.payload : null);
      case "theme":
        return events.theme?.(data.payload === "dark" ? "dark" : "light");
    }
  };
  window.addEventListener("message", onMessage);
  return () => window.removeEventListener("message", onMessage);
}
