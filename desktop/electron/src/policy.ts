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
// (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md §14.12). The composer's `!` command
// (`thread/shellCommand`, run in the user's shell with no sandbox) is refused the same way, judged
// by the level of the thread it names (`airgapThread`; §14.13). No Electron here, so this tests on its own.

// Compiled in, as the Rust crate compiles it in: a running app cannot be pointed at a weaker file.
import policyFile from "../../../ling-rs/web/policy.json";

type JsonObject = Record<string, unknown>;

/** Where the policy gets what the page may only name: a prompt's composed text, and a new scratch folder for an Ask thread. */
export interface PromptSource {
  /** The system prompt a session under `name` receives, exactly as `ling prompt show <name> --composed` prints it. */
  composed(name: string): string;
  /** Creates a new, empty scratch folder for one Ask thread and returns its path. */
  newScratchFolder(prompt: string): string;
  /** The scratch folder of an existing Ask thread, or null for any other thread. */
  scratchFolderOf(threadId: string): string | null;
  /**
   * Whether the air-gap level is `on`, as the launcher resolves it: for the thread `threadId` names,
   * or the configured level (`ling airgapped`, no thread) for null. Installing is not one thread's
   * act and a thread being opened has no level yet, so both ask with null; a `!` command asks for its
   * thread. An implementation that cannot tell answers true.
   */
  airgappedOn(threadId: string | null): boolean;
  /** Whether `cwd` is an existing checkout recorded in `$CODEX_HOME/chat-repos/index.json` under `$CODEX_HOME/chat-repos/`: `readOnly` when recorded, `null` otherwise. */
  repoCheckout(cwd: string): boolean | null;
  /** The recorded checkout `cwd` when `threadId` is in `app_threads` of a checkout in `$CODEX_HOME/chat-repos/index.json`, or `null` otherwise. */
  repoThreadCwd(threadId: string): string | null;
  /** Whether `threadId` is a repository thread whose checkout is currently granted Full Access. */
  repoThreadFullAccess(threadId: string): boolean;
  /** Whether the caller is a paired phone bridge device or bridge session rather than the owner's browser session or desktop app. */
  isBridge(): boolean;
  /**
   * Whether `cwd` is a folder a Work thread may work in: an existing directory, named by an absolute
   * path, outside the Ask folders' root. A fork of an Ask thread may move there (a project's primary
   * folder, DESKTOP §16 B33). Without it, or when it cannot tell, the answer is no.
   */
  workFolder?(cwd: string): boolean;
  /**
   * The workspace sandbox the policy writes on a repository thread's `turn/start` without Full
   * Access, as the thread would get it from `sandbox: "workspace-write"`: the user's
   * `[sandbox_workspace_write]`, with no network while the thread's air gap is on. Without it, or
   * when it cannot tell, no network and no extra roots.
   */
  workspaceSandbox?(threadId: string): WorkspaceSandbox;
}

/** What a `workspaceWrite` sandbox policy holds beside its type (the app-server's `SandboxPolicy`). */
export interface WorkspaceSandbox {
  networkAccess: boolean;
  writableRoots: string[];
  excludeTmpdirEnvVar: boolean;
  excludeSlashTmp: boolean;
}

const NO_WORKSPACE_EXTRAS: WorkspaceSandbox = { networkAccess: false, writableRoots: [], excludeTmpdirEnvVar: false, excludeSlashTmp: false };

/**
 * The `sandboxPolicy` for a turn: `readOnly` in a read-only checkout, else `workspaceWrite`. The
 * read-only sandbox takes the same network setting, as the launcher gives it (patch 0031).
 */
