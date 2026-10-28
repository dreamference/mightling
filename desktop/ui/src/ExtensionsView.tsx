// Skills, plugins, MCP servers, apps and hooks (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md
// §2.11): what a thread can use, read from the server (`skills/list`, `plugin/list`,
// `mcpServerStatus/list`, `app/list`, `hooks/list`), changed through the server: a skill turned
// off or on (`skills/config/write`), a marketplace added from git or a folder (`marketplace/add`),
// a plugin installed or removed (`plugin/install`, `plugin/uninstall`; the vendor's catalogue stays
// closed, patch 0015, and at airgapped on the app's policy refuses installs: AIRGAPPED §14.12),
// an MCP server added, turned off or on and the servers read again (`config/value/write`,
// `config/mcpServer/reload`), an app turned off or on, a hook trusted or turned off (`config/batchWrite`
// on `hooks.state`, as the TUI does: Codex skips a hook that is not managed until its definition is
// trusted, and again once it changes; DESKTOP_PARITY §4.5 item 15). The Plugins tab is PluginsTab;
// an MCP server's environment, folder, headers and token variable, Remove and its startup status
// are DESKTOP §16 B34 (mcpservers.ts).

import { useCallback, useEffect, useState } from "react";

import { Icon } from "./icons";
import { ImportPanel } from "./ImportPanel";
import { McpBundles } from "./McpBundles";
import { mcpBundles } from "./bridge";
import type { Listen } from "./turns";
import type { RpcClient } from "./rpc";
import type { AppInfo } from "./protocol/v2/AppInfo";
import type { ConfigBatchWriteParams } from "./protocol/v2/ConfigBatchWriteParams";
import type { HookMetadata } from "./protocol/v2/HookMetadata";
import { offeredServers } from "./mcprecommended";
import { RULE_LABELS, ruleEdits, toolRules, type ToolRule } from "./mcptools";
import { bundleRemoval } from "./mcpbundleform";
import { configured, removable, serverEnabled, serverForm, serverValue, startupLabel, type McpServerForm, type Startup } from "./mcpservers";
import { McpServerAdd } from "./McpServerAdd";
import { PluginsTab } from "./PluginsTab";
import type { HooksListEntry } from "./protocol/v2/HooksListEntry";
import type { PluginMarketplaceEntry } from "./protocol/v2/PluginMarketplaceEntry";
import type { SkillMetadata } from "./protocol/v2/SkillMetadata";

export type ExtensionsTab = "skills" | "plugins" | "mcp" | "apps" | "hooks" | "import";

interface McpServer {
  name: string;
  runtimeStatus: string | null;
  tools: Record<string, unknown>;
  toolsError: string | null;
  authStatus: string;
  pluginId?: string | null;
  httpOrigin: string | null;
}

/** Where a skill comes from, in words: the user's, the project's, another agent's linked in (SKILLS spec), the system's. */
export function skillSource(skill: Pick<SkillMetadata, "scope" | "path">): string {
  const fromAgent = skill.path.match(/[\\/]skills[\\/]from-([a-z0-9-]+)[\\/]/i);
  if (fromAgent) return `from ${fromAgent[1]}`;
  return { user: "yours", repo: "this project's", system: "built in", admin: "set by the machine" }[skill.scope] ?? skill.scope;
}

export { MCP_NAME } from "./mcpservers";

/** Whether Codex skips this hook until it is reviewed: it is not managed and was never trusted, or changed since. */
export function hookNeedsReview(hook: Pick<HookMetadata, "trustStatus">): boolean {
  return hook.trustStatus === "untrusted" || hook.trustStatus === "modified";
}

/** A hook's trust state, in words. */
export function hookTrustLabel(hook: Pick<HookMetadata, "trustStatus">): string {
  return { managed: "managed", trusted: "trusted", untrusted: "not trusted yet: skipped", modified: "changed since it was trusted: skipped" }[hook.trustStatus] ?? hook.trustStatus;
}

