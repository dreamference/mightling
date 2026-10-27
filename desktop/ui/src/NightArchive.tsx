// Scheduled's archive (DESKTOP §16 B41): the runs and reports `ling-admin night archive` moved to
// `~/.mightling/night/archive/`, searched by words through the main process (nightinbox.ts
// `searchArchive`). A run opens on what it left (NightRunDetails), a report in full. Read only.

import { Fragment, useEffect, useState } from "react";

import * as bridge from "./bridge";
import { renderMarkdown } from "./markdown";
import { NightRunDetails } from "./NightRunDetails";

type Found = Awaited<ReturnType<typeof bridge.night.archive>>;

/** How long typing pauses before the archive is searched, in milliseconds. */
const SEARCH_PAUSE = 250;

export function NightArchive(props: { notice: (error: unknown) => void; onOpenThread?: (threadId: string) => void }) {
  const { notice } = props;
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<Found | null>(null);
  const [run, setRun] = useState<string | null>(null);
  const [report, setReport] = useState<{ name: string; text: string | null } | null>(null);
  useEffect(() => {
    let current = true;
    const timer = setTimeout(() => {
      bridge.night.archive(query.trim()).then((answer) => { if (current) setFound(answer); }).catch((error) => { if (current) setFound({ tasks: [], reports: [] }); notice(error); });
    }, query ? SEARCH_PAUSE : 0);
    return () => { current = false; clearTimeout(timer); };
  }, [query]);
  const name = (folder: string) => folder.split(/[\\/]/).filter(Boolean).pop() ?? folder;
  const day = (file: string) => file.replace(/\.md$/, "");

  return (
    <section className="night-archive" aria-label="Archive">
      <h3>Archive</h3>
      <p className="planned-note">Runs and reports older than a few weeks, moved here by <code>ling-admin night archive</code>; their branches stay.</p>
      <input type="search" value={query} placeholder="Search the archive" aria-label="Search the archive" onChange={(event) => setQuery(event.target.value)} />
      {found === null ? null : found.tasks.length === 0 && found.reports.length === 0 ? (
        <p className="empty small">{query.trim() ? "Nothing in the archive holds these words." : "The archive is empty."}</p>
      ) : (
        <table className="shortcut-table">
          <tbody>
            {found.tasks.map((entry) => (
              <Fragment key={entry.id}>
                <tr className={`night-run archived${run === entry.id ? " open" : ""}`}>
                  <td><button className="link-button" aria-expanded={run === entry.id} onClick={() => setRun(run === entry.id ? null : entry.id)}>{entry.task.split("\n")[0]}</button></td>
                  <td className="planned-note">{name(entry.repo)} · <code>{entry.branch}</code>{entry.at ? ` · ${entry.at.slice(0, 10)}` : ""}</td>
                  <td><span className={`night-status ${entry.status}`}>{entry.status}</span></td>
                </tr>
                {run === entry.id ? <tr className="night-details"><td colSpan={3}><NightRunDetails entry={entry} archived notice={notice} onOpenThread={props.onOpenThread} /></td></tr> : null}
              </Fragment>
            ))}
            {found.reports.map((entry) => (
              <Fragment key={entry.name}>
                <tr className="night-archived-report">
                  <td><button className="link-button" aria-expanded={report?.name === entry.name} onClick={() => {
                    if (report?.name === entry.name) return setReport(null);
                    setReport({ name: entry.name, text: null });
                    bridge.night.report(entry.name, true).then((text) => setReport((current) => (current?.name === entry.name ? { ...current, text } : current))).catch(notice);
                  }}>The report of {day(entry.name)}</button></td>
                  <td className="planned-note" colSpan={2}>{entry.lines.join(" … ")}</td>
                </tr>
                {report?.name === entry.name ? (
                  <tr className="night-details"><td colSpan={3}>
                    {report.text === null ? <p className="empty small">Reading the report…</p> : <div className="markdown night-report" dangerouslySetInnerHTML={{ __html: renderMarkdown(report.text) }} />}
                  </td></tr>
                ) : null}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
