// Subagents (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.4, §2.13): above the composer, each
// thread the shown one started, by its nickname and role, whether it is working, Open and Stop, and
// Stop all. The server runs them (`ext/agent`); they share the one model server, which the header's
// count of working threads makes visible.

import { Icon } from "./icons";
import type { Thread } from "./protocol/v2/Thread";

export function SubagentsPanel(props: { agents: Thread[]; working: (id: string) => boolean; onOpen: (id: string) => void; onStop: (id: string) => void; onClose: () => void }) {
  if (props.agents.length === 0) return null;
  const busy = props.agents.filter((agent) => props.working(agent.id));
  return (
    <div className="subagents" aria-label="Subagents">
      <header>
        <strong>Subagents</strong>
        <span className="planned-note">{busy.length} of {props.agents.length} working</span>
        <span className="spacer" />
        {busy.length ? <button className="link-button danger" onClick={() => busy.forEach((agent) => props.onStop(agent.id))}>Stop all</button> : null}
        <button className="icon" aria-label="Hide the subagents" onClick={props.onClose}><Icon name="x" size={12} /></button>
      </header>
      {props.agents.map((agent) => (
        <div key={agent.id} className="subagent">
          {props.working(agent.id) ? <span className="running" aria-label="working" /> : <Icon name="check" size={12} />}
          <span className="subagent-name">{agent.agentNickname || agent.name || agent.preview || "subagent"}</span>
          {agent.agentRole ? <span className="planned-note">{agent.agentRole}</span> : null}
          <span className="spacer" />
          <button className="link-button" onClick={() => props.onOpen(agent.id)}>Open</button>
          {props.working(agent.id) ? <button className="link-button danger" onClick={() => props.onStop(agent.id)}>Stop</button> : null}
        </div>
      ))}
    </div>
  );
}
