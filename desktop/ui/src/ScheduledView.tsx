// Scheduled (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.12), Mightling's: Night Shift is the
// scheduler. An inbox of finished tasks (unread until seen), the queue, the latest morning report,
// a task added to tonight's queue for a project, a queued task dropped, and Run now. Everything goes
// through the launcher's `ling night` and the runner (desktop/electron/src/night.ts). Since the owner's
// decision 7 (2026-10-21): a finished task opens on its result, its changes and its transcript; a task
// repeats every night or every week (`ling-admin night every`; since DESKTOP §16 B42 also on chosen
// weekdays, every N nights or on a day of the month, cadence.ts); one queued from a thread carries that
// thread's latest messages (tonight.ts). Since DESKTOP §16 B41: each finished run is unread until it is
// opened, with Mark all as read (nightinbox.ts), and the archive is searched (NightArchive.tsx).

import { Fragment, useCallback, useEffect, useState } from "react";

import * as bridge from "./bridge";
import { type Cadence, cadenceWords, toggleDay, WEEKDAYS } from "./cadence";
import { Icon } from "./icons";
import { renderMarkdown } from "./markdown";
import { NightArchive } from "./NightArchive";
import { INBOX_STATUSES, isUnread, unreadRuns } from "./nightinbox";
import { NightRunDetails } from "./NightRunDetails";
import { withThreadContext } from "./tonight";

/** Statuses of a task that has run (the runner's words, ling-rs/src/night.rs), as the inbox lists them (nightinbox.ts). */
export const FINISHED = INBOX_STATUSES;

/** Finished tasks newer than `seen` (an ISO time) and not opened since (`read`), for the unread mark. */
export function unseen(tasks: bridge.NightTask[], seen: string | null, read: readonly string[] = []): number {
  return unreadRuns(tasks, seen, read);
}

/** The attempt counts the Queue form offers (`--best-of`); the launcher takes any count of 1 or more. */
export const BEST_OF = [1, 2, 3, 4, 5];

/** Whether Night Shift runs by itself, in words, for the top of Scheduled. */
export function timerWords(status: { timer: "on" | "off" | "unsupported"; window: string | null }): string {
  if (status.timer === "on") return status.window ? `Night Shift is on: it works through the queue every night, ${status.window}.` : "Night Shift is on: it works through the queue every night.";
  if (status.timer === "off") return "Night Shift is off: queued tasks wait until Run now, or until it is turned on with ling-admin night enable.";
  return "Night Shift has no timer on this computer: queued tasks run on Run now, or on the node that takes them.";
}

