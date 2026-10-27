// An Ask question continued in a project (DESKTOP §16 B33; DESKTOP_PARITY_CLAUDE §2.1, ASK §3.3's
// "Continue as Work"): the question is forked into the project's primary folder (`thread/fork` with
// that `cwd`), and the copy joins the project (`thread/metadata/update`'s `projectId`) and opens in
// Work. The policy (policy.rs, policy.ts) moves an Ask thread's fork only into a Work folder (an
// existing folder outside the Ask root), with the sandbox its own folder had and nothing broader;
// anywhere else the copy stays in the question's folder, which this says and undoes. The question
// itself stays in Ask. Every method here is in ling-rs/web/policy.json. Pure but for the client.

import { isAskThread } from "./ask";
import { primaryFolder, shownProjects } from "./projects";
import type { Project } from "./protocol/v2/Project";
import type { Thread } from "./protocol/v2/Thread";
import type { RpcClient } from "./rpc";

/** A project a question can be continued in: one the sidebar shows, with a primary folder. */
export interface ContinueTarget {
  id: string;
  name: string;
  folder: string;
}

/** The projects offered, in the sidebar's order: a project with no folder (one for questions) is not. */
export function continueTargets(projects: Project[]): ContinueTarget[] {
  return shownProjects(projects).flatMap((project) => {
    const folder = primaryFolder(project);
    return folder ? [{ id: project.id, name: project.name, folder }] : [];
  });
}

/**
 * Forks `question` into the project's primary folder and puts the copy in the project; resolves with
 * the new Work thread. A copy the policy kept in the question's folder (the folder is missing, or not
 * one a Work thread may use) is deleted again and the promise rejects, saying why.
 */
export async function continueInProject(client: RpcClient, question: Pick<Thread, "id">, target: ContinueTarget, model: string | null, askRoot: string | null): Promise<Thread> {
  const forked = (await client.request("thread/fork", { threadId: question.id, model, cwd: target.folder })) as { thread: Thread };
  if (isAskThread(forked.thread, askRoot)) {
    await client.request("thread/delete", { threadId: forked.thread.id }).catch(() => {});
    throw new Error(`The question could not be continued in ${target.name}: its folder ${target.folder} is missing, or not one a Work thread may use.`);
  }
  const moved = (await client.request("thread/metadata/update", { threadId: forked.thread.id, projectId: target.id })) as { thread?: Thread };
  return { ...forked.thread, projectId: moved.thread?.projectId ?? target.id };
}
