// New project and Edit project (DESKTOP_PARITY §4.5 item 13): a name, its folders with the primary
// first (Make primary), standing instructions, and knowledge files (in the desktop app; they live in
// the app's data, so `ling web` lists none). Saved through `project/create` or `project/update`.

import { useEffect, useState } from "react";

import * as bridge from "./bridge";
import { Icon } from "./icons";
import { INSTRUCTIONS_KEY, instructionsOf, nameFromFolder, withMetadata, withPrimary } from "./projects";
import type { Project } from "./protocol/v2/Project";
import type { RpcClient } from "./rpc";

export function ProjectDialog(props: {
  client: RpcClient;
  /** The project edited, or null for a new one. */
  project: Project | null;
  /** A new project's first folder, when one was chosen already (Ctrl+O, a folder's menu). */
  folder: string | null;
  onSaved: (project: Project) => void;
  onClose: () => void;
  notice: (error: unknown) => void;
}) {
  const { client, project, notice } = props;
  const [name, setName] = useState(project?.name ?? (props.folder ? nameFromFolder(props.folder) : ""));
  const [roots, setRoots] = useState<Project["roots"]>(project?.roots ?? (props.folder ? [{ path: props.folder }] : []));
  const [typed, setTyped] = useState("");
  const [instructions, setInstructions] = useState(project ? instructionsOf(project) : "");
  const [files, setFiles] = useState<bridge.KnowledgeFile[]>([]);
  const [saving, setSaving] = useState(false);
  const desktop = bridge.knowledge.available();

  useEffect(() => {
    if (!project || !desktop) return;
    bridge.knowledge.list(project.id).then(setFiles).catch(notice);
  }, [project, desktop, notice]);

  const addFolder = (path: string) => {
    const folder = path.trim();
    if (!folder || roots.some((root) => root.path === folder)) return;
    setRoots([...roots, { path: folder }]);
    if (!name.trim()) setName(nameFromFolder(folder));
  };

  const save = async () => {
    if (!name.trim()) return notice("Give the project a name.");
    // A folder typed but not yet added with Enter is saved too, not dropped.
    const pending = typed.trim();
    const sent = pending && !roots.some((root) => root.path === pending) ? [...roots, { path: pending }] : roots;
    setSaving(true);
    try {
      const answer = project
        ? await client.request("project/update", { projectId: project.id, name: name.trim(), roots: sent, metadata: withMetadata(project.metadata, { [INSTRUCTIONS_KEY]: instructions.trim() }) })
        : await client.request("project/create", { name: name.trim(), roots: sent, metadata: withMetadata({}, { [INSTRUCTIONS_KEY]: instructions.trim() }), idempotencyKey: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}` });
      props.onSaved((answer as { project: Project }).project);
    } catch (error) {
      notice(error);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog project-dialog" role="dialog" aria-label={project ? "Edit project" : "New project"} onClick={(event) => event.stopPropagation()}>
        <header><h2>{project ? "Edit project" : "New project"}</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <label className="field">
          <span>Name</span>
          <input value={name} autoFocus onChange={(event) => setName(event.target.value)} aria-label="Project name" />
        </label>
        <h3>Folders</h3>
        <p className="dialog-note">New threads start in the primary folder, and git and AGENTS.md come from it; the agent may read and change the others too. A project with no folder is for questions (Ask).</p>
        {roots.length === 0 ? <p className="empty small">No folders.</p> : (
          <ul className="project-folders">
            {roots.map((root, index) => (
              <li key={root.path}>
                <Icon name="folder" size={14} /> <code title={root.path}>{root.path}</code>
                {index === 0 ? <span className="planned-note"> primary</span> : <button className="link-button" onClick={() => setRoots(withPrimary(roots, root.path))}>Make primary</button>}
                <button className="link-button" onClick={() => setRoots(roots.filter((each) => each.path !== root.path))} aria-label={`Remove ${root.path}`}>Remove</button>
              </li>
            ))}
          </ul>
        )}
        <div className="project-add-folder">
          {desktop ? (
            <button onClick={() => void bridge.chooseFolder().then((folder) => folder && addFolder(folder)).catch(notice)}>Add folder…</button>
          ) : null}
          <input value={typed} placeholder="/path/to/folder" aria-label="Folder to add" onChange={(event) => setTyped(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              addFolder(typed);
              setTyped("");
            }} />
        </div>
        <h3>Instructions</h3>
        <textarea className="project-instructions" value={instructions} rows={5} aria-label="Project instructions"
          placeholder="What every conversation in this project should know or do: the audience, the tone, the conventions, what to avoid."
          onChange={(event) => setInstructions(event.target.value)} />
        <h3>Knowledge files</h3>
        {!desktop ? <p className="dialog-note">Knowledge files are added in the desktop app.</p> : !project ? (
          <p className="dialog-note">Save the project first, then add files here: they are copied into Mightling's own folder for the project, and its threads read them when they bear on a request.</p>
        ) : (
          <>
            {files.length === 0 ? <p className="empty small">No knowledge files.</p> : (
              <ul className="project-files">
                {files.map((file) => (
                  <li key={file.name}>
                    <Icon name="file" size={14} /> {file.name} <span className="planned-note">{Math.max(1, Math.round(file.size / 1024))} KB</span>
                    <button className="link-button" aria-label={`Remove ${file.name}`} onClick={() => void bridge.knowledge.remove(project.id, file.name).then(setFiles).catch(notice)}>Remove</button>
                  </li>
                ))}
              </ul>
            )}
            <button onClick={() => void bridge.knowledge.add(project.id).then(setFiles).catch(notice)}>Add files…</button>
          </>
        )}
        <div className="buttons">
          <button className="primary" disabled={saving} onClick={() => void save()}>{project ? "Save" : "Create project"}</button>
          <button onClick={props.onClose}>Cancel</button>
        </div>
      </section>
    </div>
  );
}
