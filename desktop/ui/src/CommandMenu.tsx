// The command menu (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1; Ctrl+K or Ctrl+Shift+P):
// commands, threads, projects and settings pages in one list, filtered as you type, Enter runs
// the highlighted one.

import { useEffect, useMemo, useRef, useState } from "react";

import { filterEntries, type MenuEntry } from "./shell";

export function CommandMenu(props: { entries: MenuEntry[]; onRun: (entry: MenuEntry) => void; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const list = useRef<HTMLUListElement>(null);
  const shown = useMemo(() => filterEntries(props.entries, query).slice(0, 60), [props.entries, query]);
  useEffect(() => setHighlight(0), [query]);
  useEffect(() => {
    list.current?.querySelector(".selected")?.scrollIntoView({ block: "nearest" });
  }, [highlight]);
  const run = (entry: MenuEntry | undefined) => {
    if (!entry) return;
    props.onClose();
    props.onRun(entry);
  };
  return (
    <div className="dialog-backdrop command-backdrop" onClick={props.onClose}>
      <section className="dialog command-menu" role="dialog" aria-label="Command menu" onClick={(event) => event.stopPropagation()}>
        <input autoFocus value={query} placeholder="Type a command, a thread or a project" aria-label="Command"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              setHighlight((index) => (shown.length ? (index + (event.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length : 0));
            } else if (event.key === "Enter") {
              event.preventDefault();
              run(shown[highlight]);
            } else if (event.key === "Escape") {
              event.preventDefault();
              props.onClose();
            }
          }} />
        <ul ref={list} role="listbox" aria-label="Results">
          {shown.length === 0 ? <li className="empty small">Nothing matches.</li> : null}
          {shown.map((entry, index) => (
            <li key={`${entry.kind}:${entry.id}`} role="option" aria-selected={index === highlight} className={index === highlight ? "selected" : ""}
              onMouseEnter={() => setHighlight(index)} onMouseDown={(event) => { event.preventDefault(); run(entry); }}>
              <span className={`kind ${entry.kind}`}>{entry.kind === "command" ? "Command" : entry.kind === "thread" ? "Thread" : entry.kind === "project" ? "Project" : "Settings"}</span>
              <span className="command-label">{entry.label}</span>
              <span className="command-detail">{entry.detail}</span>
              {entry.shortcut ? <kbd>{entry.shortcut}</kbd> : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