/** What a hook runs: its command, the MCP tool it calls, or its kind. */
export function hookHandler(hook: HookMetadata): string {
  return hook.handlerType === "command" ? hook.command : hook.handlerType === "mcpTool" ? `${hook.server}.${hook.tool}` : hook.handlerType;
}

/** The hooks every listed folder shows, once each (a user hook is listed for every folder asked about). */
export function uniqueHooks(entries: HooksListEntry[]): HookMetadata[] {
  const seen = new Map<string, HookMetadata>();
  for (const hook of entries.flatMap((entry) => entry.hooks)) if (!seen.has(hook.key)) seen.set(hook.key, hook);
  return [...seen.values()];
}

/** The write that trusts these hooks as they are now: `hooks.state.<key>.trusted_hash` = their current hash, reloaded at once (the TUI's `write_hook_trusts`). */
export function hookTrustEdit(hooks: Pick<HookMetadata, "key" | "currentHash">[]): ConfigBatchWriteParams {
  const value = Object.fromEntries(hooks.map((hook) => [hook.key, { trusted_hash: hook.currentHash }]));
  return { edits: [{ keyPath: "hooks.state", value, mergeStrategy: "upsert" }], filePath: null, expectedVersion: null, reloadUserConfig: true };
}

/** The write that turns a hook off or on again: `hooks.state.<key>.enabled`. */
export function hookEnabledEdit(key: string, enabled: boolean): ConfigBatchWriteParams {
  return { edits: [{ keyPath: "hooks.state", value: { [key]: { enabled } }, mergeStrategy: "upsert" }], filePath: null, expectedVersion: null, reloadUserConfig: true };
}

