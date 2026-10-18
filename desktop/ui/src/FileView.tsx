// A file tab (DESKTOP_PARITY §2.8): a file read through the server's `fs/readFile`, highlighted,
// with line numbers, followed with `fs/watch` so the agent's edits show as they land. Opened from
// the file tree, the review pane or a message; `line` (1-based) is scrolled to and marked. Read
// only; Open in editor hands the file to the editor chosen in Settings.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import * as bridge from "./bridge";
import { LARGEST_SHOWN, decodeFile, highlighted, languageOf, lineCount, parentOf, relativeTo, sizeLabel } from "./files";
import { Icon } from "./icons";
import type { RpcClient } from "./rpc";
import type { Listen } from "./turns";

/** One line's height in the code view, in px; styles.css `.code-view` uses the same. */
export const LINE_HEIGHT = 20;

type Loaded =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "large"; bytes: number }
  | { status: "binary"; bytes: number }
  | { status: "text"; text: string; bytes: number };

let nextWatch = 1;

export interface FileViewProps {
  path: string;
  /** The line to show and mark, 1-based; null for the top. */
  line: number | null;
  /** The thread's folder, so the path shows relative to it. */
  root: string | null;
  client: RpcClient;
  listen: Listen;
  editor: string | null;
  onMention: (path: string) => void;
  notice: (error: unknown) => void;
}

export function FileView(props: FileViewProps) {
  const { path, client, listen, notice } = props;
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [force, setForce] = useState(false);
  const [marked, setMarked] = useState<number | null>(props.line);
  const body = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const size = await bridge.fileSize(path).catch(() => null);
      if (size !== null && size > LARGEST_SHOWN && !force) return setLoaded({ status: "large", bytes: size });
      const answer = await client.request("fs/readFile", { path });
      const file = decodeFile(answer.dataBase64);
      setLoaded(file.text === null ? { status: "binary", bytes: file.bytes } : { status: "text", text: file.text, bytes: file.bytes });
    } catch (error) {
      setLoaded({ status: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }, [path, client, force]);

  useEffect(() => {
    setLoaded({ status: "loading" });
    void load();
  }, [load]);

  // Follow the file: a change the agent (or anyone) makes reads it again.
  useEffect(() => {
    const watchId = `file${nextWatch++}`;
    client.request("fs/watch", { watchId, path }).catch(() => {});
    let timer: ReturnType<typeof setTimeout> | null = null;
    const stop = listen((notification) => {
      if (notification.method !== "fs/changed" || notification.params.watchId !== watchId) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void load(), 150);
    });
    return () => {
      stop();
      if (timer) clearTimeout(timer);
      client.request("fs/unwatch", { watchId }).catch(() => {});
    };
  }, [path, client, listen, load]);

  useEffect(() => setMarked(props.line), [props.line]);

  // Bring the asked-for line into view, a third of the way down.
  useLayoutEffect(() => {
    const element = body.current;
    if (!element || loaded.status !== "text" || !props.line) return;
    element.scrollTop = Math.max(0, (props.line - 1) * LINE_HEIGHT - element.clientHeight / 3);
  }, [props.line, loaded.status]);

  const language = languageOf(path);
  const html = useMemo(() => (loaded.status === "text" ? highlighted(loaded.text, language) : ""), [loaded, language]);
  const lines = loaded.status === "text" ? lineCount(loaded.text) : 0;
  const numbers = useMemo(() => Array.from({ length: lines }, (_, index) => index + 1), [lines]);
  const shown = relativeTo(props.root, path);

  const openEditor = () => {
    bridge.openInEditor(props.root ?? parentOf(path), relativeTo(props.root ?? parentOf(path), path), marked, props.editor).catch(notice);
  };

  return (
    <div className="file-view">
      <div className="file-tools">
        <span className="file-path" title={path}>{shown}</span>
        {loaded.status === "text" ? <span className="file-meta">{lines.toLocaleString()} lines · {sizeLabel(loaded.bytes)}{language ? ` · ${language}` : ""}</span> : null}
        <span className="spacer" />
        <button className="link-button" title="Name this file in the composer" onClick={() => props.onMention(marked ? `${path}:${marked}` : path)}>Mention</button>
        {bridge.git.available() ? <button className="link-button" title="Open in the editor chosen in Settings" onClick={openEditor}>Open in editor</button> : null}
        <button className="icon" title="Read the file again" aria-label="Reload the file" onClick={() => void load()}><Icon name="undo" size={13} /></button>
      </div>
      {loaded.status === "loading" ? <p className="empty small">Reading…</p> : null}
      {loaded.status === "error" ? <div className="notice">{loaded.message}</div> : null}
      {loaded.status === "binary" ? <p className="empty small">A binary file ({sizeLabel(loaded.bytes)}): nothing to show as text.</p> : null}
      {loaded.status === "large" ? (
        <p className="empty small">
          This file is {sizeLabel(loaded.bytes)}, too large to show here without slowing the window.{" "}
          <button className="link-button" onClick={() => setForce(true)}>Show it anyway</button>
        </p>
      ) : null}
      {loaded.status === "text" ? (
        <div className="code-view" ref={body} role="region" aria-label={`${shown}, ${lines} lines`}>
          <div className="code-gutter" aria-hidden="true">
            {numbers.map((number) => (
              <span key={number} className={number === marked ? "marked" : ""} onClick={() => setMarked(number === marked ? null : number)}>{number}</span>
            ))}
          </div>
          <div className="code-text">
            {marked ? <div className="line-mark" style={{ top: (marked - 1) * LINE_HEIGHT }} /> : null}
            <pre className="hljs"><code dangerouslySetInnerHTML={{ __html: html }} /></pre>
          </div>
        </div>
      ) : null}
    </div>
  );
}
