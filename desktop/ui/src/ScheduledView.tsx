// Scheduled (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.12), Mightling's: Night Shift is the
// scheduler. An inbox of finished tasks (unread until seen), the queue, the latest morning report,
// a task added to tonight's queue for a project, a queued task dropped, and Run now. Everything goes
// through the launcher's `ling night` and the runner (desktop/electron/src/night.ts). Since the owner's
// decision 7 (2026-10-21): a finished task opens on its result, its changes and its transcript; a task
// repeats every night or every week (`ling-admin night every`); one queued from a thread carries that
// thread's latest messages (tonight.ts).

import { Fragment, useCallback, useEffect, useState } from "react";

import * as bridge from "./bridge";
import { Icon } from "./icons";
import { renderMarkdown } from "./markdown";
import { withThreadContext } from "./tonight";

const WEEKDAYS = [["mon", "Monday"], ["tue", "Tuesday"], ["wed", "Wednesday"], ["thu", "Thursday"], ["fri", "Friday"], ["sat", "Saturday"], ["sun", "Sunday"]] as const;
type Details = Awaited<ReturnType<typeof bridge.night.details>>;

/** Statuses of a task that has run (the runner's words, ling-rs/src/night.rs). */
export const FINISHED = ["done", "stalled", "failed", "cancelled", "blocked"];

/** Finished tasks newer than `seen` (an ISO time), for the unread mark. */
export function unseen(tasks: bridge.NightTask[], seen: string | null): number {
  return tasks.filter((task) => FINISHED.includes(task.status) && (!seen || (task.at ?? "") > seen)).length;
}