export function ExtensionsView(props: { client: RpcClient; listen: Listen; cwd: string | null; tab: ExtensionsTab; setTab: (tab: ExtensionsTab) => void; onClose: () => void; notice: (error: unknown) => void; focusPlugin?: string | null; airgapped?: boolean }) {
  const { client, cwd, notice } = props;
  const [skills, setSkills] = useState<SkillMetadata[] | null>(null);
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [apps, setApps] = useState<AppInfo[] | null>(null);
  const [marketplaces, setMarketplaces] = useState<PluginMarketplaceEntry[] | null>(null);
  const [hooks, setHooks] = useState<HooksListEntry[] | null>(null);
  const [adding, setAdding] = useState<McpServerForm | null>(null);
  const [changed, setChanged] = useState(false);
  /** The servers installed from a bundle (B45): their Remove, which also deletes the folder, is under Installed from files. */
  const [bundles, setBundles] = useState<string[]>([]);
  /** What a thread last reported of each server's startup while this is open (`mcpServer/startupStatus/updated`). */
  const [startups, setStartups] = useState<Record<string, Startup>>({});
  /** The user's config as the server read it, for the rules per MCP tool (DESKTOP §16 B16). */
  const [config, setConfig] = useState<unknown>(null);
  const [toolsOf, setToolsOf] = useState<string | null>(null);

  const load = useCallback(() => {
    client.request("skills/list", cwd ? { cwds: [cwd] } : {})
      .then((answer) => setSkills((answer as { data?: { skills: SkillMetadata[] }[] }).data?.flatMap((entry) => entry.skills) ?? []))
      .catch(() => setSkills([]));
    client.request("mcpServerStatus/list", {}).then((answer) => setServers((answer as { data?: McpServer[] }).data ?? [])).catch(() => setServers([]));
    client.request("config/read", {}).then((answer) => setConfig((answer as { config?: unknown }).config ?? null)).catch(() => setConfig(null));
    client.request("app/list", {}).then((answer) => setApps((answer as { data?: AppInfo[] }).data ?? [])).catch(() => setApps([]));
    client.request("plugin/list", cwd ? { cwds: [cwd] } : {}).then((answer) => setMarketplaces((answer as { marketplaces?: PluginMarketplaceEntry[] }).marketplaces ?? [])).catch(() => setMarketplaces([]));
    client.request("hooks/list", cwd ? { cwds: [cwd] } : {}).then((answer) => setHooks((answer as { data?: HooksListEntry[] }).data ?? [])).catch(() => setHooks([]));
    if (mcpBundles.available()) mcpBundles.list().then((list) => setBundles(list.map((bundle) => bundle.name)), () => setBundles([]));
  }, [client, cwd]);
  useEffect(load, [load]);
  useEffect(() => props.listen((notification) => {
    if (notification.method !== "mcpServer/startupStatus/updated") return;
    const { name, status, error } = notification.params;
    setStartups((known) => ({ ...known, [name]: { status, error } }));
  }), [props.listen]);

  /** A change through the server, then everything read again. */
  const act = async (method: "skills/config/write" | "plugin/install" | "plugin/uninstall" | "marketplace/add" | "marketplace/remove" | "config/mcpServer/reload", params: Record<string, unknown>) => {
    try {
      await client.request(method, params as never);
      setChanged(true);
      load();
      return true;
    } catch (error) {
      notice(error);
      return false;
    }
  };

  /** A `config/batchWrite` (hooks trusted or turned off or on, a plugin turned off or on, a server removed), then everything read again. */
  const writeHooks = async (params: ConfigBatchWriteParams) => {
    try {
      await client.request("config/batchWrite", params);
      setChanged(true);
      load();
    } catch (error) {
      notice(error);
    }
  };
  const trust = (chosen: HookMetadata[]) => {
    const commands = chosen.map((hook) => `${hook.eventName}: ${hookHandler(hook)}`).join("\n");
    if (!window.confirm(`Trust ${chosen.length === 1 ? "this hook" : `these ${chosen.length} hooks`}? ${chosen.length === 1 ? "It runs" : "They run"} at points of every turn, as you, outside the sandbox:\n\n${commands}`)) return;
    void writeHooks(hookTrustEdit(chosen));
  };
  const listedHooks = hooks ? uniqueHooks(hooks) : [];
  const waiting = listedHooks.filter(hookNeedsReview);

  const write = async (keyPath: string, value: unknown) => {
    try {
      await client.request("config/value/write", { keyPath, value: value as never, mergeStrategy: "upsert" });
      setChanged(true);
      load();
    } catch (error) {
      notice(error);
    }
  };

  /** Remove, after a confirmation: `mcp_servers.<name>` taken out of config.toml (B34). */
  const removeServer = (name: string) => {
    if (!window.confirm(`Remove the MCP server ${name}? Its entry leaves config.toml, with its settings.`)) return;
    if (toolsOf === name) setToolsOf(null);
    void writeHooks(bundleRemoval(name));
  };
  const addServer = (form: McpServerForm) => {
    void write(`mcp_servers.${form.name}`, serverValue(form));
    setAdding(null);
  };

  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog extensions" role="dialog" aria-label="Skills and tools" onClick={(event) => event.stopPropagation()}>
        <header><h2>Skills and tools</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <nav className="review-scopes" aria-label="Kinds">
          {(["skills", "plugins", "mcp", "apps", "hooks", "import"] as const).map((tab) => (
            <button key={tab} className={props.tab === tab ? "selected" : ""} aria-pressed={props.tab === tab} onClick={() => props.setTab(tab)}>
              {{ skills: "Skills", plugins: "Plugins", mcp: "MCP servers", apps: "Apps", hooks: waiting.length ? `Hooks (${waiting.length} to review)` : "Hooks", import: "Import" }[tab]}
            </button>
          ))}
        </nav>
        {changed ? <div className="notice">Saved to config.toml: new threads use it; a running thread keeps what it started with.</div> : null}
        {props.tab === "import" ? <ImportPanel client={client} listen={props.listen} cwd={cwd} notice={notice} onImported={() => { setChanged(true); load(); }} /> : null}
        {props.tab === "skills" ? (
          <>
            <p className="dialog-note">Skills{cwd ? ` for ${cwd}` : ""}: type $ in the composer to use one. Skills of other agents are linked in at every start (~/.mightling/skills/from-…).</p>
            {skills === null ? <p className="empty small">…</p> : skills.length === 0 ? <p className="empty small">No skills.</p> : (
              <table className="shortcut-table">
                <tbody>
                  {skills.map((skill) => (
                    <tr key={skill.path}>
                      <td><code>${skill.name}</code>{skill.enabled ? "" : <span className="planned-note"> (off)</span>}</td>
                      <td>{skill.shortDescription || skill.description}</td>
                      <td className="planned-note">{skillSource(skill)}</td>
                      <td className="shortcut-actions"><button className="link-button" onClick={() => void act("skills/config/write", { path: skill.path, enabled: !skill.enabled })}>{skill.enabled ? "Turn off" : "Turn on"}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        ) : null}
        {props.tab === "plugins" ? <PluginsTab client={client} marketplaces={marketplaces} focusPlugin={props.focusPlugin} act={act} writeConfig={writeHooks} notice={notice} /> : null}
        {props.tab === "hooks" ? (
          <>
            <p className="dialog-note">Hooks run at points of a turn, as you and outside the sandbox. One that is not managed runs only once you trust it as it is now, and again after it changes; Mightling's own ledger hook is among them.</p>
            {waiting.length ? (
              <div className="notice" role="status">
                {waiting.length === 1 ? "One hook is" : `${waiting.length} hooks are`} skipped until you review and trust {waiting.length === 1 ? "it" : "them"}.
                {waiting.length > 1 ? <> <button className="link-button" onClick={() => trust(waiting)}>Trust all {waiting.length}…</button></> : null}
              </div>
            ) : null}
            {hooks === null ? <p className="empty small">…</p> : listedHooks.length === 0 ? <p className="empty small">No hooks.</p> : (
              <table className="shortcut-table">
                <tbody>
                  {listedHooks.map((hook) => (
                    <tr key={hook.key} className={hookNeedsReview(hook) ? "hook-review" : undefined}>
                      <td><code>{hook.eventName}</code>{hook.matcher ? <span className="planned-note"> {hook.matcher}</span> : null}</td>
                      <td><code>{hookHandler(hook)}</code><div className="planned-note" title={hook.sourcePath}>{hook.pluginId ? `plugin ${hook.pluginId}` : hook.sourcePath}</div></td>
                      <td className="planned-note">{hook.enabled ? "on" : "off"} · {hookTrustLabel(hook)}</td>
                      <td>
                        {hookNeedsReview(hook) ? <button onClick={() => trust([hook])} aria-label={`Trust ${hookHandler(hook)}`}>Trust…</button> : null}
                        {hook.isManaged ? null : <button onClick={() => void writeHooks(hookEnabledEdit(hook.key, !hook.enabled))}>{hook.enabled ? "Turn off" : "Turn on"}</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        ) : null}
        {props.tab === "mcp" ? (
          <>
            <p className="dialog-note">MCP servers give the agent tools. They run outside airgapped's reach, as ling airgapped says: add only servers you trust.</p>
            {servers === null ? <p className="empty small">…</p> : servers.length === 0 ? <p className="empty small">No MCP servers are configured.</p> : (
              <table className="shortcut-table">
                <tbody>
                  {servers.map((server) => (
                    <tr key={server.name}>
                      <td><strong>{server.name}</strong>{server.httpOrigin ? <span className="planned-note"> {server.httpOrigin}</span> : null}</td>
                      <td>{server.toolsError ? `tools unavailable: ${server.toolsError}` : (
                        <button className="link-button" aria-expanded={toolsOf === server.name} onClick={() => setToolsOf(toolsOf === server.name ? null : server.name)}>{Object.keys(server.tools).length} tools</button>
                      )}</td>
                      <td className="planned-note">{startupLabel(server, serverEnabled(config, server.name) && server.runtimeStatus !== "disabled", startups[server.name])}</td>
                      <td className="shortcut-actions">
                        {server.runtimeStatus === "disabled" || !serverEnabled(config, server.name)
                          ? <button className="link-button" onClick={() => void write(`mcp_servers.${server.name}.enabled`, true)}>Turn on</button>
                          : <button className="link-button" onClick={() => void write(`mcp_servers.${server.name}.enabled`, false)}>Turn off</button>}
                        {removable(server, config, bundles) ? <button className="link-button" aria-label={`Remove ${server.name}`} onClick={() => removeServer(server.name)}>Remove</button> : null}
                      </td>
                    </tr>
                  )).flatMap((row, index) => {
                    const server = servers[index];
                    if (toolsOf !== server.name) return [row];
                    const rules = toolRules(config, server.name, Object.keys(server.tools));
                    return [row, (
                      <tr key={`${server.name}-tools`} className="mcp-tools-row">
                        <td colSpan={4}>
                          {rules.length === 0 ? <p className="empty small">This server offers no tools.</p> : (
                            <table className="mcp-tools" aria-label={`${server.name}'s tools`}>
                              <tbody>
                                {rules.map((entry) => (
                                  <tr key={entry.tool}>
                                    <td><code>{entry.tool}</code></td>
                                    <td>
                                      <select aria-label={`When ${entry.tool} runs`} value={entry.rule} disabled={!entry.editable} title={entry.editable ? undefined : "This name cannot be written as a setting"}
                                        onChange={(event) => void writeHooks({ edits: ruleEdits(config, server.name, entry.tool, event.target.value as ToolRule), reloadUserConfig: true })}>
                                        {(Object.keys(RULE_LABELS) as ToolRule[]).map((rule) => <option key={rule} value={rule}>{RULE_LABELS[rule]}</option>)}
                                      </select>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                          <p className="planned-note">Always allow runs the tool without asking; Ask each time asks before every call; Off takes it away from the agent. Kept in ~/.mightling/config.toml.</p>
                        </td>
                      </tr>
                    )];
                  })}
                </tbody>
              </table>
            )}
            {adding ? (
              <>
              <McpServerAdd form={adding} setForm={setAdding} taken={(name) => configured(config, name) || (servers ?? []).some((server) => server.name === name)} onAdd={() => addServer(adding)} onCancel={() => setAdding(null)} />
              {/* Recommended servers (DESKTOP §16 B26): a click fills the form in; nothing installs until Add. */}
              {props.airgapped ? (
                <p className="dialog-note">Recommended servers are not offered while airgapped is on: a package runner fetches each of them from the network.</p>
              ) : (
                <div className="mcp-recommended" role="list" aria-label="Recommended MCP servers">
                  {offeredServers(false, (servers ?? []).map((server) => server.name)).map((server) => (
                    <div key={server.name} role="listitem" className="mcp-recommended-entry">
                      <button className="link-button" onClick={() => setAdding(serverForm({ name: server.name, target: server.command, args: server.args }))}>{server.title}</button>
                      <span className="small">{server.description} <span className="needs-network" title={server.network}>Needs the network: {server.network}.</span></span>
                    </div>
                  ))}
                </div>
              )}
              </>
            ) : (
              <div className="buttons dialog-buttons">
                <button className="link-button" onClick={() => setAdding(serverForm())}>Add an MCP server</button>
                <button className="link-button" onClick={() => void act("config/mcpServer/reload", {})}>Read the servers again</button>
              </div>
            )}
            {mcpBundles.available() ? <McpBundles client={client} config={config} airgapped={props.airgapped} notice={notice} onChanged={() => { setChanged(true); load(); }} /> : null}
          </>
        ) : null}
        {props.tab === "apps" ? (
          <>
            <p className="dialog-note">Apps are Mightling's own connectors (Gmail, Drive …), signed in on this machine; their tools only read.</p>
            {apps === null ? <p className="empty small">…</p> : apps.length === 0 ? <p className="empty small">No apps are set up. `ling apps` sets one up.</p> : (
              <table className="shortcut-table">
                <tbody>
                  {apps.map((app) => (
                    <tr key={app.id}>
                      <td><strong>{app.name}</strong></td>
                      <td>{app.description}</td>
                      <td className="planned-note">{app.isAccessible ? "signed in" : "not signed in"}</td>
                      <td className="shortcut-actions">
                        <button className="link-button" onClick={() => void write(`apps.${app.id}.enabled`, !app.isEnabled)}>{app.isEnabled ? "Turn off" : "Turn on"}</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        ) : null}
      </section>
    </div>
  );
}
