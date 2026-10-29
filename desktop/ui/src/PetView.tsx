// The pet's window (DESKTOP_PARITY §2.1, Phase 3 item 6; desktop/electron/src/pet.ts): Mightling's
// mark, the faceted ling, at rest, swimming while the agent works, waving when it waits for you and
// hopping when a turn has finished; or a picture of the user's own. A click opens a bubble with what
// is going on, Open Mightling, a quick question for Ask, and Hide. Dragging moves the window.

import { useEffect, useRef, useState } from "react";

interface PetState {
  working: number;
  waiting: number;
  finished: number;
  size: "small" | "medium" | "large";
  image: string | null;
  /** Threads that need the user: waiting for an answer, or blocked (DESKTOP §16 B18). */
  needs?: { id: string; title: string; why: "waiting" | "blocked" }[];
}

function send<T>(message: object): Promise<T> {
  const bridge = window.electronBridge;
  if (!bridge) return Promise.reject(new Error("the pet lives in the desktop app"));
  return bridge.sendMessageFromView(message) as Promise<T>;
}

/** What the pet is doing, from the agent's state: the first that applies. */
export function mood(state: PetState | null): "waiting" | "blocked" | "working" | "done" | "resting" {
  if (!state) return "resting";
  if (state.waiting > 0) return "waiting";
  if (state.needs?.some((need) => need.why === "blocked")) return "blocked";
  if (state.working > 0) return "working";
  if (state.finished > 0) return "done";
  return "resting";
}

/** The bubble's first line. */
export function moodText(state: PetState | null): string {
  if (!state) return "Resting.";
  const parts: string[] = [];
  if (state.waiting) parts.push(`${state.waiting === 1 ? "A thread is" : `${state.waiting} threads are`} waiting for you`);
  const blocked = state.needs?.filter((need) => need.why === "blocked").length ?? 0;
  if (blocked) parts.push(`${blocked === 1 ? "one thread is" : `${blocked} threads are`} blocked`);
  if (state.working) parts.push(`${state.working === 1 ? "one thread" : `${state.working} threads`} working`);
  if (!parts.length && state.finished) parts.push(`${state.finished === 1 ? "A turn" : `${state.finished} turns`} finished`);
  if (!parts.length) return "Resting: nothing is running.";
  const text = parts.join(", ");
  return `${text[0].toUpperCase()}${text.slice(1)}.`;
}

export function PetView() {
  const [state, setState] = useState<PetState | null>(null);
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);

  useEffect(() => {
    document.documentElement.classList.add("pet-page");
    send<PetState>({ type: "pet/state" }).then(setState).catch(() => {});
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { channel?: string; payload?: PetState } | null;
      if (data?.channel === "pet://state" && data.payload) setState(data.payload);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const toggle = (next: boolean) => {
    setOpen(next);
    send({ type: "pet/expand", open: next }).catch(() => {});
  };

  const current = mood(state);
  return (
    <div className={`pet ${open ? "open" : ""} size-${state?.size ?? "medium"}`}>
      {open ? (
        <section className="pet-bubble" role="dialog" aria-label="Mightling pet">
          <p className="pet-mood">{moodText(state)}</p>
          {state?.needs?.length ? (
            <ul className="pet-needs" aria-label="Threads that need you">
              {state.needs.map((need) => (
                <li key={need.id}>
                  <button title={need.why === "blocked" ? "It waits for an answer this app cannot give, or the agent stopped with an error: open it to see" : "It waits for your answer"}
                    onClick={() => { send({ type: "pet/thread", thread: need.id }).catch(() => {}); toggle(false); }}>
                    <span className={`pet-need ${need.why}`}>{need.why === "blocked" ? "Blocked" : "Waiting"}</span> {need.title}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <form onSubmit={(event) => {
            event.preventDefault();
            if (!question.trim()) return;
            const asked = question.trim();
            // Not sent: the question comes back to the box (unless something new was typed meanwhile).
            send({ type: "pet/ask", prompt: asked }).catch(() => setQuestion((now) => now || asked));
            setQuestion("");
            toggle(false);
          }}>
            <input autoFocus value={question} placeholder="Ask Mightling…" aria-label="A quick question" onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Escape") toggle(false); }} />
          </form>
          <div className="pet-actions">
            <button onClick={() => { send({ type: "pet/open" }).catch(() => {}); toggle(false); }}>Open Mightling</button>
            <button onClick={() => send({ type: "pet/hide" }).catch(() => {})}>Hide the pet</button>
          </div>
        </section>
      ) : null}
      <button className={`pet-body mood-${current}`} aria-label={`Mightling pet: ${moodText(state)}`} title={moodText(state)}
        onPointerDown={(event) => {
          drag.current = { x: event.screenX, y: event.screenY, moved: false };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const start = drag.current;
          if (!start) return;
          const dx = event.screenX - start.x;
          const dy = event.screenY - start.y;
          if (!start.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
          start.moved = true;
          drag.current = { x: event.screenX, y: event.screenY, moved: true };
          send({ type: "pet/move", dx, dy }).catch(() => {});
        }}
        onPointerUp={() => {
          const moved = drag.current?.moved;
          drag.current = null;
          if (!moved) toggle(!open);
        }}>
        {state?.image ? <img src={state.image} alt="" draggable={false} /> : <PetMark />}
        {current === "waiting" ? <span className="pet-badge" aria-hidden="true">!</span> : current === "blocked" ? <span className="pet-badge blocked" aria-hidden="true">‖</span> : null}
      </button>
    </div>
  );
}

/** Mightling's mark without its wordmark (icons.tsx `MightlingMark`): its wing flaps and its eye blinks. */
function PetMark() {
  return (
    <svg className="pet-mark" viewBox="20 6 236 132" aria-hidden="true" focusable="false">
      <g className="pet-wing">
        <polygon points="96,64 52,14 78,16 134,56" fill="var(--mark-body)" />
        <polygon points="96,64 78,16 134,56" fill="var(--mark-facet-1)" />
      </g>
      <polygon points="28,96 70,70 130,56 176,44 196,52 196,92 182,100 140,118 90,122 44,112" fill="var(--mark-body)" />
      <polygon points="130,56 176,44 168,86" fill="var(--mark-facet-2)" />
      <polygon points="70,70 130,56 118,92" fill="var(--mark-facet-3)" />
      <polygon points="178,99 140,116 92,120 58,110 102,103 148,95" fill="var(--mark-light)" />
      <polygon points="196,52 196,92 172,86 174,54" fill="var(--mark-light)" />
      <polygon className="pet-eye" points="184,58 189,63 184,68 179,63" fill="var(--mark-eye)" />
      <polygon points="196,52 250,74 196,72" fill="var(--mark-beak-1)" />
      <polygon points="196,72 250,74 196,92" fill="#e4572e" />
      <polygon points="98,121 112,131 118,119" fill="var(--mark-beak-1)" />
    </svg>
  );
}
