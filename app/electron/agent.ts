// AgentRunner — tool-calling agent loop running in Electron main.
//
// 结构:
// - 多 chat 会话(ChatState),按 chatId 索引,持久化到 db.ts(SQLite)
// - 两阶段 system prompt:Phase A 短(start_qc/sheet_list_my_pending)→ start_qc 后切 Phase B
//   = phase_b_header.md + agent_workflow.md;三段 prompt 文本都在 doc/prompts/,构造时注入目录读入
// - 上下文压缩两层:软压(messages > 30 在 user 边界裁中段,留 summaryTrail)+ 硬压(切歌清空,
//   只塞上一首 trail 摘要进新会话);LLM 每轮强制 <summary>...</summary>,extractSummary 抽出来续命
// - 工具调用走 MCP(sidecar 起的 stdio 子进程)+ 本地工具(start_qc / human_check)
// - LLM 走 sidecar /agent/completion 代理(api_key 不出 sidecar 域)
// - reload 后从 db hydrate,LLM messages + summaryTrail + Phase/song 全部重建

import * as fs from "node:fs";
import * as path from "node:path";
import type { Client as McpClient } from "@modelcontextprotocol/sdk/client/index.js";
import type { BrowserWindow } from "electron";
import {
  createSessionWithId,
  getSession,
  hydrateSession,
  appendMessage,
  appendPart,
  updateSessionPhase,
  updateSessionLiveFrom,
  getMaxTurnIndex,
  type HydratedSession,
  type PartContentText,
  type PartContentToolCall,
  type PartContentToolResult,
  type PartContentSummary,
} from "./db";
import {
  COMPRESS_KEEP_RECENT,
  COMPRESS_MAX_LEN,
  COMPRESS_EVERY,
  CONTEXT_WIN_CHARS,
  messagesCost,
  extractSummary,
  cleanContent,
  compressHistoryTags,
  trimOverflow,
  buildAnchorMessage,
} from "./compaction";
import { mcpResultText } from "./mcpResult";
import { logTurn, logLlmPrompt, logLlmResponse, setDumpLlmContext, isDumpLlmContextOn } from "./agentDebug";

// 调试落盘(logTurn / GA 风格会话日志 / dump 开关)在 ./agentDebug。这里 re-export 开关,
// 让 main.ts 仍从 "./agent" 拿(对外门面不变)。
export { setDumpLlmContext, isDumpLlmContextOn };

type Role = "system" | "user" | "assistant" | "tool";

interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export interface Message {
  role: Role;
  content?: string | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  name?: string;
}

export interface ToolSchema {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}

interface ChatState {
  chatId: string;
  phase: "A" | "B";
  song: string | null;
  baseSystem: string;       // 不含历史摘要的静态 system prompt(Phase A 短文 / Phase B header+workflow)
  messages: Message[];
  // 跟 messages 一一对齐的"db turn_index"指针:
  //   - 写 db 的消息(user/assistant/tool)→ row.turn_index
  //   - 不写 db 的(system / 软压 trailMsg / 硬压上一首摘要 / nudge user / 占位 tool)→ null
  // 软压时拿 keepFrom 处的 turn_index 写到 session.live_from_turn_index,
  // reload 时 rebuildState 据此过滤,保证恢复态 = 关闭前。
  messageDbTurnIndexes: (number | null)[];
  summaryTrail: string[];   // 每轮 <summary> 抽取出的锚点行,作为 GA <history> block 注入下一轮
  turn: number;
  cancelled: boolean;
  running: boolean;
  // GA 协议:上一轮 LLM 漏写了 <summary>,本轮 anchor 里要给 [DANGER] 警告一次性提示
  missedSummary: boolean;
}

interface AgentEvent {
  chatId: string;
  type:
    | "assistant_text"
    | "tool_use"
    | "tool_result"
    | "phase_change"
    | "turn_done"
    | "awaiting_human"
    | "compacted"
    | "error";
  data?: unknown;
}

// UI 工具的 main 侧实现接口。AgentRunner 不直接知道 BrowserWindow / mixTracks 细节,
// main.ts 注入这三个函数;agent 调时只看 {ok, code?, message?, ...} 形态。
export interface UiTools {
  openFile(path: string): Promise<{ ok: boolean; code?: string; message?: string }>;
  loadSongMix(
    songPath: string,
    mode: "stems_plus_master" | "proj_files_plus_master",
  ): Promise<{ ok: boolean; code?: string; message?: string; loaded?: string[] }>;
  togglePlayback(
    kind: "beat" | "structure",
    on: boolean,
  ): Promise<{ ok: boolean; code?: string; message?: string }>;
}

// UI 重建用的 turn shape,与 AgentSidebar.tsx Turn 对齐。
// 不直接 import 渲染端类型(避免跨 main/renderer 边界),用 string literal kind 即可。
export type UiTurn =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; name: string; args: unknown; result?: unknown }
  | { kind: "phase"; label: string };

// 上下文压缩的纯逻辑(tag-aware truncate / 撑爆 trim / anchor / summary 抽取 / cleanContent)
// 全在 ./compaction。本文件只保留 "何时压" 的节流(compressCd)+ "压完后持久化 / 通知" 的
// 副作用编排(见 maybeCompact)。压缩参数常量也从 compaction 导入。

// prompt 文本不再内联:三段都在 doc/prompts/ 下,构造时按注入的目录读入(见构造函数)。
// 读失败给占位字符串(沿用原 workflow 软兜底),不让整个 runner 起不来。
function readPromptFile(dir: string, name: string): string {
  try {
    return fs.readFileSync(path.join(dir, name), "utf-8");
  } catch (e) {
    console.warn(`[agent] failed to load prompt ${name}:`, e);
    return `(prompt file missing: ${name})`;
  }
}

export class AgentRunner {
  private chats = new Map<string, ChatState>();
  private phaseAPrompt = "";   // doc/prompts/phase_a.md
  private phaseBHeader = "";   // doc/prompts/phase_b_header.md(含 {song} 占位)
  private workflowText = "";   // doc/prompts/agent_workflow.md
  private workspaceRoot: string | null = null;
  // 每个 chat 同时只有一个 human_check 在挂起;resolver 被设上后,
  // executeTool human_check 的 await 会卡住,直到 resolveHumanCheck 调用它。
  private pendingHumanCheck = new Map<string, (result: Record<string, unknown>) => void>();
  // 同时记一份待解析卡片的 decisions 数,cancel 时上报"已答 0/N",
  // 让模型知道用户中断时进度走到哪。
  private pendingHumanCheckMeta = new Map<string, { decisionCount: number }>();

