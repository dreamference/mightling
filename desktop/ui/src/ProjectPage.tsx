// A project's page in Ask (DESKTOP_PARITY_CLAUDE §4.7), shown while a new question would start in it:
// its standing instructions, its knowledge files and whether the agent can search them (`ling-docs`,
// the collection knowledge.ts keeps), and its questions. Editing stays in the project dialog
// (ProjectDialog.tsx); adding files here goes through the same main-process call.

import { useEffect, useState } from "react";

import * as bridge from "./bridge";
import { humanBytes } from "./SettingsDialog";
import { Icon } from "./icons";
import { instructionsOf, knowledgeCollection, type KnowledgeFile } from "./projects";
import type { Project } from "./protocol/v2/Project";
import type { Thread } from "./protocol/v2/Thread";

/** How the agent reaches the knowledge: searched (indexed), being indexed, or read as files only. */
export function knowledgeReach(files: number, collection: Pick<bridge.DocsCollection, "counts"> | null): "none" | "searchable" | "indexing" | "files" {
  if (files === 0) return "none";
  if (!collection) return "files";
  return collection.counts.indexed > 0 && collection.counts.pending === 0 ? "searchable" : "indexing";
}

const REACH: Record<ReturnType<typeof knowledgeReach>, string> = {
  none: "",
  searchable: "The agent searches these files for the passages a question needs, and reads them whole when it must.",
  indexing: "Being indexed for search; until then the agent reads the files whole.",
  files: "The agent reads these files whole (search over them needs the local file index: Settings › Your documents).",
};

export function ProjectPage(props: {
  project: Project;
  questions: Thread[];
  title: (thread: Thread) => string;
  onOpen: (threadId: string) => void;
  onEdit: () => void;
  onLeave: () => void;
  notice: (error: unknown) => void;
}) {
  const [files, setFiles] = useState<KnowledgeFile[] | null>(null);
  const [collection, setCollection] = useState<bridge.DocsCollection | null>(null);
  const id = props.project.id;
  const readStatus = () => bridge.docs.status().then((status) => setCollection(status.collections.find((entry) => entry.name === knowledgeCollection(id)) ?? null)).catch(() => {});
  useEffect(() => {
    let current = true;
    if (!bridge.knowledge.available()) return setFiles([]);
    bridge.knowledge.list(id).then((found) => current && setFiles(found)).catch(() => current && setFiles([]));
    void readStatus();
    return () => {
      current = false;
    };
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  /** Files added: listed at once; the collection's state once the main process has had it indexed. */
  const addFiles = () => bridge.knowledge.add(id).then((found) => {
    setFiles(found);
    setTimeout(() => void readStatus(), 1500);
  }).catch(props.notice);
  const instructions = instructionsOf(props.project).trim();
  const reach = knowledgeReach(files?.length ?? 0, collection);
  return (
    <div className="project-page" aria-label={`Project ${props.project.name}`}>
      <header>
        <Icon name="folderTree" size={22} />
        <h1>{props.project.name}</h1>
        <button className="link-button" onClick={props.onEdit}>Edit project…</button>
      </header>
      <p className="dialog-note">New questions here start with the project's instructions and its knowledge. <button className="link-button" onClick={props.onLeave}>Ask outside it</button></p>
      <section>
        <h2>Instructions</h2>
        {instructions ? <p className="project-instructions-text">{instructions}</p> : <p className="empty small">None yet: Edit project… gives the project standing instructions.</p>}
      </section>
      <section>
        <h2>Knowledge</h2>
        {files === null ? <p className="empty small">…</p> : files.length === 0 ? <p className="empty small">No files yet.</p> : (
          <ul className="project-files">
            {files.map((file) => <li key={file.name}><Icon name="file" size={13} /><span>{file.name}</span><span className="dialog-note">{file.size < 1024 ? `${file.size} B` : humanBytes(file.size)}</span></li>)}
          </ul>
        )}
        {reach !== "none" ? <p className="dialog-note knowledge-reach">{REACH[reach]}</p> : null}
        {bridge.knowledge.available() ? (
          <button className="project-add-files" onClick={() => void addFiles()}><Icon name="plus" size={13} />Add files…</button>
        ) : null}
      </section>
      <section>
        <h2>Questions</h2>
        {props.questions.length === 0 ? <p className="empty small">None yet: write the first below.</p> : (
          <ul className="project-questions">
            {props.questions.map((thread) => (
              <li key={thread.id}><button className="link-button" onClick={() => props.onOpen(thread.id)}>{props.title(thread)}</button></li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