function turnSandboxPolicy(workspace: WorkspaceSandbox, readOnly: boolean): JsonObject {
  if (readOnly) return { type: "readOnly", networkAccess: workspace.networkAccess };
  return {
    type: "workspaceWrite",
    writableRoots: [...workspace.writableRoots],
    networkAccess: workspace.networkAccess,
    excludeTmpdirEnvVar: workspace.excludeTmpdirEnvVar,
    excludeSlashTmp: workspace.excludeSlashTmp,
  };
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
  repoPrompts: ReadonlySet<string>;
  repoThreadParams: Readonly<JsonObject>;
  repoReadOnlyThreadParams: Readonly<JsonObject>;
  pinnedTurnFields: readonly string[];
  scratchFields: readonly string[];
  scratchSandbox: string;
  /** The request that forks a thread: an Ask thread's fork may leave its folder for a Work folder. */
  forkRequest: string;
  importRequest: string;
  refusedImportItems: ReadonlySet<string>;
  /** Requests refused at `on`: they fetch a plugin or a marketplace outside the sandbox, or run a `!` command with no sandbox. */
  airgappedRefusedRequests: ReadonlySet<string>;
  /** Why a request is refused at `on`, for those with a reason of their own. */
  airgappedRefusalReasons: ReadonlyMap<string, string>;
  /** Import items dropped at `on`, beside `refusedImportItems`. */
  airgappedRefusedImportItems: ReadonlySet<string>;
  /** The `config` overrides a thread opener gets at `on`: no plugins. */
  airgappedThreadConfig: Readonly<JsonObject>;
  /** The `config` a thread of a named prompt gets, started or resumed (a phone's: no image search). */
  promptThreadConfig: ReadonlyMap<string, Readonly<JsonObject>>;
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

function reasons(value: JsonObject, key: string): Map<string, string> {
  const table = value[key];
  if (!isObject(table)) throw new Error(`policy.json: \`${key}\` must be an object`);
  return new Map(Object.entries(table).map(([method, reason]) => {
    if (typeof reason !== "string") throw new Error(`policy.json: the reason \`${method}\` is refused at on must be a string`);
    return [method, reason];
  }));
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
    repoPrompts: new Set(strings(value, "repoPrompts")),
    repoThreadParams: isObject(value.repoThreadParams) ? value.repoThreadParams : (() => { throw new Error("policy.json: `repoThreadParams` must be an object"); })(),
    repoReadOnlyThreadParams: isObject(value.repoReadOnlyThreadParams) ? value.repoReadOnlyThreadParams : (() => { throw new Error("policy.json: `repoReadOnlyThreadParams` must be an object"); })(),
    pinnedTurnFields: strings(value, "pinnedTurnFields"),
    scratchFields: strings(value, "scratchFields"),
    scratchSandbox: string(value, "scratchSandbox"),
    forkRequest: string(value, "forkRequest"),
    importRequest: string(value, "importRequest"),
    refusedImportItems: new Set(strings(value, "refusedImportItems")),
    airgappedRefusedRequests: new Set(strings(value, "airgappedRefusedRequests")),
    airgappedRefusedImportItems: new Set(strings(value, "airgappedRefusedImportItems")),
    airgappedRefusalReasons: reasons(value, "airgappedRefusalReasons"),
    airgappedThreadConfig: isObject(value.airgappedThreadConfig) ? value.airgappedThreadConfig : (() => { throw new Error("policy.json: `airgappedThreadConfig` must be an object"); })(),
    promptThreadConfig: new Map(
      Object.entries(value.promptThreadConfig === undefined ? {} : isObject(value.promptThreadConfig) ? value.promptThreadConfig : (() => { throw new Error("policy.json: `promptThreadConfig` must be an object"); })()).map(([name, config]) => {
        if (!isObject(config)) throw new Error(`policy.json: the config of the prompt \`${name}\` must be an object`);
        return [name, config] as [string, JsonObject];
      }),
    ),
    windowToolsField: string(value, "windowToolsField"),
    windowTools: new Map(Object.entries(isObject(value.windowTools) ? value.windowTools : (() => { throw new Error("policy.json: `windowTools` must be an object"); })()) as [string, unknown][]),
  };
  for (const [name, spec] of policy.windowTools) {
    if (!isObject(spec) || spec.name !== name) throw new Error(`policy.json: the window tool \`${name}\` must carry its own name`);
  }
  if (![...policy.scratchPrompts].every((name) => policy.namedPrompts.has(name))) {
    throw new Error("policy.json: every scratch prompt must also be a named prompt");
  }
  if (![...policy.repoPrompts].every((name) => policy.namedPrompts.has(name))) {
    throw new Error("policy.json: every repo prompt must also be a named prompt");
  }
  if ([...policy.repoPrompts].some((name) => policy.scratchPrompts.has(name))) {
    throw new Error("policy.json: a repo prompt cannot also be a scratch prompt");
  }
  if (![...policy.airgappedRefusedRequests].every((method) => policy.allowedRequests.has(method))) {
    throw new Error("policy.json: every request refused at airgapped on must be allowed at off");
  }
  for (const method of policy.airgappedRefusalReasons.keys()) {
    if (!policy.airgappedRefusedRequests.has(method)) throw new Error(`policy.json: \`${method}\` has a reason to be refused at on but is not refused there`);
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
 * The thread whose air-gap level decides a request refused at `on`, when it names one (a `!`
 * command names its thread; an install does not, and takes the configured level). The host
 * resolves that thread's level before vetting, failing closed.
 */
export function airgapThread(message: unknown, policy: Policy = POLICY): string | null {
  if (!isObject(message) || typeof message.method !== "string" || !("id" in message) || !policy.airgappedRefusedRequests.has(message.method)) return null;
  return threadIdOf(message);
}

function threadIdOf(message: JsonObject): string | null {
  const thread = isObject(message.params) ? message.params.threadId : undefined;
  return typeof thread === "string" ? thread : null;
}

/**
 * The thread a message from the app-server describes and the folder it works in, if it carries one
 * (`thread/started`, and the answers to `thread/start|resume|fork|read`): the host keeps it, so a
 * thread's level is read with its own repository's configuration file.
 */
export function threadPlace(value: unknown): { thread: string; cwd: string } | null {
  if (!isObject(value)) return null;
  const holder = isObject(value.params) && isObject(value.params.thread) ? value.params.thread : isObject(value.result) && isObject(value.result.thread) ? value.result.thread : null;
  if (!holder || typeof holder.id !== "string" || typeof holder.cwd !== "string") return null;
  return { thread: holder.id, cwd: holder.cwd };
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

/** The prompt with a config of its own whose threads live in `folder` (`<prompt>-<hex>`). */
function promptOfFolder(policy: Policy, folder: string): string | undefined {
  const base = folder.split("/").pop() ?? folder;
  return [...policy.promptThreadConfig.keys()].find((prompt) => base.startsWith(`${prompt}-`));
}

/** Puts a thread in its scratch folder: whatever the page said about where it works and what it may write is replaced. */
function confine(policy: Policy, params: JsonObject, folder: string): void {
  for (const field of policy.scratchFields) delete params[field];
  params.cwd = folder;
  params.sandbox = policy.scratchSandbox;
}

function vetThreadParams(policy: Policy, method: string, original: JsonObject, prompts: PromptSource): JsonObject {
  const params: JsonObject = { ...original };
  for (const field of policy.droppedThreadFields) delete params[field];
  // A resumed or forked Ask thread stays where it was started, unless a fork continues it in a Work
  // folder (a project's, DESKTOP §16 B33): a Work thread there, with the sandbox an Ask folder gets
  // and nothing broader, and without its prompt's config.
  let existing = typeof params.threadId === "string" ? prompts.scratchFolderOf(params.threadId) : null;
  const moved = existing !== null && method === policy.forkRequest && typeof params.cwd === "string" && prompts.workFolder?.(params.cwd) === true ? params.cwd : null;
  if (moved !== null) existing = null;
  if (moved !== null || existing !== null) confine(policy, params, moved ?? (existing as string));
  // A resumed or forked chat-work thread stays in its recorded checkout with its sandbox and on-request approvals.
  const existingRepoCwd = typeof params.threadId === "string" ? prompts.repoThreadCwd(params.threadId) : null;
  if (existingRepoCwd !== null) {
    for (const field of policy.scratchFields) delete params[field];
    params.cwd = existingRepoCwd;
    const readOnly = prompts.repoCheckout(existingRepoCwd) === true;
    Object.assign(params, readOnly ? policy.repoReadOnlyThreadParams : policy.repoThreadParams);
  }
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
  let config: JsonObject = prompts.airgappedOn(null) ? { ...policy.airgappedThreadConfig } : {};
  // A resumed thread of a prompt with a config of its own is known by its folder's name.
  const resumedAs = existing === null ? undefined : promptOfFolder(policy, existing);
  if (resumedAs !== undefined) config = { ...config, ...policy.promptThreadConfig.get(resumedAs) };
  if (existingRepoCwd !== null) {
    for (const prompt of policy.repoPrompts) {
      if (policy.promptThreadConfig.has(prompt)) config = { ...config, ...policy.promptThreadConfig.get(prompt) };
    }
  }
  const named = policy.promptField in params;
  const name = params[policy.promptField];
  delete params[policy.promptField];
  if (named && typeof name !== "string") throw new Error(`\`${policy.promptField}\` must name a prompt`);
  if (typeof name === "string" && !policy.namedPrompts.has(name)) {
    throw new Error(`no prompt named "${name}" may be chosen here; named prompts: ${[...policy.namedPrompts].sort().join(", ")}`);
  }
  if (
    policy.promptOpeners.has(method) &&
    prompts.isBridge() &&
    !(typeof name === "string" && (policy.scratchPrompts.has(name) || policy.repoPrompts.has(name)))
  ) {
    throw new Error("a phone bridge starts only Ask threads or chat-work threads in a forge checkout");
  }
  if (
    !policy.promptOpeners.has(method) &&
    prompts.isBridge() &&
    existing === null &&
    existingRepoCwd === null
  ) {
    throw new Error("a phone bridge resumes only Ask threads or chat-work threads");
  }
  if (typeof name === "string" && policy.promptThreadConfig.has(name)) config = { ...config, ...policy.promptThreadConfig.get(name) };
  if (Object.keys(config).length) params.config = config;
  if (typeof name !== "string") return params;
  if (policy.repoPrompts.has(name)) {
    const readOnly = typeof params.cwd === "string" ? prompts.repoCheckout(params.cwd) : null;
    if (readOnly === null) {
      throw new Error("a chat-work thread starts only in a checkout ling web made for it");
    }
    // Its checkout is its only place and the pinned sandbox its only one: no permissions profile or
    // extra workspace roots asked beside them.
    for (const field of policy.scratchFields) if (field !== "cwd") delete params[field];
    Object.assign(params, readOnly ? policy.repoReadOnlyThreadParams : policy.repoThreadParams);
  }
  // `default` is what the launcher's catalog already carries: setting it again would only freeze
  // today's text into the thread.
  if (name !== "default") params.baseInstructions = prompts.composed(name);
  if (policy.scratchPrompts.has(name)) confine(policy, params, prompts.newScratchFolder(name as string));
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
  // The app-server also reads a list of params, field by field in declaration order, so a list
  // would carry `sandbox` or `baseInstructions` past every rule below, which read only an object's
  // fields. Absent or null is left to the app-server, which refuses what needs params.
  if (object.params !== undefined && object.params !== null && !isObject(object.params)) {
    throw new Error("a message's params must be a JSON object");
  }
  const method = typeof object.method === "string" ? object.method : undefined;
  const key = "id" in object ? JSON.stringify(object.id) : undefined;
  if (method !== undefined && key !== undefined) {
    if (!policy.allowedRequests.has(method)) throw new Error(`the Mightling UI does not send ${method}`);
    if (policy.airgappedRefusedRequests.has(method) && prompts.airgappedOn(threadIdOf(object))) {
      throw new Error(policy.airgappedRefusalReasons.get(method) ?? `airgapped is on: ${method} fetches a plugin or a marketplace with git outside the sandbox. Run ling airgapped default off to install one.`);
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
      object.params = vetThreadParams(policy, method, object.params, prompts);
    }
    if (method === "turn/start" && isObject(object.params)) {
      const params: JsonObject = { ...object.params };
      const threadId = typeof params.threadId === "string" ? params.threadId : null;
      const repoCwd = threadId !== null ? prompts.repoThreadCwd(threadId) : null;
      const isRepo = repoCwd !== null;
      // An Ask thread keeps the folder and sandbox it was started with: a turn's `sandboxPolicy`
      // would hold for the turns after it.
      const isAsk = threadId !== null && prompts.scratchFolderOf(threadId) !== null;
      if (prompts.isBridge() || isRepo || isAsk) {
        for (const field of policy.pinnedTurnFields) delete params[field];
      }
      // A repository turn's sandbox is written on every turn, never left to the thread: a turn's
      // `sandboxPolicy` holds for the turns after it. Full Access only for a turn from the page
      // (never a messenger bridge's) on a writable checkout whose grant still stands; otherwise the sandbox
      // `workspace-write` gives the thread, from the user's own configuration (network on unless the
      // air gap is), read-only in a read-only checkout.
      if (threadId !== null && repoCwd !== null) {
        const readOnly = prompts.repoCheckout(repoCwd) === true;
        params.sandboxPolicy =
          !readOnly && !prompts.isBridge() && prompts.repoThreadFullAccess(threadId)
            ? { type: "dangerFullAccess" }
            : turnSandboxPolicy(prompts.workspaceSandbox?.(threadId) ?? NO_WORKSPACE_EXTRAS, readOnly);
      }
      object.params = params;
    }
    if (method === policy.importRequest) {
      object.params = vetImportParams(policy, isObject(object.params) ? object.params : {}, prompts.airgappedOn(null));
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
