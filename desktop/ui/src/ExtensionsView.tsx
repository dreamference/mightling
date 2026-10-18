// Skills, plugins, MCP servers, apps and hooks (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md
// §2.11): what a thread can use, read from the server (`skills/list`, `plugin/list`,
// `mcpServerStatus/list`, `app/list`, `hooks/list`), changed through the server: a skill turned
// off or on (`skills/config/write`), a plugin of a local or repository marketplace installed or
// removed (`plugin/install`, `plugin/uninstall`; the vendor's catalogue stays closed, patch 0015),
// an MCP server added, turned off or on and the servers read again (`config/value/write`,
// `config/mcpServer/reload`), an app turned off or on.

import { useCallback, useEffect, useState } from "react";

import { Icon } from "./icons";
import { ImportPanel } from "./ImportPanel";
import type { Listen } from "./turns";
import type { RpcClient } from "./rpc";
import type { AppInfo } from "./protocol/v2/AppInfo";
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
  httpOrigin: string | null;
}

/** Where a skill comes from, in words: the user's, the project's, another agent's linked in (SKILLS spec), the system's. */
export function skillSource(skill: Pick<SkillMetadata, "scope" | "path">): string {
  const fromAgent = skill.path.match(/[\\/]skills[\\/]from-([a-z0-9-]+)[\\/]/i);
  if (fromAgent) return `from ${fromAgent[1]}`;
  return { user: "yours", repo: "this project's", system: "built in", admin: "set by the machine" }[skill.scope] ?? skill.scope;
}

/** An MCP server's name as config.toml keys it. */
export const MCP_NAME = /^[A-Za-z0-9_-]{1,64}$/;

/** The config.toml value of a new MCP server: a command with its arguments, or a URL. */
export function mcpServerValue(kind: "command" | "url", target: string, args: string): Record<string, unknown> {
  if (kind === "url") return { url: target.trim() };
  const words = args.trim() ? args.trim().split(/\s+/) : [];
  return { command: target.trim(), args: words };
}

