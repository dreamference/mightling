// The composer (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.3): the text, its completion menus
// (`@` files through `fuzzyFileSearch`, `$` skills from `skills/list`, `/` commands), what Enter does
// (the window's preference), ↑ to walk back through earlier prompts, Esc to stop and Esc Esc to take
// the last message back, Enter and Esc on an open approval, images by Shift+drag or paste, the queue
// of follow-ups waiting for the running turn, and the model and effort picker. The logic is in
// composer.ts; this file is the component (named apart from it, for case-insensitive file systems).

import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent, type ReactNode } from "react";

import {
  activeTrigger, applyCompletion, effortChoices, enterSends, type EnterBehaviour, matchCommands, matchSkills, mentionPath, slashSkills,
  type CommandContext, type PromptHistory, type SlashCommand, type Trigger,
} from "./composer";
import { Icon } from "./icons";
import { acceptsPrediction } from "./suggestions";
import { keyAction, keyLabel, matches, shortcutLabel } from "./keymap";
import type { FuzzyFileSearchResult } from "./protocol/FuzzyFileSearchResult";
import type { Model } from "./protocol/v2/Model";
import type { QueuedSubmission } from "./protocol/v2/QueuedSubmission";
import type { SkillMetadata } from "./protocol/v2/SkillMetadata";

/** An attachment shown above the text: an Ask file still to upload, or a Work image by its path. */
/** A queued message as its row shows it: the words, other inputs by kind. */
function queuedText(item: QueuedSubmission): string {
  return item.input.map((input) => (input.type === "text" ? input.text : `[${input.type}]`)).join(" ");
}

export interface ComposerAttachment {
  id: number;
  name: string;
  preview: string | null;
  /** What hovering shows: the start of a long paste. */
  title?: string;
  /** Said on the chip: an image the served model cannot see (DESKTOP §16 B13). */
  warning?: string;
}

interface MenuEntry {
  key: string;
  label: string;
  detail: string;
  insert: string;
  command?: SlashCommand;
}

export interface ComposerProps {
  draft: string;
  setDraft: (text: string) => void;
  placeholder: string;
  /** The predicted next message (DESKTOP_PARITY_CLAUDE §4.6), shown in grey in the empty composer; Tab or → takes it. */
  prediction?: string | null;
  running: boolean;
  sending: boolean;
  enterBehaviour: EnterBehaviour;
  followUp: "steer" | "queue";
  /** The folder `@` searches and paths are made relative to; null hides `@`. */
  root: string | null;
  context: CommandContext;
  history: PromptHistory;
  threadTexts: string[];
  attachments: ComposerAttachment[];
  onRemoveAttachment: (id: number) => void;
  queue: QueuedSubmission[];
  onSteerQueued: (item: QueuedSubmission) => void;
  onDropQueued: (item: QueuedSubmission) => void;
  /** A queued message's words changed, or the message moved up or down (DESKTOP_PARITY §4.5 item 22). */
  onEditQueued: (item: QueuedSubmission, text: string) => void;
  onMoveQueued: (item: QueuedSubmission, by: -1 | 1) => void;
  approvalOpen: boolean;
  onApprovalKey: (key: "approve" | "decline") => void;
  onSend: (text: string, mode: "default" | "steer" | "queue") => void;
  onStop: () => void;
  onEditPrevious: () => void;
  onCommand: (command: SlashCommand) => void;
  onFiles: (files: File[], asImages: boolean) => void;
  onPaste: (event: ClipboardEvent<HTMLTextAreaElement>) => void;
  searchFiles: (query: string) => Promise<FuzzyFileSearchResult[]>;
  skills: SkillMetadata[];
  /** The left of the action row: the paperclip in Ask, the permissions picker in Work. */
  leading: ReactNode;
  models: Model[];
  model: string | null;
  effort: string | null;
  recentModels: string[];
  picker: "closed" | "open" | "recent";
  setPicker: (state: "closed" | "open" | "recent") => void;
  onPick: (model: string, effort: string | null) => void;
  /** Plan mode for the next turn; null when the server offers none. */
  plan: boolean | null;
  onTogglePlan: () => void;
}

const DOUBLE_ESCAPE_MS = 600;

