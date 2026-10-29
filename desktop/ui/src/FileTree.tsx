// The file tree (DESKTOP_PARITY §2.8, Ctrl+Shift+E): the thread's folder through the server's
// `fs/readDirectory`, a folder read when it is opened, and every open folder followed with
// `fs/watch` so a file the agent writes appears without a refresh. Read only: nothing here writes,
// moves or deletes (the policy refuses `fs/writeFile` and `fs/remove` from the window).

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { joinPath, treeEntries } from "./files";
import { Icon } from "./icons";
import type { FsReadDirectoryEntry } from "./protocol/v2/FsReadDirectoryEntry";
import type { RpcClient } from "./rpc";
import type { Listen } from "./turns";
import { shortcutLabel } from "./keymap";

type Listing = FsReadDirectoryEntry[] | "loading" | { error: string };

let nextWatch = 1;

export function FileTree(props: { root: string; client: RpcClient; listen: Listen; selected: string | null; onOpen: (path: string) => void; onClose: () => void }) {
  const { root, client, listen } = props;
  const [listings, setListings] = useState<Record<string, Listing>>({});
  const [open, setOpen] = useState<Set<string>>(new Set([root]));
  /** watch id → folder, for the folders followed now. */
  const watches = useRef(new Map<string, string>());
  const prefix = useRef(`tree${nextWatch++}-`);

  const read = useCallback((dir: string) => {
    client.request("fs/readDirectory", { path: dir })
      .then((answer) => setListings((known) => ({ ...known, [dir]: treeEntries(answer.entries) })))
      .catch((error) => setListings((known) => ({ ...known, [dir]: { error: error instanceof Error ? error.message : String(error) } })));
  }, [client]);

  const follow = useCallback((dir: string) => {
    if ([...watches.current.values()].includes(dir)) return;
    const id = `${prefix.current}${watches.current.size}-${Date.now().toString(36)}`;
    watches.current.set(id, dir);
    client.request("fs/watch", { watchId: id, path: dir }).catch(() => watches.current.delete(id));
  }, [client]);

  const unfollow = useCallback((dir: string) => {
    for (const [id, watched] of watches.current) {
      if (watched !== dir) continue;
      watches.current.delete(id);
      client.request("fs/unwatch", { watchId: id }).catch(() => {});
    }
  }, [client]);

  // A new root: forget the old tree, read and follow the new one.
  useEffect(() => {
    setListings({ [root]: "loading" });
    setOpen(new Set([root]));
    read(root);
    follow(root);
    const followed = watches.current;
    return () => {
      for (const id of followed.keys()) client.request("fs/unwatch", { watchId: id }).catch(() => {});
      followed.clear();
    };
  }, [root, read, follow, client]);

  // A change under a followed folder: read that folder again (once per burst of changes).
  useEffect(() => {
    const due = new Set<string>();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const stop = listen((notification) => {
      if (notification.method !== "fs/changed") return;
      const dir = watches.current.get(notification.params.watchId);
      if (!dir) return;
      due.add(dir);
      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        for (const folder of due) read(folder);
        due.clear();
      }, 150);
    });
    return () => {
      stop();
      if (timer) clearTimeout(timer);
    };
  }, [listen, read]);

  const toggle = (dir: string) => {
    const next = new Set(open);
    if (next.has(dir)) {
      next.delete(dir);
      unfollow(dir);
    } else {
      next.add(dir);
      if (!listings[dir] || typeof listings[dir] === "object" && "error" in (listings[dir] as object)) {
        setListings((known) => ({ ...known, [dir]: "loading" }));
      }
      // Read again even when it was read before: closed, it was not followed, so what changed meanwhile is not in its listing.
      read(dir);
      follow(dir);
    }
    setOpen(next);
  };

  const branch = (dir: string, depth: number): ReactNode => {
    const listing = listings[dir];
    if (!listing || listing === "loading") return <div className="tree-note" style={{ paddingLeft: 12 + depth * 14 }}>…</div>;
    if (!Array.isArray(listing)) return <div className="tree-note error" style={{ paddingLeft: 12 + depth * 14 }}>{folderError(listing.error)}</div>;
    if (listing.length === 0) return <div className="tree-note" style={{ paddingLeft: 12 + depth * 14 }}>empty</div>;
    return listing.map((entry) => {
      const full = joinPath(dir, entry.fileName);
      if (entry.isDirectory) {
        const expanded = open.has(full);
        return (
          <div key={full} role="treeitem" aria-expanded={expanded} aria-label={entry.fileName}>
            <button className="tree-row folder" style={{ paddingLeft: 8 + depth * 14 }} onClick={() => toggle(full)} title={full}>
              <Icon name={expanded ? "chevronDown" : "chevronRight"} size={12} /><Icon name="folder" size={14} /><span>{entry.fileName}</span>
            </button>
            {expanded ? <div role="group">{branch(full, depth + 1)}</div> : null}
          </div>
        );
      }
      return (
        <div key={full} role="treeitem" aria-label={entry.fileName} aria-selected={props.selected === full}>
          <button className={`tree-row file${props.selected === full ? " selected" : ""}`} style={{ paddingLeft: 22 + depth * 14 }} onClick={() => props.onOpen(full)} title={full} data-path={full}>
            <Icon name="file" size={14} /><span>{entry.fileName}</span>
          </button>
        </div>
      );
    });
  };

  return (
    <nav className="file-tree" aria-label="Files">
      <header className="tree-head">
        <span className="tree-root" title={root}>{root.split(/[\\/]/).filter(Boolean).pop() ?? root}</span>
        <span className="spacer" />
        <button className="icon" title="Read the folder again" aria-label="Refresh the files" onClick={() => { for (const dir of open) read(dir); }}><Icon name="undo" size={13} /></button>
        <button className="icon" title={`Hide the file tree (${shortcutLabel("fileTree")})`} aria-label="Hide the file tree" onClick={props.onClose}><Icon name="x" size={13} /></button>
      </header>
      <div className="tree-body" role="tree" aria-label={root}>{branch(root, 0)}</div>
    </nav>
  );
}

/**
 * What a folder that could not be read says: on a Mac, a read that macOS holds behind its access
 * prompt (~/Documents, ~/Desktop, ~/Downloads) ends as "Interrupted system call (os error 4)", and a
 * refused one as "Operation not permitted (os error 1)"; both mean the prompt, which says so.
 */
export function folderError(message: string): string {
  if (/os error 4\b|Interrupted system call/i.test(message)) return "macOS is asking whether Mightling may read this folder: answer its prompt, then open the folder again.";
  if (/os error 1\b|Operation not permitted/i.test(message)) return "macOS did not let Mightling read this folder: allow it in System Settings, Privacy & Security, Files and Folders.";
  return message;
}
