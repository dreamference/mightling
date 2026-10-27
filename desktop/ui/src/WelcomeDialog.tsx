// First run (DESKTOP_PARITY §4.5 item 29, DESKTOP_PARITY_CLAUDE §2.12, DESKTOP §16 B6): shown once, to
// someone with no threads yet, naming the ways in: a project folder, a thread with no folder, another
// agent's setup brought over, a question in Ask. Each choice, or Not now, marks it seen (`welcomed`).
import { Icon, MightlingMark, type IconName } from "./icons";

export type WelcomeChoice = "folder" | "chat" | "import" | "ask" | "later";

/** Whether the first-run page shows: never seen, in a full window, and nothing to come back to yet. */
export function showsWelcome(welcomed: boolean, threads: number, fullWindow: boolean): boolean {
  return !welcomed && threads === 0 && fullWindow;
}

const CHOICES: { id: Exclude<WelcomeChoice, "later">; icon: IconName; title: string; body: string }[] = [
  { id: "folder", icon: "folder", title: "Open a project folder", body: "Work on code: the agent reads, edits and runs it in that folder, asking before anything you have not allowed." },
  { id: "chat", icon: "squarePen", title: "Start a thread with no folder", body: "Think something through with the agent before there is a project." },
  { id: "import", icon: "arrowDown", title: "Bring over another agent's setup", body: "Instructions, skills, MCP servers and earlier sessions from an agent you used before, chosen one by one." },
  { id: "ask", icon: "messageSquare", title: "Ask a question", body: "A question with no project: answered in a folder of its own, with web search unless airgapped is on." },
];

export function WelcomeDialog(props: { quickShortcut: string; onChoose: (choice: WelcomeChoice) => void }) {
  return (
    <div className="dialog-backdrop" onClick={() => props.onChoose("later")}>
      <section className="dialog welcome" role="dialog" aria-label="Welcome to Mightling" onClick={(event) => event.stopPropagation()}>
        <header className="welcome-head">
          <MightlingMark size={44} />
          <h2>Welcome to Mightling</h2>
        </header>
        <p className="dialog-note">Everything runs on this machine and its own model server; nothing you write leaves it unless you let the agent search the web.</p>
        <div className="welcome-choices">
          {CHOICES.map((choice) => (
            <button key={choice.id} className="welcome-choice" onClick={() => props.onChoose(choice.id)}>
              <Icon name={choice.icon} size={18} />
              <span><strong>{choice.title}</strong><span className="welcome-body">{choice.body}</span></span>
            </button>
          ))}
        </div>
        <p className="dialog-note">Two things worth knowing: <code>/airgapped on</code> in a thread closes the network to everything the agent runs, and Settings › General turns on Quick Entry, a question from any app with {props.quickShortcut}.</p>
        <div className="buttons dialog-buttons"><button onClick={() => props.onChoose("later")}>Not now</button></div>
      </section>
    </div>
  );
}
