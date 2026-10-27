// The pull-request tab (DESKTOP_PARITY §2.5, Phase 3 item 9): the vendor's Code Review plugin, the
// Mightling way: the user's own GitHub CLI in the thread's repository (desktop/electron/src/prs.ts),
// optional and off by default (Settings › Git), refused at `/airgapped on`. An inbox (review
// requested, mine, every open one); a pull request's summary, checks and changes; Review with
// Mightling and Fix a failing check put a request in the composer (the local model does the work,
// with `gh` in its own sandbox); a review is submitted as the user, after a confirmation. DESKTOP §16
// B32: the pull request's comments and reviews (`gh pr view --json comments,reviews`), and Create pull
// request (`gh pr create`, after a confirmation) with a description the local model can write from the
// branch's changes and Settings › Git's instruction; both refused at `/airgapped on` by main.ts.

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

/** One comment or review, as the conversation lists it. */
export interface ActivityEntry {
  key: string;
  who: string;
  what: string;
  when: string;
  body: string;
}

const REVIEW_WORDS: Record<string, string> = { APPROVED: "approved", CHANGES_REQUESTED: "requested changes", COMMENTED: "reviewed", DISMISSED: "reviewed (dismissed)" };

/** Comments and submitted reviews in the order they were made; a pending review (the user's own draft) is left out. */
export function activityEntries(activity: bridge.PullRequestActivity | null | undefined): ActivityEntry[] {
  const comments = (activity?.comments ?? []).map((comment, index) => ({ key: `c${index}`, who: comment.author?.login ?? "someone", what: "commented", when: comment.createdAt ?? "", body: comment.body ?? "" }));
  const reviews = (activity?.reviews ?? []).filter((review) => review.state !== "PENDING")
    .map((review, index) => ({ key: `r${index}`, who: review.author?.login ?? "someone", what: REVIEW_WORDS[review.state] ?? "reviewed", when: review.submittedAt ?? "", body: review.body ?? "" }));
  return [...comments, ...reviews].sort((a, b) => a.when.localeCompare(b.when));
}

/** What Create pull request's form holds. */
interface Creating {
  title: string;
  body: string;
  base: string;
  draft: boolean;
}

