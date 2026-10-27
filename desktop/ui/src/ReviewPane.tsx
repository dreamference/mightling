// The review pane (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.5): the repository's state
// beside the thread, not only the agent's edits. Scopes Unstaged (the default), Staged, Commit,
// Branch (against the base) from window-side git, and Last turn from the server's
// `turn/diff/updated`; a repository selector; stage, unstage and revert by whole diff, file and
// hunk; unified or split; inline comments sent to the composer; viewed marks; a file opened in the
// chosen editor (Ctrl+click a line for that line); commit with a generated message, push, and the
// forge's pull-request page. In a browser on `ling web` only Last turn is shown.

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";

import * as bridge from "./bridge";
import { anchorLine, findingMessage, repoPath, type Finding } from "./findings";
import { commentsMessage, editable, editPatch, zeroContext, fingerprint, hunkNewText, hunkPatch, parseDiff, splitRows, type DiffLine, type FileDiff, type ReviewComment } from "./diff";
import { Icon } from "./icons";
import { keyLabel } from "./keymap";
import { filesIn, repoName, repoOption, turnBase, turnLines, type Lines } from "./repos";

export type ReviewScope = bridge.GitScope | "turn";

const SCOPES: { id: ReviewScope; label: string }[] = [
  { id: "unstaged", label: "Unstaged" },
  { id: "staged", label: "Staged" },
  { id: "commit", label: "Commit" },
  { id: "branch", label: "Branch" },
  { id: "turn", label: "Last turn" },
];

const CHAT_REPO_SCOPES: { id: ReviewScope; label: string }[] = [
  { id: "uncommitted", label: "Uncommitted" },
  { id: "branch", label: "Branch" },
  { id: "turn", label: "Last turn" },
];

/**
 * The review scopes offered in the current host:
 * - desktop app (`electron`): `unstaged`, `staged`, `commit`, `branch`, `turn`;
 * - `ling web` on a `chat-repos` thread (`web` + `gitAvailable`): `uncommitted`, `branch`, `turn` (CHAT_REPOS §10 G6);
 * - an ordinary browser thread: `turn` only.
 */
export function reviewScopesFor(currentHost: bridge.Host, gitAvailable: boolean): { id: ReviewScope; label: string }[] {
  if (!gitAvailable) return [{ id: "turn", label: "Last turn" }];
  if (currentHost === "web") return CHAT_REPO_SCOPES;
  return SCOPES;
}

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
  /** Into a window of its own, or (`docks`, in that window) back into the thread's (several windows); null where there are no windows. */
  detach?: { docks: boolean; run: () => void } | null;
  /** The thread's latest `/review` findings (findings.ts, DESKTOP §16 B12), shown on the lines they name. */
  findings?: Finding[];
  /** Optional initial scope and diff for static rendering in tests. */
  initialScope?: ReviewScope;
  initialDiff?: string;
}

interface Draft {
  path: string;
  line: number;
  side: "new" | "old";
  quote: string;
  text: string;
}