  constructor(
    private mcp: McpClient,
    private getMainWindow: () => BrowserWindow | null,
    private promptsDir: string,
    private sidecarBase: string,
    private uiTools: UiTools,
  ) {
    this.phaseAPrompt = readPromptFile(promptsDir, "phase_a.md");
    this.phaseBHeader = readPromptFile(promptsDir, "phase_b_header.md");
    this.workflowText = readPromptFile(promptsDir, "agent_workflow.md");
  }

  /** Renderer 切换工作区时调,后续 startQc 用它做候选检索。 */
  setWorkspace(root: string | null): void {
    this.workspaceRoot = root;
    console.log(`[agent] workspace set to ${root}`);
  }

  /** 相对路径拼 workspaceRoot,绝对路径原样返回。
   *  workflow 鼓励 agent 传相对路径(短、省 token),UI 工具底层需要绝对路径。 */
  private resolveWorkspacePath(p: string): string {
    if (path.isAbsolute(p)) return p;
    if (!this.workspaceRoot) return p; // 没设 root 就交给底层报错
    return path.join(this.workspaceRoot, p);
  }

  private getOrCreate(chatId: string): ChatState {
    let s = this.chats.get(chatId);
    if (s) return s;

    // 先看 db 有没有这条 chat —— renderer reload / Electron 重启后,chatId 在
    // localStorage 里持久,但 in-memory chats Map 是空的。db 里有就 hydrate,
    // 没有就新建一条 session(用 renderer 传来的 id,避免 id 漂移)。
    const dbSession = getSession(chatId);
    if (dbSession) {
      const hydrated = hydrateSession(chatId);
      s = this.rebuildState(chatId, hydrated!);
      // GA tag-aware truncate 是确定性的:每次 reload 重压一次,内存里的 messages
      // 跟关闭前(已被截断的)一致;不强制就要等到第 5 次 callLlm 节流到才压,前 4 次
      // 用全量原文调 LLM,token 会暴涨。
      compressHistoryTags(s.messages, COMPRESS_KEEP_RECENT, COMPRESS_MAX_LEN);
      this.compressCd = 0; // 等价原 force=true:reload 重压一次后重置节流
      this.chats.set(chatId, s);
      return s;
    }

    // 新会话:db 插一条,内存里也建一条 + 写入 system message。
    // 默认标题"新会话",renderer 首发消息时自动改成 user text 前 24 字。
    createSessionWithId(chatId, "新会话", undefined);
    s = {
      chatId,
      phase: "A",
      song: null,
      baseSystem: this.phaseAPrompt,
      messages: [{ role: "system", content: this.phaseAPrompt }],
      messageDbTurnIndexes: [null],
      summaryTrail: [],
      turn: 0,
      cancelled: false,
      running: false,
      missedSummary: false,
    };
    this.chats.set(chatId, s);
    // 持久化 system seed
    const msgRow = appendMessage({ session_id: chatId, role: "system" });
    appendPart({ message_id: msgRow.id, type: "text", content: { text: this.phaseAPrompt } as PartContentText });
    return s;
  }

  /** 从 db hydrate 出来的 session,重建内存 ChatState(LLM messages + summaryTrail + phase)。
   *  关键:按 session.live_from_turn_index 过滤 — turn_index < live_from 的 message
   *  只贡献 summary 到 trail,不进 messages[]。这样 reload 后 LLM 看到的上下文跟
   *  关闭前(已被软压/硬压)完全一致,不会"复活"被压缩掉的轮次。 */
  private rebuildState(chatId: string, h: HydratedSession): ChatState {
    const phase: "A" | "B" = h.session.phase === "B" ? "B" : "A";
    const song = h.session.song;
    const liveFrom = h.session.live_from_turn_index ?? 0;
    let baseSystem = this.phaseAPrompt;
    if (phase === "B" && song) {
      baseSystem = this.phaseBHeader.replace("{song}", song) + this.workflowText;
    }

    // 第一遍:把所有 assistant.summary parts 拼成 trail(不论 turn_index,全保留)。
    // trail 跟历史 t1/t2/... 标号一一对齐,沿用原 rebuildState 计数方式。
    const summaryTrail: string[] = [];
    let assistantCount = 0;
    for (const m of h.messages) {
      if (m.role !== "assistant") continue;
      assistantCount += 1;
      for (const p of m.parts) {
        if (p.type === "summary") {
          summaryTrail.push(`[t${assistantCount}] ${(p.content as PartContentSummary).summary}`);
        }
      }
    }

    // 第二遍:组装 llmMessages + 平行 turnIndex 数组。
    const llmMessages: Message[] = [{ role: "system", content: baseSystem }];
    const dbTurnIdxs: (number | null)[] = [null];

    // live_from > 0 表示发生过压缩:在 system 后注入 trail 摘要 user(对齐内存里 maybeCompact 做法)
    if (liveFrom > 0 && summaryTrail.length > 0) {
      llmMessages.push({
        role: "user",
        content:
          `<历史摘要轨迹>\n` +
          `(已压缩:turn_index < ${liveFrom} 的老消息从 LLM 上下文剔除;以下 <summary> 锚点串成上下文脉络)\n` +
          summaryTrail.join("\n") +
          `\n</历史摘要轨迹>`,
      });
      dbTurnIdxs.push(null);
    }

    for (const m of h.messages) {
      if (m.role === "system") continue; // baseSystem 已就位
      if (m.turn_index < liveFrom) continue; // 被压缩掉
      if (m.role === "user") {
        const text = (m.parts.find((p) => p.type === "text")?.content as PartContentText | undefined)?.text ?? "";
        llmMessages.push({ role: "user", content: text });
        dbTurnIdxs.push(m.turn_index);
        continue;
      }
      if (m.role === "assistant") {
        let content = "";
        const toolCalls: ToolCall[] = [];
        for (const p of m.parts) {
          if (p.type === "text") content += (p.content as PartContentText).text;
          else if (p.type === "tool_call") {
            const tc = p.content as PartContentToolCall;
            toolCalls.push({
              id: tc.tool_use_id,
              type: "function",
              function: { name: tc.tool_name, arguments: JSON.stringify(tc.input) },
            });
          }
        }
        const msg: Message = { role: "assistant", content: content || null };
        if (toolCalls.length > 0) msg.tool_calls = toolCalls;
        llmMessages.push(msg);
        dbTurnIdxs.push(m.turn_index);
        continue;
      }
      if (m.role === "tool") {
        const tr = m.parts.find((p) => p.type === "tool_result")?.content as PartContentToolResult | undefined;
        llmMessages.push({
          role: "tool",
          tool_call_id: m.tool_call_id ?? tr?.tool_use_id ?? "",
          name: m.tool_name ?? undefined,
          content: typeof tr?.output === "string" ? tr.output : JSON.stringify(tr?.output ?? null),
        });
        dbTurnIdxs.push(m.turn_index);
      }
    }
    return {
      chatId,
      phase,
      song,
      baseSystem,
      messages: llmMessages,
      messageDbTurnIndexes: dbTurnIdxs,
      summaryTrail,
      turn: assistantCount,
      cancelled: false,
      running: false,
      missedSummary: false,
    };
  }

