// Skills and tools › Plugins (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.11; DESKTOP §16 B34):
// marketplaces added (`marketplace/add`) and removed (`marketplace/remove`, after a confirmation), a
// search over their plugins, the Installed row with each plugin turned off or on
// (`plugins.<id>.enabled`) or removed (`plugin/uninstall`), and Install showing what `plugin/read`
// says of the plugin first: only the details' Install sends `plugin/install`, which the app's policy
// still refuses at airgapped on (AIRGAPPED §14.12).

import { useState } from "react";

import { installedPlugins, pluginEnabledEdit, pluginFacts, pluginMatches, searchMarketplaces, type PluginFacts } from "./plugins";
import type { RpcClient } from "./rpc";
import type { ConfigBatchWriteParams } from "./protocol/v2/ConfigBatchWriteParams";
import type { PluginMarketplaceEntry } from "./protocol/v2/PluginMarketplaceEntry";
import type { PluginReadResponse } from "./protocol/v2/PluginReadResponse";

type PluginChange = "plugin/install" | "plugin/uninstall" | "marketplace/add" | "marketplace/remove";

export function PluginsTab(props: {
  client: RpcClient;
  marketplaces: PluginMarketplaceEntry[] | null;
  focusPlugin?: string | null;
  act: (method: PluginChange, params: Record<string, unknown>) => Promise<boolean>;
  writeConfig: (params: ConfigBatchWriteParams) => Promise<void>;
  notice: (error: unknown) => void;
}) {
  const { act, marketplaces } = props;
  const [source, setSource] = useState("");
  const [search, setSearch] = useState("");
  /** The plugin whose details are shown, with what installing it takes. */
  const [details, setDetails] = useState<{ facts: PluginFacts; marketplacePath: string; pluginName: string } | null>(null);

  const showDetails = async (marketplacePath: string, pluginName: string) => {
    try {
      const answer = (await props.client.request("plugin/read", { marketplacePath, pluginName })) as PluginReadResponse;
      setDetails({ facts: pluginFacts(answer.plugin), marketplacePath, pluginName });
    } catch (error) {
      props.notice(error);
    }
  };
  const install = async () => {
    if (details && (await act("plugin/install", { marketplacePath: details.marketplacePath, pluginName: details.pluginName }))) setDetails(null);
  };
  const removeMarketplace = (name: string) => {
    if (window.confirm(`Remove the marketplace ${name}? Its plugins are no longer offered here.`)) void act("marketplace/remove", { marketplaceName: name });
  };

  const installed = installedPlugins(marketplaces ?? []).filter(({ plugin }) => pluginMatches(plugin, search));
  const offered = marketplaces ? searchMarketplaces(marketplaces, search) : null;
  const facts = details?.facts;

  return (
    <>
      <p className="dialog-note">Plugins from marketplaces in git repositories and folders on this machine. A plugin's MCP servers and hooks run as you, outside the sandbox: add only what you trust. The vendor's own catalogue is closed in Mightling, and while airgapped is on, plugins are off and nothing is installed.</p>
      <form className="plugin-market-add" onSubmit={(event) => { event.preventDefault(); const chosen = source.trim(); if (chosen) void act("marketplace/add", { source: chosen }).then((added) => added && setSource("")); }}>
        <input aria-label="Marketplace source" placeholder="owner/repo, a git URL or a folder" value={source} onChange={(event) => setSource(event.target.value)} />
        <button type="submit" disabled={!source.trim()}>Add marketplace</button>
      </form>
      <input className="plugin-search" type="search" aria-label="Search plugins" placeholder="Search plugins" value={search} onChange={(event) => setSearch(event.target.value)} />
      {facts ? (
        <section className="plugin-detail-card" aria-label="Plugin details">
          <h3>{facts.title}</h3>
          {facts.description ? <p>{facts.description}</p> : null}
          {([["Skills", facts.skills], ["MCP servers", facts.mcpServers], ["Hooks", facts.hooks], ["Apps", facts.apps]] as const).map(([label, lines]) => (
            <div key={label} className="plugin-detail-section"><strong>{label}:</strong> {lines.length ? lines.join(", ") : "none"}</div>
          ))}
          <p className="planned-note">Its MCP servers and hooks run as you, outside the sandbox.</p>
          <div className="buttons">
            <button className="primary" onClick={() => void install()}>Install</button>
            <button onClick={() => setDetails(null)}>Cancel</button>
          </div>
        </section>
      ) : null}
      {installed.length ? (
        <section className="plugin-market" aria-label="Installed plugins">
          <h3>Installed</h3>
          <table className="shortcut-table">
            <tbody>
              {installed.map(({ plugin, market }) => (
                <tr key={plugin.id}>
                  <td><strong>{plugin.name}</strong>{plugin.version ? <span className="planned-note"> {plugin.version}</span> : null}</td>
                  <td className="planned-note">{plugin.enabled ? "on" : "off"} · {market}</td>
                  <td className="shortcut-actions">
                    <button className="link-button" aria-label={`${plugin.enabled ? "Turn off" : "Turn on"} ${plugin.name}`} onClick={() => void props.writeConfig(pluginEnabledEdit(plugin.id, !plugin.enabled))}>{plugin.enabled ? "Turn off" : "Turn on"}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      {offered === null ? <p className="empty small">…</p> : (marketplaces ?? []).every((market) => market.plugins.length === 0) ? <p className="empty small">No marketplace offers a plugin here.</p> : offered.length === 0 ? <p className="empty small">No plugin matches the search.</p> : offered.map((market) => (
        <section key={market.name} className="plugin-market">
          <div className="plugin-market-header">
            <h3>{market.name}</h3>
            <button className="link-button" aria-label={`Remove the marketplace ${market.name}`} onClick={() => removeMarketplace(market.name)}>Remove marketplace</button>
          </div>
          <table className="shortcut-table">
            <tbody>
              {market.plugins.map((plugin) => (
                <tr key={plugin.id} className={props.focusPlugin === plugin.id ? "focused" : undefined} aria-current={props.focusPlugin === plugin.id ? "true" : undefined}
                  ref={props.focusPlugin === plugin.id ? (row) => row?.scrollIntoView({ block: "center" }) : undefined}>
                  <td><strong>{plugin.name}</strong>{plugin.version ? <span className="planned-note"> {plugin.version}</span> : null}</td>
                  <td className="planned-note">{plugin.installed ? (plugin.enabled ? "installed" : "installed, off") : "not installed"}</td>
                  <td className="shortcut-actions">
                    {plugin.installed
                      ? <button className="link-button" onClick={() => void act("plugin/uninstall", { pluginId: plugin.id })}>Remove</button>
                      : <button className="link-button" disabled={!market.path} title={market.path ? "" : "Only marketplaces on this machine install here"} onClick={() => market.path && void showDetails(market.path, plugin.name)}>Install</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </>
  );
}
