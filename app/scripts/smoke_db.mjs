// 烟测脚本:验证 SQLite chat 持久化的 schema + CRUD。
// 不依赖 Electron。跑法:
//   npm rebuild better-sqlite3   # 切到 Node ABI(NODE_MODULE_VERSION 115)
//   node app/scripts/smoke_db.mjs
//   npx electron-rebuild -f -w better-sqlite3 -v 33.4.11   # 切回 Electron ABI(130)
//
// 这两步切换是 better-sqlite3 是 native 模块的代价 —— Electron 内嵌的 Node 用
// 不同 ABI。日常开发不用跑这个,只在改 db.ts schema 时跑一次确认能跑通。
// 用 tmp 目录的临时 DB,跑完删掉。

import * as os from "node:os";
import * as path from "node:path";
import * as fs from "node:fs";

// 编译后的 db.js 在 dist-electron/。如果没 build 过,直接 import 源文件需要 ts-node;
// 这里取巧:先 build 再 import。但更简单是手写一份小逻辑直接验证 schema。
// 选项 A:跑前手动 build。选项 B:复制 schema 到本脚本验证。
// 走 B,因为 schema 是字符串,复制没成本,验证完整性最高。

import Database from "better-sqlite3";
import * as crypto from "node:crypto";

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "smoke_db_"));
const dbPath = path.join(tmpDir, "chats.db");
const db = new Database(dbPath);
db.pragma("journal_mode = WAL");
db.pragma("foreign_keys = ON");

// 复制 db.ts 里的 SCHEMA_SQL,verify schema 能执行 + CRUD 能跑通
db.exec(`
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
  turn_index               INTEGER NOT NULL,
  role                     TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
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
  part_index   INTEGER NOT NULL,
  type         TEXT NOT NULL,
  content      TEXT NOT NULL,
  created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_part_message ON part(message_id, part_index);
`);

const newId = (p) => `${p}_${crypto.randomBytes(6).toString("hex")}`;
const now = () => Date.now();

// 1. 建一个 session
const sid = newId("chat");
const t = now();
db.prepare("INSERT INTO session VALUES (?,?,?,?,?)").run(sid, "测试 chat", "claude-opus-4-7", t, t);

// 2. 加一条 user message
const uid = newId("msg");
db.prepare(
  "INSERT INTO message (id,session_id,turn_index,role,created_at) VALUES (?,?,?,?,?)"
).run(uid, sid, 0, "user", now());

const upid = newId("part");
db.prepare(
  "INSERT INTO part (id,message_id,part_index,type,content,created_at) VALUES (?,?,?,?,?,?)"
).run(upid, uid, 0, "text", JSON.stringify({ text: "玫瑰三愿这首歌验收一下" }), now());

// 3. 加一条 assistant message,带两个 part(text + tool_call)
const aid = newId("msg");
db.prepare(
  "INSERT INTO message (id,session_id,turn_index,role,finish_reason,input_tokens,output_tokens,created_at) VALUES (?,?,?,?,?,?,?,?)"
).run(aid, sid, 1, "assistant", "tool_use", 1234, 56, now());

db.prepare(
  "INSERT INTO part (id,message_id,part_index,type,content,created_at) VALUES (?,?,?,?,?,?)"
).run(newId("part"), aid, 0, "text", JSON.stringify({ text: "好的,我先扫一下错误" }), now());

db.prepare(
  "INSERT INTO part (id,message_id,part_index,type,content,created_at) VALUES (?,?,?,?,?,?)"
).run(
  newId("part"),
  aid,
  1,
  "tool_call",
  JSON.stringify({
    tool_name: "audit_run_check",
    tool_use_id: "toolu_abc",
    input: { song_path: "C:/...玫瑰三愿" },
  }),
  now()
);

// 4. 查回来
const sessions = db.prepare("SELECT * FROM session").all();
console.log("[smoke] sessions:", sessions.length, sessions[0].title);

const messages = db
  .prepare("SELECT * FROM message WHERE session_id = ? ORDER BY turn_index")
  .all(sid);
console.log("[smoke] messages:", messages.length, messages.map((m) => m.role));

const partsForA = db
  .prepare("SELECT * FROM part WHERE message_id = ? ORDER BY part_index")
  .all(aid)
  .map((p) => ({ ...p, content: JSON.parse(p.content) }));
console.log("[smoke] assistant parts:", partsForA.length);
console.log("  - part[0] type:", partsForA[0].type, "content:", partsForA[0].content);
console.log("  - part[1] type:", partsForA[1].type, "tool:", partsForA[1].content.tool_name);

// 5. CASCADE delete:删 session 应连带带走 message 和 part
db.prepare("DELETE FROM session WHERE id = ?").run(sid);
const orphanMsgs = db.prepare("SELECT COUNT(*) AS n FROM message").get().n;
const orphanParts = db.prepare("SELECT COUNT(*) AS n FROM part").get().n;
console.log("[smoke] after CASCADE delete: messages=" + orphanMsgs + " parts=" + orphanParts + " (should both be 0)");

db.close();
fs.rmSync(tmpDir, { recursive: true, force: true });
console.log("[smoke] done");
