// An MCP server asking the user (DESKTOP_PARITY §4.5 item 14): `mcpServer/elicitation/request`, a form
// to fill in (its `requestedSchema`, MCP's flat object of strings, numbers, booleans and enums) or a
// URL to open, answered with `accept` and the content, `decline` or `cancel`. Until 1.7.1 the window
// declined every one without showing it, so a tool that asked could never run. Pure: no DOM.

export type ElicitationAction = "accept" | "decline" | "cancel";

export interface ElicitationOption {
  value: string;
  label: string;
}

export interface ElicitationField {
  name: string;
  label: string;
  description: string | null;
  kind: "string" | "number" | "integer" | "boolean" | "choice" | "choices";
  required: boolean;
  options: ElicitationOption[];
  /** The value the field starts with, as the form holds it (text, or "true"/"false", or the chosen values). */
  initial: string | string[];
  /** For a string: its format (email, uri, date, date-time), when the schema names one. */
  format: string | null;
}

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

/** An enum's choices: `oneOf`/`anyOf` of `{const, title}`, or `enum` with `enumNames` (MCP's older form). */
function choicesOf(schema: Json): ElicitationOption[] {
  const titled = (Array.isArray(schema.oneOf) ? schema.oneOf : Array.isArray(schema.anyOf) ? schema.anyOf : null) as unknown[] | null;
  if (titled) return titled.filter(isObject).map((option) => ({ value: String(option.const), label: String(option.title ?? option.const) }));
  if (Array.isArray(schema.enum)) {
    const names = Array.isArray(schema.enumNames) ? (schema.enumNames as unknown[]) : [];
    return schema.enum.map((value, index) => ({ value: String(value), label: String(names[index] ?? value) }));
  }
  return [];
}

/** The fields of a requested schema, in its order; a property of a kind MCP does not allow is left out. */
export function elicitationFields(requested: unknown): ElicitationField[] {
  if (!isObject(requested) || !isObject(requested.properties)) return [];
  const required = new Set(Array.isArray(requested.required) ? requested.required.map(String) : []);
  const fields: ElicitationField[] = [];
  for (const [name, raw] of Object.entries(requested.properties)) {
    if (!isObject(raw)) continue;
    const label = String(raw.title ?? name);
    const description = typeof raw.description === "string" ? raw.description : null;
    const base = { name, label, description, required: required.has(name), format: null as string | null };
    if (raw.type === "array") {
      const items = isObject(raw.items) ? raw.items : {};
      const options = choicesOf(items);
      if (options.length === 0) continue;
      fields.push({ ...base, kind: "choices", options, initial: Array.isArray(raw.default) ? raw.default.map(String) : [] });
      continue;
    }
    const options = choicesOf(raw);
    if (options.length > 0) {
      fields.push({ ...base, kind: "choice", options, initial: raw.default === undefined ? "" : String(raw.default) });
      continue;
    }
    if (raw.type === "boolean") fields.push({ ...base, kind: "boolean", options: [], initial: raw.default === true ? "true" : "false" });
    else if (raw.type === "number" || raw.type === "integer") fields.push({ ...base, kind: raw.type, options: [], initial: raw.default === undefined ? "" : String(raw.default) });
    else if (raw.type === "string") fields.push({ ...base, kind: "string", options: [], initial: raw.default === undefined ? "" : String(raw.default), format: typeof raw.format === "string" ? raw.format : null });
  }
  return fields;
}

/**
 * The content an accepted form sends: each field as its schema's type, an empty optional field left
 * out. `problems` names what stops it: a required field left empty, a number that is not one.
 */
export function elicitationContent(fields: ElicitationField[], values: Record<string, string | string[]>): { content: Json; problems: string[] } {
  const content: Json = {};
  const problems: string[] = [];
  for (const field of fields) {
    const value = values[field.name] ?? field.initial;
    if (field.kind === "boolean") {
      content[field.name] = value === "true";
      continue;
    }
    if (field.kind === "choices") {
      const chosen = Array.isArray(value) ? value : [];
      if (chosen.length) content[field.name] = chosen;
      else if (field.required) problems.push(`${field.label} needs a choice.`);
      continue;
    }
    const text = (Array.isArray(value) ? value.join(",") : value).trim();
    if (!text) {
      if (field.required) problems.push(`${field.label} is required.`);
      continue;
    }
    if (field.kind === "number" || field.kind === "integer") {
      const number = Number(text);
      if (!Number.isFinite(number) || (field.kind === "integer" && !Number.isInteger(number))) problems.push(`${field.label} must be ${field.kind === "integer" ? "a whole number" : "a number"}.`);
      else content[field.name] = number;
      continue;
    }
    content[field.name] = text;
  }
  return { content, problems };
}

/** The response to the request: the content only with `accept`. */
export function elicitationAnswer(action: ElicitationAction, content: Json | null = null): { action: ElicitationAction; content: Json | null; _meta: null } {
  return { action, content: action === "accept" ? content ?? {} : null, _meta: null };
}

/** Whether the window shows this request: every mode but the vendor's own identity check, which it declines unseen. */
export function shownElicitation(params: unknown): boolean {
  return !(isObject(params) && params.mode === "openai/userVerification");
}
