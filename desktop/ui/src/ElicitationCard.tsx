// An MCP server's question in the thread it belongs to (DESKTOP_PARITY §4.5 item 14): a form of the
// fields its schema asks for, or a URL it wants opened, with Accept, Decline and Cancel.

import { useState } from "react";

import { openWebLink } from "./bridge";
import { elicitationAnswer, elicitationContent, elicitationFields, type ElicitationField } from "./elicitation";

export function ElicitationCard(props: { params: Record<string, unknown>; airgappedOn: boolean; onAnswer: (result: unknown) => void }) {
  const { params, onAnswer } = props;
  const server = String(params.serverName ?? "An MCP server");
  const message = typeof params.message === "string" ? params.message : "";
  const fields = params.mode === "url" ? [] : elicitationFields(params.requestedSchema);
  const [values, setValues] = useState<Record<string, string | string[]>>(() => Object.fromEntries(fields.map((field) => [field.name, field.initial])));
  const [problems, setProblems] = useState<string[]>([]);
  const [opened, setOpened] = useState(false);
  const set = (name: string, value: string | string[]) => setValues((current) => ({ ...current, [name]: value }));

  const accept = () => {
    if (params.mode === "url") return onAnswer(elicitationAnswer("accept"));
    const { content, problems: found } = elicitationContent(fields, values);
    setProblems(found);
    if (found.length === 0) onAnswer(elicitationAnswer("accept", content));
  };

  const input = (field: ElicitationField) => {
    const value = values[field.name] ?? field.initial;
    if (field.kind === "boolean") {
      return <input type="checkbox" checked={value === "true"} aria-label={field.label} onChange={(event) => set(field.name, event.target.checked ? "true" : "false")} />;
    }
    if (field.kind === "choice") {
      return (
        <select value={String(value)} aria-label={field.label} onChange={(event) => set(field.name, event.target.value)}>
          <option value="" disabled={field.required}>{field.required ? "Choose…" : "None"}</option>
          {field.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      );
    }
    if (field.kind === "choices") {
      const chosen = Array.isArray(value) ? value : [];
      return (
        <span className="elicitation-choices" role="group" aria-label={field.label}>
          {field.options.map((option) => (
            <label key={option.value}>
              <input type="checkbox" checked={chosen.includes(option.value)}
                onChange={(event) => set(field.name, event.target.checked ? [...chosen, option.value] : chosen.filter((each) => each !== option.value))} />
              {option.label}
            </label>
          ))}
        </span>
      );
    }
    const type = field.kind === "number" || field.kind === "integer" ? "number" : field.format === "email" ? "email" : field.format === "date" ? "date" : "text";
    return <input type={type} value={String(value)} aria-label={field.label} onChange={(event) => set(field.name, event.target.value)} />;
  };

  return (
    <div className="approval elicitation">
      <strong>{server} asks</strong>
      {message ? <p>{message}</p> : null}
      {params.mode === "url" ? (
        <>
          <pre className="elicitation-url">{String(params.url ?? "")}</pre>
          {props.airgappedOn ? <p className="warn">Airgapped is on: open this link on another device, or turn airgapped off first.</p> : (
            <p><button className="link-button" onClick={() => {
              void openWebLink(String(params.url ?? ""), null);
              setOpened(true);
            }}>Open in the browser</button>{opened ? " · then come back and Accept when you are done there." : null}</p>
          )}
        </>
      ) : (
        fields.map((field) => (
          <label key={field.name} className="question">
            <strong>{field.label}{field.required ? " *" : ""}</strong>{field.description ? <span className="planned-note"> {field.description}</span> : null}
            {input(field)}
          </label>
        ))
      )}
      {problems.length ? <p className="warn" role="alert">{problems.join(" ")}</p> : null}
      <div className="buttons">
        <button className="primary" onClick={accept}>{params.mode === "url" ? "Done, continue" : "Send"}</button>
        <button onClick={() => onAnswer(elicitationAnswer("decline"))}>Decline</button>
        <button onClick={() => onAnswer(elicitationAnswer("cancel"))}>Cancel</button>
      </div>
    </div>
  );
}
