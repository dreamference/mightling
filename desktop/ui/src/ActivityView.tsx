// The Activity view (Ctrl+Alt+U). Its main tab (DESKTOP_PARITY §4.5 item 17, decided 2026-10-21) is
// what needs the user: threads waiting for an answer, working, or unread, filtered by Work, Ask,
// Pinned and Scheduled, with Mark all as read (attention.ts). Its second is the timeline of Phase 3
// item 5: what the agent did across threads, newest first and by day (activity.ts). A row opens its thread.

import { useEffect, useMemo, useState } from "react";

import { KIND_LABELS, activityOf, dayLabel, merged, type ActivityEntry, type ActivityKind } from "./activity";
import { STATE_LABELS, filtered, type AttentionEntry, type AttentionFilter } from "./attention";
import { Icon, type IconName } from "./icons";
import type { Thread } from "./protocol/v2/Thread";
import type { Turn } from "./protocol/v2/Turn";
import type { RpcClient } from "./rpc";

/** How many of the most recent threads the view reads, and how many turns of each. */
const THREADS = 20;
const TURNS = 12;

const ICONS: Record<ActivityKind, IconName> = { command: "terminal", files: "fileDiff", tool: "settings", web: "globe", agents: "gitFork" };

export interface NeedsYou {
  entries: AttentionEntry[];
  /** Night Shift's finished tasks not yet seen (Scheduled). */
  scheduled: number;
  label: (thread: Thread) => string;
  onMarkAllRead: () => void;
  onOpenScheduled: () => void;
}

export function ActivityView(props: { client: RpcClient; threads: Thread[]; projects: string[]; needs: NeedsYou; onOpen: (threadId: string) => void; onClose: () => void }) {
  const [tab, setTab] = useState<"needs" | "timeline">("needs");
  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog activity" role="dialog" aria-label="Activity" onClick={(event) => event.stopPropagation()}>
        <header><h2>Activity</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <nav className="review-scopes" aria-label="Activity">
          <button className={tab === "needs" ? "selected" : ""} aria-pressed={tab === "needs"} onClick={() => setTab("needs")}>Needs you{props.needs.entries.length + props.needs.scheduled ? ` (${props.needs.entries.length + props.needs.scheduled})` : ""}</button>
          <button className={tab === "timeline" ? "selected" : ""} aria-pressed={tab === "timeline"} onClick={() => setTab("timeline")}>What the agent did</button>
        </nav>
        {tab === "needs" ? <NeedsYouTab {...props} /> : <Timeline {...props} />}
      </section>
    </div>
  );
}

const FILTER_LABELS: Record<AttentionFilter, string> = { work: "Work", ask: "Ask", pinned: "Pinned" };

function NeedsYouTab(props: { needs: NeedsYou; onOpen: (threadId: string) => void; onClose: () => void }) {
  const { needs } = props;
  const [filters, setFilters] = useState<Set<AttentionFilter | "scheduled">>(new Set());
  const threadFilters = new Set([...filters].filter((filter): filter is AttentionFilter => filter !== "scheduled"));
  // Scheduled alone shows only Night Shift's line; with other filters, both.
  const shown = filters.size === 1 && filters.has("scheduled") ? [] : filtered(needs.entries, threadFilters);
  const showScheduled = needs.scheduled > 0 && (filters.size === 0 || filters.has("scheduled"));
  const toggle = (filter: AttentionFilter | "scheduled") => setFilters((set) => {
    const next = new Set(set);
    if (next.has(filter)) next.delete(filter);
    else next.add(filter);
    return next;
  });
  const unread = needs.entries.some((entry) => entry.states.includes("unread"));
  return (
    <>
      <div className="activity-filters">
        {(["work", "ask", "pinned", "scheduled"] as const).map((filter) => (
          <button key={filter} className={`chip-toggle${filters.has(filter) ? " on" : ""}`} aria-pressed={filters.has(filter)} onClick={() => toggle(filter)}>
            {filter === "scheduled" ? "Scheduled" : FILTER_LABELS[filter]}
          </button>
        ))}
        <span className="spacer" />
        <button className="link-button" disabled={!unread && needs.scheduled === 0} onClick={needs.onMarkAllRead}>Mark all as read</button>
      </div>
      <div className="activity-list" role="list" aria-label="What needs you">
        {shown.length === 0 && !showScheduled ? <p className="empty small">Nothing needs you: no thread is waiting, working or unread.</p> : null}
        {showScheduled ? (
          <button role="listitem" className="activity-row attention-row" onClick={() => { props.onClose(); needs.onOpenScheduled(); }}>
            <Icon name="clock" size={14} />
            <span className="activity-summary">Night Shift finished {needs.scheduled} task{needs.scheduled === 1 ? "" : "s"} you have not seen</span>
            <span className="attention-state unread">Scheduled</span>
          </button>
        ) : null}
        {shown.map((entry) => (
          <button key={entry.thread.id} role="listitem" className="activity-row attention-row" onClick={() => { props.onClose(); props.onOpen(entry.thread.id); }}
            title={entry.thread.cwd}>
            <Icon name={entry.ask ? "messageSquare" : "squarePen"} size={14} />
            <span className="activity-summary">{needs.label(entry.thread)}</span>
            {entry.states.map((state) => <span key={state} className={`attention-state ${state}`}>{STATE_LABELS[state]}</span>)}
          </button>
        ))}
      </div>
    </>
  );
}

function Timeline(props: { client: RpcClient; threads: Thread[]; projects: string[]; onOpen: (threadId: string) => void; onClose: () => void }) {
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
    <>
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
    </>
  );
}