export function ExtensionsView(props: { client: RpcClient; listen: Listen; cwd: string | null; tab: ExtensionsTab; setTab: (tab: ExtensionsTab) => void; onClose: () => void; notice: (error: unknown) => void }) {
  const { client, cwd, notice } = props;
  const [skills, setSkills] = useState<SkillMetadata[] | null>(null);
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [apps, setApps] = useState<AppInfo[] | null>(null);
  const [marketplaces, setMarketplaces] = useState<PluginMarketplaceEntry[] | null>(null);
  const [hooks, setHooks] = useState<HooksListEntry[] | null>(null);
  const [adding, setAdding] = useState<{ name: string; kind: "command" | "url"; target: string; args: string } | null>(null);
  const [changed, setChanged] = useState(false);

  const load = useCallback(() => {
    client.request("skills/list", cwd ? { cwds: [cwd] } : {})
      .then((answer) => setSkills((answer as { data?: { skills: SkillMetadata[] }[] }).data?.flatMap((entry) => entry.skills) ?? []))
      .catch(() => setSkills([]));
    client.request("mcpServerStatus/list", {}).then((answer) => setServers((answer as { data?: McpServer[] }).data ?? [])).catch(() => setServers([]));
    client.request("app/list", {}).then((answer) => setApps((answer as { data?: AppInfo[] }).data ?? [])).catch(() => setApps([]));
    client.request("plugin/list", cwd ? { cwds: [cwd] } : {}).then((answer) => setMarketplaces((answer as { marketplaces?: PluginMarketplaceEntry[] }).marketplaces ?? [])).catch(() => setMarketplaces([]));
    client.request("hooks/list", cwd ? { cwds: [cwd] } : {}).then((answer) => setHooks((answer as { data?: HooksListEntry[] }).data ?? [])).catch(() => setHooks([]));
  }, [client, cwd]);
  useEffect(load, [load]);

  /** A change through the server, then everything read again. */
  const act = async (method: "skills/config/write" | "plugin/install" | "plugin/uninstall" | "config/mcpServer/reload", params: Record<string, unknown>) => {
    try {
      await client.request(method, params as never);
      setChanged(true);
      load();
    } catch (error) {
      notice(error);
    }
  };

  const write = async (keyPath: string, value: unknown) => {
    try {
      await client.request("config/value/write", { keyPath, value: value as never, mergeStrategy: "upsert" });
      setChanged(true);
      load();
    } catch (error) {
      notice(error);
    }
  };

  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog extensions" role="dialog" aria-label="Skills and tools" onClick={(event) => event.stopPropagation()}>
        <header><h2>Skills and tools</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <nav className="review-scopes" aria-label="Kinds">
          {(["skills", "plugins", "mcp", "apps", "hooks", "import"] as const).map((tab) => (
            <button key={tab} className={props.tab === tab ? "selected" : ""} aria-pressed={props.tab === tab} onClick={() => props.setTab(tab)}>
              {{ skills: "Skills", plugins: "Plugins", mcp: "MCP servers", apps: "Apps", hooks: "Hooks", import: "Import" }[tab]}
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
        {props.tab === "plugins" ? (
          <>
            <p className="dialog-note">Plugins from this machine's and this project's marketplaces. The vendor's own catalogue is closed in Mightling.</p>
            {marketplaces === null ? <p className="empty small">…</p> : marketplaces.every((market) => market.plugins.length === 0) ? <p className="empty small">No marketplace offers a plugin here.</p> : marketplaces.map((market) => (
              <section key={market.name} className="plugin-market">
                <h3>{market.name}</h3>
                <table className="shortcut-table">
                  <tbody>
                    {market.plugins.map((plugin) => (
                      <tr key={plugin.id}>
                        <td><strong>{plugin.name}</strong>{plugin.version ? <span className="planned-note"> {plugin.version}</span> : null}</td>
                        <td className="planned-note">{plugin.installed ? (plugin.enabled ? "installed" : "installed, off") : "not installed"}</td>
                        <td className="shortcut-actions">
                          {plugin.installed
                            ? <button className="link-button" onClick={() => void act("plugin/uninstall", { pluginId: plugin.id })}>Remove</button>
                            : <button className="link-button" disabled={!market.path} title={market.path ? "" : "Only local and repository marketplaces install here"} onClick={() => void act("plugin/install", { marketplacePath: market.path, pluginName: plugin.name })}>Install</button>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            ))}
          </>
        ) : null}
        {props.tab === "hooks" ? (
          <>
            <p className="dialog-note">Hooks run at points of a turn; listed here to read. Mightling's own ledger hook is among them.</p>
            {hooks === null ? <p className="empty small">…</p> : hooks.every((entry) => entry.hooks.length === 0) ? <p className="empty small">No hooks.</p> : (
              <table className="shortcut-table">
                <tbody>
                  {hooks.flatMap((entry) => entry.hooks).map((hook) => (
                    <tr key={hook.key}>
                      <td><code>{hook.eventName}</code>{hook.matcher ? <span className="planned-note"> {hook.matcher}</span> : null}</td>
                      <td>{hook.handlerType === "command" ? <code>{hook.command}</code> : hook.handlerType === "mcpTool" ? `${hook.server}.${hook.tool}` : hook.handlerType}</td>
                      <td className="planned-note">{hook.enabled ? "on" : "off"}{hook.pluginId ? ` · ${hook.pluginId}` : ""}</td>
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
                      <td>{server.toolsError ? `tools unavailable: ${server.toolsError}` : `${Object.keys(server.tools).length} tools`}</td>
                      <td className="planned-note">{server.runtimeStatus ?? "not started"}</td>
                      <td className="shortcut-actions">
                        {server.runtimeStatus === "disabled"
                          ? <button className="link-button" onClick={() => void write(`mcp_servers.${server.name}.enabled`, true)}>Turn on</button>
                          : <button className="link-button" onClick={() => void write(`mcp_servers.${server.name}.enabled`, false)}>Turn off</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {adding ? (
              <div className="mcp-add">
                <input value={adding.name} placeholder="name" aria-label="Server name" onChange={(event) => setAdding({ ...adding, name: event.target.value })} />
                <select value={adding.kind} aria-label="Server kind" onChange={(event) => setAdding({ ...adding, kind: event.target.value as "command" | "url" })}>
                  <option value="command">a command</option>
                  <option value="url">a URL</option>
                </select>
                <input className="script" value={adding.target} placeholder={adding.kind === "url" ? "http://127.0.0.1:8765/mcp" : "npx"} aria-label="Server command or URL" onChange={(event) => setAdding({ ...adding, target: event.target.value })} />
                {adding.kind === "command" ? <input className="script" value={adding.args} placeholder="arguments" aria-label="Server arguments" onChange={(event) => setAdding({ ...adding, args: event.target.value })} /> : null}
                <button className="primary" disabled={!MCP_NAME.test(adding.name) || !adding.target.trim()}
                  onClick={() => { void write(`mcp_servers.${adding.name}`, mcpServerValue(adding.kind, adding.target, adding.args)); setAdding(null); }}>Add</button>
                <button onClick={() => setAdding(null)}>Cancel</button>
              </div>
            ) : (
              <div className="buttons dialog-buttons">
                <button className="link-button" onClick={() => setAdding({ name: "", kind: "command", target: "", args: "" })}>Add an MCP server</button>
                <button className="link-button" onClick={() => void act("config/mcpServer/reload", {})}>Read the servers again</button>
              </div>
            )}
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
