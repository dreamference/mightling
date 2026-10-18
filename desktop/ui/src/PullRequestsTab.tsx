// The pull-request tab (DESKTOP_PARITY §2.5, Phase 3 item 9): the vendor's Code Review plugin, the
// Mightling way: the user's own GitHub CLI in the thread's repository (desktop/electron/src/prs.ts),
// optional and off by default (Settings › Git), refused at `/airgapped on`. An inbox (review
// requested, mine, every open one); a pull request's summary, checks and changes; Review with
// Mightling and Fix a failing check put a request in the composer (the local model does the work,
// with `gh` in its own sandbox); a review is submitted as the user, after a confirmation.

import { useCallback, useEffect, useState } from "react";

import * as bridge from "./bridge";
import { copyText } from "./clipboard";
import { parseDiff } from "./diff";
import { Icon } from "./icons";
import { renderMarkdown } from "./markdown";

type Inbox = "review" | "mine" | "open";

const INBOXES: { id: Inbox; label: string }[] = [
  { id: "review", label: "Review requested" },
  { id: "mine", label: "Mine" },
  { id: "open", label: "All open" },
];

type Check = NonNullable<bridge.PullRequest["statusCheckRollup"]>[number];

/** A check in words: its name, and passed / failed / running. */
export function checkState(check: Check): { name: string; state: "passed" | "failed" | "running" | "skipped" } {
  const name = check.name ?? check.context ?? check.workflowName ?? "check";
  const verdict = (check.conclusion ?? check.state ?? check.status ?? "").toUpperCase();
  if (["SUCCESS", "NEUTRAL"].includes(verdict)) return { name, state: "passed" };
  if (["FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"].includes(verdict)) return { name, state: "failed" };
  if (verdict === "SKIPPED") return { name, state: "skipped" };
  return { name, state: "running" };
}

/** What Review with Mightling puts in the composer. */
export function reviewRequest(pr: bridge.PullRequest): string {
  return `Review pull request #${pr.number}, "${pr.title}" (${pr.headRefName} into ${pr.baseRefName}). Read it with \`gh pr view ${pr.number}\` and \`gh pr diff ${pr.number}\`, then list what should change before it merges, by file and line, the most serious first. Do not push anything or comment on GitHub.`;
}

/** What Fix puts in the composer for a failing check. */
export function fixRequest(pr: bridge.PullRequest, check: string): string {
  return `The check "${check}" fails on pull request #${pr.number} (branch ${pr.headRefName}). Find out why with \`gh pr checks ${pr.number}\` and \`gh run view --log-failed\`, then fix it on ${pr.headRefName} and run the check's command locally. Do not push.`;
}

