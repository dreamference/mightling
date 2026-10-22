// What the Mightling UI may say to `ling app-server`, enforced in the app's main process
// (specs/DREAMFERENCE_MIGHTLING_ASK.md §2.3, §3.2): a port of `ling-rs/web/src/policy.rs`, reading
// the same `policy.json` and held to the same conformance cases (`ling-rs/web/vectors/`), so the
// browser's host and the app's cannot drift.
//
// Everything the window sends passes `vetOutgoing`: a request outside the allow-list, a
// notification it may not send, or an answer to a server request that is not waiting is refused;
// a thread opener loses the fields that would replace Mightling's prompt, provider or policy, and a
// named prompt becomes `baseInstructions` here, never from the page's own text. An Ask thread
// (§3.1) also gets a fresh scratch folder as its `cwd` and a sandbox rooted there, whatever folder
// the page asked for. Plugins are allowed while `/airgapped` is off; at a configured `on` what
// fetches one is refused (`airgappedRefusedRequests`), an import loses its plugin items and every
// thread opener gets `airgappedThreadConfig`, so it starts without plugins
// (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md §14.12). No Electron here, so this tests on its own.

// Compiled in, as the Rust crate compiles it in: a running app cannot be pointed at a weaker file.
import policyFile from "../../../ling-rs/web/policy.json";

type JsonObject = Record<string, unknown>;

/** Where the policy gets what the page may only name: a prompt's composed text, and a new scratch folder for an Ask thread. */
export interface PromptSource {
  /** The system prompt a session under `name` receives, exactly as `ling prompt show <name> --composed` prints it. */
  composed(name: string): string;
  /** Creates a new, empty scratch folder for one Ask thread and returns its path. */
  newScratchFolder(): string;
  /** The scratch folder of an existing Ask thread, or null for any other thread. */
  scratchFolderOf(threadId: string): string | null;
  /** Whether the configured air-gap level is `on` (`ling airgapped`, no thread): installing is not one thread's act. */
  airgappedOn(): boolean;
}

/** The rules of `policy.json`. */
export interface Policy {
  allowedRequests: ReadonlySet<string>;
  allowedNotifications: ReadonlySet<string>;
  threadOpeners: ReadonlySet<string>;
  droppedThreadFields: readonly string[];
  promptField: string;
  promptOpeners: ReadonlySet<string>;
  namedPrompts: ReadonlySet<string>;
  scratchPrompts: ReadonlySet<string>;
  scratchFields: readonly string[];
  scratchSandbox: string;
  importRequest: string;
  refusedImportItems: ReadonlySet<string>;
  /** Requests refused at `on`: they fetch a plugin or a marketplace outside the sandbox. */
  airgappedRefusedRequests: ReadonlySet<string>;
  /** Import items dropped at `on`, beside `refusedImportItems`. */
  airgappedRefusedImportItems: ReadonlySet<string>;
  /** The `config` overrides a thread opener gets at `on`: no plugins. */
  airgappedThreadConfig: Readonly<JsonObject>;
  /** The `thread/start` field naming the window's own tools (the terminal's output, the preview's console); the policy writes their definitions as `dynamicTools`. */
  windowToolsField: string;
  windowTools: ReadonlyMap<string, unknown>;
}

const isObject = (value: unknown): value is JsonObject => typeof value === "object" && value !== null && !Array.isArray(value);

function strings(value: JsonObject, key: string): string[] {
  const list = value[key];
  if (!Array.isArray(list)) throw new Error(`policy.json: \`${key}\` must be a list`);
  return list.map((item) => {
    if (typeof item !== "string") throw new Error(`policy.json: \`${key}\` holds a non-string`);
    return item;
  });
}

function string(value: JsonObject, key: string): string {
  const item = value[key];
  if (typeof item !== "string") throw new Error(`policy.json: \`${key}\` must be a string`);
  return item;
}