export function Composer(props: ComposerProps) {
  const text = useRef<HTMLTextAreaElement>(null);
  const [trigger, setTrigger] = useState<Trigger | null>(null);
  const [entries, setEntries] = useState<MenuEntry[]>([]);
  const [highlight, setHighlight] = useState(0);
  /** The queued message being edited, and its words so far. */
  const [editingQueued, setEditingQueued] = useState<{ id: string; text: string } | null>(null);
  const lastEscape = useRef(0);
  const { draft, setDraft, root, context, skills, searchFiles } = props;

  // The menu follows the word at the caret.
  useEffect(() => {
    if (!trigger) {
      setEntries([]);
      return;
    }
    let current = true;
    if (trigger.kind === "/") {
      setEntries([
        ...matchCommands(trigger.query, context).map((command) => ({
          key: command.name, label: `/${command.name}`, detail: command.args ? `${command.args} — ${command.description}` : command.description,
          insert: `/${command.name}`, command,
        })),
        // The enabled skills follow the commands, as in the Codex app; choosing one inserts `$name`.
        ...slashSkills(skills, trigger.query).map((skill) => ({
          key: `skill:${skill.path}`, label: `$${skill.name}`, detail: skill.shortDescription || skill.description, insert: `$${skill.name}`,
        })),
      ]);
    } else if (trigger.kind === "$") {
      setEntries(matchSkills(skills, trigger.query).slice(0, 12).map((skill) => ({
        key: skill.path, label: `$${skill.name}`, detail: skill.shortDescription || skill.description, insert: `$${skill.name}`,
      })));
    } else if (root) {
      const timer = setTimeout(() => {
        searchFiles(trigger.query).then((files) => {
          if (!current) return;
          setEntries(files.slice(0, 12).map((file) => ({
            key: `${file.root}/${file.path}`, label: file.file_name, detail: file.path, insert: `@${mentionPath(root, `${file.root}/${file.path}`)}`,
          })));
        }).catch(() => current && setEntries([]));
      }, 120);
      return () => {
        current = false;
        clearTimeout(timer);
      };
    }
    setHighlight(0);
    return () => {
      current = false;
    };
  }, [trigger, context, skills, root, searchFiles]);

  useEffect(() => setHighlight(0), [entries.length]);

  const follow = (value: string, caret: number) => {
    const found = activeTrigger(value, caret);
    setTrigger(found && (found.kind !== "@" || root) ? found : null);
  };

  const choose = (entry: MenuEntry) => {
    if (!trigger) return;
    // A command with no words to take, or typed out in full, runs; otherwise its name is completed.
    if (entry.command && (!entry.command.args || trigger.query === entry.command.name)) {
      setTrigger(null);
      setDraft("");
      props.onCommand(entry.command);
      return;
    }
    const next = applyCompletion(draft, trigger, entry.insert);
    setDraft(next.text);
    setTrigger(null);
    requestAnimationFrame(() => {
      text.current?.focus();
      text.current?.setSelectionRange(next.caret, next.caret);
    });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing) return;
    const menuOpen = trigger !== null && entries.length > 0;
    if (!menuOpen && acceptsPrediction(event, draft, props.prediction ?? null)) {
      event.preventDefault();
      setDraft(props.prediction as string);
      return;
    }
    if (menuOpen) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        setHighlight((index) => (index + (event.key === "ArrowDown" ? 1 : entries.length - 1)) % entries.length);
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        choose(entries[highlight] ?? entries[0]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setTrigger(null);
        return;
      }
    }
    // An open approval takes Enter and Esc while there is nothing typed.
    if (props.approvalOpen && !draft.trim()) {
      if (matches(event, keyAction("approve")) && event.key === "Enter") {
        event.preventDefault();
        props.onApprovalKey("approve");
        return;
      }
      if (matches(event, keyAction("decline"))) {
        event.preventDefault();
        props.onApprovalKey("decline");
        return;
      }
    }
    // `send` is Enter in the table; the preference decides which modifiers go with it (enterSends).
    if (event.key === keyAction("send").keys[0] && enterSends(event, props.enterBehaviour, draft)) {
      event.preventDefault();
      props.onSend(draft, "default");
      return;
    }
    if (matches(event, keyAction("restorePrompt")) && (!draft || props.history.walking) && caretAtStart(text.current)) {
      const older = props.history.older(draft, props.threadTexts);
      if (older !== null) {
        event.preventDefault();
        setDraft(older);
      }
      return;
    }
    if (matches(event, keyAction("nextPrompt")) && props.history.walking && caretAtEnd(text.current)) {
      const newer = props.history.newer(props.threadTexts);
      if (newer !== null) {
        event.preventDefault();
        setDraft(newer);
      }
      return;
    }
    if (matches(event, keyAction("stopTurn"))) {
      if (props.running) {
        event.preventDefault();
        props.onStop();
        return;
      }
      // Esc Esc on an empty composer: take the last message back to edit it.
      const now = Date.now();
      if (!draft.trim() && now - lastEscape.current < DOUBLE_ESCAPE_MS && keyAction("editPrevious").keys.includes("Escape Escape")) {
        lastEscape.current = 0;
        event.preventDefault();
        props.onEditPrevious();
        return;
      }
      lastEscape.current = now;
    }
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const files = Array.from(event.dataTransfer?.files ?? []);
    if (files.length) props.onFiles(files, event.shiftKey);
  };

  const sendLabel = props.running ? (props.followUp === "queue" ? "Queue" : "Steer") : "Send";
  const enterHint = props.enterBehaviour === "ctrlEnter" || (props.enterBehaviour === "multiline" && draft.includes("\n")) ? `${keyLabel("Ctrl+Enter", "composer")}; Enter for a new line` : `Enter; ${shortcutLabel("newLine")} for a new line`;
  const menuOpen = trigger !== null && entries.length > 0;

  return (
    <footer className="composer">
      {props.queue.length > 0 ? (
        <div className="queue" aria-label="Queued follow-ups">
          {props.queue.map((item, index) => (
            <div key={item.id} className="queued">
              <Icon name="clock" size={14} />
              {editingQueued?.id === item.id ? (
                <input className="queued-edit" value={editingQueued.text} aria-label="The queued message" autoFocus
                  onChange={(event) => setEditingQueued({ id: item.id, text: event.target.value })}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      props.onEditQueued(item, editingQueued.text);
                      setEditingQueued(null);
                    } else if (event.key === "Escape") {
                      event.preventDefault();
                      event.stopPropagation();
                      setEditingQueued(null);
                    }
                  }}
                  onBlur={() => setEditingQueued(null)} />
              ) : (
                <span className="queued-text">{queuedText(item)}</span>
              )}
              <button className="icon" aria-label="Edit the queued message" title="Edit (Enter keeps it, Esc leaves it as it was)" onMouseDown={(event) => event.preventDefault()}
                onClick={() => setEditingQueued(editingQueued?.id === item.id ? null : { id: item.id, text: item.input.filter((input) => input.type === "text").map((input) => (input.type === "text" ? input.text : "")).join("\n") })}><Icon name="pencil" size={13} /></button>
              <button className="icon" aria-label="Move up" title="Earlier in the queue" disabled={index === 0} onClick={() => props.onMoveQueued(item, -1)}><Icon name="arrowUp" size={13} /></button>
              <button className="icon" aria-label="Move down" title="Later in the queue" disabled={index === props.queue.length - 1} onClick={() => props.onMoveQueued(item, 1)}><Icon name="arrowDown" size={13} /></button>
              <button className="link-button" onClick={() => props.onSteerQueued(item)} title="Send it into the running turn now">steer now</button>
              <button className="icon" aria-label="Remove from the queue" onClick={() => props.onDropQueued(item)}><Icon name="x" size={14} /></button>
            </div>
          ))}
        </div>
      ) : null}
      <div className="composer-card" onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
        {menuOpen ? (
          <ul className="completions" role="listbox" aria-label={trigger?.kind === "/" ? "Commands" : trigger?.kind === "$" ? "Skills" : "Files"}>
            {entries.map((entry, index) => (
              <li key={entry.key} role="option" aria-selected={index === highlight} className={index === highlight ? "selected" : ""}
                onMouseDown={(event) => { event.preventDefault(); choose(entry); }} onMouseEnter={() => setHighlight(index)}>
                <span className="completion-label">{entry.label}</span>
                <span className="completion-detail">{entry.detail}</span>
              </li>
            ))}
          </ul>
        ) : null}
        {props.attachments.length > 0 ? (
          <div className="attachments">
            {props.attachments.map((item) => (
              <span key={item.id} className={`attachment${item.warning ? " unseen" : ""}`} title={item.warning ?? item.title}>
                {item.preview ? <img src={item.preview} alt="" /> : null}
                <span className="attachment-name">{item.name}</span>
                {item.warning ? <span className="attachment-warning" role="note">Text-only model: not seen</span> : null}
                <button className="icon" aria-label={`Remove ${item.name}`} onClick={() => props.onRemoveAttachment(item.id)}><Icon name="x" size={14} /></button>
              </span>
            ))}
          </div>
        ) : null}
        <textarea ref={text} value={draft} rows={3} onPaste={props.onPaste} aria-label="Message"
          placeholder={props.prediction && !draft ? `${props.prediction}   (Tab to use)` : props.placeholder}
          onChange={(event) => {
            props.history.reset();
            setDraft(event.target.value);
            follow(event.target.value, event.target.selectionStart ?? event.target.value.length);
          }}
          onSelect={(event) => follow(event.currentTarget.value, event.currentTarget.selectionStart ?? 0)}
          onBlur={() => setTimeout(() => setTrigger(null), 150)}
          onKeyDown={onKeyDown} />
        <div className="composer-actions">
          {props.leading}
          <ModelPicker {...props} />
          {props.plan !== null ? (
            <button className={`chip plan-chip${props.plan ? " on" : ""}`} aria-pressed={props.plan} title="Plan mode: the agent plans with you before it changes anything (/plan)" onClick={props.onTogglePlan}>
              <Icon name="clock" size={12} /> Plan
            </button>
          ) : null}
          <span className="spacer" />
          {props.running ? <button className="round stop" onClick={props.onStop} title="Stop (Esc)" aria-label="Stop"><Icon name="square" size={14} /></button> : null}
          {props.running ? (
            <>
              <button className="steer" disabled={props.sending} title={`${sendLabel} (${enterHint})`} onClick={() => props.onSend(draft, "default")}>{props.sending ? "Sending…" : sendLabel}</button>
              <button className="link-button other-follow-up" disabled={props.sending} onClick={() => props.onSend(draft, props.followUp === "queue" ? "steer" : "queue")}
                title={props.followUp === "queue" ? "Send into the running turn instead" : "Wait for the next turn instead"}>{props.followUp === "queue" ? "steer" : "queue"}</button>
            </>
          ) : (
            <button className="round primary send" disabled={props.sending} onClick={() => props.onSend(draft, "default")} title={`Send (${enterHint})`} aria-label={props.sending ? "Sending…" : "Send"}><Icon name="arrowUp" size={18} /></button>
          )}
        </div>
      </div>
    </footer>
  );
}

