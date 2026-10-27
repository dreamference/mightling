// The worktree controls (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.6): under the composer of
// a new thread in a git project, Local or Worktree, the base branch and the local environment whose
// setup runs; in the header of a thread, where its work is (worktree or local), Create branch here,
// Hand off, Open in the editor, a terminal or the file manager, and the environment's actions.

import { useEffect, useRef, useState } from "react";

import * as bridge from "./bridge";
import { actionIcon } from "./BottomPanel";
import { Icon } from "./icons";
import type { WorktreeLink } from "./prefs";
import { formatBudgetLeft, type ChatRepoState } from "./repos";
import { shortcutLabel } from "./keymap";

export interface NewThreadChoice {
  mode: "local" | "worktree";
  /** The branch to start from; null for the current one. */
  base: string | null;
  /** With the current branch: apply its uncommitted changes to the worktree. */
  withChanges: boolean;
  /** The environment (its file) whose setup runs, or null. */
  environment: string | null;
}

export function NewThreadOptions(props: {
  choice: NewThreadChoice;
  onChange: (choice: NewThreadChoice) => void;
  branches: { current: string | null; branches: string[] };
  environments: bridge.LocalEnvironment[];
}) {
  const { choice, onChange, branches } = props;
  return (
    <div className="new-thread-options" aria-label="Where the new thread works">
      <span className="segmented" role="radiogroup" aria-label="Checkout">
        <button role="radio" aria-checked={choice.mode === "local"} className={choice.mode === "local" ? "selected" : ""} onClick={() => onChange({ ...choice, mode: "local" })}>Local</button>
        <button role="radio" aria-checked={choice.mode === "worktree"} className={choice.mode === "worktree" ? "selected" : ""} onClick={() => onChange({ ...choice, mode: "worktree" })}><Icon name="gitFork" size={12} /> Worktree</button>
      </span>
      {choice.mode === "worktree" ? (
        <>
          <label>from
            <select aria-label="Base branch" value={choice.base ?? ""} onChange={(event) => onChange({ ...choice, base: event.target.value || null })}>
              <option value="">{branches.current ?? "HEAD"} (current{choice.withChanges ? ", with its changes" : ""})</option>
              {branches.branches.filter((name) => name !== branches.current).map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          {!choice.base ? (
            <label><input type="checkbox" checked={choice.withChanges} onChange={(event) => onChange({ ...choice, withChanges: event.target.checked })} /> uncommitted changes</label>
          ) : null}
          {props.environments.length ? (
            <label>setup
              <select aria-label="Local environment" value={choice.environment ?? ""} onChange={(event) => onChange({ ...choice, environment: event.target.value || null })}>
                <option value="">none</option>
                {props.environments.map((environment) => <option key={environment.file} value={environment.file}>{environment.name}</option>)}
              </select>
            </label>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export function WorktreeHeader(props: {
  link: WorktreeLink | undefined;
  branch: string | null;
  canHandOff: boolean;
  inGit: boolean;
  environment: bridge.LocalEnvironment | null;
  onCreateBranch: (name: string) => void;
  /** Settings › Git: what a new branch's name starts with. */
  branchPrefix: string;
  onHandOff: (direction: "toLocal" | "toWorktree") => void;
  onOpen: (where: "editor" | "terminal" | "folder") => void;
  onAction: (action: bridge.EnvironmentAction) => void;
  /** `chat-repos` checkout state on `ling web` (CHAT_REPOS §10 G13). */
  chatRepo?: ChatRepoState | null;
}) {
  const [menu, setMenu] = useState<"handoff" | "open" | "branch" | null>(null);
  const [name, setName] = useState("");
  const { link, chatRepo } = props;
  useEffect(() => setMenu(null), [link?.checkout, link?.location]);
  // An open menu closes on Escape and on a click anywhere outside these controls.
  const area = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!menu) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(null);
    };
    const onDown = (event: MouseEvent) => {
      if (area.current && !area.current.contains(event.target as Node)) setMenu(null);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [menu]);
  if (chatRepo && bridge.host() === "web") {
    const onMain = chatRepo.branch !== null && (chatRepo.branch === "main" || chatRepo.branch === chatRepo.base);
    return (
      <span className="worktree-header chat-repo-header" ref={area}>
        <span className="chip repo-name">
          {chatRepo.private ? <span className="repo-lock" aria-label="Private" title="Private repository">🔒 </span> : null}
          {chatRepo.repo}
        </span>
        <span className={`chip location ${onMain ? "main" : "worktree"}`}>
          <Icon name="gitFork" size={12} /> {chatRepo.branch ?? "read-only"}
        </span>
        {onMain ? <span className="chip main-badge" data-badge="main">main</span> : null}
        <span className="chip repo-budget" title={`${chatRepo.budget.used.toLocaleString("en-US")} of ${chatRepo.budget.limit.toLocaleString("en-US")} tokens used`}>
          {formatBudgetLeft(chatRepo.budget)}
        </span>
        {(chatRepo.warnings ?? []).map((warning, index) => (
          <span key={index} className="repo-warning notice" role="status">{warning}</span>
        ))}
      </span>
    );
  }
  const inWorktree = link?.location === "worktree";
  return (
    <span className="worktree-header" ref={area}>
      {props.environment?.actions.slice(0, 4).map((action, index) => (
        <button key={action.name} className="icon bar-action" title={`${action.name}${index === 0 ? ` (${shortcutLabel("environmentAction1")})` : ""}: ${action.script}`} aria-label={action.name} onClick={() => props.onAction(action)}>
          <Icon name={actionIcon(action.icon)} size={14} />
        </button>
      ))}
      {props.inGit ? (
        <span className={`chip location ${inWorktree ? "worktree" : "local"}`} title={link ? (inWorktree ? link.checkout : link.source) : undefined}>
          {inWorktree ? <><Icon name="gitFork" size={12} /> worktree</> : "local"}{props.branch ? ` · ${props.branch}` : inWorktree ? " · detached" : ""}
        </span>
      ) : null}
      {inWorktree && !props.branch ? (
        <span className="menu-anchor">
          <button className="link-button" onClick={() => { if (menu !== "branch") setName(props.branchPrefix); setMenu(menu === "branch" ? null : "branch"); }}>Create branch here</button>
          {menu === "branch" ? (
            <span className="menu inline-form" role="dialog" aria-label="Create branch here">
              <input autoFocus value={name} placeholder="feature/name" aria-label="Branch name" onChange={(event) => setName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && name.trim()) {
                    props.onCreateBranch(name.trim());
                    setMenu(null);
                  }
                  if (event.key === "Escape") setMenu(null);
                }} />
              <button className="primary" disabled={!name.trim()} onClick={() => { props.onCreateBranch(name.trim()); setMenu(null); }}>Create</button>
            </span>
          ) : null}
        </span>
      ) : null}
      {props.inGit ? (
        <span className="menu-anchor">
          <button className="link-button" disabled={!props.canHandOff} title={props.canHandOff ? "Move this thread and its work to the other checkout" : "Wait for the turn to end"} onClick={() => setMenu(menu === "handoff" ? null : "handoff")}>Hand off</button>
          {menu === "handoff" ? (
            <span className="menu" role="menu" aria-label="Hand off">
              <button role="menuitem" disabled={!inWorktree} onClick={() => props.onHandOff("toLocal")}>To the local checkout</button>
              <button role="menuitem" disabled={inWorktree} onClick={() => props.onHandOff("toWorktree")}>To {link ? "its" : "a new"} worktree</button>
            </span>
          ) : null}
        </span>
      ) : null}
      <span className="menu-anchor">
        <button className="icon bar-action" aria-label="Open" title="Open the thread's folder" onClick={() => setMenu(menu === "open" ? null : "open")}><Icon name="folder" size={16} /></button>
        {menu === "open" ? (
          <span className="menu" role="menu" aria-label="Open">
            <button role="menuitem" onClick={() => { props.onOpen("editor"); setMenu(null); }}>In the editor</button>
            <button role="menuitem" onClick={() => { props.onOpen("terminal"); setMenu(null); }}>In a terminal</button>
            <button role="menuitem" onClick={() => { props.onOpen("folder"); setMenu(null); }}>In the file manager</button>
          </span>
        ) : null}
      </span>
    </span>
  );
}
