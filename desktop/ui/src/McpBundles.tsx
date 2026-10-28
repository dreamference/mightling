// MCP bundles installed from a file, in Skills and tools › MCP servers (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md
// §16 B45). Install from a file opens a file dialog in the main process, which reads the bundle's
// manifest; it is shown first (name, author, the command it runs, the settings it asks for, whether a
// signature is attached), and only Install unpacks it (main process, `mcpb/install`) and adds its server
// to config.toml through `config/batchWrite`. Remove takes the server out of config.toml and deletes
// the bundle's folder. The desktop app only: a browser on `ling web` has no main process to unpack into.

import { useCallback, useEffect, useState } from "react";

import { mcpBundles, type BundleSummary, type InstalledBundle } from "./bridge";
import { bundleRemoval, bundleWrite, commandLine, configuredServer, formValues, initialForm, missingSettings, sizeText, type BundleForm } from "./mcpbundleform";
import type { RpcClient } from "./rpc";

export function McpBundles(props: { client: RpcClient; config: unknown; airgapped?: boolean; notice: (error: unknown) => void; onChanged: () => void }) {
  const { client, notice, onChanged } = props;
  const [installed, setInstalled] = useState<InstalledBundle[] | null>(null);
  const [picked, setPicked] = useState<{ token: string; file: string; summary: BundleSummary } | null>(null);
  const [form, setForm] = useState<BundleForm>({});
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    mcpBundles.list().then(setInstalled, () => setInstalled([]));
  }, []);
  useEffect(refresh, [refresh]);

  const pick = async () => {
    try {
      const chosen = await mcpBundles.pick();
      if (!chosen) return;
      setForm(initialForm(chosen.summary.settings));
      setPicked(chosen);
    } catch (error) {
      notice(error);
    }
  };

  const summary = picked?.summary ?? null;
  const replacing = summary !== null && (summary.installed || configuredServer(props.config, summary.name));
  const missing = summary ? missingSettings(summary.settings, form) : [];
  const blocked = summary !== null && (summary.refusal !== null || (summary.fetches !== null && props.airgapped === true));

  const install = async () => {
    if (!picked || !summary) return;
    setBusy(true);
    try {
      const done = await mcpBundles.install(picked.token, formValues(summary.settings, form), replacing);
      try {
        await client.request("config/batchWrite", bundleWrite(done.name, done.server));
      } catch (error) {
        // Not in config.toml, so not left on disk either (a replaced bundle keeps its new folder: its old entry points there).
        if (!replacing) await mcpBundles.remove(done.name).catch(() => null);
        throw error;
      }
      setPicked(null);
      refresh();
      onChanged();
    } catch (error) {
      notice(error);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (bundle: InstalledBundle) => {
    if (!window.confirm(`Remove ${bundle.displayName}? Its server leaves config.toml and its folder is deleted:\n\n${bundle.dir}`)) return;
    try {
      await client.request("config/batchWrite", bundleRemoval(bundle.name));
      await mcpBundles.remove(bundle.name);
      refresh();
      onChanged();
    } catch (error) {
      notice(error);
    }
  };

  return (
    <section className="mcp-bundles" aria-label="MCP bundles">
      {installed && installed.length ? (
        <>
          <h3>Installed from files</h3>
          <table className="shortcut-table" aria-label="Installed bundles">
            <tbody>
              {installed.map((bundle) => (
                <tr key={bundle.name}>
                  <td><strong>{bundle.displayName}</strong>{bundle.version ? <span className="planned-note"> {bundle.version}</span> : null}</td>
                  <td className="planned-note">{bundle.author ? `by ${bundle.author}` : ""} · server <code>{bundle.name}</code></td>
                  <td className="shortcut-actions"><button className="link-button" aria-label={`Remove ${bundle.displayName}`} onClick={() => void remove(bundle)}>Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
      {summary && picked ? (
        <div className="mcp-bundle" role="region" aria-label="Bundle to install">
          <h3>{summary.displayName}{summary.version ? <span className="planned-note"> {summary.version}</span> : null}</h3>
          <p className="dialog-note">{summary.author ? `By ${summary.author}. ` : "No author given. "}{summary.description}</p>
          <dl className="mcp-bundle-facts">
            <dt>Runs</dt>
            <dd><code className="mcp-bundle-command">{commandLine(summary.command, summary.args)}</code></dd>
            {Object.keys(summary.env).length ? (
              <>
                <dt>Environment</dt>
                <dd>{Object.entries(summary.env).map(([key, value]) => <div key={key}><code>{key}={value}</code></div>)}</dd>
              </>
            ) : null}
            <dt>Signature</dt>
            <dd>{summary.signed ? "A signature is attached; Mightling does not check it, so it says nothing about who made the bundle." : "Not signed."}</dd>
            <dt>Files</dt>
            <dd>{summary.files} files, {sizeText(summary.unpackedBytes)}, unpacked into {summary.dir} as the server <code>{summary.name}</code>; from {picked.file}</dd>
          </dl>
          {summary.refusal ? <div className="notice" role="alert">Not installed here: {summary.refusal}.</div> : null}
          {summary.fetches ? (
            <div className="notice" role="status">
              This bundle runs {summary.fetches}, which fetches what it runs from the network{props.airgapped ? ": it is not installed while airgapped is on." : "."}
            </div>
          ) : null}
          {replacing ? <div className="notice" role="status">A server named {summary.name} is set up already: installing replaces it.</div> : null}
          {summary.settings.length ? (
            <fieldset className="mcp-bundle-settings">
              <legend>Settings it asks for</legend>
              {summary.settings.map((setting) => (
                <label key={setting.key}>
                  <span>{setting.title}{setting.required ? " (required)" : ""}</span>
                  {setting.type === "boolean" ? (
                    <input type="checkbox" aria-label={setting.title} checked={form[setting.key] === true} onChange={(event) => setForm({ ...form, [setting.key]: event.target.checked })} />
                  ) : setting.multiple ? (
                    <textarea aria-label={setting.title} rows={2} placeholder="one per line" value={String(form[setting.key] ?? "")} onChange={(event) => setForm({ ...form, [setting.key]: event.target.value })} />
                  ) : (
                    <input aria-label={setting.title} type={setting.type === "number" ? "number" : setting.sensitive ? "password" : "text"}
                      min={setting.min ?? undefined} max={setting.max ?? undefined} value={String(form[setting.key] ?? "")}
                      onChange={(event) => setForm({ ...form, [setting.key]: event.target.value })} />
                  )}
                  {setting.description ? <small className="planned-note">{setting.description}</small> : null}
                </label>
              ))}
              <p className="planned-note">Kept in ~/.mightling/config.toml with the server, a secret too.</p>
            </fieldset>
          ) : null}
          <div className="buttons dialog-buttons">
            <button className="primary" disabled={busy || blocked || missing.length > 0} title={missing.length ? `Fill in ${missing.join(", ")}` : undefined} onClick={() => void install()}>{replacing ? "Replace" : "Install"}</button>
            <button onClick={() => setPicked(null)}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="buttons dialog-buttons">
          <button className="link-button" onClick={() => void pick()}>Install from a file…</button>
          <span className="planned-note">An MCP bundle (.mcpb): its manifest is shown before anything is unpacked, and nothing is fetched.</span>
        </div>
      )}
    </section>
  );
}