/** Parses a policy file's contents, with the checks the Rust crate makes. */
export function parsePolicy(value: unknown): Policy {
  if (!isObject(value)) throw new Error("policy.json: not an object");
  const policy: Policy = {
    allowedRequests: new Set(strings(value, "allowedRequests")),
    allowedNotifications: new Set(strings(value, "allowedNotifications")),
    threadOpeners: new Set(strings(value, "threadOpeners")),
    droppedThreadFields: strings(value, "droppedThreadFields"),
    promptField: string(value, "promptField"),
    promptOpeners: new Set(strings(value, "promptOpeners")),
    namedPrompts: new Set(strings(value, "namedPrompts")),
    scratchPrompts: new Set(strings(value, "scratchPrompts")),
    scratchFields: strings(value, "scratchFields"),
    scratchSandbox: string(value, "scratchSandbox"),
    importRequest: string(value, "importRequest"),
    refusedImportItems: new Set(strings(value, "refusedImportItems")),
    airgappedRefusedRequests: new Set(strings(value, "airgappedRefusedRequests")),
    airgappedRefusedImportItems: new Set(strings(value, "airgappedRefusedImportItems")),
    airgappedThreadConfig: isObject(value.airgappedThreadConfig) ? value.airgappedThreadConfig : (() => { throw new Error("policy.json: `airgappedThreadConfig` must be an object"); })(),
    windowToolsField: string(value, "windowToolsField"),
    windowTools: new Map(Object.entries(isObject(value.windowTools) ? value.windowTools : (() => { throw new Error("policy.json: `windowTools` must be an object"); })()) as [string, unknown][]),
  };
  for (const [name, spec] of policy.windowTools) {
    if (!isObject(spec) || spec.name !== name) throw new Error(`policy.json: the window tool \`${name}\` must carry its own name`);
  }
  if (![...policy.scratchPrompts].every((name) => policy.namedPrompts.has(name))) {
    throw new Error("policy.json: every scratch prompt must also be a named prompt");
  }
  if (![...policy.airgappedRefusedRequests].every((method) => policy.allowedRequests.has(method))) {
    throw new Error("policy.json: every request refused at airgapped on must be allowed at off");
  }
  if (![...policy.promptOpeners].every((method) => policy.threadOpeners.has(method))) {
    throw new Error("policy.json: every prompt opener must also be a thread opener");
  }
  return policy;
}

/** The compiled-in policy. */
export const POLICY: Policy = parsePolicy(policyFile);

/**
 * The prompt a message names, when the policy will need its composed text: a prompt opener naming
 * an allowed prompt other than `default`. The host composes it before vetting, because composing
 * runs `ling` and vetting does not wait.
 */
export function namedPrompt(message: unknown, policy: Policy = POLICY): string | null {
  if (!isObject(message) || typeof message.method !== "string" || !policy.promptOpeners.has(message.method)) return null;
  const name = isObject(message.params) ? message.params[policy.promptField] : undefined;
  return typeof name === "string" && name !== "default" && policy.namedPrompts.has(name) ? name : null;
}

/**
 * Whether vetting a message depends on the air-gap level: a request refused at `on`, an import, or
 * a thread opener. The host asks `ling airgapped` beforehand, only for these.
 */
export function levelMatters(message: unknown, policy: Policy = POLICY): boolean {
  if (!isObject(message) || typeof message.method !== "string" || !("id" in message)) return false;
  const method = message.method;
  return policy.airgappedRefusedRequests.has(method) || policy.threadOpeners.has(method) || method === policy.importRequest;
}

/** Puts a thread in its scratch folder: whatever the page said about where it works and what it may write is replaced. */
function confine(policy: Policy, params: JsonObject, folder: string): void {
  for (const field of policy.scratchFields) delete params[field];
  params.cwd = folder;
  params.sandbox = policy.scratchSandbox;
}