export function PullRequestsTab(props: { root: string | null; thread: string | null; onCompose: (text: string) => void; notice: (error: unknown) => void }) {
  const { root, thread, notice } = props;
  const [inbox, setInbox] = useState<Inbox>("review");
  const [list, setList] = useState<bridge.PullRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<bridge.PullRequest | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    if (!root) return;
    setList(null);
    setError(null);
    bridge.prs.list(root, inbox, thread).then(setList).catch((failure) => { setList([]); setError(failure instanceof Error ? failure.message : String(failure)); });
  }, [root, inbox, thread]);
  useEffect(load, [load]);

  const open = (pr: bridge.PullRequest) => {
    if (!root) return;
    setChosen(pr);
    setDiff(null);
    setBody("");
    bridge.prs.view(root, pr.number, thread).then(setChosen).catch(notice);
    bridge.prs.diff(root, pr.number, thread).then(setDiff).catch((failure) => setDiff(`(the changes could not be read: ${failure instanceof Error ? failure.message : String(failure)})`));
  };

  const submit = async (verdict: "comment" | "approve" | "request-changes") => {
    if (!root || !chosen) return;
    const words = { comment: "comment on", approve: "approve", "request-changes": "request changes on" }[verdict];
    if (!window.confirm(`Submit a review to GitHub as you: ${words} #${chosen.number}?`)) return;
    setBusy(true);
    try {
      notice(await bridge.prs.review(root, chosen.number, verdict, body, thread));
      setBody("");
      open(chosen);
    } catch (failure) {
      notice(failure);
    } finally {
      setBusy(false);
    }
  };

  if (!root) return <p className="empty small">Open a thread in a GitHub repository to see its pull requests.</p>;
  const files = diff && !diff.startsWith("(") ? parseDiff(diff) : [];
  const checks = (chosen?.statusCheckRollup ?? []).map(checkState);

  return (
    <div className="prs-tab">
      <div className="prs-list">
        <nav className="review-scopes" aria-label="Inbox">
          {INBOXES.map((entry) => <button key={entry.id} className={inbox === entry.id ? "selected" : ""} aria-pressed={inbox === entry.id} onClick={() => setInbox(entry.id)}>{entry.label}</button>)}
          <button className="icon" aria-label="Read the pull requests again" title="Read again" onClick={load}><Icon name="refresh" size={13} /></button>
        </nav>
        {error ? <div className="notice">{error}</div> : null}
        {list === null ? <p className="empty small">Asking GitHub…</p> : list.length === 0 && !error ? <p className="empty small">No pull requests here.</p> : null}
        <ul aria-label="Pull requests">
          {(list ?? []).map((pr) => (
            <li key={pr.number}>
              <button className={`pr-row${chosen?.number === pr.number ? " selected" : ""}`} onClick={() => open(pr)}>
                <span className="pr-number">#{pr.number}</span>
                <span className="pr-title">{pr.title}{pr.isDraft ? <span className="planned-note"> draft</span> : null}</span>
                <span className="pr-meta">{pr.author?.login ?? ""} · {pr.headRefName}{pr.reviewDecision ? ` · ${pr.reviewDecision.toLowerCase().replace(/_/g, " ")}` : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      {chosen ? (
        <section className="pr-detail" aria-label={`Pull request #${chosen.number}`}>
          <h3>#{chosen.number} {chosen.title}</h3>
          <p className="pr-meta">{chosen.author?.login} wants to merge <code>{chosen.headRefName}</code> into <code>{chosen.baseRefName}</code>{chosen.changedFiles !== undefined ? ` · ${chosen.changedFiles} files, +${chosen.additions} −${chosen.deletions}` : ""}</p>
          <div className="buttons">
            <button className="primary" onClick={() => props.onCompose(reviewRequest(chosen))}>Review with Mightling</button>
            <button onClick={() => void copyText(chosen.url)}>Copy the link</button>
          </div>
          {chosen.body ? <div className="markdown pr-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(chosen.body) }} /> : null}
          {checks.length ? (
            <>
              <h4>Checks</h4>
              <ul className="pr-checks" aria-label="Checks">
                {checks.map((check) => (
                  <li key={check.name} className={`check-${check.state}`}>
                    <span className="check-state">{check.state}</span> {check.name}
                    {check.state === "failed" ? <button className="link-button" onClick={() => props.onCompose(fixRequest(chosen, check.name))}>Fix</button> : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          <h4>Changes</h4>
          {diff === null ? <p className="empty small">Reading the changes…</p> : diff.startsWith("(") ? <p className="empty small">{diff}</p> : (
            files.map((file) => (
              <details key={file.path} className="review-file pr-file" open={files.length <= 6}>
                <summary><span className="file-name">{file.path}</span> <span className="diffstat"><span className="plus">+{file.added}</span> <span className="minus">−{file.removed}</span></span></summary>
                <table className="diff-table"><colgroup><col style={{ width: 44 }} /><col style={{ width: 44 }} /><col /></colgroup><tbody>
                  {file.hunks.flatMap((hunk, index) => [
                    <tr key={`h${index}`} className="hunk-row"><td colSpan={3} className="code note">{hunk.header}</td></tr>,
                    ...hunk.lines.map((line, at) => (
                      <tr key={`${index}-${at}`}><td className="ln">{line.oldNo ?? ""}</td><td className="ln">{line.newNo ?? ""}</td><td className={`code ${line.kind}`}><span className="sign">{line.kind === "add" ? "+" : line.kind === "del" ? "-" : " "}</span>{line.text}</td></tr>
                    )),
                  ])}
                </tbody></table>
              </details>
            ))
          )}
          <h4>Your review</h4>
          <textarea rows={3} value={body} placeholder="A comment for the author (needed to comment or request changes)" aria-label="Your review" onChange={(event) => setBody(event.target.value)} />
          <div className="buttons">
            <button disabled={busy || !body.trim()} onClick={() => void submit("comment")}>Comment</button>
            <button disabled={busy} onClick={() => void submit("approve")}>Approve</button>
            <button className="danger" disabled={busy || !body.trim()} onClick={() => void submit("request-changes")}>Request changes</button>
          </div>
        </section>
      ) : <p className="empty small pr-detail">Choose a pull request.</p>}
    </div>
  );
}