export function ReviewPane(props: ReviewPaneProps) {
  const currentHost = bridge.host();
  const windowGit = bridge.git.available(props.cwd);
  const shownScopes = reviewScopesFor(currentHost, windowGit);
  const defaultScope: ReviewScope = !windowGit ? "turn" : currentHost === "web" ? "uncommitted" : "unstaged";
  const [repos, setRepos] = useState<string[]>(() => (props.initialDiff !== undefined && props.cwd ? [props.cwd] : []));
  const [root, setRoot] = useState<string | null>(() => (props.initialDiff !== undefined ? props.cwd : null));
  const [scope, setScope] = useState<ReviewScope>(props.initialScope ?? defaultScope);
  const [diff, setDiff] = useState(props.initialDiff ?? "");
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
  /** A hunk being edited in place (item 16): its file, its index and the text so far. */
  const [editing, setEditing] = useState<{ path: string; index: number; text: string } | null>(null);
  const [includeUnstaged, setIncludeUnstaged] = useState(false);
  const [forcePush, setForcePush] = useState(props.forcePushDefault);
  const [editors, setEditors] = useState<bridge.Editor[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [gitMissing, setGitMissing] = useState<string | null>(null);
  /** The folder whose repositories `repos` and `root` are. */
  const [resolvedCwd, setResolvedCwd] = useState<string | null>(null);
  /** DESKTOP §16 B32: Last turn over one repository of several, null for All repos; each repository's lines changed. */
  const [turnRepo, setTurnRepo] = useState<string | null>(null);
  const [repoLines, setRepoLines] = useState<Record<string, Lines>>({});
  const [linesTick, setLinesTick] = useState(0);
  /** The folder and repository the diff and status on screen were read for. */
  const [shown, setShown] = useState<{ cwd: string | null; root: string | null } | null>(null);
  const { notice } = props;
  const cwdRef = useRef(props.cwd);
  cwdRef.current = props.cwd;

  // What the user wrote and marked belongs to the thread and folder it was written for: each keeps its
  // own comments, draft, viewed and folded files and commit message, put away when the pane shows
  // another and given back on return, so thread A's comments never go to thread B's composer nor A's
  // message into B's commit (§9.3; until 1.7.1 they followed the pane to whatever thread was shown).
  const kept = useRef(new Map<string, { comments: ReviewComment[]; draft: Draft | null; viewed: Set<string>; collapsed: Set<string>; message: string }>());
  const current = useRef({ comments, draft, viewed, collapsed, message });
  current.current = { comments, draft, viewed, collapsed, message };
  const owner = `${props.threadId ?? ""}\n${props.cwd ?? ""}`;
  const shownOwner = useRef(owner);
  useEffect(() => {
    if (shownOwner.current === owner) return;
    kept.current.set(shownOwner.current, current.current);
    shownOwner.current = owner;
    const back = kept.current.get(owner);
    setComments(back?.comments ?? []);
    setDraft(back?.draft ?? null);
    setViewed(back?.viewed ?? new Set());
    setCollapsed(back?.collapsed ?? new Set());
    setMessage(back?.message ?? "");
    setEditing(null);
  }, [owner]);

  // The repositories of the thread's folder.
  useEffect(() => {
    if (!windowGit || !props.cwd) {
      setRepos([]);
      setRoot(null);
      return;
    }
    let current = true;
    // Another thread's folder: nothing of the last repository stays, not even its root, until this
    // folder's own repositories answer (they took 20-45 s on a Mac waiting on a folder-access prompt,
    // and the pane showed the last repository's diff with its Revert buttons live meanwhile).
    setRepos([]);
    setRoot(null);
    setResolvedCwd(null);
    setTurnRepo(null);
    setDiff("");
    setStatus(null);
    setAgainst(null);
    setCommits([]);
    const cwd = props.cwd;
    bridge.git.missing().then((why) => current && setGitMissing(why)).catch(() => {});
    bridge.git.repos(cwd).then((found) => {
      if (!current || cwdRef.current !== cwd) return;
      setRepos(found);
      setRoot((chosen) => (chosen && found.includes(chosen) ? chosen : found[0] ?? null));
      setResolvedCwd(cwd);
    }).catch(notice);
    if (currentHost === "electron") bridge.listEditors().then((found) => current && setEditors(found)).catch(() => {});
    return () => {
      current = false;
    };
  }, [props.cwd, windowGit, currentHost, notice]);

  useEffect(() => {
    if (!shownScopes.some((entry) => entry.id === scope)) {
      setScope(shownScopes[0]?.id ?? "turn");
    }
  }, [shownScopes, scope]);

  // Each repository's lines changed against HEAD, for the selector (several repositories only).
  useEffect(() => {
    if (!windowGit || repos.length < 2) {
      setRepoLines({});
      return;
    }
    let live = true;
    void Promise.all(repos.map((repo) => bridge.git.changes(repo).then((lines) => [repo, lines] as const).catch(() => null))).then((answers) => {
      if (live) setRepoLines(Object.fromEntries(answers.filter((answer) => answer !== null)));
    });
    return () => {
      live = false;
    };
  }, [repos, windowGit, props.refreshKey, linesTick]);

  const rootRef = useRef(root);
  rootRef.current = root;
  const load = useCallback(async () => {
    setError(null);
    if (scope === "turn") {
      setDiff(props.turnDiff ?? "");
      setAgainst(null);
      return;
    }
    const cwd = cwdRef.current;
    if (!root) {
      // Nothing of the last repository's carries over to a folder outside any (it showed "main, against the index").
      setDiff("");
      setStatus(null);
      setAgainst(null);
      setCommits([]);
      setShown({ cwd, root: null });
      return;
    }
    try {
      const [answer, now] = await Promise.all([bridge.git.diff(root, scope, scope === "commit" ? commit : null), bridge.git.status(root)]);
      // An answer for a repository, or a folder, the pane has since left is dropped.
      if (rootRef.current !== root || cwdRef.current !== cwd) return;
      setDiff(answer.diff);
      setAgainst(answer.against);
      setStatus(now);
      setShown({ cwd, root });
      if (scope === "commit" && commits.length === 0) setCommits(await bridge.git.log(root));
    } catch (failure) {
      setDiff("");
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  }, [scope, root, commit, commits.length, props.turnDiff]);

  useEffect(() => {
    void load();
  }, [load, props.refreshKey]);

  // What is on screen belongs to this thread's folder and the repository chosen in it, or nothing is
  // shown: no diff, no Stage or Revert, no commit box (decided during the render, so not even one
  // frame of the last thread's repository with live buttons). Last turn is the thread's own diff.
  const ready = props.initialDiff !== undefined || !windowGit || (resolvedCwd === props.cwd && shown !== null && shown.cwd === props.cwd && shown.root === root);
  const turnFiles = useMemo(() => parseDiff(props.turnDiff ?? ""), [props.turnDiff]);
  const turnCounts = useMemo(() => turnLines(turnFiles, props.cwd, repos), [turnFiles, props.cwd, repos]);
  const files = useMemo(() => (scope === "turn" ? filesIn(turnFiles, props.cwd, repos, repos.length > 1 && turnRepo && repos.includes(turnRepo) ? turnRepo : null) : ready ? parseDiff(diff) : []),
    [scope, turnFiles, props.cwd, repos, turnRepo, ready, diff]);
  /** Where the shown diff's paths start: Last turn's from the server's display root (turnBase), the others from the repository. */
  const pathBase = scope === "turn" ? turnBase(props.cwd, repos, root) : root ?? props.cwd;

  /** Runs a git step, then reads the repository again. */
  const step = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label);
    try {
      await action();
    } catch (failure) {
      notice(failure);
    } finally {
      setBusy(null);
      setLinesTick((tick) => tick + 1);
      await load();
    }
  };

  const confirmRevert = (what: string) => window.confirm(`Throw away ${what}? This cannot be undone from here.`);

  const fileActions = (file: FileDiff) => {
    if (currentHost !== "electron" || !root || scope === "turn" || scope === "commit" || scope === "branch" || scope === "uncommitted") return null;
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
    if (currentHost !== "electron" || !root || (scope !== "unstaged" && scope !== "staged")) return null;
    const patch = hunkPatch(file, index);
    if (scope === "unstaged") {
      return (
        <>
          {editable(file, index) && !(editing?.path === file.path && editing.index === index) ? (
            <button className="link-button" onClick={() => setEditing({ path: file.path, index, text: hunkNewText(file.hunks[index]) })}>Edit</button>
          ) : null}
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
    const base = pathBase;
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
        <td className={`code ${line.kind}`} onClick={(event) => onLineClick(event, file, line)} title={`${keyLabel("Ctrl+click")} to open the file at this line`}>
          {line.kind !== "note" ? <button className="add-comment" aria-label="Comment on this line" onClick={(event) => { event.stopPropagation(); startComment(file, line); }}>+</button> : null}
          <span className="sign">{sign}</span>{line.text}
        </td>
      </>
    );
  };

  // Review findings (DESKTOP §16 B12): each on the first line of its range the diff shows, folded on a click.
  const findingBase = root ?? props.cwd;
  const findingKey = (finding: Finding) => `${finding.path}:${finding.start}:${finding.title}`;
  const [foldedFindings, setFoldedFindings] = useState<Set<string>>(new Set());
  const findingsAt = (file: FileDiff, line: DiffLine | null): Finding[] => {
    if (!line || line.newNo === null || !findingBase || !props.findings?.length) return [];
    return props.findings.filter((finding) => repoPath(finding, findingBase) === file.path && anchorLine(file, finding) === line.newNo);
  };
  const elsewhere = (props.findings ?? []).filter((finding) => {
    const at = findingBase ? repoPath(finding, findingBase) : null;
    const file = at ? files.find((entry) => entry.path === at) : undefined;
    return !file || anchorLine(file, finding) === null;
  });
  const findingCard = (finding: Finding, shown: string) => {
    const key = findingKey(finding);
    const folded = foldedFindings.has(key);
    const toggle = () => setFoldedFindings((set) => {
      const next = new Set(set);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    return (
      <div key={key} className={`review-finding${folded ? " folded" : ""}`}>
        <div className="review-finding-head">
          <button className="icon" aria-label={folded ? "Unfold the finding" : "Fold the finding"} aria-expanded={!folded} onClick={toggle}><Icon name={folded ? "chevronRight" : "chevronDown"} size={12} /></button>
          <strong>{finding.title}</strong>
          <span className="review-finding-where">{shown}:{finding.start === finding.end ? finding.start : `${finding.start}-${finding.end}`}</span>
        </div>
        {folded ? null : (
          <>
            {finding.body ? <p>{finding.body}</p> : null}
            <div className="buttons">
              <button onClick={() => props.onComments(findingMessage(finding, shown))}>Send to the composer</button>
              {props.onOpenFile ? <button className="link-button" onClick={() => props.onOpenFile?.(finding.path, finding.start)}>Open the file</button> : null}
            </div>
          </>
        )}
      </div>
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
    const noted = [...new Set(lines.flatMap((line) => findingsAt(file, line)))];
    const drafting = draft && draft.path === file.path && lines.some((line) => line && (line.newNo ?? line.oldNo) === draft.line && (line.newNo === null ? "old" : "new") === draft.side);
    if (here.length === 0 && noted.length === 0 && !drafting) return null;
    return (
      <tr className="comment-row">
        <td colSpan={span}>
          {noted.map((finding) => findingCard(finding, file.path))}
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
          <button className="file-name" title={windowGit ? "Open in the editor" : file.path} onClick={() => open(file, null)} data-path={pathBase ? `${pathBase.replace(/\/+$/, "")}/${file.path}` : undefined}>{file.path}</button>
          {props.onOpenFile && pathBase && file.status !== "deleted" ? (
            <button className="icon" title="Open in a tab" aria-label={`Open ${file.path} in a tab`} onClick={() => props.onOpenFile?.(`${pathBase.replace(/\/+$/, "")}/${file.path}`, null)}><Icon name="file" size={13} /></button>
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
            {editing && editing.path === file.path && editing.index === index && scope === "unstaged" && root ? (
              <div className="hunk-editor">
                <textarea value={editing.text} aria-label={`Edit ${file.path} at line ${hunk.newStart}`} spellCheck={false}
                  rows={Math.min(30, editing.text.split("\n").length + 1)}
                  onChange={(event) => setEditing({ ...editing, text: event.target.value })}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setEditing(null);
                  }} />
                <div className="buttons">
                  <button className="primary" disabled={busy !== null} onClick={() => {
                    const patch = editPatch(file, index, editing.text);
                    setEditing(null);
                    // Applied to the working tree only, as Revert hunk is; git refuses it, and the file
                    // stays as it was, when the file changed since this diff was read.
                    if (patch) void step("edit", () => bridge.git.apply(root, patch, { zeroContext: zeroContext(patch) }));
                  }}>Save</button>
                  <button onClick={() => setEditing(null)}>Cancel</button>
                  <span className="planned-note">Lines {hunk.newStart} to {hunk.newStart + hunkNewText(hunk).split("\n").length - 1} of the file as it is now.</span>
                </div>
              </div>
            ) : (
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
            )}
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
        {repos.length > 1 && scope === "turn" ? (
          <select aria-label="Repository" value={turnRepo && repos.includes(turnRepo) ? turnRepo : ""} title="What the last turn changed, in every repository or one"
            onChange={(event) => {
              const chosen = event.target.value || null;
              setTurnRepo(chosen);
              if (chosen) setRoot(chosen);
            }}>
            <option value="">{repoOption("All repos", turnCounts.total)}</option>
            {repos.map((repo) => <option key={repo} value={repo}>{repoOption(repoName(repo), turnCounts.byRepo.get(repo))}</option>)}
          </select>
        ) : repos.length > 1 ? (
          <select aria-label="Repository" value={root ?? ""} title="Lines changed against HEAD in each repository" onChange={(event) => setRoot(event.target.value)}>
            {repos.map((repo) => <option key={repo} value={repo}>{repoOption(repoName(repo), repoLines[repo])}</option>)}
          </select>
        ) : null}
        <span className="spacer" />
        <button className="icon" title={props.layout === "split" ? "Show the diff in one column" : "Show the old and the new side by side"} aria-label="Switch the view" onClick={() => props.setLayout(props.layout === "split" ? "unified" : "split")}>{props.layout === "split" ? "One column" : "Side by side"}</button>
        <button className="icon" title="Read the repository again" aria-label="Refresh" onClick={() => {
          setLinesTick((tick) => tick + 1);
          void load();
        }}><Icon name="undo" size={14} /></button>
        {props.detach ? (
          <button className="icon" aria-label={props.detach.docks ? "Dock the diff" : "Diff in its own window"} title={props.detach.docks ? "Back into the thread's window" : "Open the diff in a window of its own"} onClick={props.detach.run}>
            <Icon name={props.detach.docks ? "dock" : "externalLink"} size={14} />
          </button>
        ) : null}
      </header>
      <nav className="review-scopes" aria-label="Scope">
        {shownScopes.map((entry) => (
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
        {scope === "turn" ? "What the last turn changed, from the agent." : !ready ? "Reading this folder's repository…" : status ? (
          <>
            {status.branch ? <span><Icon name="gitFork" size={12} /> {status.branch}</span> : <span>detached at {status.head?.slice(0, 8) ?? "nothing yet"}</span>}
            {status.upstream ? <span>{status.ahead ? ` ↑${status.ahead}` : ""}{status.behind ? ` ↓${status.behind}` : ""}{!status.ahead && !status.behind ? " up to date" : ""} with {status.upstream}</span> : null}
            {against ? <span className="against">against {against}</span> : null}
          </>
        ) : !windowGit ? null : root ? "…" : gitMissing ? "Git is unavailable on this machine." : !props.cwd ? "Name the project folder to see its changes." : "This folder is not in a git repository."}
      </div>
      {gitMissing && scope !== "turn" ? <div className="notice">{gitMissing}</div> : error ? <div className="notice">{error}</div> : null}
      {currentHost === "electron" && files.length > 0 && root && ready && (scope === "unstaged" || scope === "staged") ? (
        <div className="review-all">
          {scope === "unstaged" ? (
            <>
              <button className="link-button" onClick={() => void step("stage", () => bridge.git.stage(root, files.map((file) => file.path)))}>Stage all</button>
              <button className="link-button danger" onClick={() => confirmRevert("every unstaged change") && void step("revert", () => bridge.git.discard(root, files.map((file) => file.path)))}>Revert all</button>
            </>
          ) : <button className="link-button" onClick={() => void step("unstage", () => bridge.git.unstage(root, files.map((file) => file.path)))}>Unstage all</button>}
        </div>
      ) : null}
      {currentHost === "electron" && scope === "turn" && props.onRevertTurn && files.length > 0 ? (
        <div className="review-all"><button className="link-button danger" onClick={props.onRevertTurn}>Revert the turn</button></div>
      ) : null}
      {elsewhere.length > 0 ? (
        <section className="review-findings-elsewhere" aria-label="Review findings outside this diff">
          <h4>{elsewhere.length === 1 ? "A review finding on lines this diff does not show" : `${elsewhere.length} review findings on lines this diff does not show`}</h4>
          {elsewhere.map((finding) => findingCard(finding, (findingBase && repoPath(finding, findingBase)) || finding.path))}
        </section>
      ) : null}
      <div className="review-files">
        {files.length === 0 ? (scope === "turn" || ready ? <p className="empty small">{scope === "turn" ? (turnFiles.length > 0 && turnRepo ? `The last turn changed nothing in ${repoName(turnRepo)}.` : "The last turn changed no files.") : "No changes."}</p> : null) : files.map(fileBlock)}
      </div>
      {comments.length > 0 ? (
        <div className="review-send">
          <span>{comments.length} comment{comments.length > 1 ? "s" : ""}</span>
          <button className="primary" onClick={() => { props.onComments(commentsMessage(comments)); setComments([]); }}>Send to the composer</button>
          <button className="link-button" onClick={() => setComments([])}>Discard</button>
        </div>
      ) : null}
      {currentHost === "electron" && windowGit && root && ready && (scope === "unstaged" || scope === "staged") ? (
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