  /** Renderer 重新加载后用 chatId hydrate 一份 UI Turn[] 回放。
   *  - chatId 在 db 不存在 → 返回 []
   *  - 存在 → 解析 messages + parts,拼成与 AgentSidebar Turn 对齐的轻量 shape
   *  作为副作用,getOrCreate 会把 ChatState 拉进内存供后续 send 使用。 */
  hydrate(chatId: string): { phase: "A" | "B"; song: string | null; turns: UiTurn[] } {
    this.getOrCreate(chatId); // 确保 ChatState 已在内存
    const h = hydrateSession(chatId);
    if (!h) return { phase: "A", song: null, turns: [] };
    const turns: UiTurn[] = [];
    const toolTurnByCallId = new Map<string, number>(); // tool_use_id → turns 下标
    if (h.session.phase === "B" && h.session.song) {
      turns.push({ kind: "phase", label: `进入质检流程 · ${h.session.song}` });
    }
    for (const m of h.messages) {
      if (m.role === "system") continue;
      if (m.role === "user") {
        const text = (m.parts.find((p) => p.type === "text")?.content as PartContentText | undefined)?.text ?? "";
        // 跳过 hint / 续写 nudge 等系统注入的 user 消息(以 [上一首 / <历史摘要轨迹> /
        // 上一条回复被 max_tokens / 上一条回复是空 开头);它们对用户是噪声。
        if (
          text.startsWith("[上一首") ||
          text.startsWith("<历史摘要轨迹>") ||
          text.startsWith("上一条回复被 max_tokens") ||
          text.startsWith("上一条回复是空的") ||
          text.startsWith("开始质检 ")
        ) {
          continue;
        }
        turns.push({ kind: "user", text });
        continue;
      }
      if (m.role === "assistant") {
        let content = "";
        for (const p of m.parts) {
          if (p.type === "text") content += (p.content as PartContentText).text;
        }
        const cleaned = content.replace(/<summary>[\s\S]*?<\/summary>/g, "").trim();
        if (cleaned) turns.push({ kind: "assistant", text: cleaned });
        for (const p of m.parts) {
          if (p.type === "tool_call") {
            const tc = p.content as PartContentToolCall;
            turns.push({ kind: "tool", name: tc.tool_name, args: tc.input });
            toolTurnByCallId.set(tc.tool_use_id, turns.length - 1);
          }
        }
        continue;
      }
      if (m.role === "tool") {
        const tr = m.parts.find((p) => p.type === "tool_result")?.content as PartContentToolResult | undefined;
        const key = m.tool_call_id ?? tr?.tool_use_id ?? "";
        const idx = toolTurnByCallId.get(key);
        if (idx != null && turns[idx]?.kind === "tool") {
          (turns[idx] as { result?: unknown }).result = tr?.output;
        }
      }
    }
    return { phase: h.session.phase === "B" ? "B" : "A", song: h.session.song, turns };
  }

  // Cache 友好原则:system = baseSystem 始终不变。
  // trail 只在 maybeCompact 软压缩时,作为独立 user 消息注入夹在 system 和尾巴之间。
  // 这样在两次压缩之间,prefix (system + 早期固定 user) 字节稳定,DeepSeek 这种自动
  // prefix-cache 的后端能持续命中 workflow.md 那几 KB。

  private emit(ev: AgentEvent) {
    const win = this.getMainWindow();
    if (win && !win.isDestroyed()) {
      win.webContents.send("agent:event", ev);
    }
  }

  // ============ Phase 切换 ============

  private enterPhaseB(state: ChatState, song: string) {
    const wasPhaseB = state.phase === "B";
    const prevSong = state.song;
    state.phase = "B";
    state.song = song;
    const newBase =
      this.phaseBHeader.replace("{song}", song) +
      this.workflowText;

    if (wasPhaseB) {
      // 切歌 = 硬压缩: 上一首的 messages 全部丢弃,只把 summary 轨迹折成一条 user
      // 摘要塞进新会话,模型保留对前情的一句话感知;summaryTrail 清零(新歌从 t1 重计)。
      const prevTrail = state.summaryTrail.slice();
      state.summaryTrail = [];
      state.messages = [{ role: "system", content: newBase }];
      state.messageDbTurnIndexes = [null];
      state.baseSystem = newBase;
      state.turn = 0;
      if (prevTrail.length > 0 && prevSong) {
        state.messages.push({
          role: "user",
          content:
            `[上一首 "${prevSong}" 的摘要轨迹,仅供参考,本轮处理 "${song}"]\n` +
            prevTrail.join("\n"),
        });
        state.messageDbTurnIndexes.push(null);
      }
      // 持久化 live_from:取已有 db messages 的 max turn_index + 1。
      // 这样后续 send 写入的新 user(turn_index 必然更大)都进 LLM 上下文,
      // 而切歌前的所有 db messages(< live_from)都被剔除。
      const liveFrom = getMaxTurnIndex(state.chatId) + 1;
      updateSessionLiveFrom(state.chatId, liveFrom);
      this.emit({
        chatId: state.chatId,
        type: "compacted",
        data: { reason: "song_switch", from: prevSong, to: song, droppedTrail: prevTrail.length },
      });
    } else {
      // 首次 A → B: 保留早期对话,仅替换 system。平行同步 turnIndex 数组。
      const oldMsgs = state.messages;
      const oldIdxs = state.messageDbTurnIndexes;
      state.messages = [{ role: "system", content: newBase }];
      state.messageDbTurnIndexes = [null];
      for (let k = 0; k < oldMsgs.length; k++) {
        if (oldMsgs[k].role === "system") continue;
        state.messages.push(oldMsgs[k]);
        state.messageDbTurnIndexes.push(oldIdxs[k] ?? null);
      }
      state.baseSystem = newBase;
    }
    // 持久化 phase / song;baseSystem 不再写 system message,rebuild 时按 workflow.md
    // 最新版重建,保证 doc 更新后老 chat 也用新 prompt。
    updateSessionPhase(state.chatId, "B", song);
    this.emit({ chatId: state.chatId, type: "phase_change", data: { phase: "B", song } });
  }