function caretAtStart(element: HTMLTextAreaElement | null): boolean {
  if (!element) return true;
  return element.selectionStart === element.selectionEnd && !element.value.slice(0, element.selectionStart).includes("\n");
}

function caretAtEnd(element: HTMLTextAreaElement | null): boolean {
  if (!element) return true;
  return element.selectionStart === element.selectionEnd && !element.value.slice(element.selectionEnd).includes("\n");
}

/** The model chip and its menu: the served models and the efforts the chosen one allows (Ctrl+Shift+M; Alt+M for recent ones). */
function ModelPicker(props: ComposerProps) {
  const current = props.models.find((model) => model.id === props.model || model.model === props.model) ?? props.models[0] ?? null;
  const { efforts, preferred } = effortChoices(current);
  const label = `${current?.displayName || props.model || "model"}${props.effort ? ` · ${props.effort}` : ""}`;
  const recent = props.recentModels
    .map((id) => props.models.find((model) => model.id === id))
    .filter((model): model is Model => Boolean(model));
  return (
    <span className="picker-anchor">
      <button className="chip model-chip" title={`Model and effort (${shortcutLabel("modelPicker")})`} aria-haspopup="menu" aria-expanded={props.picker !== "closed"}
        onClick={() => props.setPicker(props.picker === "closed" ? "open" : "closed")}>
        <span className="chip-label">{label}</span><Icon name="chevronDown" size={12} />
      </button>
      {props.picker !== "closed" ? (
        <div className="picker" role="menu" aria-label="Model and effort" onKeyDown={(event) => event.key === "Escape" && props.setPicker("closed")}>
          {props.picker === "recent" && recent.length > 0 ? (
            <>
              <h3>Recent</h3>
              {recent.map((model) => (
                <button key={`recent-${model.id}`} role="menuitemradio" aria-checked={model.id === current?.id} autoFocus={model.id === recent[0].id}
                  onClick={() => props.onPick(model.id, props.effort)}>{model.displayName || model.id}</button>
              ))}
            </>
          ) : null}
          <h3>Model</h3>
          {props.models.length === 0 ? <p className="empty small">The model server lists no models.</p> : null}
          {props.models.map((model) => (
            <button key={model.id} role="menuitemradio" aria-checked={model.id === current?.id} autoFocus={props.picker === "open" && model.id === current?.id}
              onClick={() => props.onPick(model.id, props.effort)}>
              <span>{model.displayName || model.id}</span>
              {model.description ? <span className="picker-detail">{model.description}</span> : null}
            </button>
          ))}
          {efforts.length > 0 ? (
            <>
              <h3>Reasoning effort</h3>
              <div className="efforts">
                {efforts.map((effort) => (
                  <button key={effort} role="menuitemradio" aria-checked={(props.effort ?? preferred) === effort}
                    className={(props.effort ?? preferred) === effort ? "selected" : ""}
                    onClick={() => current && props.onPick(current.id, effort)}>{effort}{effort === preferred ? " (default)" : ""}</button>
                ))}
              </div>
            </>
          ) : null}
          <p className="picker-note">One model is served at a time; the efforts are the ones its catalog entry allows.</p>
        </div>
      ) : null}
    </span>
  );
}