function vetThreadParams(policy: Policy, original: JsonObject, prompts: PromptSource): JsonObject {
  const params: JsonObject = { ...original };
  for (const field of policy.droppedThreadFields) delete params[field];
  // A resumed or forked Ask thread stays where it was started.
  const existing = typeof params.threadId === "string" ? prompts.scratchFolderOf(params.threadId) : null;
  if (existing !== null) confine(policy, params, existing);
  if (policy.windowToolsField in params) {
    const names = params[policy.windowToolsField];
    delete params[policy.windowToolsField];
    if (!Array.isArray(names) || !names.every((name) => typeof name === "string")) throw new Error(`\`${policy.windowToolsField}\` must list tool names`);
    const tools = (names as string[]).map((name) => {
      const spec = policy.windowTools.get(name);
      if (spec === undefined) throw new Error(`no window tool named "${name}"`);
      return spec;
    });
    if (tools.length) params.dynamicTools = tools;
  }
  // At `on` the thread starts without plugins, whatever the page asked (`config` is dropped).
  if (prompts.airgappedOn() && Object.keys(policy.airgappedThreadConfig).length) params.config = { ...policy.airgappedThreadConfig };
  if (!(policy.promptField in params)) return params;
  const name = params[policy.promptField];
  delete params[policy.promptField];
  if (typeof name !== "string") throw new Error(`\`${policy.promptField}\` must name a prompt`);
  if (!policy.namedPrompts.has(name)) {
    throw new Error(`no prompt named "${name}" may be chosen here; named prompts: ${[...policy.namedPrompts].sort().join(", ")}`);
  }
  // `default` is what the launcher's catalog already carries: setting it again would only freeze
  // today's text into the thread.
  if (name !== "default") params.baseInstructions = prompts.composed(name);
  if (policy.scratchPrompts.has(name)) confine(policy, params, prompts.newScratchFolder());
  return params;
}

/**
 * Removes the items an import may not carry (`refusedImportItems`, and at `on`
 * `airgappedRefusedImportItems`: plugins, whose sources are fetched from the network); the others
 * are imported as asked. An item that does not name its type, or an import left with nothing to
 * do, is refused rather than guessed at.
 */
function vetImportParams(policy: Policy, original: JsonObject, airgappedOn: boolean): JsonObject {
  const items = original.migrationItems;
  if (!Array.isArray(items)) throw new Error("an import must list its migrationItems");
  const kept = items.filter((item) => {
    const kind = isObject(item) ? item.itemType : undefined;
    if (typeof kind !== "string") throw new Error("every migration item must name its itemType");
    return !policy.refusedImportItems.has(kind) && !(airgappedOn && policy.airgappedRefusedImportItems.has(kind));
  });
  if (kept.length === 0) {
    throw new Error(
      airgappedOn ? "nothing left to import: plugins are not imported while airgapped is on, since their sources are fetched from the network" : "nothing left to import",
    );
  }
  return { ...original, migrationItems: kept };
}

/**
 * Checks a message the page wants to send, removes or sets what only the policy decides, and
 * returns the message to forward. `pending` holds the ids (as JSON) of server requests not yet
 * answered; answering one removes it, and an answer to anything else is refused, so the page
 * cannot invent approvals.
 */
export function vetOutgoing(message: unknown, pending: Set<string>, prompts: PromptSource, policy: Policy = POLICY): JsonObject {
  if (!isObject(message)) throw new Error("a message must be a JSON object");
  const object: JsonObject = { ...message };
  delete object.jsonrpc;
  const method = typeof object.method === "string" ? object.method : undefined;
  const key = "id" in object ? JSON.stringify(object.id) : undefined;
  if (method !== undefined && key !== undefined) {
    if (!policy.allowedRequests.has(method)) throw new Error(`the Mightling UI does not send ${method}`);
    if (policy.airgappedRefusedRequests.has(method) && prompts.airgappedOn()) {
      throw new Error(`airgapped is on: ${method} fetches a plugin or a marketplace with git outside the sandbox. Run ling airgapped default off to install one.`);
    }
    const namesPrompt = isObject(object.params) && policy.promptField in object.params;
    if (namesPrompt && !policy.promptOpeners.has(method)) {
      throw new Error(`a prompt can only be chosen when a thread is started, not by ${method}`);
    }
    const namesTools = isObject(object.params) && policy.windowToolsField in object.params;
    if (namesTools && !policy.promptOpeners.has(method)) {
      throw new Error(`window tools can only be named when a thread is started, not by ${method}`);
    }
    if (policy.threadOpeners.has(method) && isObject(object.params)) {
      object.params = vetThreadParams(policy, object.params, prompts);
    }
    if (method === policy.importRequest) {
      object.params = vetImportParams(policy, isObject(object.params) ? object.params : {}, prompts.airgappedOn());
    }
    return object;
  }
  if (method !== undefined) {
    if (policy.allowedNotifications.has(method)) return object;
    throw new Error(`the Mightling UI does not send the notification ${method}`);
  }
  if (key !== undefined && ("result" in object || "error" in object)) {
    if (!pending.delete(key)) throw new Error(`no server request ${key} is waiting for an answer`);
    return object;
  }
  throw new Error("not a request, a notification or an answer");
}