  // 调 LLM 前的压缩入口:节流软压 + 撑爆兜底 trim。压缩纯逻辑在 ./compaction,
  // 这里只管 "何时压"(compressCd 节流)和压完后的副作用(写 live_from / emit compacted)。
  private compressCd = 0;
  private maybeCompact(state: ChatState): void {
    // 节流:每 COMPRESS_EVERY 次才真软压一次(GA 同款,中间 turn 跳过省开销)
    this.compressCd += 1;
    if (this.compressCd % COMPRESS_EVERY === 0) {
      compressHistoryTags(state.messages);
    }
    if (messagesCost(state.messages) <= CONTEXT_WIN_CHARS * 3) return;
    // 真撑爆 → 重置节流再 force 压一遍(keepRecent=4;trim 必破 prefix cache,GA:compress more btw)
    this.compressCd = 0;
    compressHistoryTags(state.messages, 4, COMPRESS_MAX_LEN);
    const { popped, newLiveFromTurnIdx } = trimOverflow(
      state.messages,
      state.messageDbTurnIndexes,
      CONTEXT_WIN_CHARS * 3 * 0.6,
    );
    if (popped > 0) {
      const costAfter = messagesCost(state.messages);
      console.log(`[agent] trim: popped ${popped} oldest messages, ${costAfter} chars left`);
      if (newLiveFromTurnIdx != null) updateSessionLiveFrom(state.chatId, newLiveFromTurnIdx);
      this.emit({
        chatId: state.chatId,
        type: "compacted",
        data: { reason: "trim_overflow", popped, costAfter },
      });
    }
  }

  // ============ 工具组装 ============

  private async listMcpTools(): Promise<ToolSchema[]> {
    const { tools } = await this.mcp.listTools();
    return tools.map((t) => ({
      type: "function",
      function: {
        name: t.name,
        description: (t.description ?? "").slice(0, 1024),
        parameters: (t.inputSchema as Record<string, unknown>) ?? {
          type: "object",
          properties: {},
        },
      },
    }));
  }

  private localToolSchemas(phase: "A" | "B"): ToolSchema[] {
    const startQc: ToolSchema = {
      type: "function",
      function: {
        name: "start_qc",
        description:
          "Enter QC workflow for a specific song. Switches the system prompt to the full workflow manual and locks subsequent tool calls to that song.",
        parameters: {
          type: "object",
          properties: { song: { type: "string" } },
          required: ["song"],
          additionalProperties: false,
        },
      },
    };
    const humanCheck: ToolSchema = {
      type: "function",
      function: {
        name: "human_check",
        description:
          "Ask the human one or more multiple-choice questions and block until they answer. Use ANY time you need a human decision (not just inside QC workflow): each decision in `decisions` becomes one paginated card; UI shows option buttons + a free-text input. Returns `{ok: true, answers: [{choice, note}, ...]}` aligned with decisions order; on user cancel returns `{ok: false, code: 'USER_CANCELLED', answered: N}`. `choice`='' means user only typed a custom note; `note` may be empty when user clicked an option without comment.",
        parameters: {
          type: "object",
          properties: {
            reason: { type: "string", description: "上下文/总问题,卡片顶部展示一次" },
            decisions: {
              type: "array",
              minItems: 1,
              items: {
                type: "object",
                properties: {
                  question: { type: "string", description: "本题问什么" },
                  options: {
                    type: "array",
                    items: { type: "string" },
                    description: "候选选项;用户可点其中一个,也可自填",
                  },
                },
                required: ["question", "options"],
                additionalProperties: false,
              },
            },
            state: { type: "string", description: "可选 QC 态 id,卡片右上角小徽章展示" },
          },
          required: ["reason", "decisions"],
          additionalProperties: false,
        },
      },
    };
    const uiOpenFile: ToolSchema = {
      type: "function",
      function: {
        name: "ui_open_file",
        description:
          "Open a file in the main window editor. Routes by extension: .wav/.mp3/.flac → AudioViewer, .mid/.midi → MidiViewer (auto-loads same-name wav as comparison track), .csv → CsvViewer, .txt/.md/.json/.toml/.yaml → Monaco. Use before human_check when user needs to see/listen to a file (e.g. 2.5 MIDI vs WAV alignment, 2.6/2.7 playback toggles). Returns {ok, code?, message?}. On failure: {ok:false, code: 'FILE_NOT_FOUND' | 'NO_MAIN_WINDOW' | 'INVALID_ARG'}.",
        parameters: {
          type: "object",
          properties: {
            path: { type: "string", description: "工作区相对或绝对路径" },
          },
          required: ["path"],
          additionalProperties: false,
        },
      },
    };
    const mixLoadSong: ToolSchema = {
      type: "function",
      function: {
        name: "mix_load_song",
        description:
          "Open the MixConsole window and load all wavs of a song under the given mode. `mode='stems_plus_master'` → 分轨wav + 总轨wav (用于 2.3);`mode='proj_files_plus_master'` → 混音工程原文件 + 总轨wav (用于 2.4)。Replaces any previously loaded tracks. Returns {ok, loaded?: paths[], code?, message?}. On failure: {ok:false, code: 'STEMS_DIR_MISSING' | 'MASTER_DIR_MISSING' | 'NO_WAVS' | 'INVALID_ARG'}.",
        parameters: {
          type: "object",
          properties: {
            song_path: { type: "string", description: "歌曲文件夹的工作区相对或绝对路径" },
            mode: {
              type: "string",
              enum: ["stems_plus_master", "proj_files_plus_master"],
              description: "stems_plus_master = 分轨wav + 总轨wav;proj_files_plus_master = 混音工程原文件 + 总轨wav",
            },
          },
          required: ["song_path", "mode"],
          additionalProperties: false,
        },
      },
    };
    const playbackToggleBeat: ToolSchema = {
      type: "function",
      function: {
        name: "playback_toggle_beat_render",
        description:
          "Overlay strong/weak beat lines + metronome click on the CURRENTLY OPEN wav in AudioViewer (data source: 同歌 csv/Beat.csv)。前置:Beat.csv 已过 1.6 syntax 校验;当前主窗口正在显示某 wav (先 ui_open_file 总轨某 wav)。退出 2.6 前必须再调一次 on=false 关掉。Returns {ok, code?, message?}.",
        parameters: {
          type: "object",
          properties: {
            on: { type: "boolean", description: "true = 开启叠层;false = 关掉" },
          },
          required: ["on"],
          additionalProperties: false,
        },
      },
    };
    const playbackToggleStructure: ToolSchema = {
      type: "function",
      function: {
        name: "playback_toggle_structure_render",
        description:
          "Overlay green dashed段落分隔线 + 段落标签 on the CURRENTLY OPEN wav in AudioViewer (data source: 同歌 csv/Structure.csv)。前置同 playback_toggle_beat_render。退出 2.7 前必须 on=false 关掉。Returns {ok, code?, message?}.",
        parameters: {
          type: "object",
          properties: {
            on: { type: "boolean", description: "true = 开启叠层;false = 关掉" },
          },
          required: ["on"],
          additionalProperties: false,
        },
      },
    };
    if (phase === "A") return [startQc];
    return [startQc, humanCheck, uiOpenFile, mixLoadSong, playbackToggleBeat, playbackToggleStructure];
  }

