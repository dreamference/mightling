// A stand-in for `ling app-server` for the UI's tests (composer.test.ts, review.test.ts): answers each
// request from a script, records what was sent, and can send notifications of its own. Imported only
// by tests; the app's bundle never reaches it.

import { RpcClient, type Transport } from "./rpc";
import type { ServerNotification } from "./protocol/ServerNotification";

export type Handler = (params: Record<string, unknown>, server: ScriptedServer) => unknown;

export class ScriptedServer implements Transport {
  sent: { method?: string; params?: Record<string, unknown> }[] = [];
  client: RpcClient;
  private readonly listeners = new Set<(notification: ServerNotification) => void>();

  constructor(private readonly script: Record<string, Handler>) {
    this.client = new RpcClient(this);
    this.client.onNotification = (notification) => this.listeners.forEach((listener) => listener(notification));
  }

  async send(message: object): Promise<void> {
    const m = message as { id?: number; method?: string; params?: Record<string, unknown> };
    this.sent.push({ method: m.method, params: m.params });
    if (m.id === undefined || !m.method) return;
    const handler = this.script[m.method];
    queueMicrotask(() => {
      if (!handler) return this.client.receive({ id: m.id, error: { code: -32601, message: `not scripted: ${m.method}` } });
      // A handler may answer later (a promise), as the server answers command/exec only at exit.
      Promise.resolve()
        .then(() => handler(m.params ?? {}, this))
        .then((result) => this.client.receive({ id: m.id, result }))
        .catch((error: unknown) => this.client.receive({ id: m.id, error: { code: -32600, message: String(error) } }));
    });
  }

  /** A notification from the server, delivered after the current answers. */
  notify(method: string, params: object): void {
    setTimeout(() => this.client.receive({ method, params }), 0);
  }

  /** What the window's own listeners subscribe through (turns.ts `Listen`). */
  listen = (listener: (notification: ServerNotification) => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  methods(): string[] {
    return this.sent.map((message) => message.method ?? "(answer)");
  }
}
