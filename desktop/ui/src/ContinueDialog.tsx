// Continue in a project (DESKTOP §16 B33): the project picker for an Ask question. Each project with a
// folder is offered by its name and primary folder; choosing one forks the question there as a Work
// thread (continueproject.ts). A project with no folder is for questions and is not offered.

import { useEffect, useRef } from "react";

import type { ContinueTarget } from "./continueproject";

export function ContinueDialog(props: { question: string; targets: ContinueTarget[]; busy: boolean; onPick: (target: ContinueTarget) => void; onClose: () => void }) {
  const first = useRef<HTMLButtonElement>(null);
  // Focused once when it opens: the window re-renders while a turn streams, and that keeps the focus where it is.
  const close = useRef(props.onClose);
  close.current = props.onClose;
  useEffect(() => {
    first.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      close.current();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog continue-dialog" role="dialog" aria-label="Continue in a project" onClick={(event) => event.stopPropagation()}>
        <h3>Continue in a project</h3>
        <p className="dialog-note">A copy of “{props.question}” becomes a Work thread in the project's primary folder, where the agent may read and change files. The question stays in Ask.</p>
        {props.targets.length ? (
          <div className="continue-targets">
            {props.targets.map((target, index) => (
              <button key={target.id} ref={index === 0 ? first : undefined} className="continue-target" disabled={props.busy} onClick={() => props.onPick(target)} aria-label={`Continue in ${target.name}`}>
                <span className="continue-name">{target.name}</span>
                <span className="continue-folder">{target.folder}</span>
              </button>
            ))}
          </div>
        ) : (
          <p className="dialog-note">No project has a folder yet. Create one in Work (New project) with the folder to work in.</p>
        )}
        <div className="dialog-actions">
          <button ref={props.targets.length ? undefined : first} onClick={props.onClose}>Cancel</button>
        </div>
      </section>
    </div>
  );
}