  private async buildTools(state: ChatState): Promise<ToolSchema[]> {
    if (state.phase === "A") {
      const mcp = await this.listMcpTools();
      const allowed = new Set(["sheet_list_my_pending"]);
      return [
        ...this.localToolSchemas("A"),
        ...mcp.filter((t) => allowed.has(t.function.name)),
      ];
    }
    const mcp = await this.listMcpTools();
    return [...this.localToolSchemas("B"), ...mcp];
  }

  // ============ 工具执行 ============

  private async executeTool(
    state: ChatState,
    name: string,
    args: Record<string, unknown>,
  ): Promise<unknown> {
    if (name === "start_qc") {
      const input = String(args.song ?? "").trim();
      if (!input) return { ok: false, code: "INVALID_ARG", message: "song is empty" };
      const resolved = await this.resolveSongFolder(input);
      if (resolved.error) {
        return {
          ok: false,
          code: resolved.code ?? "RESOLVE_FAILED",
          message: resolved.error,
          candidates: resolved.candidates,
          hint: "用 candidates 里的精确 folder name(= {歌手}_{歌曲}_{扒曲人})再调一次 start_qc。如果是 sheet 上同名多歌,先 sheet_list_my_pending 看 row_index 再用 sheet_get_song_meta(song_name, row_index) 拿到 owner 帮你认歌。",
        };
      }
      const songFolder = resolved.folder!;
      this.enterPhaseB(state, songFolder);
      const hint = await this.buildWorkspaceHint(songFolder);
      // hint 嵌进 tool result(不能 push 独立 user 消息,会破坏 assistant.tool_calls
      // → tool result 的紧邻约束,触发 OpenAI 400)
      return {
        ok: true,
        message: `已进入质检流程,song=${songFolder}`,
        song_folder: songFolder,
        workspace_hint: hint || null,
      };
    }
    if (name === "human_check") {
      // 阻塞:emit awaiting_human → 挂起 Promise → 等 resolveHumanCheck 被 IPC 唤醒
      const decisions = Array.isArray(args.decisions) ? args.decisions : [];
      if (decisions.length === 0) {
        return {
          ok: false,
          code: "INVALID_ARG",
          message: "human_check 需要至少 1 个 decision (decisions: [{question, options}, ...])",
        };
      }
      this.emit({
        chatId: state.chatId,
        type: "awaiting_human",
        data: args,
      });
      this.pendingHumanCheckMeta.set(state.chatId, { decisionCount: decisions.length });
      return await new Promise<Record<string, unknown>>((resolve) => {
        this.pendingHumanCheck.set(state.chatId, resolve);
      });
    }
    if (name === "ui_open_file") {
      const p = String(args.path ?? "").trim();
      if (!p) return { ok: false, code: "INVALID_ARG", message: "path is empty" };
      const resolved = this.resolveWorkspacePath(p);
      return await this.uiTools.openFile(resolved);
    }
    if (name === "mix_load_song") {
      const sp = String(args.song_path ?? "").trim();
      const mode = String(args.mode ?? "").trim();
      if (!sp) return { ok: false, code: "INVALID_ARG", message: "song_path is empty" };
      if (mode !== "stems_plus_master" && mode !== "proj_files_plus_master") {
        return {
          ok: false,
          code: "INVALID_ARG",
          message: `mode 必须是 'stems_plus_master' 或 'proj_files_plus_master',得到 '${mode}'`,
        };
      }
      const resolved = this.resolveWorkspacePath(sp);
      return await this.uiTools.loadSongMix(resolved, mode);
    }
    if (name === "playback_toggle_beat_render") {
      return await this.uiTools.togglePlayback("beat", !!args.on);
    }
    if (name === "playback_toggle_structure_render") {
      return await this.uiTools.togglePlayback("structure", !!args.on);
    }
    // MCP tool
    try {
      const r = await this.mcp.callTool({ name, arguments: args });
      if (r.isError) {
        return { ok: false, code: "TOOL_ERROR", content: r.content };
      }
      // MCP content 是 TextContent[] 等;取第一段 text payload,JSON 就解析,否则原样回 {text}
      const text = mcpResultText(r.content);
      try {
        return JSON.parse(text);
      } catch {
        return { text };
      }
    } catch (e) {
      return { ok: false, code: "MCP_EXCEPTION", message: String(e) };
    }
  }

  // ============ LLM 调用 ============

