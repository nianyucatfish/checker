// 上下文压缩 —— GA tag-aware truncate + 撑爆兜底 trim 的纯逻辑(从 agent.ts 抽出,见 llmcore.py:26-89)。
// 这里的函数只对 messages / 字符串做确定性变换:不碰 db、不 emit、不持有节流状态。
// "每 N 次才真压" 的节流,以及 "压完后 updateSessionLiveFrom / emit compacted" 的副作用,
// 都留在 AgentRunner.maybeCompact —— 副作用收敛在边界,核心逻辑可单测。

import type { Message } from "./agent";

const COMPRESS_KEEP_RECENT = 10;
const COMPRESS_MAX_LEN = 800;
export const COMPRESS_EVERY = 5;
// 估算上下文窗口的"字符数"等价 — DeepSeek/MiniMax 兼容层多在 60K-200K token,
// 一字符 ≈ 1 token 是粗估;真撑爆是 3 倍 + 60% target 的两层保险,所以这里给小点偏安全
export const CONTEXT_WIN_CHARS = 60_000;
export { COMPRESS_KEEP_RECENT, COMPRESS_MAX_LEN };

/** messages 的粗略"字符成本"(≈ token):JSON 序列化后总长度。 */
export function messagesCost(messages: Message[]): number {
  return messages.reduce((s, m) => s + JSON.stringify(m).length, 0);
}

/** 抽 <summary>...</summary> 内文;没有则 null。 */
export function extractSummary(text: string | null | undefined): string | null {
  if (!text) return null;
  const m = text.match(/<summary>([\s\S]*?)<\/summary>/);
  return m ? m[1].trim() : null;
}

// MiniMax / 类 ChatML 兼容层有时会把 native function-call 标记泄漏到 content 字段
// (例如 `<|DSML|>function_calls<|...|>`)。我们的 tool_calls 已经走结构化字段拿到了,
// content 里这些残留只会让前端显示乱码,直接清掉。
/** 清掉 LLM content 里泄漏的 native function-call 标记(ASCII `|` 与全角 `｜` 都覆盖)。 */
export function cleanContent(text: string | null | undefined): string {
  if (!text) return "";
  return text
    // 完整闭合 <|...|> / <｜...｜>
    .replace(/<[|｜][\s\S]*?[|｜]>/g, "")
    // 残留未闭合 <|... 到行尾
    .replace(/<[|｜][^\n]*?(?=\n|$)/g, "")
    // 孤立的 |> / ｜> 收尾标记
    .replace(/[|｜]>/g, "")
    // 配套 XML wrapper
    .replace(/<\/?DSML>/g, "")
    .replace(/<\/?function_calls>/g, "")
    .replace(/<\/?function_call>/g, "")
    .trim();
}

