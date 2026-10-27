// The git bar for a `chat-repos` thread on `ling web` (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md
// §4.3–§4.7, §5.4, §9, §10 G9): branch and ahead/behind from `/api/repos/state`, with Commit (a
// message box), Push, Update (and Abort when a merge is in progress), Merge, PR (opens the `/forge/`
// link in a new tab), and "Work on main" when allowed. Every action first fetches a single-use
// `confirm` nonce via `GET /api/repos/state?thread=<id>&intent=<intent>`, asks for inline
// confirmation, then POSTs with `confirm`.

import { useEffect, useMemo, useState } from "react";

import * as bridge from "./bridge";
import { Icon } from "./icons";
import {
  beginGitAction,
  chatRepos,
  executeGitAction,
  isChatRepoCwd,
  type ChatRepoState,
  type ChatReposApi,
  type GitActionOutcome,
  type GitBarAction,
  type PendingGitAction,
} from "./repos";

export interface GitBarProps {
  thread: string | null;
  cwd?: string | null;
  state: ChatRepoState | null;
  api?: ChatReposApi;
  onStateChange?: (state: ChatRepoState) => void;
  openTab?: (url: string) => void;
  initialPending?: PendingGitAction | null;
  initialOutcome?: GitActionOutcome | null;
  initialMergeInProgress?: boolean;
}

const CONFIRM_LABELS: Record<Exclude<GitBarAction, "commit">, string> = {
  push: "Push commits to the forge?",
  update: "Merge base branch into this checkout?",
  abort: "Abort the merge in progress?",
  merge: "Squash-merge this branch into main?",
  pr: "Open a pull request on the forge?",
  main: "Switch this checkout to work on main?",
};

export function GitBar(props: GitBarProps) {
  const api = useMemo(() => props.api ?? chatRepos(), [props.api]);
  const [pending, setPending] = useState<PendingGitAction | null>(props.initialPending ?? null);
  const [outcome, setOutcome] = useState<GitActionOutcome | null>(props.initialOutcome ?? null);
  const [mergeInProgress, setMergeInProgress] = useState<boolean>(
    props.initialMergeInProgress ?? Boolean(props.state?.merge_in_progress),
  );
  const [mainRefused, setMainRefused] = useState<boolean>(false);
  const [busy, setBusy] = useState<boolean>(false);

  useEffect(() => {
    setPending(props.initialPending ?? null);
    setOutcome(props.initialOutcome ?? null);
    setMergeInProgress(props.initialMergeInProgress ?? Boolean(props.state?.merge_in_progress));
    setMainRefused(false);
  }, [props.thread]);

  useEffect(() => {
    if (props.state?.merge_in_progress !== undefined) {
      setMergeInProgress(Boolean(props.state.merge_in_progress));
    }
  }, [props.state?.merge_in_progress]);

  if (bridge.host() !== "web" || !props.thread || !props.state) return null;
  if (props.cwd !== undefined && props.cwd !== null && !isChatRepoCwd(props.cwd)) return null;

  const { thread, state } = props;
  const readOnly = state.read_only;
  const onMain = state.branch !== null && (state.branch === "main" || state.branch === state.base);
  const showWorkOnMain = !onMain && state.allow_main !== false && !mainRefused;

  const startAction = async (action: GitBarAction) => {
    if (readOnly || busy) return;
    setBusy(true);
    setOutcome(null);
    try {
      const started = await beginGitAction(api, thread, action);
      props.onStateChange?.(started.state);
      setPending(started.pending);
    } catch (failure: unknown) {
      setOutcome({ text: failure instanceof Error ? failure.message : String(failure) });
    } finally {
      setBusy(false);
    }
  };

  const confirmAction = async () => {
    if (!pending || busy) return;
    setBusy(true);
    try {
      const result = await executeGitAction(api, thread, pending, { openTab: props.openTab });
      setPending(null);
      setOutcome(result);
      if (result.mergeInProgress !== undefined) setMergeInProgress(result.mergeInProgress);
      if (result.mainRefused) setMainRefused(true);
      await api.state(thread).then((fresh) => props.onStateChange?.(fresh)).catch(() => {});
    } catch (failure: unknown) {
      setOutcome({ text: failure instanceof Error ? failure.message : String(failure) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="git-bar" role="region" aria-label="Repository actions">
      <span className="git-bar-status">
        <span className="git-bar-branch">
          <Icon name="gitFork" size={12} /> {state.branch ?? "read-only"}
        </span>
        <span className="git-bar-ahead-behind" title={`Ahead ${state.ahead}, behind ${state.behind} (${state.base})`}>
          ↑{state.ahead} ↓{state.behind}
        </span>
      </span>

      <span className="git-bar-actions">
        <button disabled={readOnly || busy} onClick={() => void startAction("commit")}>Commit</button>
        <button disabled={readOnly || busy} onClick={() => void startAction("push")}>Push</button>
        <button disabled={readOnly || busy} onClick={() => void startAction("update")}>Update</button>
        {mergeInProgress ? (
          <button className="danger-button" disabled={readOnly || busy} onClick={() => void startAction("abort")}>Abort</button>
        ) : null}
        <button disabled={readOnly || busy} onClick={() => void startAction("merge")}>Merge</button>
        <button disabled={readOnly || busy} onClick={() => void startAction("pr")}>PR</button>
        {showWorkOnMain ? (
          <button disabled={readOnly || busy} onClick={() => void startAction("main")}>Work on main</button>
        ) : null}
      </span>

      {readOnly ? (
        <span className="git-bar-readonly" role="status">This repository is read-only on the forge</span>
      ) : null}

      {pending ? (
        pending.action === "commit" ? (
          <div className="git-confirm commit-confirm" role="group" aria-label="Commit message">
            <input
              type="text"
              className="git-commit-input"
              value={pending.message}
              placeholder="Commit message"
              aria-label="Commit message"
              onChange={(event) => setPending({ ...pending, message: event.target.value })}
              onKeyDown={(event) => {
                if (event.key === "Enter" && pending.message.trim()) void confirmAction();
                if (event.key === "Escape") setPending(null);
              }}
            />
            <button className="primary" disabled={busy || !pending.message.trim()} onClick={() => void confirmAction()}>
              Confirm commit
            </button>
            <button disabled={busy} onClick={() => setPending(null)}>Cancel</button>
          </div>
        ) : (
          <div className="git-confirm" role="group" aria-label={`Confirm ${pending.action}`}>
            <span className="git-confirm-prompt">{CONFIRM_LABELS[pending.action]}</span>
            <button className="primary" disabled={busy} onClick={() => void confirmAction()}>Confirm</button>
            <button disabled={busy} onClick={() => setPending(null)}>Cancel</button>
          </div>
        )
      ) : null}

      {outcome ? (
        <span className="git-bar-outcome" role="status">
          {outcome.text}
          {outcome.prUrl ? (
            <> (<a href={outcome.prUrl} target="_blank" rel="noopener noreferrer">{outcome.prUrl}</a>)</>
          ) : null}
        </span>
      ) : null}
    </div>
  );
}
