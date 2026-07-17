// Agent 调试落盘工具(从 agent.ts 抽出):每轮 LLM 原始响应日志 + 可选的完整上下文 dump。
// 纯副作用(写 tmp/),无业务逻辑;开关 dumpLlmContextOn 是进程级状态,main 经 IPC 热切换。

import * as fs from "node:fs";
import * as path from "node:path";
import { messagesCost } from "./compaction";
import type { Message, ToolSchema } from "./agent";

// 调试日志路径由 main 在启动时注入；默认值保留开发环境兼容。
let logPath = path.resolve(__dirname, "..", "..", "tmp", "agent_turns.jsonl");
let dumpDir = path.resolve(__dirname, "..", "..", "tmp", "agent_contexts");
let logReady = false;
let dumpDirReady = false;

export function configureAgentDebugPaths(logDir: string): void {
  logPath = path.join(logDir, "agent_turns.jsonl");
  dumpDir = path.join(logDir, "agent_contexts");
  logReady = false;
  dumpDirReady = false;
}
export function logTurn(entry: Record<string, unknown>) {
  try {
    if (!logReady) {
      fs.mkdirSync(path.dirname(logPath), { recursive: true });
      logReady = true;
    }
    fs.appendFileSync(logPath, JSON.stringify({ ts: new Date().toISOString(), ...entry }) + "\n");
  } catch (e) {
    console.warn("[agent] logTurn failed:", e);
  }
}

// 调试会话日志:风格学 GA llmcore.py:_write_llm_log(=== Prompt === / === Response === 块),
// 但**每轮一个文件**——因为我们切歌=硬压清空、turn 归零,塞一个文件会乱。开关默认从环境变量
// AUDIO_QC_DUMP_LLM=1 取初值,运行期经开发者菜单 setDumpLlmContext 热切换。开启后每轮 LLM 调用:
//   prompt 前写 → tmp/agent_contexts/<chatId>_<song>_t<NNN>.txt(完整 messages + 工具清单)
//   response 后 append 进同一文件(原始返回 + tool_calls + finish/usage)
// 文件名带 song(切歌 turn 归零,避免两首歌 t001 互相覆盖);phase A 用 "phaseA"。
let dumpLlmContextOn = process.env.AUDIO_QC_DUMP_LLM === "1";
export function setDumpLlmContext(on: boolean): void {
  dumpLlmContextOn = !!on;
  console.log(`[agent] dump LLM context: ${dumpLlmContextOn ? "ON" : "OFF"}`);
}
export function isDumpLlmContextOn(): boolean {
  return dumpLlmContextOn;
}

function _logTs(): string {
  return new Date().toISOString().replace("T", " ").slice(0, 19);
}

function _ensureDumpDir(): boolean {
  try {
    if (!dumpDirReady) {
      fs.mkdirSync(dumpDir, { recursive: true });
      dumpDirReady = true;
    }
    return true;
  } catch (e) {
    console.warn("[agent] dump dir failed:", e);
    return false;
  }
}

// 本轮日志文件路径:<chatId>_<song>_t<NNN>.txt。song 里的非法文件名字符替成 _。
function _turnFile(chatId: string, song: string | null, turn: number): string {
  const songToken = (song || "phaseA").replace(/[\\/:*?"<>|]/g, "_");
  return path.join(dumpDir, `${chatId}_${songToken}_t${String(turn).padStart(3, "0")}.txt`);
}

function _renderMessage(m: Message, i: number): string {
  const head =
    m.role === "tool"
      ? `--- [${i}] tool (${m.name ?? "?"}) tool_call_id=${m.tool_call_id ?? "?"} ---`
      : `--- [${i}] ${m.role} ---`;
  const lines = [head];
  if (m.content) {
    lines.push(typeof m.content === "string" ? m.content : JSON.stringify(m.content));
  }
  if (m.role === "assistant" && m.tool_calls) {
    for (const tc of m.tool_calls) {
      lines.push(`<tool_call id=${tc.id}> ${tc.function.name}(${tc.function.arguments})`);
    }
  }
  return lines.join("\n");
}

// GA 风格 Prompt 块:调 LLM 前写(覆盖建本轮文件),完整 messages + 工具清单。
export function logLlmPrompt(
  chatId: string,
  turn: number,
  song: string | null,
  messages: Message[],
  tools: ToolSchema[],
): void {
  if (!dumpLlmContextOn || !_ensureDumpDir()) return;
  const header =
    `=== Prompt === ${_logTs()} | chat=${chatId} song=${song ?? "(phase A)"} turn=${turn} | ` +
    `msgs=${messages.length} tools=${tools.length} ~${messagesCost(messages)}chars`;
  const toolLine = `[tools] ${tools.map((t) => t.function.name).join(", ")}`;
  const body = messages.map((m, i) => _renderMessage(m, i)).join("\n");
  try {
    fs.writeFileSync(_turnFile(chatId, song, turn), `${header}\n${toolLine}\n${body}\n\n`, "utf-8");
  } catch (e) {
    console.warn("[agent] prompt log failed:", e);
  }
}

// GA 风格 Response 块:调 LLM 后 append 进同一轮文件,原始响应 + tool_calls + finish/usage。
export function logLlmResponse(
  chatId: string,
  turn: number,
  song: string | null,
  resp: {
    content: string;
    toolCalls: { function: { name: string; arguments: string } }[];
    finishReason: string;
    promptTokens: number;
    completionTokens: number;
    cachedTokens: number;
  },
): void {
  if (!dumpLlmContextOn || !_ensureDumpDir()) return;
  const header =
    `=== Response === ${_logTs()} | chat=${chatId} turn=${turn} | ` +
    `finish=${resp.finishReason} in=${resp.promptTokens} out=${resp.completionTokens} cache=${resp.cachedTokens}`;
  const lines = [header, resp.content || "(empty)"];
  if (resp.toolCalls.length > 0) {
    lines.push("[tool_calls]");
    for (const tc of resp.toolCalls) {
      lines.push(`  • ${tc.function.name}(${tc.function.arguments})`);
    }
  }
  try {
    fs.appendFileSync(_turnFile(chatId, song, turn), `${lines.join("\n")}\n\n`, "utf-8");
  } catch (e) {
    console.warn("[agent] response log failed:", e);
  }
}
