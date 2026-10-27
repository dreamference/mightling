// The sidebar's bar while several threads are chosen (DESKTOP §16 B36, selection.ts): how many,
// Archive, Move to a project, Delete for good, and Clear (Escape does the same).
import { useState } from "react";

import { Icon } from "./icons";
import type { Project } from "./protocol/v2/Project";

export interface SelectionBarProps {
  count: number;
  /** The projects a thread can move to (the sidebar's), and whether any chosen thread is in one. */
  projects: Project[];
  inProject: boolean;
  busy: boolean;
  onArchive: () => void;
  onMove: (projectId: string | null) => void;
  onDelete: () => void;
  onClear: () => void;
}

export function SelectionBar({ count, projects, inProject, busy, onArchive, onMove, onDelete, onClear }: SelectionBarProps) {
  const [moving, setMoving] = useState(false);
  return (
    <div className="selection-bar" role="toolbar" aria-label="Chosen threads">
      <span className="selection-count" aria-live="polite">{count} selected</span>
      <button title="Archive the chosen threads (Ctrl+Z brings them back)" disabled={busy} onClick={onArchive}>Archive</button>
      <span className="menu-anchor">
        <button title="Move the chosen threads to a project" aria-haspopup="menu" disabled={busy || (projects.length === 0 && !inProject)}
          onClick={() => setMoving(!moving)}>Move</button>
        {moving ? (
          <span className="menu" role="menu" aria-label="Move chosen" onClick={() => setMoving(false)}>
            {projects.map((project) => <button key={project.id} role="menuitem" onClick={() => onMove(project.id)}>Move to project {project.name}</button>)}
            {inProject ? <button role="menuitem" onClick={() => onMove(null)}>Out of their project</button> : null}
          </span>
        ) : null}
      </span>
      <button className="danger-button" title="Delete the chosen threads for good, after a confirmation" disabled={busy} onClick={onDelete}>Delete…</button>
      <button className="icon" title="Clear the choice (Escape)" aria-label="Clear the choice" onClick={onClear}><Icon name="x" size={14} /></button>
    </div>
  );
}
