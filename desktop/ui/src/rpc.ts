// The Work window's JSON-RPC client for `ling app-server` (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md
// §4.3). Codex's app-server speaks JSON-RPC without the `"jsonrpc"` field, one object per line;
// the bridge in src-tauri/src/bridge.rs carries the lines, and this matches answers to requests.
// Types come from src/protocol, generated from the pinned Codex (dreamference/chat/
// desktop_protocol_types.py), so a protocol change is a compile error here.

import type { ClientRequest } from "./protocol/ClientRequest";
import type { InitializeResponse } from "./protocol/InitializeResponse";
import type { RequestId } from "./protocol/RequestId";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { ServerRequest } from "./protocol/ServerRequest";
import type { ConfigReadResponse } from "./protocol/v2/ConfigReadResponse";
import type { GetAccountResponse } from "./protocol/v2/GetAccountResponse";
import type { PermissionProfileListResponse } from "./protocol/v2/PermissionProfileListResponse";
import type { ThreadCompactStartResponse } from "./protocol/v2/ThreadCompactStartResponse";
import type { ThreadListResponse } from "./protocol/v2/ThreadListResponse";
import type { ThreadReadResponse } from "./protocol/v2/ThreadReadResponse";
import type { ThreadResumeResponse } from "./protocol/v2/ThreadResumeResponse";
import type { ThreadSearchResponse } from "./protocol/v2/ThreadSearchResponse";
import type { ThreadRevertResponse } from "./protocol/v2/ThreadRevertResponse";
import type { ThreadStartResponse } from "./protocol/v2/ThreadStartResponse";
import type { TurnInterruptResponse } from "./protocol/v2/TurnInterruptResponse";
import type { TurnStartResponse } from "./protocol/v2/TurnStartResponse";
import type { TurnSteerResponse } from "./protocol/v2/TurnSteerResponse";

export type Method = ClientRequest["method"];
export type ParamsOf<M extends Method> = Extract<ClientRequest, { method: M }>["params"];

/** The answers this window reads; anything else comes back as `unknown`. */
interface Responses {
  "initialize": InitializeResponse;
  "account/read": GetAccountResponse;
  "config/read": ConfigReadResponse;
  "permissionProfile/list": PermissionProfileListResponse;
  "thread/start": ThreadStartResponse;
  "thread/resume": ThreadResumeResponse;
  "thread/read": ThreadReadResponse;
  "thread/list": ThreadListResponse;
  "thread/search": ThreadSearchResponse;
  "thread/compact/start": ThreadCompactStartResponse;
  "thread/revert": ThreadRevertResponse;
  "turn/start": TurnStartResponse;
  "turn/steer": TurnSteerResponse;
  "turn/interrupt": TurnInterruptResponse;
}
export type ResponseOf<M extends Method> = M extends keyof Responses ? Responses[M] : unknown;

/** How messages reach the server: the bridge in the app, a scripted stand-in in tests. */
export interface Transport {
  send(message: object): Promise<void>;
}

/** An error the server answered with. */
export class RpcError extends Error {
  readonly code: number;
  constructor(code: number, message: string) {
    super(message);
    this.code = code;
  }
}

interface Waiting {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
}

const key = (id: RequestId): string => JSON.stringify(id);

export class RpcClient {
  private nextId = 1;
  private readonly waiting = new Map<string, Waiting>();
  onNotification: (notification: ServerNotification) => void = () => {};
  onServerRequest: (request: ServerRequest) => void = () => {};
  private readonly transport: Transport;

  constructor(transport: Transport) {
    this.transport = transport;
  }

  /** Sends a request and resolves with the server's answer. */
  request<M extends Method>(method: M, params: ParamsOf<M>): Promise<ResponseOf<M>> {
    const id = this.nextId++;
    return new Promise<ResponseOf<M>>((resolve, reject) => {
      this.waiting.set(key(id), { resolve: resolve as (value: unknown) => void, reject });
      this.transport.send({ id, method, params }).catch((error: unknown) => {
        this.waiting.delete(key(id));
        reject(error instanceof Error ? error : new Error(String(error)));
      });
    });
  }

  /** The one notification a client sends: after `initialize` succeeds. */
  initialized(): Promise<void> {
    return this.transport.send({ method: "initialized" });
  }

  /** Answers a server request (an approval, a question). */
  respond(id: RequestId, result: unknown): Promise<void> {
    return this.transport.send({ id, result });
  }

  /** Refuses a server request this window does not handle. */
  respondError(id: RequestId, code: number, message: string): Promise<void> {
    return this.transport.send({ id, error: { code, message } });
  }

  /** Takes one message the server wrote. */
  receive(message: unknown): void {
    if (typeof message !== "object" || message === null) return;
    const m = message as Record<string, unknown>;
    if (typeof m.method === "string" && "id" in m) {
      this.onServerRequest(message as ServerRequest);
    } else if (typeof m.method === "string") {
      this.onNotification(message as ServerNotification);
    } else if ("id" in m) {
      const waiting = this.waiting.get(key(m.id as RequestId));
      if (!waiting) return;
      this.waiting.delete(key(m.id as RequestId));
      if ("error" in m && m.error) {
        const error = m.error as { code?: number; message?: string };
        waiting.reject(new RpcError(error.code ?? -1, error.message ?? "the server refused the request"));
      } else {
        waiting.resolve(m.result);
      }
    }
  }

  /** The server went away: every request still waiting fails with `reason`. */
  fail(reason: string): void {
    for (const waiting of this.waiting.values()) waiting.reject(new Error(reason));
    this.waiting.clear();
  }

  /** How many requests are still waiting for an answer. */
  get waitingCount(): number {
    return this.waiting.size;
  }
}
