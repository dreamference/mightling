// Search files (DESKTOP_PARITY §2.8, Ctrl+P): the thread's folder searched by name with the
// server's `fuzzyFileSearch`, the result opened in a file tab; `name:12` opens it at line 12, and
// `:12` alone moves the file in front to that line (go to line, Ctrl+L, asks the same).

import { useEffect, useRef, useState } from "react";

import { parseFileQuery, resolvedPath } from "./files";
import type { FuzzyFileSearchResult } from "./protocol/FuzzyFileSearchResult";

export function FileSearch(props: {
  root: string;
  search: (query: string) => Promise<FuzzyFileSearchResult[]>;
  /** The file in front, for `:12`; null when no file tab is. */
  front: string | null;
  initial?: string;
  onOpen: (path: string, line: number | null) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(props.initial ?? "");
  const [results, setResults] = useState<FuzzyFileSearchResult[]>([]);
  const [highlight, setHighlight] = useState(0);
  const list = useRef<HTMLUListElement>(null);
  const { text, line } = parseFileQuery(query);
  const { search } = props;

  useEffect(() => {
    if (!text) {
      setResults([]);
      return;
    }
    let current = true;
    const timer = setTimeout(() => {
      search(text).then((found) => current && setResults(found.filter((result) => result.match_type !== "directory").slice(0, 50))).catch(() => current && setResults([]));
    }, 80);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [text, search]);
  useEffect(() => setHighlight(0), [text]);
  useEffect(() => {
    list.current?.querySelector(".selected")?.scrollIntoView({ block: "nearest" });
  }, [highlight]);

  const run = (index: number) => {
    if (!text && line && props.front) {
      props.onClose();
      return props.onOpen(props.front, line);
    }
    const result = results[index];
    if (!result) return;
    props.onClose();
    props.onOpen(resolvedPath(result.root || props.root, result.path), line);
  };

  return (
    <div className="dialog-backdrop command-backdrop" onClick={props.onClose}>
      <section className="dialog command-menu file-search" role="dialog" aria-label="Search files" onClick={(event) => event.stopPropagation()}>
        <input autoFocus value={query} placeholder={props.front ? "A file's name, name:line, or :line in this file" : "A file's name, or name:line"} aria-label="File name"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              setHighlight((index) => (results.length ? (index + (event.key === "ArrowDown" ? 1 : results.length - 1)) % results.length : 0));
            } else if (event.key === "Enter") {
              event.preventDefault();
              run(highlight);
            } else if (event.key === "Escape") {
              event.preventDefault();
              props.onClose();
            }
          }} />
        <ul ref={list} role="listbox" aria-label="Files found">
          {!text && line && props.front ? <li className="selected" role="option" aria-selected="true" onMouseDown={(event) => { event.preventDefault(); run(0); }}>Go to line {line}</li> : null}
          {text && results.length === 0 ? <li className="empty small">No file matches.</li> : null}
          {results.map((result, index) => (
            <li key={`${result.root}:${result.path}`} role="option" aria-selected={index === highlight} className={index === highlight ? "selected" : ""}
              onMouseEnter={() => setHighlight(index)} onMouseDown={(event) => { event.preventDefault(); run(index); }}>
              <span className="command-label">{result.file_name}</span>
              <span className="command-detail">{result.path}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
