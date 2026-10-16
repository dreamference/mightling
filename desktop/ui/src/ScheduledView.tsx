// Scheduled (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.12), Mightling's: Night Shift is the
// scheduler. An inbox of finished tasks (unread until seen), the queue, the latest morning report,
// a task added to tonight's queue for a project, a queued task dropped, and Run now. Everything goes
// through the launcher's `ling night` and the runner (desktop/electron/src/night.ts).

import { useCallback, useEffect, useState } from "react";

import * as bridge from "./bridge";
import { Icon } from "./icons";
import { renderMarkdown } from "./markdown";

/** Statuses of a task that has run (the runner's words, ling-rs/src/night.rs). */
export const FINISHED = ["done", "stalled", "failed", "cancelled", "blocked"];

/** Finished tasks newer than `seen` (an ISO time), for the unread mark. */
export function unseen(tasks: bridge.NightTask[], seen: string | null): number {
  return tasks.filter((task) => FINISHED.includes(task.status) && (!seen || (task.at ?? "") > seen)).length;
}

export function ScheduledView(props: { projects: string[]; defaultProject: string | null; seen: string | null; onSeen: (at: string) => void; onClose: () => void; notice: (error: unknown) => void }) {
  const { notice } = props;
  const [data, setData] = useState<{ tasks: bridge.NightTask[]; reports: { name: string; modified: number }[] } | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const [repo, setRepo] = useState(props.defaultProject ?? props.projects[0] ?? "");
  const [task, setTask] = useState("");
  const [test, setTest] = useState("");
  const [said, setSaid] = useState<string | null>(null);

  const load = useCallback(() => {
    bridge.night.list().then((answer) => {
      setData(answer);
      if (answer.reports[0]) bridge.night.report(answer.reports[0].name).then(setReport).catch(() => setReport(null));
    }).catch((error) => { setData({ tasks: [], reports: [] }); notice(error); });
  }, [notice]);
  useEffect(load, [load]);
  // Opening the view marks what it shows as seen.
  useEffect(() => {
    if (data) props.onSeen(new Date().toISOString());
  }, [data]);

  const finished = data?.tasks.filter((entry) => FINISHED.includes(entry.status)) ?? [];
  const waiting = data?.tasks.filter((entry) => !FINISHED.includes(entry.status)) ?? [];
  const name = (folder: string) => folder.split(/[\\/]/).filter(Boolean).pop() ?? folder;

  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog scheduled" role="dialog" aria-label="Scheduled" onClick={(event) => event.stopPropagation()}>
        <header><h2>Scheduled</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <p className="dialog-note">Night Shift works through these at night, each in its own worktree and branch; nothing is merged or pushed. Run now starts it at once, if the machine is free.</p>
        <div className="night-add">
          <select value={repo} aria-label="Project" onChange={(event) => setRepo(event.target.value)}>
            {props.projects.map((folder) => <option key={folder} value={folder}>{name(folder)}</option>)}
          </select>
          <input value={task} placeholder="A task for tonight" aria-label="Task" onChange={(event) => setTask(event.target.value)} />
          <input value={test} placeholder="test command (optional)" aria-label="Test command" onChange={(event) => setTest(event.target.value)} />
          <button className="primary" disabled={!repo || !task.trim()} onClick={() => bridge.night.add(repo, task, test || null).then((text) => { setSaid(text); setTask(""); setTest(""); load(); }).catch(notice)}>Queue</button>
          <button onClick={() => bridge.night.runNow().then(() => setSaid("Night Shift started; it gives way if the machine is busy.")).catch(notice)}>Run now</button>
        </div>
        {said ? <div className="notice">{said}</div> : null}
        <h3>Inbox</h3>
        {data === null ? <p className="empty small">…</p> : finished.length === 0 ? <p className="empty small">Nothing has run yet.</p> : (
          <table className="shortcut-table">
            <tbody>
              {finished.map((entry) => (
                <tr key={entry.id} className={!props.seen || (entry.at ?? "") > props.seen ? "unseen" : ""}>
                  <td>{entry.task}</td>
                  <td className="planned-note">{name(entry.repo)} · <code>{entry.branch}</code></td>
                  <td><span className={`night-status ${entry.status}`}>{entry.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <h3>Queued</h3>
        {data === null ? null : waiting.length === 0 ? <p className="empty small">Nothing is queued.</p> : (
          <table className="shortcut-table">
            <tbody>
              {waiting.map((entry) => (
                <tr key={entry.id}>
                  <td>{entry.task}{entry.test ? <span className="planned-note"> · test: {entry.test}</span> : null}</td>
                  <td className="planned-note">{name(entry.repo)}{entry.on ? ` · on ${entry.on}` : ""}</td>
                  <td><span className={`night-status ${entry.status}`}>{entry.status}</span></td>
                  <td className="shortcut-actions">{entry.status === "queued" ? <button className="link-button" onClick={() => bridge.night.drop(entry.repo, entry.id).then(load).catch(notice)}>Drop</button> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {report ? (
          <>
            <h3>The latest report{data?.reports[0] ? ` (${data.reports[0].name.replace(/\.md$/, "")})` : ""}</h3>
            <div className="markdown night-report" dangerouslySetInnerHTML={{ __html: renderMarkdown(report) }} />
          </>
        ) : null}
      </section>
    </div>
  );
}
