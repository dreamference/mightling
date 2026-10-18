// The agent message board (DESKTOP_PARITY §2.13, Phase 3 item 10): the switches in config.toml
// that turn it on, and its posts grouped into discussions for the view.

import type { BoardPost } from "./bridge";

/** Whether the board is on: Codex's board extension needs both features (core/src/agent_message_board.rs). */
export function boardOn(config: Record<string, unknown> | null): boolean {
  const features = (config?.features ?? {}) as Record<string, unknown>;
  return features.agent_message_board === true && features.multi_agent_v2 === true;
}

/** The SQLite home config.toml names, if any (the server's default is CODEX_HOME). */
export function sqliteHome(config: Record<string, unknown> | null): string | null {
  return typeof config?.sqlite_home === "string" ? config.sqlite_home : null;
}

export interface Discussion {
  root: BoardPost;
  replies: BoardPost[];
}

/** Posts (newest first, as read) as discussions: a first post with its replies, oldest reply first; the newest discussion first. */
export function discussions(posts: BoardPost[], channel: string | null): Discussion[] {
  const shown = posts.filter((post) => !channel || post.channel === channel);
  const byRoot = new Map<string, Discussion>();
  for (const post of [...shown].sort((a, b) => a.at - b.at)) {
    if (post.id === post.root) byRoot.set(post.id, { root: post, replies: byRoot.get(post.id)?.replies ?? [] });
    else {
      const found = byRoot.get(post.root);
      if (found) found.replies.push(post);
      // A reply whose first post is older than what was read stands for its discussion.
      else byRoot.set(post.root, { root: post, replies: [] });
    }
  }
  const last = (entry: Discussion) => Math.max(entry.root.at, ...entry.replies.map((reply) => reply.at));
  return [...byRoot.values()].sort((a, b) => last(b) - last(a));
}
