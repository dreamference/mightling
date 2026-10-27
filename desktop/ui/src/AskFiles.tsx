// What Ask made, in one list (DESKTOP §16 B30, DESKTOP_PARITY_CLAUDE §2.1): the files in the folders
// of Ask's questions, read with `fs/readDirectory`, newest question first, each opening in its
// question's tab area. Hidden files (the incognito mark and the like) are left out.

import { useEffect, useState } from "react";

import { fileKindLabel } from "./ask";
import { artifactKind } from "./artifacts";
import { Icon } from "./icons";
import type { Thread } from "./protocol/v2/Thread";
import type { RpcClient } from "./rpc";

/** How many questions the list reads, newest first. */
export const QUESTIONS_READ = 60;

/** One question's files, as the list shows them. */
export interface QuestionFiles {
  thread: string;
  title: string;
  files: string[];
}

/** The files a question's folder listing shows: regular files only, no hidden ones, by name. */
export function shownFiles(entries: { fileName: string; isFile: boolean }[]): string[] {
  return entries.filter((entry) => entry.isFile && !entry.fileName.startsWith(".")).map((entry) => entry.fileName).sort((a, b) => a.localeCompare(b));
}

/** The questions to read: kept ones with a folder, newest first, capped. */
export function questionsToRead(threads: Thread[]): Thread[] {
  return threads.filter((thread) => !thread.ephemeral && thread.cwd).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, QUESTIONS_READ);
}

export function AskFiles(props: { client: RpcClient; questions: Thread[]; titleOf: (thread: Thread) => string; onOpen: (thread: string, path: string) => void; onClose: () => void }) {
  const { client, questions } = props;
  const [found, setFound] = useState<QuestionFiles[] | null>(null);
  useEffect(() => {
    let current = true;
    void Promise.all(questionsToRead(questions).map(async (thread) => {
      const answer = await client.request("fs/readDirectory", { path: thread.cwd }).catch(() => null);
      const files = answer ? shownFiles(answer.entries) : [];
      return { thread: thread.id, title: props.titleOf(thread), files: files.map((name) => `${thread.cwd.replace(/[\\/]+$/, "")}/${name}`) };
    })).then((list) => current && setFound(list.filter((entry) => entry.files.length > 0)));
    return () => {
      current = false;
    };
    // The list is read once, when it opens.
  }, [client]);
  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <section className="dialog ask-files" role="dialog" aria-label="Files from answers" onClick={(event) => event.stopPropagation()}>
        <header><h2>Files from answers</h2><button className="icon" aria-label="Close" onClick={props.onClose}><Icon name="x" size={16} /></button></header>
        <p className="dialog-note">What Ask's answers made, and what you attached, in each question's folder; a click opens it beside its question.</p>
        {found === null ? <p className="empty small">Reading…</p> : found.length === 0 ? <p className="empty small">No question has a file yet.</p> : (
          <div className="ask-files-list">
            {found.map((question) => (
              <section key={question.thread} aria-label={question.title}>
                <h3>{question.title}</h3>
                {question.files.map((file) => (
                  <button key={file} className="ask-file" onClick={() => props.onOpen(question.thread, file)}>
                    <Icon name="file" size={14} /><span className="ask-file-name">{file.split("/").pop()}</span><span className="ask-file-kind">{fileKindLabel(artifactKind(file))}</span>
                  </button>
                ))}
              </section>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
