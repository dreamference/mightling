// A Work thread's Night Shift tasks as cards (DESKTOP §16 B40, nightthread.ts): what each is doing,
// and once it has run, its attempts, the chosen branch and its block from the morning report as a
// note, with Apply (`ling night apply` in the thread's folder) and Open the result (Scheduled, on
// that run). The note is shown here only: never an item of the thread, never sent to the model.
// The cards read Night Shift's files through the main process (desktop/electron/src/night.ts) when
// the thread opens, and again every minute while a task is still in flight.

import { useCallback, useEffect, useState } from "react";

import * as bridge from "./bridge";
import { Icon } from "./icons";
import { renderMarkdown } from "./markdown";
import { cardOf, noteBody, reportDate } from "./nightthread";

/** How often the cards look again while a task is queued or running. */
const POLL_MS = 60_000;

type Note = { report: string; text: string } | null;

export function NightCards(props: {
  /** The tasks tied to this thread (nightthread.ts `tasksOf`). */
  ids: string[];
  /** The thread's folder, where Apply brings the branch: its worktree when it has one. */
  cwd: string | null;
  notice: (error: unknown) => void;
  onOpenResult: (id: string) => void;
}) {
  const { ids, notice } = props;
  const key = ids.join(" ");
  const [tasks, setTasks] = useState<Record<string, bridge.NightTask>>({});
  const [notes, setNotes] = useState<Record<string, Note>>({});
  const [said, setSaid] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    if (!key) return;
    bridge.night.list().then((answer) => {
      const found: Record<string, bridge.NightTask> = {};
      for (const task of answer.tasks) if (ids.includes(task.id)) found[task.id] = task;
      setTasks(found);
      for (const id of ids) {
        const card = cardOf(id, found[id]);
        if (card.ended) bridge.night.note(id).then((note) => setNotes((current) => ({ ...current, [id]: note }))).catch(() => {});
      }
    }).catch(() => {});
    // `ids` is `key` split: the list changes only when `key` does.
  }, [key]);
  useEffect(load, [load]);
  const inFlight = ids.some((id) => !cardOf(id, tasks[id]).ended);
  useEffect(() => {
    if (!inFlight) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [inFlight, load]);

  if (ids.length === 0) return null;
  return (
    <>
      {ids.map((id) => {
        const card = cardOf(id, tasks[id]);
        const note = notes[id];
        return (
          <section key={id} className={`night-card ${card.ended ? "ended" : "waiting"}`} aria-label={`Night Shift: ${card.title}`}>
            <header>
              <Icon name="moon" size={14} />
              <strong>Night Shift</strong>
              <span className="night-card-title">{card.title}</span>
              <span className={`night-status ${card.status}`}>{card.status}</span>
            </header>
            <p className="night-card-state">
              {card.state}
              {card.attempts ? <> · {card.attempts}</> : null}
              {card.branch ? <> · on <code>{card.branch}</code></> : null}
              {card.applied ? <> · applied {card.applied}</> : null}
            </p>
            {note ? (
              <div className="night-note" role="note" aria-label={`From the night's report, ${reportDate(note.report)}`}>
                <div className="night-note-head">From the night's report, {reportDate(note.report)}</div>
                <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(noteBody(note.text)) }} />
              </div>
            ) : null}
            {said[id] ? <pre className="info-pre night-card-said">{said[id]}</pre> : null}
            {card.ended ? (
              <div className="buttons">
                {card.canApply ? (
                  <button className="primary" disabled={!props.cwd} title={props.cwd ? `Bring ${card.branch} into ${props.cwd} as uncommitted changes, all or nothing` : "Open the thread's folder first"}
                    onClick={() => {
                      if (!props.cwd) return;
                      bridge.night.apply(props.cwd, id).then((text) => { setSaid((current) => ({ ...current, [id]: text })); load(); }).catch(notice);
                    }}>Apply</button>
                ) : null}
                <button onClick={() => props.onOpenResult(id)}>Open the result</button>
              </div>
            ) : null}
          </section>
        );
      })}
    </>
  );
}
