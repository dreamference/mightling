// The agent message board (DESKTOP_PARITY Phase 3 item 10): read from the extension's own SQLite file, read only.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";

import { boardFile, postText, readBoards } from "./board";

/** A board file as `ext/agent-message-board` (local.rs) lays it out. */
function boardDb(file: string): void {
  const db = new DatabaseSync(file);
  db.exec(`CREATE TABLE deleted_boards (board TEXT PRIMARY KEY NOT NULL);
    CREATE TABLE channels (board TEXT NOT NULL, name TEXT NOT NULL, name_search TEXT NOT NULL, created_at TEXT NOT NULL, timestamp INTEGER NOT NULL, author TEXT NOT NULL, PRIMARY KEY(board,name));
    CREATE TABLE posts (seq INTEGER PRIMARY KEY AUTOINCREMENT, board TEXT NOT NULL, id TEXT NOT NULL, channel TEXT NOT NULL, root TEXT NOT NULL, author TEXT NOT NULL, timestamp INTEGER NOT NULL, body_search TEXT NOT NULL, payload TEXT NOT NULL, request_id TEXT NOT NULL, request TEXT NOT NULL);`);
  db.exec(`INSERT INTO channels VALUES ('b1', 'retry', 'retry', '2026-10-18T09:00:00Z', 1000, '/root');
    INSERT INTO channels VALUES ('gone', 'old', 'old', '2026-10-01T09:00:00Z', 10, '/root');
    INSERT INTO deleted_boards VALUES ('gone');`);
  const post = db.prepare("INSERT INTO posts(board,id,channel,root,author,timestamp,body_search,payload,request_id,request) VALUES(?,?,?,?,?,?,?,?,?,?)");
  post.run("b1", "p1", "retry", "p1", "/root/explorer", 1_760_000_000_000_000, "", JSON.stringify({ metadata: {}, text: "The flaky test sleeps for real." }), "r1", "{}");
  post.run("b1", "p2", "retry", "p1", "/root/reviewer", 1_760_000_060_000_000, "", JSON.stringify({ metadata: {}, text: "Patch backoff() to zero in the test." }), "r2", "{}");
  post.run("gone", "p3", "old", "p3", "/root", 20, "", JSON.stringify({ text: "x" }), "r3", "{}");
  db.close();
}

describe("the agent message board", () => {
  it("finds the file where the server keeps it", () => {
    expect(boardFile("/home/u/.mightling", null, {})).toBe("/home/u/.mightling/agent_message_board_1.sqlite");
    expect(boardFile("/home/u/.mightling", "/data/sqlite", {})).toBe("/data/sqlite/agent_message_board_1.sqlite");
    expect(boardFile("/home/u/.mightling", "/data/sqlite", { CODEX_SQLITE_HOME: "/env/sqlite" })).toBe("/env/sqlite/agent_message_board_1.sqlite");
  });

  it("reads boards, channels and posts newest first, leaves deleted boards out, changes nothing", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "board-"));
    const file = path.join(dir, "agent_message_board_1.sqlite");
    boardDb(file);
    const before = fs.readFileSync(file);
    const boards = readBoards(file);
    expect(boards).toHaveLength(1);
    expect(boards[0].channels).toEqual([{ name: "retry", author: "/root", posts: 2 }]);
    expect(boards[0].posts.map((p) => [p.author, p.text])).toEqual([["/root/reviewer", "Patch backoff() to zero in the test."], ["/root/explorer", "The flaky test sleeps for real."]]);
    expect(boards[0].posts[0].root).toBe("p1");
    expect(boards[0].posts[0].at).toBe(1_760_000_060_000);
    expect(fs.readFileSync(file).equals(before)).toBe(true);
    expect(readBoards(path.join(dir, "none.sqlite"))).toEqual([]);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("reads a post's text from its payload", () => {
    expect(postText('{"metadata":{},"text":"hi"}')).toBe("hi");
    expect(postText("not json")).toBe("");
  });
});
