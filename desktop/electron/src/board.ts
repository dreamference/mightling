// The agent message board (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.13, Phase 3 item 10):
// Codex's `ext/agent-message-board` keeps the discussions subagents post to each other in a local
// SQLite file, `agent_message_board_1.sqlite` in the SQLite home (`CODEX_SQLITE_HOME`, else
// `sqlite_home` in config.toml, else `$CODEX_HOME`); the app-server has no method for it, so the
// window reads that file, read only, with Node's own `node:sqlite` (no dependency). Writing stays
// the agents' (their board tools); nothing here changes the file.

import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

export const BOARD_FILE = "agent_message_board_1.sqlite";

export interface BoardPost {
  id: string;
  channel: string;
  /** The post that starts its discussion (itself for a first post). */
  root: string;
  author: string;
  /** Unix milliseconds (the extension stores microseconds). */
  at: number;
  text: string;
}

export interface Board {
  /** The agent tree's session (its root thread's). */
  board: string;
  channels: { name: string; author: string; posts: number }[];
  posts: BoardPost[];
}

/** Where the board's file is: the SQLite home the server uses. */
export function boardFile(codexHome: string, configuredHome: string | null, env: NodeJS.ProcessEnv = process.env): string {
  const fromEnv = env.CODEX_SQLITE_HOME?.trim();
  const home = fromEnv ? path.resolve(fromEnv) : configuredHome && path.isAbsolute(configuredHome) ? configuredHome : codexHome;
  return path.join(home, BOARD_FILE);
}

/** A stored post's text: the payload is `{ metadata, text }` as the extension writes it. */
export function postText(payload: string): string {
  try {
    const parsed = JSON.parse(payload) as { text?: unknown };
    return typeof parsed.text === "string" ? parsed.text : "";
  } catch {
    return "";
  }
}

/** Every board's channels and its latest posts (500 at most in all), newest first; none when the file does not exist. */
export function readBoards(file: string, limit = 500): Board[] {
  if (!fs.existsSync(file)) return [];
  const db = new DatabaseSync(file, { readOnly: true });
  try {
    const deleted = new Set((db.prepare("SELECT board FROM deleted_boards").all() as { board: string }[]).map((row) => row.board));
    const boards = new Map<string, Board>();
    const boardOf = (id: string) => {
      let found = boards.get(id);
      if (!found) boards.set(id, (found = { board: id, channels: [], posts: [] }));
      return found;
    };
    const counts = db.prepare("SELECT board, channel, COUNT(*) AS n FROM posts GROUP BY board, channel").all() as { board: string; channel: string; n: number }[];
    const count = new Map(counts.map((row) => [`${row.board}␟${row.channel}`, Number(row.n)]));
    for (const row of db.prepare("SELECT board, name, author FROM channels ORDER BY timestamp").all() as { board: string; name: string; author: string }[]) {
      if (deleted.has(row.board)) continue;
      boardOf(row.board).channels.push({ name: row.name, author: row.author, posts: count.get(`${row.board}␟${row.name}`) ?? 0 });
    }
    const rows = db.prepare("SELECT board, id, channel, root, author, timestamp, payload FROM posts ORDER BY seq DESC LIMIT ?").all(limit) as { board: string; id: string; channel: string; root: string; author: string; timestamp: number; payload: string }[];
    for (const row of rows) {
      if (deleted.has(row.board)) continue;
      boardOf(row.board).posts.push({ id: row.id, channel: row.channel, root: row.root, author: row.author, at: Math.floor(Number(row.timestamp) / 1000), text: postText(row.payload) });
    }
    return [...boards.values()].sort((a, b) => (b.posts[0]?.at ?? 0) - (a.posts[0]?.at ?? 0));
  } finally {
    db.close();
  }
}
