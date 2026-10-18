// The Activity view (DESKTOP_PARITY §2.4, Phase 3 item 5; Ctrl+Alt+U): what the agent did across
// threads, newest first and by day, from the turns of the most recent threads (activity.ts). A
// row opens its thread; the filters keep a kind of action or one project.

import { useEffect, useMemo, useState } from "react";

import { KIND_LABELS, activityOf, dayLabel, merged, type ActivityEntry, type ActivityKind } from "./activity";
import { Icon, type IconName } from "./icons";
import type { Thread } from "./protocol/v2/Thread";
import type { Turn } from "./protocol/v2/Turn";
import type { RpcClient } from "./rpc";

/** How many of the most recent threads the view reads, and how many turns of each. */
const THREADS = 20;
const TURNS = 12;

const ICONS: Record<ActivityKind, IconName> = { command: "terminal", files: "fileDiff", tool: "settings", web: "globe", agents: "gitFork" };

export function ActivityView(props: { client: RpcClient; threads: Thread[]; projects: string[]; onOpen: (threadId: string) => void; onClose: () => void }) {
  const { client } = props;
  const [lists, setLists] = useState<ActivityEntry[][] | null>(null);
  const [kinds, setKinds] = useState<Set<ActivityKind>>(new Set(Object.keys(KIND_LABELS) as ActivityKind[]));
  const [project, setProject] = useState<string | null>(null);
  const recent = useMemo(() => [...props.threads].filter((thread) => !thread.ephemeral).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, THREADS), [props.threads]);

  useEffect(() => {
    let current = true;
    Promise.all(recent.map((thread) => client.request("thread/turns/list", { threadId: thread.id, limit: TURNS, itemsView: "full" })
      .then((answer) => activityOf(thread, ((answer as { data?: Turn[] }).data ?? [])))
      .catch(() => [] as ActivityEntry[])))
      .then((found) => current && setLists(found));
    return () => {
      current = false;
    };
  }, [client, recent]);

  const shown = lists ? merged(lists, kinds, project) : [];
  const toggle = (kind: ActivityKind) => setKinds((set) => {
    const next = new Set(set);
    if (next.has(kind)) next.delete(kind);
    else next.add(kind);
    return next;
  });
  let lastDay = "";

  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog activity" role="dialog" aria-label="Activity" onClick={(event) => event.stopPropagation()}>
        <header><h2>Activity</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <p className="dialog-note">What the agent did in the {recent.length} most recent threads, newest first, read from this machine's session files.</p>
        <div className="activity-filters">
          {(Object.keys(KIND_LABELS) as ActivityKind[]).map((kind) => (
            <button key={kind} className={`chip-toggle${kinds.has(kind) ? " on" : ""}`} aria-pressed={kinds.has(kind)} onClick={() => toggle(kind)}>
              <Icon name={ICONS[kind]} size={12} /> {KIND_LABELS[kind]}
            </button>
          ))}
          <select aria-label="Project" value={project ?? ""} onChange={(event) => setProject(event.target.value || null)}>
            <option value="">Every project</option>
            {props.projects.map((folder) => <option key={folder} value={folder}>{folder.split(/[\\/]/).filter(Boolean).pop()}</option>)}
          </select>
        </div>
        <div className="activity-list" role="list" aria-label="What the agent did">
          {lists === null ? <p className="empty small">Reading the threads…</p> : null}
          {lists !== null && shown.length === 0 ? <p className="empty small">Nothing to show for these filters.</p> : null}
          {shown.slice(0, 400).map((entry) => {
            const day = dayLabel(entry.at);
            const heading = day !== lastDay ? <h3 key={`day-${day}`} className="activity-day">{day}</h3> : null;
            lastDay = day;
            return [
              heading,
              <button key={entry.key} role="listitem" className={`activity-row${entry.failed ? " failed" : ""}`} onClick={() => { props.onClose(); props.onOpen(entry.threadId); }} title={`${entry.thread} — ${entry.cwd}`}>
                <span className="activity-time">{new Date(entry.at * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
                <Icon name={ICONS[entry.kind]} size={14} />
                <span className="activity-summary">{entry.summary}</span>
                <span className="activity-thread">{entry.thread}</span>
              </button>,
            ];
          })}
        </div>
      </section>
    </div>
  );
}
