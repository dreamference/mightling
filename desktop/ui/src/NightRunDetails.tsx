// What a finished Night Shift run left, as Scheduled shows it under the run's row: its result (the
// last message), its changes and its transcript, read through the main process (night.ts
// `taskDetails`), from the queue or, for a run `ling-admin night archive` moved, from the archive
// (DESKTOP §16 B41). Shared by the inbox and the archive's search.

import { useEffect, useState } from "react";

import * as bridge from "./bridge";
import { renderMarkdown } from "./markdown";

type Details = Awaited<ReturnType<typeof bridge.night.details>>;

export function NightRunDetails(props: {
  entry: bridge.NightTask;
  /** Read from `night/archive/` rather than the queue. */
  archived?: boolean;
  notice: (error: unknown) => void;
  /** Opens the run's session as a thread in Work (Scheduled closes first). */
  onOpenThread?: (threadId: string) => void;
}) {
  const { entry, notice } = props;
  const [tab, setTab] = useState<"result" | "diff" | "transcript">("result");
  const [details, setDetails] = useState<Details | null>(null);
  useEffect(() => {
    let current = true;
    bridge.night.details(entry.id, props.archived === true).then((answer) => { if (current) setDetails(answer); }).catch(notice);
    return () => { current = false; };
  }, [entry.id, props.archived]);
  const name = (folder: string) => folder.split(/[\\/]/).filter(Boolean).pop() ?? folder;

  return (
    <>
      <nav className="review-scopes" aria-label="What the run left">
        {(["result", "diff", "transcript"] as const).map((each) => (
          <button key={each} className={tab === each ? "selected" : ""} aria-pressed={tab === each} onClick={() => setTab(each)}>
            {{ result: "Result", diff: "Changes", transcript: "Transcript" }[each]}
          </button>
        ))}
        {details?.session && props.onOpenThread ? <button className="link-button" onClick={() => props.onOpenThread?.(details.session!)}>Open as a thread</button> : null}
      </nav>
      {props.archived ? <p className="planned-note">Archived: the run's branch stays, <code>{entry.branch}</code> in {name(entry.repo)}.</p> : entry.status === "done" ? <p className="planned-note">Not merged: the changes are on <code>{entry.branch}</code>. <code>ling night apply {entry.id}</code> in {name(entry.repo)} brings them into your checkout as uncommitted changes.</p> : null}
      {details === null ? <p className="empty small">Reading the run…</p> : tab === "result" ? (
        details.result ? <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(details.result) }} /> : <p className="empty small">The run left no message.</p>
      ) : tab === "diff" ? (
        details.diff ? <pre className="diff night-diff">{details.diff.split("\n").map((line, index) => <span key={index} className={line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : line.startsWith("@@") ? "hunk" : ""}>{line}{"\n"}</span>)}</pre> : <p className="empty small">No changes.</p>
      ) : (
        details.transcript ? <pre className="night-transcript">{details.transcript}</pre> : <p className="empty small">No transcript was kept.</p>
      )}
    </>
  );
}