export function ScheduledView(props: {
  projects: string[]; defaultProject: string | null; seen: string | null; onSeen: (at: string) => void; onClose: () => void; notice: (error: unknown) => void;
  /** The thread shown when Scheduled opened: its title and latest messages, offered as the task's context. */
  fromThread?: { title: string; context: string[] } | null;
  /** Opens a finished run's session as a thread in Work. */
  onOpenThread?: (threadId: string) => void;
}) {
  const { notice } = props;
  const [data, setData] = useState<{ tasks: bridge.NightTask[]; reports: { name: string; modified: number }[] } | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const [repo, setRepo] = useState(props.defaultProject ?? props.projects[0] ?? "");
  const [task, setTask] = useState("");
  const [test, setTest] = useState("");
  const [said, setSaid] = useState<string | null>(null);
  const [repeat, setRepeat] = useState<"once" | "daily" | "weekly">("once");
  const [weekday, setWeekday] = useState<string>("mon");
  const [withContext, setWithContext] = useState(Boolean(props.fromThread?.context.length));
  const [repeating, setRepeating] = useState<bridge.RepeatingTask[] | null>(null);
  const [opened, setOpened] = useState<{ id: string; tab: "result" | "diff" | "transcript"; details: Details | null } | null>(null);

  const load = useCallback(() => {
    bridge.night.repeating().then(setRepeating).catch(() => setRepeating([]));
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
          <select value={repeat} aria-label="Repeat" onChange={(event) => setRepeat(event.target.value as typeof repeat)}>
            <option value="once">Tonight</option>
            <option value="daily">Every night</option>
            <option value="weekly">Every week</option>
          </select>
          {repeat === "weekly" ? (
            <select value={weekday} aria-label="Day" onChange={(event) => setWeekday(event.target.value)}>
              {WEEKDAYS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          ) : null}
          <button className="primary" disabled={!repo || !task.trim()} onClick={() => {
            const words = withContext && props.fromThread ? withThreadContext(task, props.fromThread.title, props.fromThread.context) : task;
            const queued = repeat === "once" ? bridge.night.add(repo, words, test || null) : bridge.night.every(repo, words, test || null, repeat, repeat === "weekly" ? weekday : null);
            queued.then((text) => { setSaid(text); setTask(""); setTest(""); load(); }).catch(notice);
          }}>{repeat === "once" ? "Queue" : "Repeat"}</button>
          <button onClick={() => bridge.night.runNow().then(() => setSaid("Night Shift started; it gives way if the machine is busy.")).catch(notice)}>Run now</button>
        </div>
        {props.fromThread?.context.length ? (
          <label className="night-context"><input type="checkbox" checked={withContext} onChange={(event) => setWithContext(event.target.checked)} /> With what was said in <strong>{props.fromThread.title}</strong> (its latest {props.fromThread.context.length} messages)</label>
        ) : null}
        {said ? <div className="notice">{said}</div> : null}
        <h3>Inbox</h3>
        {data === null ? <p className="empty small">…</p> : finished.length === 0 ? <p className="empty small">Nothing has run yet.</p> : (
          <table className="shortcut-table">
            <tbody>
              {finished.map((entry) => (
                <Fragment key={entry.id}>
                  <tr className={`night-run${!props.seen || (entry.at ?? "") > props.seen ? " unseen" : ""}${opened?.id === entry.id ? " open" : ""}`}>
                    <td><button className="link-button" aria-expanded={opened?.id === entry.id} onClick={() => {
                      if (opened?.id === entry.id) return setOpened(null);
                      setOpened({ id: entry.id, tab: "result", details: null });
                      bridge.night.details(entry.id).then((details) => setOpened((current) => (current?.id === entry.id ? { ...current, details } : current))).catch(notice);
                    }}>{entry.task.split("\n")[0]}</button></td>
                    <td className="planned-note">{name(entry.repo)} · <code>{entry.branch}</code></td>
                    <td><span className={`night-status ${entry.status}`}>{entry.status}</span></td>
                  </tr>
                  {opened?.id === entry.id ? (
                    <tr className="night-details"><td colSpan={3}>
                      <nav className="review-scopes" aria-label="What the run left">
                        {(["result", "diff", "transcript"] as const).map((tab) => (
                          <button key={tab} className={opened.tab === tab ? "selected" : ""} aria-pressed={opened.tab === tab} onClick={() => setOpened({ ...opened, tab })}>
                            {{ result: "Result", diff: "Changes", transcript: "Transcript" }[tab]}
                          </button>
                        ))}
                        {opened.details?.session && props.onOpenThread ? <button className="link-button" onClick={() => { props.onClose(); props.onOpenThread?.(opened.details!.session!); }}>Open as a thread</button> : null}
                      </nav>
                      {opened.details === null ? <p className="empty small">Reading the run…</p> : opened.tab === "result" ? (
                        opened.details.result ? <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(opened.details.result) }} /> : <p className="empty small">The run left no message.</p>
                      ) : opened.tab === "diff" ? (
                        opened.details.diff ? <pre className="diff night-diff">{opened.details.diff.split("\n").map((line, index) => <span key={index} className={line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : line.startsWith("@@") ? "hunk" : ""}>{line}{"\n"}</span>)}</pre> : <p className="empty small">No changes.</p>
                      ) : (
                        opened.details.transcript ? <pre className="night-transcript">{opened.details.transcript}</pre> : <p className="empty small">No transcript was kept.</p>
                      )}
                    </td></tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
        <h3>Repeating</h3>
        {repeating === null ? null : repeating.length === 0 ? <p className="empty small">Nothing repeats. Choose Every night or Every week above.</p> : (
          <table className="shortcut-table">
            <tbody>
              {repeating.map((entry) => (
                <tr key={entry.id}>
                  <td>{entry.task.split("\n")[0]}{entry.test ? <span className="planned-note"> · test: {entry.test}</span> : null}</td>
                  <td className="planned-note">{name(entry.repo)} · {entry.cadence === "weekly" ? `every ${WEEKDAYS.find(([value]) => value === entry.weekday)?.[1] ?? entry.weekday}` : "every night"}{entry.lastQueued ? ` · last queued ${entry.lastQueued}` : ""}{entry.lastError ? ` · not queued: ${entry.lastError}` : ""}</td>
                  <td className="shortcut-actions"><button className="link-button" onClick={() => bridge.night.dropRepeating(entry.id).then(load).catch(notice)}>Stop</button></td>
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
