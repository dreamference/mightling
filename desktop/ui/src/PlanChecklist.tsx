// The agent's plan as a checklist (`turn/plan/updated`, DESKTOP_PARITY §4.5 item 21, DESKTOP §16 B3):
// above the composer while its turn runs, and kept, folded, in the finished turn.
import type { Checklist } from "./store";

import { Icon } from "./icons";

const STATUS_WORDS = { pending: "to do", inProgress: "in progress", completed: "done" } as const;

/** "2 of 5 done", the checklist's count in words. */
export function checklistCount(checklist: Checklist): string {
  const done = checklist.steps.filter((step) => step.status === "completed").length;
  return `${done} of ${checklist.steps.length} done`;
}

function Steps({ checklist }: { checklist: Checklist }) {
  return (
    <>
      {checklist.explanation ? <p className="checklist-explanation">{checklist.explanation}</p> : null}
      <ol className="checklist-steps">
        {checklist.steps.map((step, index) => (
          <li key={index} className={`checklist-step status-${step.status}`} aria-label={`${step.step}: ${STATUS_WORDS[step.status]}`}>
            <span className="checklist-mark" aria-hidden="true">{step.status === "completed" ? <Icon name="check" size={12} strokeWidth={3} /> : null}</span>
            <span className="checklist-text">{step.step}</span>
          </li>
        ))}
      </ol>
    </>
  );
}

/** The checklist of the turn running now, above the composer. */
export function LiveChecklist({ checklist }: { checklist: Checklist }) {
  if (!checklist.steps.length) return null;
  return (
    <section className="checklist live" aria-label="The agent's plan">
      <header><strong>Plan</strong><span className="checklist-count">{checklistCount(checklist)}</span></header>
      <Steps checklist={checklist} />
    </section>
  );
}

/** The checklist kept in a finished turn, folded under its count. */
export function TurnChecklist({ checklist }: { checklist: Checklist }) {
  if (!checklist.steps.length) return null;
  return (
    <details className="checklist kept">
      <summary>Plan · {checklistCount(checklist)}</summary>
      <Steps checklist={checklist} />
    </details>
  );
}