export function PullRequestsTab(props: {
  root: string | null; thread: string | null; onCompose: (text: string) => void; notice: (error: unknown) => void;
  /** DESKTOP §16 B32: whether the thread is at `/airgapped on` (Create is refused there), and the local model's description of the branch's changes. */
  airgapOn?: boolean; describe?: ((root: string, title: string, diff: string) => Promise<string>) | null;
}) {
  const { root, thread, notice } = props;
  const [inbox, setInbox] = useState<Inbox>("review");
  const [list, setList] = useState<bridge.PullRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<bridge.PullRequest | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  /** The chosen pull request's comments and reviews; a string says why they could not be read. */
  const [activity, setActivity] = useState<bridge.PullRequestActivity | string | null>(null);
  const [creating, setCreating] = useState<Creating | null>(null);
  const [writing, setWriting] = useState(false);

  const load = useCallback(() => {
    if (!root) return;
    setList(null);
    setError(null);
    bridge.prs.list(root, inbox, thread).then(setList).catch((failure) => { setList([]); setError(failure instanceof Error ? failure.message : String(failure)); });
  }, [root, inbox, thread]);
  useEffect(load, [load]);

  const open = (pr: bridge.PullRequest) => {
    if (!root) return;
    setCreating(null);
    setChosen(pr);
    setDiff(null);
    setBody("");
    setActivity(null);
    bridge.prs.view(root, pr.number, thread).then(setChosen).catch(notice);
    bridge.prs.activity(root, pr.number, thread).then(setActivity).catch((failure) => setActivity(`(the comments could not be read: ${failure instanceof Error ? failure.message : String(failure)})`));
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

  const startCreating = () => {
    if (!root) return;
    setChosen(null);
    setCreating({ title: "", body: "", base: "", draft: false });
    // The title starts as the branch's last commit's subject.
    bridge.git.log(root).then((log) => setCreating((form) => (form && !form.title && log[0] ? { ...form, title: log[0].subject } : form))).catch(() => {});
  };

  const writeDescription = async () => {
    if (!root || !creating || !props.describe) return;
    setWriting(true);
    try {
      const branch = await bridge.git.diff(root, "branch");
      if (!branch.against) throw new Error("there is no base branch to compare this branch with");
      if (!branch.diff.trim()) throw new Error(`this branch changes nothing against ${branch.against}`);
      const text = await props.describe(root, creating.title, branch.diff);
      setCreating((form) => (form ? { ...form, body: text } : form));
    } catch (failure) {
      notice(failure);
    } finally {
      setWriting(false);
    }
  };

  const create = async () => {
    if (!root || !creating || !creating.title.trim()) return;
    const base = creating.base.trim();
    if (!window.confirm(`Create a pull request on GitHub as you: "${creating.title.trim()}" from this branch${base ? ` into ${base}` : ""}${creating.draft ? ", as a draft" : ""}?`)) return;
    setBusy(true);
    try {
      const url = await bridge.prs.create(root, { title: creating.title.trim(), body: creating.body, base: base || null, draft: creating.draft }, thread);
      notice(url ? `Pull request created: ${url}` : "Pull request created.");
      setCreating(null);
      load();
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
          <button className={creating ? "selected" : ""} disabled={props.airgapOn} title={props.airgapOn ? "Airgapped is on: creating a pull request would reach the network" : "gh pr create, from this branch"} onClick={startCreating}>Create pull request</button>
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
          <h4>Comments and reviews</h4>
          {activity === null ? <p className="empty small">Reading the conversation…</p> : typeof activity === "string" ? <p className="empty small">{activity}</p> : activityEntries(activity).length === 0 ? <p className="empty small">No comments or reviews yet.</p> : (
            <ul className="pr-activity" aria-label="Comments and reviews">
              {activityEntries(activity).map((entry) => (
                <li key={entry.key}>
                  <div className="pr-meta"><strong>{entry.who}</strong> {entry.what}{entry.when ? ` · ${new Date(entry.when).toLocaleString()}` : ""}</div>
                  {entry.body ? <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(entry.body) }} /> : null}
                </li>
              ))}
            </ul>
          )}
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
      ) : creating ? (
        <section className="pr-detail pr-create" aria-label="Create a pull request">
          <h3>Create a pull request</h3>
          <p className="pr-meta">From the branch checked out here, as you, through <code>gh pr create</code>. Push the branch first (the review pane's Push).</p>
          <label className="field">Title
            <input type="text" value={creating.title} aria-label="Title" onChange={(event) => setCreating({ ...creating, title: event.target.value })} />
          </label>
          <label className="field">Description
            <textarea rows={8} value={creating.body} aria-label="Description" placeholder="What changes and why (Markdown)" onChange={(event) => setCreating({ ...creating, body: event.target.value })} />
          </label>
          {props.describe ? (
            <div className="buttons">
              <button className="link-button" disabled={writing} title="The local model writes it from the branch's changes, following Settings › Git" onClick={() => void writeDescription()}>{writing ? "Writing…" : "Write the description"}</button>
            </div>
          ) : null}
          <label className="field">Into
            <input type="text" value={creating.base} aria-label="Base branch" placeholder="the repository's default branch" onChange={(event) => setCreating({ ...creating, base: event.target.value })} />
          </label>
          <label><input type="checkbox" checked={creating.draft} onChange={(event) => setCreating({ ...creating, draft: event.target.checked })} /> A draft</label>
          <div className="buttons">
            <button className="primary" disabled={busy || props.airgapOn || !creating.title.trim()} onClick={() => void create()}>Create</button>
            <button onClick={() => setCreating(null)}>Cancel</button>
          </div>
        </section>
      ) : <p className="empty small pr-detail">Choose a pull request.</p>}
    </div>
  );
}
