// The Add server form in Skills and tools › MCP servers (DESKTOP §16 B34): a command with its
// arguments, environment and working folder, or a URL with its headers and the name of the variable
// holding its bearer token. Add stays off, saying why, until `formProblem` is satisfied.

import { formProblem, type McpServerForm } from "./mcpservers";

export function McpServerAdd(props: { form: McpServerForm; setForm: (form: McpServerForm) => void; taken: (name: string) => boolean; onAdd: () => void; onCancel: () => void }) {
  const { form, setForm } = props;
  const set = (field: keyof McpServerForm) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });
  const problem = formProblem(form, props.taken);
  return (
    <div className="mcp-add">
      <input value={form.name} placeholder="name" aria-label="Server name" onChange={set("name")} />
      <select value={form.kind} aria-label="Server kind" onChange={set("kind")}>
        <option value="command">a command</option>
        <option value="url">a URL</option>
      </select>
      <input className="script" value={form.target} placeholder={form.kind === "url" ? "http://127.0.0.1:8765/mcp" : "npx"} aria-label="Server command or URL" onChange={set("target")} />
      {form.kind === "command" ? (
        <>
          <input className="script" value={form.args} placeholder="arguments" aria-label="Server arguments" onChange={set("args")} />
          <textarea className="script" rows={2} value={form.env} placeholder="environment: NAME=value, one a line (kept in config.toml as typed)" aria-label="Environment variables" onChange={set("env")} />
          <input className="script" value={form.cwd} placeholder="working folder (a full path)" aria-label="Working folder" onChange={set("cwd")} />
        </>
      ) : (
        <>
          <textarea className="script" rows={2} value={form.headers} placeholder="headers: Name: value, one a line" aria-label="HTTP headers" onChange={set("headers")} />
          <input className="script" value={form.bearer} placeholder="bearer-token variable, e.g. MCP_TOKEN (its name: the token is never written)" aria-label="Bearer-token variable" onChange={set("bearer")} />
        </>
      )}
      {problem && form.name ? <p className="planned-note" role="status">{problem}</p> : null}
      <div className="buttons">
        <button className="primary" disabled={problem !== null} onClick={props.onAdd}>Add</button>
        <button onClick={props.onCancel}>Cancel</button>
      </div>
    </div>
  );
}
