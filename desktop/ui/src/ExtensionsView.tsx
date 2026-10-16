// Skills, MCP servers and apps (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.11): what a thread
// can use, read from the server (`skills/list`, `mcpServerStatus/list`, `app/list`), with the changes
// the policy already allows written to config.toml through the server (`config/value/write`): an MCP
// server added, turned off or on, an app turned off or on. Turning a skill off, plugins, reloading MCP
// servers and the hooks list need methods that wait for policy.json (tests/test_desktop_parity.py).

import { useCallback, useEffect, useState } from "react";

import { Icon } from "./icons";
import type { RpcClient } from "./rpc";
import type { AppInfo } from "./protocol/v2/AppInfo";
import type { SkillMetadata } from "./protocol/v2/SkillMetadata";

export type ExtensionsTab = "skills" | "mcp" | "apps";

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

export function ExtensionsView(props: { client: RpcClient; cwd: string | null; tab: ExtensionsTab; setTab: (tab: ExtensionsTab) => void; onClose: () => void; notice: (error: unknown) => void }) {
  const { client, cwd, notice } = props;
  const [skills, setSkills] = useState<SkillMetadata[] | null>(null);
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [apps, setApps] = useState<AppInfo[] | null>(null);
  const [adding, setAdding] = useState<{ name: string; kind: "command" | "url"; target: string; args: string } | null>(null);
  const [changed, setChanged] = useState(false);

  const load = useCallback(() => {
    client.request("skills/list", cwd ? { cwds: [cwd] } : {})
      .then((answer) => setSkills((answer as { data?: { skills: SkillMetadata[] }[] }).data?.flatMap((entry) => entry.skills) ?? []))
      .catch(() => setSkills([]));
    client.request("mcpServerStatus/list", {}).then((answer) => setServers((answer as { data?: McpServer[] }).data ?? [])).catch(() => setServers([]));
    client.request("app/list", {}).then((answer) => setApps((answer as { data?: AppInfo[] }).data ?? [])).catch(() => setApps([]));
  }, [client, cwd]);
  useEffect(load, [load]);

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
          {(["skills", "mcp", "apps"] as const).map((tab) => (
            <button key={tab} className={props.tab === tab ? "selected" : ""} aria-pressed={props.tab === tab} onClick={() => props.setTab(tab)}>
              {tab === "skills" ? "Skills" : tab === "mcp" ? "MCP servers" : "Apps"}
            </button>
          ))}
        </nav>
        {changed ? <div className="notice">Saved to config.toml: new threads use it; a running thread keeps what it started with.</div> : null}
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
            ) : <button className="link-button" onClick={() => setAdding({ name: "", kind: "command", target: "", args: "" })}>Add an MCP server</button>}
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
