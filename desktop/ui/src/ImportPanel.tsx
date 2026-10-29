// Skills and tools › Import (DESKTOP_PARITY §2.11, Phase 3 item 8): another agent's configuration
// and histories found on this machine (`externalAgentConfig/detect`, the home folder and the
// project's), ticked and imported by the server (`externalAgentConfig/import`, progress and
// completion as notifications), and the imports done before (`externalAgentConfig/import/readHistories`).
// What is written goes to `~/.mightling` and the project's own files, as the server decides.

import { useCallback, useEffect, useRef, useState } from "react";

import { TYPE_LABELS, caveat, itemKey, resultLines, scopeOf, startsTicked } from "./imports";
import type { ExternalAgentConfigImportHistory } from "./protocol/v2/ExternalAgentConfigImportHistory";
import type { ExternalAgentConfigImportTypeResult } from "./protocol/v2/ExternalAgentConfigImportTypeResult";
import type { ExternalAgentConfigMigrationItem } from "./protocol/v2/ExternalAgentConfigMigrationItem";
import type { RpcClient } from "./rpc";
import type { Listen } from "./turns";

export function ImportPanel(props: { client: RpcClient; listen: Listen; cwd: string | null; notice: (error: unknown) => void; onImported: () => void }) {
  const { client, listen, cwd, notice, onImported } = props;
  const [items, setItems] = useState<ExternalAgentConfigMigrationItem[] | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [running, setRunning] = useState<string | null>(null);
  const [results, setResults] = useState<ExternalAgentConfigImportTypeResult[] | null>(null);
  const [history, setHistory] = useState<ExternalAgentConfigImportHistory[]>([]);

  const detect = useCallback(() => {
    setItems(null);
    client.request("externalAgentConfig/detect", { includeHome: true, cwds: cwd ? [cwd] : [] })
      .then((answer) => {
        const found = (answer as { items?: ExternalAgentConfigMigrationItem[] }).items ?? [];
        setItems(found);
        setTicked(new Set(found.filter(startsTicked).map(itemKey)));
      })
      .catch((error) => { setItems([]); notice(error); });
    client.request("externalAgentConfig/import/readHistories", undefined)
      .then((answer) => setHistory((answer as { data?: ExternalAgentConfigImportHistory[] }).data ?? []))
      .catch(() => setHistory([]));
  }, [client, cwd, notice]);
  useEffect(detect, [detect]);

  // Imports that ended, so one whose end came before the request's answer is not shown running for ever.
  const finished = useRef(new Set<string>());
  // The import's progress and its end, as the server reports them.
  useEffect(() => listen((notification) => {
    if (notification.method !== "externalAgentConfig/import/progress" && notification.method !== "externalAgentConfig/import/completed") return;
    if (running && notification.params.importId !== running) return;
    setResults(notification.params.itemTypeResults);
    if (notification.method === "externalAgentConfig/import/completed") {
      finished.current.add(notification.params.importId);
      setRunning(null);
      onImported();
      detect();
    }
  }), [listen, running, onImported, detect]);

  const start = async () => {
    const chosen = (items ?? []).filter((item) => ticked.has(itemKey(item)));
    if (!chosen.length) return;
    setResults(null);
    try {
      const answer = await client.request("externalAgentConfig/import", { migrationItems: chosen, providerId: "mightling_desktop" });
      const id = (answer as { importId?: string }).importId ?? "import";
      if (!finished.current.has(id)) setRunning(id);
    } catch (error) {
      notice(error);
    }
  };

  return (
    <div className="import-panel">
      <p className="dialog-note">Another coding agent's instructions, skills, MCP servers, hooks, commands and past sessions found on this machine, imported into Mightling's own files. Nothing is sent anywhere, except plugins, which come from their marketplaces.</p>
      {items === null ? <p className="empty small">Looking…</p> : items.length === 0 ? <p className="empty small">Nothing to import was found{cwd ? " in your home folder or this project" : " in your home folder"}.</p> : (
        <ul className="import-items" aria-label="Found">
          {items.map((item) => {
            const key = itemKey(item);
            const note = caveat(item);
            return (
              <li key={key}>
                <label title={note ?? undefined}>
                  <input type="checkbox" disabled={running !== null} checked={ticked.has(key)} onChange={(event) => setTicked((set) => {
                    const next = new Set(set);
                    if (event.target.checked) next.add(key);
                    else next.delete(key);
                    return next;
                  })} />
                  <strong>{TYPE_LABELS[item.itemType] ?? item.itemType}</strong> <span className="planned-note">{scopeOf(item)}</span>
                </label>
                <div className="import-description">{note ? `${item.description} ${note}` : item.description}</div>
              </li>
            );
          })}
        </ul>
      )}
      <div className="buttons">
        <button className="primary" disabled={running !== null || !(items ?? []).some((item) => ticked.has(itemKey(item)))} onClick={() => void start()}>{running ? "Importing…" : "Import the ticked items"}</button>
        <button disabled={running !== null} onClick={detect}>Look again</button>
      </div>
      {results ? (
        <div className="notice import-results" role="status">{resultLines(results).map((line) => <div key={line}>{line}</div>)}</div>
      ) : null}
      {history.length ? (
        <>
          <h3>Earlier imports</h3>
          <ul className="import-history">
            {history.slice(0, 10).map((entry) => (
              <li key={entry.importId}>{new Date(Number(entry.completedAtMs)).toLocaleString()}: {entry.successes.length} imported{entry.failures.length ? `, ${entry.failures.length} failed` : ""}</li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}
