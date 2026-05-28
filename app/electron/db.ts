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

const SCHEMA_VERSION = 3;

const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS session (
  id                      TEXT PRIMARY KEY,
  title                   TEXT NOT NULL,
  model                   TEXT,
  phase                   TEXT,      -- "A" | "B"
  song                    TEXT,      -- Phase B 锁定的 song folder
  -- 软压/硬压发生时写入,LLM 上下文的"活跃起点":turn_index < live_from 的
  -- message 只贡献 summary 到 trail,不进 messages[];0 = 全量,从未压过。
  -- 保证 reload 后 LLM messages 跟关闭前一致(否则切歌硬压后会"复活"前一首)。
  live_from_turn_index    INTEGER NOT NULL DEFAULT 0,
  created_at              INTEGER NOT NULL,
  updated_at              INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS message (
  id                       TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
  -- turn 顺序,session 内单调递增。0 = 系统消息(如有),1 起为对话
  turn_index               INTEGER NOT NULL,
  role                     TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
  -- assistant 才有的元数据;user / system / tool 留 NULL
  finish_reason            TEXT,
  input_tokens             INTEGER,
  output_tokens            INTEGER,
  cache_creation_tokens    INTEGER,
  cache_read_tokens        INTEGER,
  -- tool 消息的 tool_call_id;其他 role 为 NULL
  tool_call_id             TEXT,
  tool_name                TEXT,
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
  // schema 版本不匹配的迁移策略:能 ALTER 就 ALTER,免得丢用户的历史会话。
  // 只有 v1 ≤→ v2 那次因为 CHECK 约束变化(role 加 'tool')必须重建;v2→v3 加列,ALTER 即可。
  const ver = db.pragma("user_version", { simple: true }) as number;
  if (ver === 0) {
    db.exec(SCHEMA_SQL);
    db.pragma(`user_version = ${SCHEMA_VERSION}`);
  } else if (ver < 2) {
    console.warn(`[db] schema v${ver} < 2; recreating tables (v1 CHECK 约束不兼容)`);
    db.exec(`
      DROP TABLE IF EXISTS part;
      DROP TABLE IF EXISTS message;
      DROP TABLE IF EXISTS session;
    `);
    db.exec(SCHEMA_SQL);
    db.pragma(`user_version = ${SCHEMA_VERSION}`);
  } else {
    // v2 → v3:只是 session 加一列,ALTER 即可保数据
    if (ver < 3) {
      console.log("[db] migrating v2 → v3: adding session.live_from_turn_index");
      db.exec(
        "ALTER TABLE session ADD COLUMN live_from_turn_index INTEGER NOT NULL DEFAULT 0",
      );
      db.pragma(`user_version = 3`);
    }
    db.exec(SCHEMA_SQL); // CREATE IF NOT EXISTS 走过场,顺手补漏 index
  }
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
  phase: string | null;
  song: string | null;
  live_from_turn_index: number;
  created_at: number;
  updated_at: number;
}

export function createSession(title: string, model?: string): SessionRow {
  const id = newId("chat");
  return createSessionWithId(id, title, model);
}

/** 渲染端自己生成 chatId,主进程不再覆盖。这条用 INSERT OR IGNORE,id 已存在等价 no-op。 */
export function createSessionWithId(id: string, title: string, model?: string): SessionRow {
  const t = nowMs();
  getDb()
    .prepare(
      "INSERT OR IGNORE INTO session (id, title, model, phase, song, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    .run(id, title, model ?? null, null, null, t, t);
  const row = getSession(id);
  if (!row) throw new Error(`createSessionWithId: session ${id} did not insert and was not preexisting`);
  return row;
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

/** 把 chat 的 phase / song 同步进 db,reload 后能据此重建 baseSystem。 */
export function updateSessionPhase(id: string, phase: string | null, song: string | null): void {
  getDb()
    .prepare("UPDATE session SET phase = ?, song = ?, updated_at = ? WHERE id = ?")
    .run(phase, song, nowMs(), id);
}

/** 软压/硬压时调:把 LLM messages 的"活跃起点 turn_index"持久化,reload 时
 *  rebuildState 据此过滤 — turn_index < live_from 的 message 只贡献 summary 到 trail,
 *  不进 messages[],保证恢复态跟关闭前一致。 */
export function updateSessionLiveFrom(id: string, liveFromTurnIndex: number): void {
  getDb()
    .prepare("UPDATE session SET live_from_turn_index = ?, updated_at = ? WHERE id = ?")
    .run(liveFromTurnIndex, nowMs(), id);
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
  role: "user" | "assistant" | "system" | "tool";
  finish_reason: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_creation_tokens: number | null;
  cache_read_tokens: number | null;
  tool_call_id: string | null;
  tool_name: string | null;
  created_at: number;
}

export function appendMessage(args: {
  session_id: string;
  role: "user" | "assistant" | "system" | "tool";
  finish_reason?: string;
  input_tokens?: number;
  output_tokens?: number;
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
  tool_call_id?: string;
  tool_name?: string;
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
        input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
        tool_call_id, tool_name, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
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
      args.tool_call_id ?? null,
      args.tool_name ?? null,
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
    tool_call_id: args.tool_call_id ?? null,
    tool_name: args.tool_name ?? null,
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

/** 取 session 当前最大 turn_index;空 session 返回 -1。
 *  用于 enterPhaseB 硬压时算 live_from 边界(= max + 1)。 */
export function getMaxTurnIndex(session_id: string): number {
  const row = getDb()
    .prepare("SELECT MAX(turn_index) AS m FROM message WHERE session_id = ?")
    .get(session_id) as { m: number | null };
  return row?.m ?? -1;
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

// ============================================================
//  hydrate (一次性把 session 所有 msg + parts 拉出来)
// ============================================================

export interface HydratedMessage extends MessageRow {
  parts: PartRow[];
}

export interface HydratedSession {
  session: SessionRow;
  messages: HydratedMessage[];
}

/** 一次拉全 session,reload 时给 AgentRunner / UI 重建用。 */
export function hydrateSession(id: string): HydratedSession | null {
  const session = getSession(id);
  if (!session) return null;
  const messages = listMessages(id).map((m) => ({
    ...m,
    parts: listParts(m.id),
  }));
  return { session, messages };
}
