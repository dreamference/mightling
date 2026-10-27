// Deep links in full (DESKTOP_PARITY §4.5 item 35, DESKTOP §16 B19): `ling://new?originUrl=…` starts
// the new thread in the open project whose git remote is that one, however the remote is written
// (https or ssh, with or without `.git` or a user name). Pure: no DOM.

/** A remote as `host/path`, lower-case host, no scheme, user, port, `.git` or trailing slash; null if it is not one. */
export function remoteKey(remote: string): string | null {
  const text = remote.trim();
  const url = text.match(/^(?:https?|ssh|git):\/\/(?:[^@/]+@)?([^/:]+)(?::\d+)?\/(.+?)(?:\.git)?\/?$/i);
  const ssh = text.match(/^(?:[^@/]+@)?([^:/]+):(?!\/)(.+?)(?:\.git)?\/?$/);
  const found = url ?? ssh;
  return found ? `${found[1].toLowerCase()}/${found[2]}` : null;
}

/** Whether two remotes name the same repository. */
export function sameRemote(a: string, b: string): boolean {
  const left = remoteKey(a);
  return left !== null && left === remoteKey(b);
}

/** The Settings pages a link may open by name (`ling://settings/<page>`). */
export const LINKED_SETTINGS = ["general", "documents", "profile", "shortcuts", "notifications", "appearance", "personalization", "memories", "archived", "git", "project", "worktrees", "browser", "pets", "suggestions", "agents", "node", "voice"] as const;