  private async callLlm(
    messages: Message[],
    tools: ToolSchema[],
  ): Promise<{
    message: Message;
    usage: Record<string, number>;
    finishReason: string;
    cachedTokens: number;
  }> {
    const resp = await fetch(`${this.sidecarBase}/agent/completion`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages, tools, tool_choice: "auto" }),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`LLM HTTP ${resp.status}: ${text.slice(0, 500)}`);
    }
    const data = (await resp.json()) as {
      message: Message;
      usage?: Record<string, number>;
      finish_reason?: string;
      cached_tokens?: number;
    };
    return {
      message: data.message,
      usage: data.usage ?? {},
      finishReason: data.finish_reason ?? "",
      cachedTokens: data.cached_tokens ?? 0,
    };
  }

  // ============ 主循环 ============
  // summary 抽取 / anchor 构造 / content 清洗都在 ./compaction(纯函数)。

  async send(chatId: string, userText: string): Promise<void> {
    const state = this.getOrCreate(chatId);
    if (state.running) {
      this.emit({
        chatId,
        type: "error",
        data: { message: "另一轮还在跑,请先 cancel 或等完成" },
      });
      return;
    }
    state.cancelled = false;
    state.running = true;
    // 先写 db 拿 turn_index,再 push 内存,保持 messages 跟 messageDbTurnIndexes 平行
    const msgRow = appendMessage({ session_id: chatId, role: "user" });
    appendPart({ message_id: msgRow.id, type: "text", content: { text: userText } as PartContentText });
    state.messages.push({ role: "user", content: userText });
    state.messageDbTurnIndexes.push(msgRow.turn_index);
    try {
      await this.runLoop(state);
    } catch (e) {
      this.emit({ chatId, type: "error", data: { message: String(e) } });
    } finally {
      state.running = false;
      this.emit({ chatId, type: "turn_done", data: { turn: state.turn } });
    }
  }

  async startQc(chatId: string, song: string): Promise<void> {
    const state = this.getOrCreate(chatId);
    const resolved = await this.resolveSongFolder(song);
    if (resolved.error || !resolved.folder) {
      // 解析失败:不进 Phase B,把候选清单当 assistant_text 反馈给用户
      this.emit({
        chatId,
        type: "error",
        data: {
          message: resolved.error,
          code: resolved.code,
          candidates: resolved.candidates,
        },
      });
      return;
    }
    const songFolder = resolved.folder;
    this.enterPhaseB(state, songFolder);
    const hint = await this.buildWorkspaceHint(songFolder);
    const opener = `开始质检 "${songFolder}"。请从 state_tree_read 和 1.1 开始,按工作手册推进。`;
    if (!state.running) {
      await this.send(chatId, hint ? `${opener}\n\n${hint}` : opener);
    }
  }

  /** Phase B 启动时,基于当前 workspaceRoot 算一份候选清单注入到 user 消息。 */
  private async buildWorkspaceHint(song: string): Promise<string> {
    if (!this.workspaceRoot) return "";
    try {
      const dirs = await this.listWorkspaceDirs();
      if (dirs.length === 0) return "";
      const songToken = song.trim();
      const candidates = songToken ? dirs.filter((n) => n.includes(songToken)) : [];
      const lines = [
        `工作区: ${this.workspaceRoot}`,
        `一级目录(${dirs.length}): ${dirs.slice(0, 50).join(", ")}${dirs.length > 50 ? " ..." : ""}`,
      ];
      if (candidates.length > 0) {
        lines.push(`命名含 "${songToken}" 的候选(${candidates.length}): ${candidates.join(", ")}`);
        lines.push(`(后续 audit/read/fs_list_dir/fix_execute_plan 的 path 可以直接传相对路径,例如 "${candidates[0]}" 或 "${candidates[0]}/分轨wav",sidecar 会基于 workspace 自动解析)`);
      } else {
        lines.push(`未发现命名含 "${songToken}" 的文件夹,可能扒曲方还没交付,或者命名漂移严重 —— 请按 1.1 注意区处理。`);
      }
      return lines.join("\n");
    } catch (e) {
      console.warn("[agent] buildWorkspaceHint failed:", e);
      return "";
    }
  }

  /** 把 input 解析成工作区里唯一存在的 song_folder。
   *  - 精确命中目录名 → folder
   *  - 多个目录名包含 input → 报 AMBIGUOUS_SONG_FOLDER + candidates 让模型/用户消歧
   *  - 0 个匹配 → NO_MATCH + 工作区一级目录全清单
   */
  private async resolveSongFolder(
    input: string,
  ): Promise<{ folder?: string; candidates?: string[]; error?: string; code?: string }> {
    if (!this.workspaceRoot) {
      return { error: "workspace not set; 让用户先在 UI 选工作区", code: "WORKSPACE_NOT_SET" };
    }
    let dirs: string[];
    try {
      dirs = await this.listWorkspaceDirs();
    } catch (e) {
      return { error: `fs_list_dir failed: ${e}`, code: "LIST_FAILED" };
    }
    if (dirs.includes(input)) return { folder: input };
    const cands = dirs.filter((n) => n.includes(input));
    if (cands.length === 1) return { folder: cands[0] };
    if (cands.length === 0) {
      return {
        candidates: dirs,
        error: `工作区里找不到包含 "${input}" 字样的文件夹`,
        code: "NO_MATCH",
      };
    }
    return {
      candidates: cands,
      error: `工作区里有 ${cands.length} 个文件夹同含 "${input}",需要消歧`,
      code: "AMBIGUOUS_SONG_FOLDER",
    };
  }

  /** 调 MCP fs_list_dir 取工作区一级目录名清单(只 dir,不含 file)。 */
  private async listWorkspaceDirs(): Promise<string[]> {
    if (!this.workspaceRoot) return [];
    const r = await this.mcp.callTool({
      name: "fs_list_dir",
      arguments: { path: this.workspaceRoot, max_depth: 1 },
    });
    const data = JSON.parse(mcpResultText(r.content)) as { dirs?: Array<{ name: string }> };
    return (data.dirs ?? []).map((d) => d.name).filter(Boolean);
  }

  cancel(chatId: string): void {
    const state = this.chats.get(chatId);
    if (state) state.cancelled = true;
    // 取消时也要唤醒挂起的 human_check,否则 await 永远挂住
    const pending = this.pendingHumanCheck.get(chatId);
    if (pending) {
      this.pendingHumanCheck.delete(chatId);
      this.pendingHumanCheckMeta.delete(chatId);
      pending({ ok: false, code: "USER_CANCELLED", answered: 0, message: "agent run cancelled by user" });
    }
  }

  /** Session 被删除时调:cancel 任何 in-flight run,清掉内存里的 ChatState。
   *  db 那边由调用方负责删(deleteSession),本方法不碰 db。 */
  dropChat(chatId: string): void {
    this.cancel(chatId); // 顺手 cancel + 唤醒 pending human_check
    this.chats.delete(chatId);
  }

  /** UI 收到用户答完所有 / cancel 后,通过 IPC 调到这里 resolve agent 端的挂起 promise。
   *  payload.answers 与 decisions 一一对齐(长度相等);cancelled=true 时 answers 是已答的前缀。 */
  resolveHumanCheck(
    chatId: string,
    payload: { answers: { choice: string; note?: string }[]; cancelled?: boolean },
  ): void {
    const pending = this.pendingHumanCheck.get(chatId);
    if (!pending) {
      console.warn(`[agent] resolveHumanCheck(${chatId}) but no pending check`);
      return;
    }
    this.pendingHumanCheck.delete(chatId);
    const meta = this.pendingHumanCheckMeta.get(chatId);
    this.pendingHumanCheckMeta.delete(chatId);
    const answers = (payload.answers || []).map((a) => ({
      choice: a.choice || "",
      note: a.note ?? "",
    }));
    if (payload.cancelled) {
      pending({
        ok: false,
        code: "USER_CANCELLED",
        answered: answers.length,
        decision_count: meta?.decisionCount ?? answers.length,
        answers,
        message: "用户中途 cancel;answers 是已答的前缀,可据此向用户复述并决定下一步",
      });
      return;
    }
    pending({
      ok: true,
      answers,
      message: "已收到用户作答,按 answers 推进;choice 为空表示用户只填了 note",
    });
  }

  /** 兜底:保证 OpenAI tool-calling 协议合规:
   *  - assistant.tool_calls 后必须紧跟所有对应 tool_call_id 的 tool 消息(缺则补占位)
   *  - tool 消息必须紧跟 assistant.tool_calls(否则 drop —— 孤儿 tool)
   *  同步处理 messageDbTurnIndexes,补占位的 tool 用 null(没写 db)。
   */
  private sanitizeOrphanToolCalls(state: ChatState): void {
    const out: Message[] = [];
    const outIdxs: (number | null)[] = [];
    let dropped = 0;
    let i = 0;
    while (i < state.messages.length) {
      const m = state.messages[i];
      const idx = state.messageDbTurnIndexes[i] ?? null;
      if (m.role === "tool") {
        // 走到顶层 tool 消息 = 孤儿(前面应该是被 assistant 块连续消耗的,不可能出现在这)
        dropped++;
        i++;
        continue;
      }
      out.push(m);
      outIdxs.push(idx);
      i++;
      if (m.role !== "assistant" || !m.tool_calls || m.tool_calls.length === 0) continue;
      // 收集紧跟其后的 tool 消息(必须连续),匹配 tool_call_id
      const need = new Set(m.tool_calls.map((c) => c.id));
      while (i < state.messages.length && state.messages[i].role === "tool") {
        const tm = state.messages[i];
        const tIdx = state.messageDbTurnIndexes[i] ?? null;
        const id = tm.tool_call_id ?? "";
        if (need.has(id)) {
          need.delete(id);
          out.push(tm);
          outIdxs.push(tIdx);
        } else {
          dropped++; // 重复或非匹配 → drop
        }
        i++;
      }
      // 缺的补占位(不写 db,turnIndex = null)
      for (const id of need) {
        const call = m.tool_calls.find((c) => c.id === id);
        out.push({
          role: "tool",
          tool_call_id: id,
          name: call?.function.name,
          content: JSON.stringify({ ok: false, code: "MISSING_RESULT", message: "tool result lost(中断/异常),自动补占位" }),
        });
        outIdxs.push(null);
      }
    }
    if (out.length !== state.messages.length) {
      console.log(`[agent] sanitized messages: ${state.messages.length} → ${out.length} (dropped ${dropped} orphan tool msgs)`);
      state.messages = out;
      state.messageDbTurnIndexes = outIdxs;
    }
  }

  private async runLoop(state: ChatState, maxTurns = 40): Promise<void> {
    for (let i = 0; i < maxTurns; i++) {
      if (state.cancelled) return;
      this.maybeCompact(state); // 调 LLM 前压一次,降低本次请求 tokens
      this.sanitizeOrphanToolCalls(state); // 防 OpenAI 400: insufficient tool messages
      state.turn += 1;
      const tools = await this.buildTools(state);
      // GA <history> 短期工作记忆:临时往 messages 末尾追加一条 user,调完撤掉,
      // 不污染 state.messages、不写 db。
      const anchor = buildAnchorMessage(state.summaryTrail, state.missedSummary);
      state.missedSummary = false; // 一次性:DANGER 提示已注入(若有),清掉
      if (anchor) {
        state.messages.push(anchor);
        state.messageDbTurnIndexes.push(null);
      }
      logLlmPrompt(state.chatId, state.turn, state.song, state.messages, tools);
      let llmOut;
      try {
        llmOut = await this.callLlm(state.messages, tools);
      } finally {
        if (anchor) {
          const idx = state.messages.lastIndexOf(anchor);
          if (idx >= 0) {
            state.messages.splice(idx, 1);
            state.messageDbTurnIndexes.splice(idx, 1);
          }
        }
      }
      const { message: assistant, usage, finishReason, cachedTokens } = llmOut;
      const rawContent = (assistant.content ?? "") as string;
      const content = cleanContent(rawContent);
      logTurn({
        chatId: state.chatId,
        turn: state.turn,
        finishReason,
        rawContent,
        cleanedContent: content,
        toolCalls: assistant.tool_calls ?? [],
        usage,
        cachedTokens,
        msgsCount: state.messages.length,
      });
      const promptTokens =
        (usage.prompt_tokens as number | undefined) ??
        (usage.input_tokens as number | undefined) ?? 0;
      const completionTokens =
        (usage.completion_tokens as number | undefined) ??
        (usage.output_tokens as number | undefined) ?? 0;
      console.log(
        `[agent] t${state.turn} finish=${finishReason} prompt=${promptTokens} (cache_hit=${cachedTokens}) completion=${completionTokens} msgs=${state.messages.length}`,
      );
      // GA 风格会话日志:Response 块(配对前面的 Prompt 块),记原始响应 + tool_calls + finish/usage
      logLlmResponse(state.chatId, state.turn, state.song, {
        content: rawContent,
        toolCalls: assistant.tool_calls ?? [],
        finishReason,
        promptTokens,
        completionTokens,
        cachedTokens,
      });
      // 先 appendMessage 拿 turn_index,再同步 push 到内存,保 messages 跟 turnIndexes 平行
      const assistantMsg = appendMessage({
        session_id: state.chatId,
        role: "assistant",
        finish_reason: finishReason ?? undefined,
        input_tokens: promptTokens || undefined,
        output_tokens: completionTokens || undefined,
        cache_read_tokens: cachedTokens || undefined,
      });
      state.messages.push(assistant);
      state.messageDbTurnIndexes.push(assistantMsg.turn_index);

      // 把清洗后的 content 写回 history,避免老轮次 content 里的残留 token 继续污染后续上下文
      if (content !== rawContent) assistant.content = content;
      if (content) {
        this.emit({ chatId: state.chatId, type: "assistant_text", data: { text: content } });
      }

      // summary 抽 + GA fallback(ga.py:518-527):LLM 漏写 → 从 tool_calls 派生 + 给下轮 [DANGER]
      const extracted = extractSummary(content);
      let summaryForTrail: string | null = extracted;
      if (!summaryForTrail) {
        const tcs = assistant.tool_calls ?? [];
        if (tcs.length > 0) {
          const names = tcs.map((c) => c.function.name).join(", ");
          summaryForTrail = `调工具 ${names}`;
        } else if (content) {
          summaryForTrail = "直接文字回复";
        }
        if (summaryForTrail) state.missedSummary = true;
      }
      if (summaryForTrail) {
        if (summaryForTrail.length > 100) summaryForTrail = summaryForTrail.slice(0, 100) + "...";
        state.summaryTrail.push(`[t${state.turn}] ${summaryForTrail}`);
      }

      // 持久化 assistant turn 的 parts(text / summary / tool_call)
      if (content) {
        const textWithoutSummary = extracted
          ? content.replace(/<summary>[\s\S]*?<\/summary>/g, "").trim()
          : content;
        if (textWithoutSummary) {
          appendPart({
            message_id: assistantMsg.id,
            type: "text",
            content: { text: textWithoutSummary } as PartContentText,
          });
        }
      }
      if (summaryForTrail) {
        appendPart({
          message_id: assistantMsg.id,
          type: "summary",
          content: { summary: summaryForTrail } as PartContentSummary,
        });
      }
      for (const tc of assistant.tool_calls ?? []) {
        let parsed: Record<string, unknown> = {};
        try { parsed = tc.function.arguments ? JSON.parse(tc.function.arguments) : {}; }
        catch { parsed = { _raw: tc.function.arguments }; }
        appendPart({
          message_id: assistantMsg.id,
          type: "tool_call",
          content: {
            tool_use_id: tc.id,
            tool_name: tc.function.name,
            input: parsed,
          } as PartContentToolCall,
        });
      }

      const toolCalls = assistant.tool_calls ?? [];
      if (toolCalls.length === 0) {
        // 无 tool_calls 三种处理(参 GA ga.py:439-450):
        //   1) finish_reason == "length" → max_tokens 截断,自动续写
        //   2) content 全空 → 空响应,nudge 重新生成(GA: "[System] Blank response, regenerate")
        //   3) 自然结束(finish == "stop") → 让出控制权等用户
        if (finishReason === "length") {
          console.log(`[agent] t${state.turn} truncated by max_tokens, auto-continuing`);
          // nudge user 是合成消息(不写 db),turnIndex 标 null
          state.messages.push({
            role: "user",
            content: "上一条回复被 max_tokens 截断了,请直接续写,不要重复已说的内容。",
          });
          state.messageDbTurnIndexes.push(null);
          continue;
        }
        if (!content.trim()) {
          console.log(`[agent] t${state.turn} blank response, retrying once`);
          state.messages.push({
            role: "user",
            content: "上一条回复是空的。请直接说出你下一步要干什么,或调用工具往前推。",
          });
          state.messageDbTurnIndexes.push(null);
          continue;
        }
        return;
      }

      for (let i = 0; i < toolCalls.length; i++) {
        const call = toolCalls[i];
        if (state.cancelled) {
          // 取消时把剩下未执行的 tool_calls 全部补占位结果,保证 history 合规
          // (OpenAI 要求 assistant.tool_calls 后必须紧跟所有对应 tool 消息)。
          for (let j = i; j < toolCalls.length; j++) {
            const cancelResult = { ok: false, code: "CANCELLED", message: "user cancelled" };
            const cmsg = appendMessage({
              session_id: state.chatId,
              role: "tool",
              tool_call_id: toolCalls[j].id,
              tool_name: toolCalls[j].function.name,
            });
            state.messages.push({
              role: "tool",
              tool_call_id: toolCalls[j].id,
              name: toolCalls[j].function.name,
              content: JSON.stringify(cancelResult),
            });
            state.messageDbTurnIndexes.push(cmsg.turn_index);
            appendPart({
              message_id: cmsg.id,
              type: "tool_result",
              content: { tool_use_id: toolCalls[j].id, output: cancelResult, is_error: true } as PartContentToolResult,
            });
          }
          return;
        }
        const name = call.function.name;
        let args: Record<string, unknown> = {};
        try {
          args = call.function.arguments ? JSON.parse(call.function.arguments) : {};
        } catch (e) {
          args = { _parse_error: String(e), _raw: call.function.arguments };
        }
        this.emit({ chatId: state.chatId, type: "tool_use", data: { name, args } });
        const result = await this.executeTool(state, name, args);
        this.emit({
          chatId: state.chatId,
          type: "tool_result",
          data: { name, result },
        });
        // 先 appendMessage 拿 turn_index,再同步 push 到内存
        const tmsg = appendMessage({
          session_id: state.chatId,
          role: "tool",
          tool_call_id: call.id,
          tool_name: name,
        });
        state.messages.push({
          role: "tool",
          tool_call_id: call.id,
          name,
          content: JSON.stringify(result),
        });
        state.messageDbTurnIndexes.push(tmsg.turn_index);
        appendPart({
          message_id: tmsg.id,
          type: "tool_result",
          content: { tool_use_id: call.id, output: result } as PartContentToolResult,
        });
        // human_check 是阻塞点: 让模型继续看到 result 后,本轮跑完它会再吐一段文字然后停
      }
    }
    this.emit({
      chatId: state.chatId,
      type: "error",
      data: { message: `reached max_turns=${maxTurns}` },
    });
  }
}
