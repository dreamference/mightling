// The review pane (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.5): the repository's state
// beside the thread, not only the agent's edits. Scopes Unstaged (the default), Staged, Commit,
// Branch (against the base) from window-side git, and Last turn from the server's
// `turn/diff/updated`; a repository selector; stage, unstage and revert by whole diff, file and
// hunk; unified or split; inline comments sent to the composer; viewed marks; a file opened in the
// chosen editor (Ctrl+click a line for that line); commit with a generated message, push, and the
// forge's pull-request page. In a browser on `ling web` only Last turn is shown.

import { Fragment, useCallback, useEffect, useMemo, useState, type MouseEvent } from "react";

import * as bridge from "./bridge";
import { commentsMessage, fingerprint, hunkPatch, parseDiff, splitRows, type DiffLine, type FileDiff, type ReviewComment } from "./diff";
import { Icon } from "./icons";

export type ReviewScope = bridge.GitScope | "turn";

const SCOPES: { id: ReviewScope; label: string }[] = [
  { id: "unstaged", label: "Unstaged" },
  { id: "staged", label: "Staged" },
  { id: "commit", label: "Commit" },
  { id: "branch", label: "Branch" },
  { id: "turn", label: "Last turn" },
];

export interface ReviewPaneProps {
  cwd: string | null;
  threadId: string | null;
  /** The shown thread's last turn's diff, for the Last turn scope. */
  turnDiff: string | null;
  airgapOn: boolean;
  layout: "unified" | "split";
  setLayout: (layout: "unified" | "split") => void;
  editor: string | null;
  setEditor: (editor: string | null) => void;
  /** Bumped when a turn ends, so the pane reads the repository again. */
  refreshKey: number;
  onComments: (text: string) => void;
  onRevertTurn: (() => void) | null;
  generateMessage: (root: string, diff: string) => Promise<string>;
  /** Settings › Git: Force ticked to begin with. */
  forcePushDefault: boolean;
  notice: (error: unknown) => void;
  /** Opens a changed file in a file tab of its own (Phase 3 item 1), at a line or the top. */
  onOpenFile: ((path: string, line: number | null) => void) | null;
}

interface Draft {
  path: string;
  line: number;
  side: "new" | "old";
  quote: string;
  text: string;
}

