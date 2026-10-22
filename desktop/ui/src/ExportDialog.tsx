// Export a thread (the owner's decision 6, exportthread.ts): the options, the exact text that will be
// saved, and Save, which writes a file on this machine (the desktop app's save dialog; a download in a
// browser). Nothing is uploaded anywhere.

import { useEffect, useMemo, useState } from "react";

import * as bridge from "./bridge";
import { DEFAULT_EXPORT, exportName, threadHtml, threadMarkdown, type ExportOptions, type ExportSource } from "./exportthread";
import { Icon } from "./icons";
import type { Thread } from "./protocol/v2/Thread";
import type { Turn } from "./protocol/v2/Turn";
import type { RpcClient } from "./rpc";

/** Every turn of a thread, oldest first, with full items. */
async function allTurns(client: RpcClient, threadId: string): Promise<Turn[]> {
  const turns: Turn[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < 50; page++) {
    const answer = (await client.request("thread/turns/list", { threadId, limit: 100, sortDirection: "asc", itemsView: "full", ...(cursor ? { cursor } : {}) })) as { data?: Turn[]; nextCursor?: string | null };
    turns.push(...(answer.data ?? []));
    cursor = answer.nextCursor ?? null;
    if (!cursor) break;
  }
  return turns;
}

export function ExportDialog(props: { client: RpcClient; thread: Thread; title: string; home: string | null; onClose: () => void; notice: (error: unknown) => void }) {
  const { client, thread, notice } = props;
  const [options, setOptions] = useState<ExportOptions>(DEFAULT_EXPORT);
  const [turns, setTurns] = useState<Turn[] | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    allTurns(client, thread.id).then(setTurns).catch((error) => { setTurns([]); notice(error); });
  }, [client, thread.id, notice]);

  const source: ExportSource | null = turns ? { title: props.title, cwd: thread.cwd, home: props.home, turns } : null;
  const text = useMemo(() => (source ? (options.format === "html" ? threadHtml(source, options) : threadMarkdown(source, options)) : ""), [source, options]);
  const set = (change: Partial<ExportOptions>) => {
    setSaved(null);
    setOptions((current) => ({ ...current, ...change }));
  };

  const save = async () => {
    const name = exportName(props.title, options.format);
    try {
      if (bridge.host() === "electron") {
        const path = await bridge.saveText(name, text);
        if (path) setSaved(path);
      } else {
        const link = document.createElement("a");
        link.href = URL.createObjectURL(new Blob([text], { type: options.format === "html" ? "text/html" : "text/markdown" }));
        link.download = name;
        link.click();
        URL.revokeObjectURL(link.href);
        setSaved(name);
      }
    } catch (error) {
      notice(error);
    }
  };

  const check = (key: keyof ExportOptions, label: string) => (
    <label><input type="checkbox" checked={Boolean(options[key])} onChange={(event) => set({ [key]: event.target.checked } as Partial<ExportOptions>)} /> {label}</label>
  );

  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog export-dialog" role="dialog" aria-label="Export the thread" onClick={(event) => event.stopPropagation()}>
        <header><h2>Export the thread</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <p className="dialog-note">A file on this computer, nothing uploaded. Read it below before saving: what is redacted is redacted in the text you see.</p>
        <div className="export-options">
          <label className="field">Format
            <select value={options.format} aria-label="Format" onChange={(event) => set({ format: event.target.value as ExportOptions["format"] })}>
              <option value="markdown">Markdown (.md)</option>
              <option value="html">A web page (.html)</option>
            </select>
          </label>
          {check("redactPaths", "Hide folder paths")}
          {check("redactEmails", "Hide e-mail addresses")}
          {check("redactTokens", "Hide tokens, keys and passwords")}
          {check("includeCommands", "Include the commands and their output")}
          {check("includeReasoning", "Include the agent's thinking")}
        </div>
        <pre className="export-preview" aria-label="What will be saved">{turns === null ? "Reading the thread…" : text}</pre>
        <div className="buttons">
          <button className="primary" disabled={turns === null} onClick={() => void save()}>Save…</button>
          <button onClick={props.onClose}>Close</button>
          {saved ? <span className="planned-note" role="status">Saved {saved}</span> : null}
        </div>
      </section>
    </div>
  );
}