export function ScheduledView(props: {
  /** `seen`: when everything was last marked read; `onSeen` is Mark all as read (DESKTOP §16 B41). */
  projects: string[]; defaultProject: string | null; seen: string | null; onSeen: (at: string) => void; onClose: () => void; notice: (error: unknown) => void;
  /** DESKTOP §16 B41: the runs opened since (`nightRead`), and one more opened. */
  read?: readonly string[];
  onRead?: (id: string) => void;
  /** The thread shown when Scheduled opened: its title and latest messages, offered as the task's context. */
  fromThread?: { title: string; context: string[] } | null;
  /** Opens a finished run's session as a thread in Work. */
  onOpenThread?: (threadId: string) => void;
  /** DESKTOP §16 B40: the finished run to open on (a thread's card, Open the result). */
  openRun?: string | null;
  /** DESKTOP §16 B40: a task queued for tonight with the thread's context, and what `ling night add` said: the thread keeps it as a card. */
  onQueued?: (said: string) => void;
}) {
  const { notice } = props;
  const [data, setData] = useState<{ tasks: bridge.NightTask[]; reports: { name: string; modified: number }[] } | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const [repo, setRepo] = useState(props.defaultProject ?? props.projects[0] ?? "");
  const [task, setTask] = useState("");
  const [test, setTest] = useState("");
  const [said, setSaid] = useState<string | null>(null);
  const [repeat, setRepeat] = useState<"once" | Cadence>("once");
  const [weekday, setWeekday] = useState<string>("mon");
  const [days, setDays] = useState<string[]>(["mon", "wed", "fri"]);
  const [every, setEvery] = useState("2");
  const [monthly, setMonthly] = useState("1");
  const [withContext, setWithContext] = useState(Boolean(props.fromThread?.context.length));
  const [repeating, setRepeating] = useState<bridge.RepeatingTask[] | null>(null);
  const [opened, setOpened] = useState<string | null>(props.openRun ?? null);
  const [node, setNode] = useState("");
  const [bestOf, setBestOf] = useState<number | null>(null);
  const [status, setStatus] = useState<Awaited<ReturnType<typeof bridge.night.status>> | null>(null);

  const load = useCallback(() => {
    bridge.night.status().then(setStatus).catch(() => setStatus(null));
    bridge.night.repeating().then(setRepeating).catch(() => setRepeating([]));
    bridge.night.list().then((answer) => {
      setData(answer);
      if (answer.reports[0]) bridge.night.report(answer.reports[0].name).then(setReport).catch(() => setReport(null));
    }).catch((error) => { setData({ tasks: [], reports: [] }); notice(error); });
  }, [notice]);
  useEffect(load, [load]);
  // A run opened from a thread's card (B40) counts as opened here (B41).
  useEffect(() => {
    if (props.openRun) props.onRead?.(props.openRun);
  }, [props.openRun]);

  const finished = data?.tasks.filter((entry) => FINISHED.includes(entry.status)) ?? [];
  const waiting = data?.tasks.filter((entry) => !FINISHED.includes(entry.status)) ?? [];
  const name = (folder: string) => folder.split(/[\\/]/).filter(Boolean).pop() ?? folder;
  const unread = finished.filter((entry) => isUnread(entry, props.seen, props.read ?? [])).length;
  const openThread = props.onOpenThread ? (threadId: string) => { props.onClose(); props.onOpenThread?.(threadId); } : undefined;

  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog scheduled" role="dialog" aria-label="Scheduled" onClick={(event) => event.stopPropagation()}>
        <header><h2>Scheduled</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <p className="dialog-note">Night Shift works through these at night, each in its own worktree and branch; nothing is merged or pushed. Run now starts it at once, if the machine is free.</p>
        {status ? <p className={`night-timer ${status.timer}`} role="status">{timerWords(status)}</p> : null}
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
            <option value="days">On chosen days</option>
            <option value="every">Every N nights</option>
            <option value="monthly">Every month</option>
          </select>
          {repeat === "once" ? (
            <>
              <input value={node} placeholder="on a node (optional)" aria-label="Node" title="Hand the task to another node, by the name ling node list shows; it works it once" onChange={(event) => setNode(event.target.value)} />
              <select value={bestOf ?? ""} aria-label="Attempts" disabled={!!node.trim()} title={node.trim() ? "A node works a task sent to it once" : "How many attempts Night Shift makes; it keeps the best"} onChange={(event) => setBestOf(event.target.value ? Number(event.target.value) : null)}>
                <option value="">Attempts: as set</option>
                {BEST_OF.map((count) => <option key={count} value={count}>{count === 1 ? "1 attempt" : `Best of ${count}`}</option>)}
              </select>
            </>
          ) : null}
          {repeat === "weekly" ? (
            <select value={weekday} aria-label="Day" onChange={(event) => setWeekday(event.target.value)}>
              {WEEKDAYS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          ) : null}
          {repeat === "days" ? (
            <span className="night-days" role="group" aria-label="Days">
              {WEEKDAYS.map(([value, label]) => <label key={value} title={label}><input type="checkbox" checked={days.includes(value)} onChange={() => setDays(toggleDay(days, value))} />{label.slice(0, 3)}</label>)}
            </span>
          ) : null}
          {repeat === "every" ? (
            <input className="night-number" type="number" min={1} max={365} value={every} aria-label="Nights between runs" title="Every this many nights, the first tonight (1 to 365)" onChange={(event) => setEvery(event.target.value)} />
          ) : null}
          {repeat === "monthly" ? (
            <input className="night-number" type="number" min={1} max={31} value={monthly} aria-label="Day of the month" title="This day of each month (1 to 31); in a shorter month, its last day" onChange={(event) => setMonthly(event.target.value)} />
          ) : null}
          <button className="primary" disabled={!repo || !task.trim() || (repeat === "days" && !days.length)} onClick={() => {
            const words = withContext && props.fromThread ? withThreadContext(task, props.fromThread.title, props.fromThread.context) : task;
            const queued = repeat === "once"
              ? bridge.night.add(repo, words, test || null, node.trim() ? { on: node.trim() } : { bestOf })
              : bridge.night.every(repo, words, test || null, repeat, repeat === "weekly" ? { weekday } : repeat === "days" ? { days } : repeat === "every" ? { every: Number(every) } : repeat === "monthly" ? { day: Number(monthly) } : {});
            queued.then((text) => { if (repeat === "once" && withContext && props.fromThread?.context.length) props.onQueued?.(text); setSaid(text); setTask(""); setTest(""); setNode(""); setBestOf(null); load(); }).catch(notice);
          }}>{repeat === "once" ? "Queue" : "Repeat"}</button>
          <button onClick={() => bridge.night.runNow().then(() => setSaid("Night Shift started; it gives way if the machine is busy.")).catch(notice)}>Run now</button>
        </div>
        {props.fromThread?.context.length ? (
          <label className="night-context"><input type="checkbox" checked={withContext} onChange={(event) => setWithContext(event.target.checked)} /> With what was said in <strong>{props.fromThread.title}</strong> (its latest {props.fromThread.context.length} messages)</label>
        ) : null}
        {said ? <div className="notice">{said}</div> : null}
        <div className="night-inbox-head">
          <h3>Inbox{unread ? <span className="count" aria-label={`${unread} unread`}>{unread}</span> : null}</h3>
          {unread ? <button className="link-button" onClick={() => props.onSeen(new Date().toISOString())}>Mark all as read</button> : null}
        </div>
        {data === null ? <p className="empty small">…</p> : finished.length === 0 ? <p className="empty small">Nothing has run yet.</p> : (
          <table className="shortcut-table">
            <tbody>
              {finished.map((entry) => (
                <Fragment key={entry.id}>
                  <tr className={`night-run${isUnread(entry, props.seen, props.read ?? []) ? " unseen" : ""}${opened === entry.id ? " open" : ""}`}>
                    <td><button className="link-button" aria-expanded={opened === entry.id} onClick={() => {
                      if (opened === entry.id) return setOpened(null);
                      setOpened(entry.id);
                      props.onRead?.(entry.id);
                    }}>{entry.task.split("\n")[0]}</button></td>
                    <td className="planned-note">{name(entry.repo)} · <code>{entry.branch}</code></td>
                    <td><span className={`night-status ${entry.status}`}>{entry.status}</span></td>
                  </tr>
                  {opened === entry.id ? (
                    <tr className="night-details"><td colSpan={3}><NightRunDetails entry={entry} notice={notice} onOpenThread={openThread} /></td></tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
        <h3>Repeating</h3>
        {repeating === null ? null : repeating.length === 0 ? <p className="empty small">Nothing repeats. Choose how often in Repeat above.</p> : (
          <table className="shortcut-table">
            <tbody>
              {repeating.map((entry) => (
                <tr key={entry.id}>
                  <td>{entry.task.split("\n")[0]}{entry.test ? <span className="planned-note"> · test: {entry.test}</span> : null}</td>
                  <td className="planned-note">{name(entry.repo)} · {cadenceWords(entry)}{entry.lastQueued ? ` · last queued ${entry.lastQueued}` : ""}{entry.lastError ? ` · not queued: ${entry.lastError}` : ""}</td>
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
        <NightArchive notice={notice} onOpenThread={openThread} />
      </section>
    </div>
  );
}