export function ReviewPane(props: ReviewPaneProps) {
  const windowGit = bridge.git.available();
  const [repos, setRepos] = useState<string[]>([]);
  const [root, setRoot] = useState<string | null>(null);
  const [scope, setScope] = useState<ReviewScope>(windowGit ? "unstaged" : "turn");
  const [diff, setDiff] = useState("");
  const [against, setAgainst] = useState<string | null>(null);
  const [status, setStatus] = useState<bridge.GitStatus | null>(null);
  const [commits, setCommits] = useState<bridge.GitCommit[]>([]);
  const [commit, setCommit] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [viewed, setViewed] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [comments, setComments] = useState<ReviewComment[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [message, setMessage] = useState("");
  const [includeUnstaged, setIncludeUnstaged] = useState(false);
  const [forcePush, setForcePush] = useState(props.forcePushDefault);
  const [editors, setEditors] = useState<bridge.Editor[]>([]);
  const [error, setError] = useState<string | null>(null);
  const { notice } = props;

  // The repositories of the thread's folder.
  useEffect(() => {
    if (!windowGit || !props.cwd) {
      setRepos([]);
      setRoot(null);
      return;
    }
    let current = true;
    bridge.git.repos(props.cwd).then((found) => {
      if (!current) return;
      setRepos(found);
      setRoot((chosen) => (chosen && found.includes(chosen) ? chosen : found[0] ?? null));
    }).catch(notice);
    bridge.listEditors().then((found) => current && setEditors(found)).catch(() => {});
    return () => {
      current = false;
    };
  }, [props.cwd, windowGit, notice]);

  const load = useCallback(async () => {
    setError(null);
    if (scope === "turn") {
      setDiff(props.turnDiff ?? "");
      setAgainst(null);
      return;
    }
    if (!root) {
      setDiff("");
      return;
    }
    try {
      const [answer, now] = await Promise.all([bridge.git.diff(root, scope, scope === "commit" ? commit : null), bridge.git.status(root)]);
      setDiff(answer.diff);
      setAgainst(answer.against);
      setStatus(now);
      if (scope === "commit" && commits.length === 0) setCommits(await bridge.git.log(root));
    } catch (failure) {
      setDiff("");
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  }, [scope, root, commit, commits.length, props.turnDiff]);

  useEffect(() => {
    void load();
  }, [load, props.refreshKey]);

  const files = useMemo(() => parseDiff(diff), [diff]);

  /** Runs a git step, then reads the repository again. */
  const step = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label);
    try {
      await action();
    } catch (failure) {
      notice(failure);
    } finally {
      setBusy(null);
      await load();
    }
  };

  const confirmRevert = (what: string) => window.confirm(`Throw away ${what}? This cannot be undone from here.`);

  const fileActions = (file: FileDiff) => {
    if (!root || scope === "turn" || scope === "commit" || scope === "branch") return null;
    if (scope === "unstaged") {
      return (
        <>
          <button className="link-button" onClick={() => void step("stage", () => bridge.git.stage(root, [file.path]))}>Stage</button>
          <button className="link-button danger" onClick={() => confirmRevert(`the changes to ${file.path}`) && void step("revert", () => bridge.git.discard(root, [file.path]))}>Revert</button>
        </>
      );
    }
    return <button className="link-button" onClick={() => void step("unstage", () => bridge.git.unstage(root, [file.path]))}>Unstage</button>;
  };

  const hunkActions = (file: FileDiff, index: number) => {
    if (!root || (scope !== "unstaged" && scope !== "staged")) return null;
    const patch = hunkPatch(file, index);
    if (scope === "unstaged") {
      return (
        <>
          <button className="link-button" onClick={() => void step("stage", () => bridge.git.apply(root, patch, { cached: true }))}>Stage hunk</button>
          <button className="link-button danger" onClick={() => confirmRevert("this hunk") && void step("revert", () => bridge.git.apply(root, patch, { reverse: true }))}>Revert hunk</button>
        </>
      );
    }
    return (
      <>
        <button className="link-button" onClick={() => void step("unstage", () => bridge.git.apply(root, patch, { cached: true, reverse: true }))}>Unstage hunk</button>
        <button className="link-button danger" onClick={() => confirmRevert("this hunk") && void step("revert", () => bridge.git.apply(root, patch, { index: true, reverse: true }))}>Revert hunk</button>
      </>
    );
  };

  const open = (file: FileDiff, line: number | null) => {
    const base = root ?? props.cwd;
    if (!base || !windowGit) return;
    bridge.openInEditor(base, file.path, line, props.editor).catch(notice);
  };

  const onLineClick = (event: MouseEvent, file: FileDiff, line: DiffLine) => {
    if (event.ctrlKey || event.metaKey) open(file, line.newNo ?? line.oldNo);
  };

  const startComment = (file: FileDiff, line: DiffLine) => {
    const number = line.newNo ?? line.oldNo;
    if (number === null) return;
    setDraft({ path: file.path, line: number, side: line.newNo === null ? "old" : "new", quote: line.text, text: "" });
  };

  const lineCells = (file: FileDiff, line: DiffLine | null, side: "both" | "left" | "right") => {
    if (!line) return <td className="diff-empty" colSpan={side === "both" ? 3 : 2} />;
    const sign = line.kind === "add" ? "+" : line.kind === "del" ? "-" : line.kind === "note" ? "" : " ";
    return (
      <>
        {side !== "right" ? <td className="ln">{side === "both" ? line.oldNo ?? "" : line.oldNo ?? line.newNo ?? ""}</td> : <td className="ln">{line.newNo ?? ""}</td>}
        {side === "both" ? <td className="ln">{line.newNo ?? ""}</td> : null}
        <td className={`code ${line.kind}`} onClick={(event) => onLineClick(event, file, line)} title="Ctrl+click to open the file at this line">
          {line.kind !== "note" ? <button className="add-comment" aria-label="Comment on this line" onClick={(event) => { event.stopPropagation(); startComment(file, line); }}>+</button> : null}
          <span className="sign">{sign}</span>{line.text}
        </td>
      </>
    );
  };

  const commentsAt = (file: FileDiff, line: DiffLine | null) => {
    if (!line) return [];
    const number = line.newNo ?? line.oldNo;
    const side = line.newNo === null ? "old" : "new";
    return comments.filter((comment) => comment.path === file.path && comment.line === number && comment.side === side);
  };

  const commentRows = (file: FileDiff, lines: (DiffLine | null)[], span: number) => {
    const here = lines.flatMap((line) => commentsAt(file, line));
    const drafting = draft && draft.path === file.path && lines.some((line) => line && (line.newNo ?? line.oldNo) === draft.line && (line.newNo === null ? "old" : "new") === draft.side);
    if (here.length === 0 && !drafting) return null;
    return (
      <tr className="comment-row">
        <td colSpan={span}>
          {here.map((comment, index) => (
            <div key={index} className="review-comment">
              <span>{comment.text}</span>
              <button className="icon" aria-label="Remove the comment" onClick={() => setComments((list) => list.filter((entry) => entry !== comment))}><Icon name="x" size={12} /></button>
            </div>
          ))}
          {drafting && draft ? (
            <div className="comment-draft">
              <textarea autoFocus rows={2} value={draft.text} placeholder="What should change here?" aria-label="Comment"
                onChange={(event) => setDraft({ ...draft, text: event.target.value })}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && draft.text.trim()) {
                    setComments((list) => [...list, { ...draft }]);
                    setDraft(null);
                  }
                  if (event.key === "Escape") setDraft(null);
                }} />
              <div className="buttons">
                <button className="primary" disabled={!draft.text.trim()} onClick={() => { setComments((list) => [...list, { ...draft }]); setDraft(null); }}>Add comment</button>
                <button onClick={() => setDraft(null)}>Cancel</button>
              </div>
            </div>
          ) : null}
        </td>
      </tr>
    );
  };

  const fileBlock = (file: FileDiff) => {
    const key = fingerprint(file);
    const isViewed = viewed.has(key);
    const isCollapsed = collapsed.has(file.path) || isViewed;
    const toggle = () => setCollapsed((set) => {
      const next = new Set(set);
      if (next.has(file.path)) next.delete(file.path);
      else next.add(file.path);
      return next;
    });
    return (
      <section key={file.path} className={`review-file${isViewed ? " viewed" : ""}`}>
        <header onClick={(event) => event.target === event.currentTarget && toggle()}>
          <button className="icon" aria-label={isCollapsed ? "Expand" : "Collapse"} onClick={toggle}><Icon name={isCollapsed ? "chevronRight" : "chevronDown"} size={14} /></button>
          <button className="file-name" title={windowGit ? "Open in the editor" : file.path} onClick={() => open(file, null)}>{file.path}</button>
          {props.onOpenFile && (root ?? props.cwd) && file.status !== "deleted" ? (
            <button className="icon" title="Open in a tab" aria-label={`Open ${file.path} in a tab`} onClick={() => props.onOpenFile?.(`${(root ?? props.cwd ?? "").replace(/\/+$/, "")}/${file.path}`, null)}><Icon name="file" size={13} /></button>
          ) : null}
          {file.status !== "modified" ? <span className={`file-status ${file.status}`}>{file.status}</span> : null}
          <span className="diffstat"><span className="plus">+{file.added}</span> <span className="minus">−{file.removed}</span></span>
          <span className="spacer" onClick={toggle} />
          {fileActions(file)}
          <label className="viewed-mark" title="Mark as viewed: folded until it changes">
            <input type="checkbox" checked={isViewed} onChange={() => setViewed((set) => {
              const next = new Set(set);
              if (next.has(key)) next.delete(key);
              else next.add(key);
              return next;
            })} />Viewed
          </label>
        </header>
        {isCollapsed ? null : file.binary ? <p className="empty small">A binary file.</p> : file.hunks.map((hunk, index) => (
          <div key={index} className="review-hunk">
            <div className="hunk-header"><code>{hunk.header}</code><span className="spacer" />{hunkActions(file, index)}</div>
            <table className={`diff-table ${props.layout}`}>
              <tbody>
                {props.layout === "split"
                  ? splitRows(hunk).map((row, rowIndex) => (
                    <Fragment key={rowIndex}>
                      <tr>{lineCells(file, row.left, "left")}{lineCells(file, row.right, "right")}</tr>
                      {commentRows(file, row.left === row.right ? [row.left] : [row.left, row.right], 4)}
                    </Fragment>
                  ))
                  : hunk.lines.map((line, lineIndex) => (
                    <Fragment key={lineIndex}>
                      <tr>{lineCells(file, line, "both")}</tr>
                      {commentRows(file, [line], 3)}
                    </Fragment>
                  ))}
              </tbody>
            </table>
          </div>
        ))}
      </section>
    );
  };

  const stagedCount = status?.files.filter((file) => file.staged !== "." && file.staged !== "?").length ?? 0;
  const changedCount = status?.files.length ?? 0;
  const canCommit = Boolean(root) && message.trim() !== "" && (stagedCount > 0 || (includeUnstaged && changedCount > 0));

  const generate = async () => {
    if (!root) return;
    setBusy("message");
    try {
      const source = includeUnstaged || stagedCount === 0
        ? (await bridge.git.diff(root, "staged")).diff + (await bridge.git.diff(root, "unstaged")).diff
        : (await bridge.git.diff(root, "staged")).diff;
      if (!source.trim()) throw new Error("there is nothing to describe");
      setMessage(await props.generateMessage(root, source));
    } catch (failure) {
      notice(failure);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="review-body">
      <header className="review-tools">
        {repos.length > 1 ? (
          <select aria-label="Repository" value={root ?? ""} onChange={(event) => setRoot(event.target.value)}>
            {repos.map((repo) => <option key={repo} value={repo}>{repo.split(/[\\/]/).pop()}</option>)}
          </select>
        ) : null}
        <span className="spacer" />
        <button className="icon" title={props.layout === "split" ? "Show the diff in one column" : "Show the old and the new side by side"} aria-label="Switch the view" onClick={() => props.setLayout(props.layout === "split" ? "unified" : "split")}>{props.layout === "split" ? "One column" : "Side by side"}</button>
        <button className="icon" title="Read the repository again" aria-label="Refresh" onClick={() => void load()}><Icon name="undo" size={14} /></button>
      </header>
      <nav className="review-scopes" aria-label="Scope">
        {SCOPES.filter((entry) => windowGit || entry.id === "turn").map((entry) => (
          <button key={entry.id} className={scope === entry.id ? "selected" : ""} aria-pressed={scope === entry.id} onClick={() => setScope(entry.id)}>{entry.label}</button>
        ))}
      </nav>
      {scope === "commit" ? (
        <select className="commit-choice" aria-label="Commit" value={commit ?? ""} onChange={(event) => setCommit(event.target.value || null)}>
          <option value="">HEAD{commits[0] ? `: ${commits[0].subject}` : ""}</option>
          {commits.slice(1).map((entry) => <option key={entry.sha} value={entry.sha}>{entry.sha.slice(0, 8)} {entry.subject}</option>)}
        </select>
      ) : null}
      <div className="review-status">
        {scope === "turn" ? "What the last turn changed, from the agent." : status ? (
          <>
            {status.branch ? <span><Icon name="gitFork" size={12} /> {status.branch}</span> : <span>detached at {status.head?.slice(0, 8) ?? "nothing yet"}</span>}
            {status.upstream ? <span>{status.ahead ? ` ↑${status.ahead}` : ""}{status.behind ? ` ↓${status.behind}` : ""}{!status.ahead && !status.behind ? " up to date" : ""} with {status.upstream}</span> : null}
            {against ? <span className="against">against {against}</span> : null}
          </>
        ) : !windowGit ? null : root ? "…" : "This folder is not in a git repository."}
      </div>
      {error ? <div className="notice">{error}</div> : null}
      {files.length > 0 && root && (scope === "unstaged" || scope === "staged") ? (
        <div className="review-all">
          {scope === "unstaged" ? (
            <>
              <button className="link-button" onClick={() => void step("stage", () => bridge.git.stage(root, files.map((file) => file.path)))}>Stage all</button>
              <button className="link-button danger" onClick={() => confirmRevert("every unstaged change") && void step("revert", () => bridge.git.discard(root, files.map((file) => file.path)))}>Revert all</button>
            </>
          ) : <button className="link-button" onClick={() => void step("unstage", () => bridge.git.unstage(root, files.map((file) => file.path)))}>Unstage all</button>}
        </div>
      ) : null}
      {scope === "turn" && props.onRevertTurn && files.length > 0 ? (
        <div className="review-all"><button className="link-button danger" onClick={props.onRevertTurn}>Revert the turn</button></div>
      ) : null}
      <div className="review-files">
        {files.length === 0 ? <p className="empty small">{scope === "turn" ? "The last turn changed no files." : "No changes."}</p> : files.map(fileBlock)}
      </div>
      {comments.length > 0 ? (
        <div className="review-send">
          <span>{comments.length} comment{comments.length > 1 ? "s" : ""}</span>
          <button className="primary" onClick={() => { props.onComments(commentsMessage(comments)); setComments([]); }}>Send to the composer</button>
          <button className="link-button" onClick={() => setComments([])}>Discard</button>
        </div>
      ) : null}
      {windowGit && root && (scope === "unstaged" || scope === "staged") ? (
        <footer className="commit-box">
          <textarea rows={3} value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Commit message" aria-label="Commit message" />
          <div className="commit-actions">
            <button className="link-button" disabled={busy !== null} onClick={() => void generate()}>{busy === "message" ? "Writing…" : "Write a message"}</button>
            <label><input type="checkbox" checked={includeUnstaged} onChange={(event) => setIncludeUnstaged(event.target.checked)} /> Include unstaged</label>
            <span className="spacer" />
            <button className="primary" disabled={!canCommit || busy !== null} onClick={() => void step("commit", async () => {
              await bridge.git.commit(root, message, includeUnstaged);
              setMessage("");
            })}>Commit</button>
          </div>
          <div className="commit-actions">
            <label title="git push --force-with-lease"><input type="checkbox" checked={forcePush} onChange={(event) => setForcePush(event.target.checked)} /> Force</label>
            <span className="spacer" />
            <button disabled={busy !== null || props.airgapOn || !status?.branch} title={props.airgapOn ? "Airgapped is on: a push would reach the network" : !status?.branch ? "Create a branch first" : "git push"}
              onClick={() => void step("push", () => bridge.git.push(root, forcePush, props.threadId))}>{busy === "push" ? "Pushing…" : "Push"}</button>
            <button disabled={busy !== null || props.airgapOn || !status?.branch} title={props.airgapOn ? "Airgapped is on" : "Opens the forge's new pull request page in your browser"}
              onClick={() => bridge.git.pullRequest(root, props.threadId).catch(notice)}>Pull request</button>
          </div>
          {editors.length > 0 ? (
            <label className="editor-choice">Open files in
              <select value={props.editor ?? ""} onChange={(event) => props.setEditor(event.target.value || null)}>
                <option value="">the system's choice</option>
                {editors.map((editor) => <option key={editor.id} value={editor.id}>{editor.name}</option>)}
              </select>
            </label>
          ) : null}
        </footer>
      ) : null}
    </div>
  );
}