// 软压缩(GA tag-aware truncate,llmcore.py:26-57 port 到 OpenAI Chat Completions 格式):
// - 不丢消息,保护尾部 keepRecent 条原样 → 保 OpenAI tool_call→tool 链合规
// - 老消息里:
//   1) string content 内的 <thinking>/<think>/<tool_use>/<tool_result> tag 内文 → head+tail 截
//   2) <history>/<key_info> tag → 整段折成 [...]
//   3) tool role 的 content (= stringified tool result) → 整体 head+tail 截
//   4) assistant.tool_calls[*].function.arguments → 解析 JSON 截每个 string value,解析不了就整体截
// 无条件执行 —— 节流(每 COMPRESS_EVERY 次才调一次)由 AgentRunner 掌握。
export function compressHistoryTags(
  messages: Message[],
  keepRecent = COMPRESS_KEEP_RECENT,
  maxLen = COMPRESS_MAX_LEN,
): void {
  const before = messagesCost(messages);
  const half = Math.floor(maxLen / 2);
  const truncStr = (s: string): string =>
    typeof s === "string" && s.length > maxLen
      ? s.slice(0, half) + "\n...[Truncated]...\n" + s.slice(-half)
      : s;
  const histPat = /<(history|key_info)>[\s\S]*?<\/\1>/g;
  const tagPats: RegExp[] = [
    /(<thinking>)([\s\S]*?)(<\/thinking>)/g,
    /(<think>)([\s\S]*?)(<\/think>)/g,
    /(<tool_use>)([\s\S]*?)(<\/tool_use>)/g,
    /(<tool_result>)([\s\S]*?)(<\/tool_result>)/g,
  ];
  const truncText = (text: string): string => {
    let t = text.replace(histPat, (_m, name) => `<${name}>[...]</${name}>`);
    for (const pat of tagPats) {
      t = t.replace(pat, (_m, open, inner, close) => open + truncStr(inner) + close);
    }
    return t;
  };

  const stopAt = messages.length - keepRecent;
  for (let i = 0; i < stopAt; i++) {
    const msg = messages[i];
    // user / system / assistant.content 里可能有 tag,走 truncText
    if (typeof msg.content === "string" && msg.content) {
      msg.content = truncText(msg.content);
    }
    // tool role: content 是 stringified JSON tool_result,整体截
    if (msg.role === "tool" && typeof msg.content === "string") {
      msg.content = truncStr(msg.content);
    }
    // assistant.tool_calls[*].function.arguments: JSON 字符串,逐 value 截
    if (msg.role === "assistant" && msg.tool_calls) {
      for (const tc of msg.tool_calls) {
        const argsStr = tc.function.arguments;
        if (typeof argsStr !== "string" || argsStr.length <= maxLen) continue;
        try {
          const parsed = JSON.parse(argsStr) as Record<string, unknown>;
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            for (const k of Object.keys(parsed)) {
              const v = parsed[k];
              if (typeof v === "string") parsed[k] = truncStr(v);
            }
            tc.function.arguments = JSON.stringify(parsed);
          } else {
            tc.function.arguments = truncStr(argsStr);
          }
        } catch {
          tc.function.arguments = truncStr(argsStr);
        }
      }
    }
  }
  const after = messagesCost(messages);
  if (after !== before) {
    console.log(`[agent] compress: ${before} → ${after} chars (-${before - after})`);
  }
}

// 撑爆兜底的弹出循环(GA trim_messages_history,llmcore.py:77-89):
// 从头部弹老消息(保 messages[0]=system)到下一个 user 边界,直到 cost <= targetChars 或剩 ≤5 条。
// 孤儿 tool / assistant.tool_calls 残留一并清掉(否则 OpenAI 拒)。messages 与 dbTurnIndexes
// 平行 splice。返回弹了几条 + 新的 live_from turn_index,由调用方写 db。
export function trimOverflow(
  messages: Message[],
  dbTurnIndexes: (number | null)[],
  targetChars: number,
): { popped: number; newLiveFromTurnIdx: number | null } {
  let cost = messagesCost(messages);
  let popped = 0;
  let newLiveFromTurnIdx: number | null = null;
  while (messages.length > 5 && cost > targetChars) {
    messages.splice(1, 1);
    const droppedIdx = dbTurnIndexes.splice(1, 1)[0];
    popped += 1;
    if (droppedIdx != null) newLiveFromTurnIdx = droppedIdx + 1;
    while (messages.length > 1 && messages[1].role !== "user") {
      messages.splice(1, 1);
      const d2Idx = dbTurnIndexes.splice(1, 1)[0];
      popped += 1;
      if (d2Idx != null) newLiveFromTurnIdx = d2Idx + 1;
    }
    cost = messagesCost(messages);
  }
  return { popped, newLiveFromTurnIdx };
}

// GA <history> 短期工作记忆(ga.py:504-514):拼一条临时 user 注入(最近 20 条 summary 锚点
// + 漏写 DANGER 提示)。compressHistoryTags 会把老消息里的 <history> block 折成 [...],只有当前轮完整。
/** 纯函数 —— 不清 missedSummary;调用方建完自行清(若为 true)。无内容则返回 null。 */
export function buildAnchorMessage(
  summaryTrail: string[],
  missedSummary: boolean,
): Message | null {
  const lines: string[] = [];
  if (summaryTrail.length > 0) {
    lines.push("[WORKING MEMORY]", "<history>", ...summaryTrail.slice(-20), "</history>");
  }
  if (missedSummary) {
    lines.push(
      "[DANGER] 上一轮遗漏了 <summary>。本轮必须在回复末尾按协议输出 <summary>≤40字</summary>,内容 = 上次工具结果新信息 + 本次意图。",
    );
  }
  if (lines.length === 0) return null;
  return { role: "user", content: lines.join("\n") };
}
