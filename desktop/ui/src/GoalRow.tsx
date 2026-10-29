// The goal row (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.3, `/goal`): above the composer,
// the thread's goal (its objective is the prompt and the completion criteria), how it stands, what
// it has used, and Pause, Resume, Edit and Clear, all through `thread/goal/*`.

import { useState } from "react";

import { Icon } from "./icons";
import type { ThreadGoal } from "./protocol/v2/ThreadGoal";

const STATUS: Record<ThreadGoal["status"], string> = {
  active: "working on it",
  paused: "paused",
  blocked: "blocked",
  usageLimited: "stopped at a usage limit",
  budgetLimited: "stopped at its token budget",
  complete: "complete",
};

/** How long a goal has run, as the row shows it. */
export function goalTime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export function GoalRow(props: {
  goal: ThreadGoal | null;
  editing: boolean;
  setEditing: (editing: boolean) => void;
  onSet: (objective: string) => void;
  onStatus: (status: "active" | "paused") => void;
  onClear: () => void;
}) {
  const { goal } = props;
  const [text, setText] = useState(goal?.objective ?? "");
  // Each edit starts from the goal as it is now: it may have come in, or changed, since the row opened.
  const [wasEditing, setWasEditing] = useState(props.editing);
  if (props.editing !== wasEditing) {
    setWasEditing(props.editing);
    if (props.editing) setText(goal?.objective ?? "");
  }
  if (!goal && !props.editing) return null;
  if (props.editing) {
    return (
      <div className="goal-row editing" aria-label="Goal">
        <Icon name="check" size={14} />
        <input autoFocus value={text} placeholder="The goal: what done looks like" aria-label="Goal objective"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && text.trim()) props.onSet(text.trim());
            if (event.key === "Escape") props.setEditing(false);
          }} />
        <button className="primary" disabled={!text.trim()} onClick={() => props.onSet(text.trim())}>Set goal</button>
        <button onClick={() => props.setEditing(false)}>Cancel</button>
      </div>
    );
  }
  if (!goal) return null;
  const used = goal.tokenBudget ? `${goal.tokensUsed.toLocaleString()} of ${goal.tokenBudget.toLocaleString()} tokens` : `${goal.tokensUsed.toLocaleString()} tokens`;
  return (
    <div className={`goal-row status-${goal.status}`} aria-label="Goal">
      <Icon name="check" size={14} />
      <span className="goal-objective" title={goal.objective}>{goal.objective}</span>
      <span className="goal-status">{STATUS[goal.status]} · {used} · {goalTime(goal.timeUsedSeconds)}</span>
      {goal.status === "active" ? <button className="link-button" onClick={() => props.onStatus("paused")}>Pause</button> : null}
      {goal.status === "paused" || goal.status === "blocked" ? <button className="link-button" onClick={() => props.onStatus("active")}>Resume</button> : null}
      <button className="link-button" onClick={() => { setText(goal.objective); props.setEditing(true); }}>Edit</button>
      <button className="icon" aria-label="Clear the goal" onClick={props.onClear}><Icon name="x" size={14} /></button>
    </div>
  );
}
