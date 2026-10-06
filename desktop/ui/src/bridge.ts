// The Work window's end of src-tauri/src/bridge.rs: commands to start the server and send lines,
// events for what it writes. Nothing else in the UI touches Tauri.

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

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

export const bridgeTransport: Transport = {
  send: (message: object) => invoke<void>("work_send", { message }),
};

export const startServer = () => invoke<Started>("work_start");
export const stopServer = () => invoke<void>("work_stop");
export const openChat = () => invoke<void>("work_open_chat");
export const workTarget = () => invoke<WorkTarget>("work_target");
export const airgapped = (thread: string | null) => invoke<Airgapped>("work_airgapped", { thread });

export interface BridgeEvents {
  message: (message: unknown) => void;
  stderr: (line: string) => void;
  protocolError: (line: string) => void;
  exit: (code: number | null) => void;
}

/** Subscribes to the bridge's events; resolves with a function that unsubscribes them all. */
export async function listenBridge(events: BridgeEvents): Promise<() => void> {
  const unlisten: UnlistenFn[] = await Promise.all([
    listen<unknown>("work://message", (event) => events.message(event.payload)),
    listen<string>("work://stderr", (event) => events.stderr(event.payload)),
    listen<string>("work://protocol-error", (event) => events.protocolError(event.payload)),
    listen<number | null>("work://exit", (event) => events.exit(event.payload)),
  ]);
  return () => unlisten.forEach((stop) => stop());
}
