// Chat session 持久化(SQLite)。
//
// 三张表 session / message / part,schema 对照 sst/opencode 的 message-part 拆分:
//   - session: chat 本体 meta(标题、模型、时间戳)
//   - message: 一轮对话(role / finish_reason / token usage)
//   - part:   message 内部的块(text / tool_call / tool_result / summary / thinking)
//
// part 拆分是为了 streaming friendly —— 即使现在不 stream,把 assistant turn 拆成
// "若干 text + 若干 tool_call" 多 row 也比塞 JSON 字段干净,以后接 streaming 时
// schema 不用改。
//
// 本文件只暴露 CRUD,不做业务逻辑。agent loop 落地时再写 chat.ts 那一层组合调用。

import Database from "better-sqlite3";
import * as path from "node:path";
import * as fs from "node:fs";
import * as crypto from "node:crypto";

let db: Database.Database | null = null;

function nowMs(): number {
  return Date.now();
}

function newId(prefix: string): string {
  // 短随机 id,够防碰撞,人眼看也短一点。chat_xxxxxxxx 格式。
  return `${prefix}_${crypto.randomBytes(6).toString("hex")}`;
}

// ============================================================
//  init / schema
// ============================================================

const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS session (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  model       TEXT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS message (
  id                       TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
  -- turn 顺序,session 内单调递增。0 = 系统消息(如有),1 起为对话
  turn_index               INTEGER NOT NULL,
  role                     TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
  -- assistant 才有的元数据;user / system 留 NULL
  finish_reason            TEXT,
  input_tokens             INTEGER,
  output_tokens            INTEGER,
  cache_creation_tokens    INTEGER,
  cache_read_tokens        INTEGER,
  created_at               INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_session ON message(session_id, turn_index);

CREATE TABLE IF NOT EXISTS part (
  id           TEXT PRIMARY KEY,
  message_id   TEXT NOT NULL REFERENCES message(id) ON DELETE CASCADE,
  -- message 内顺序
  part_index   INTEGER NOT NULL,
  -- 类型:text / tool_call / tool_result / summary / thinking
  type         TEXT NOT NULL,
  -- type 决定 content 的具体 schema(见下),统一 JSON 字符串存
  content      TEXT NOT NULL,
  created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_part_message ON part(message_id, part_index);
`;

export function initDb(dataDir: string): Database.Database {
  if (db) return db;
  fs.mkdirSync(dataDir, { recursive: true });
  const dbPath = path.join(dataDir, "chats.db");
  db = new Database(dbPath);
  db.pragma("journal_mode = WAL");      // 多读 + 顺序写,WAL 性价比高
  db.pragma("foreign_keys = ON");        // CASCADE 删 session 时连带清掉 messages/parts
  db.exec(SCHEMA_SQL);
  return db;
}

export function closeDb(): void {
  if (db) {
    db.close();
    db = null;
  }
}

function getDb(): Database.Database {
  if (!db) throw new Error("db not initialized; call initDb() first");
  return db;
}

// ============================================================
//  session CRUD
// ============================================================

export interface SessionRow {
  id: string;
  title: string;
  model: string | null;
  created_at: number;
  updated_at: number;
}

export function createSession(title: string, model?: string): SessionRow {
  const id = newId("chat");
  const t = nowMs();
  getDb()
    .prepare(
      "INSERT INTO session (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)"
    )
    .run(id, title, model ?? null, t, t);
  return { id, title, model: model ?? null, created_at: t, updated_at: t };
}

export function listSessions(): SessionRow[] {
  return getDb()
    .prepare("SELECT * FROM session ORDER BY updated_at DESC")
    .all() as SessionRow[];
}

export function getSession(id: string): SessionRow | null {
  const row = getDb().prepare("SELECT * FROM session WHERE id = ?").get(id) as SessionRow | undefined;
  return row ?? null;
}

export function renameSession(id: string, title: string): void {
  getDb()
    .prepare("UPDATE session SET title = ?, updated_at = ? WHERE id = ?")
    .run(title, nowMs(), id);
}

export function touchSession(id: string): void {
  // 任何时候 message/part 写入应顺手 touch 一次,让 listSessions 排序合理
  getDb().prepare("UPDATE session SET updated_at = ? WHERE id = ?").run(nowMs(), id);
}

export function deleteSession(id: string): void {
  // FK CASCADE 会带走 messages 和 parts
  getDb().prepare("DELETE FROM session WHERE id = ?").run(id);
}

// ============================================================
//  message CRUD
// ============================================================

export interface MessageRow {
  id: string;
  session_id: string;
  turn_index: number;
  role: "user" | "assistant" | "system";
  finish_reason: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_creation_tokens: number | null;
  cache_read_tokens: number | null;
  created_at: number;
}

export function appendMessage(args: {
  session_id: string;
  role: "user" | "assistant" | "system";
  finish_reason?: string;
  input_tokens?: number;
  output_tokens?: number;
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
}): MessageRow {
  const id = newId("msg");
  const t = nowMs();
  // 自动算下一 turn_index
  const last = getDb()
    .prepare("SELECT MAX(turn_index) AS m FROM message WHERE session_id = ?")
    .get(args.session_id) as { m: number | null };
  const turn_index = (last?.m ?? -1) + 1;
  getDb()
    .prepare(
      `INSERT INTO message
       (id, session_id, turn_index, role, finish_reason,
        input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .run(
      id,
      args.session_id,
      turn_index,
      args.role,
      args.finish_reason ?? null,
      args.input_tokens ?? null,
      args.output_tokens ?? null,
      args.cache_creation_tokens ?? null,
      args.cache_read_tokens ?? null,
      t
    );
  touchSession(args.session_id);
  return {
    id,
    session_id: args.session_id,
    turn_index,
    role: args.role,
    finish_reason: args.finish_reason ?? null,
    input_tokens: args.input_tokens ?? null,
    output_tokens: args.output_tokens ?? null,
    cache_creation_tokens: args.cache_creation_tokens ?? null,
    cache_read_tokens: args.cache_read_tokens ?? null,
    created_at: t,
  };
}

export function updateMessageFinish(args: {
  id: string;
  finish_reason: string;
  input_tokens?: number;
  output_tokens?: number;
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
}): void {
  getDb()
    .prepare(
      `UPDATE message
       SET finish_reason = ?,
           input_tokens = COALESCE(?, input_tokens),
           output_tokens = COALESCE(?, output_tokens),
           cache_creation_tokens = COALESCE(?, cache_creation_tokens),
           cache_read_tokens = COALESCE(?, cache_read_tokens)
       WHERE id = ?`
    )
    .run(
      args.finish_reason,
      args.input_tokens ?? null,
      args.output_tokens ?? null,
      args.cache_creation_tokens ?? null,
      args.cache_read_tokens ?? null,
      args.id
    );
}

export function listMessages(session_id: string): MessageRow[] {
  return getDb()
    .prepare("SELECT * FROM message WHERE session_id = ? ORDER BY turn_index ASC")
    .all(session_id) as MessageRow[];
}

// ============================================================
//  part CRUD
// ============================================================

export type PartType = "text" | "tool_call" | "tool_result" | "summary" | "thinking";

export interface PartRow {
  id: string;
  message_id: string;
  part_index: number;
  type: PartType;
  // 反序列化后的 content;不同 type 的 schema 不同,见 PartContent* 类型
  content: unknown;
  created_at: number;
}

// 各 type 的 content 形状(写入侧最好用对应类型,读出来 unknown 由消费方 narrow)
export interface PartContentText { text: string }
export interface PartContentToolCall { tool_name: string; input: Record<string, unknown>; tool_use_id: string }
export interface PartContentToolResult { tool_use_id: string; output: unknown; is_error?: boolean }
export interface PartContentSummary { summary: string }   // GA <summary> 协议用
export interface PartContentThinking { thinking: string }

export function appendPart(args: {
  message_id: string;
  type: PartType;
  content: unknown;
}): PartRow {
  const id = newId("part");
  const t = nowMs();
  const last = getDb()
    .prepare("SELECT MAX(part_index) AS m FROM part WHERE message_id = ?")
    .get(args.message_id) as { m: number | null };
  const part_index = (last?.m ?? -1) + 1;
  getDb()
    .prepare(
      "INSERT INTO part (id, message_id, part_index, type, content, created_at) VALUES (?, ?, ?, ?, ?, ?)"
    )
    .run(id, args.message_id, part_index, args.type, JSON.stringify(args.content), t);
  return {
    id,
    message_id: args.message_id,
    part_index,
    type: args.type,
    content: args.content,
    created_at: t,
  };
}

export function listParts(message_id: string): PartRow[] {
  const rows = getDb()
    .prepare("SELECT * FROM part WHERE message_id = ? ORDER BY part_index ASC")
    .all(message_id) as Array<Omit<PartRow, "content"> & { content: string }>;
  return rows.map((r) => ({ ...r, content: JSON.parse(r.content) as unknown }));
}
