import { describe, it, expect } from "vitest";
import {
  messagesCost,
  extractSummary,
  cleanContent,
  compressHistoryTags,
  trimOverflow,
  buildAnchorMessage,
} from "./compaction";
import type { Message } from "./agent";

describe("extractSummary", () => {
  it("抽出 <summary> 内文", () => {
    expect(extractSummary("文字 <summary> 本轮要点 </summary> 尾")).toBe("本轮要点");
  });
  it("无标签 / null → null", () => {
    expect(extractSummary("没有标签")).toBeNull();
    expect(extractSummary(null)).toBeNull();
  });
});

describe("cleanContent", () => {
  it("清掉 <|...|> 与全角 <｜...｜>", () => {
    expect(cleanContent("<|DSML|>foo<|end|>bar")).toBe("foobar");
    expect(cleanContent("文本<｜tool｜>结束")).toBe("文本结束");
  });
  it("清掉 function_calls wrapper", () => {
    expect(cleanContent("<function_calls>x</function_calls>hello")).toBe("xhello");
  });
  it("空输入 → 空串", () => {
    expect(cleanContent("")).toBe("");
  });
});

describe("buildAnchorMessage", () => {
  it("仅 trail → WORKING MEMORY / <history>", () => {
    expect(buildAnchorMessage(["[t1] a", "[t2] b"], false)).toEqual({
      role: "user",
      content: "[WORKING MEMORY]\n<history>\n[t1] a\n[t2] b\n</history>",
    });
  });
  it("仅 missedSummary → DANGER,无 history", () => {
    const m = buildAnchorMessage([], true)!;
    expect(m.content!.startsWith("[DANGER]")).toBe(true);
    expect(m.content).not.toContain("<history>");
  });
  it("两者皆空 → null", () => {
    expect(buildAnchorMessage([], false)).toBeNull();
  });
  it("trail 只取最近 20 条", () => {
    const trail = Array.from({ length: 30 }, (_, i) => `[t${i}] s`);
    const m = buildAnchorMessage(trail, false)!;
    expect(m.content).toContain("[t29] s");
    expect(m.content).not.toContain("[t9] s");
  });
});

describe("compressHistoryTags", () => {
  it("截老消息(tag 内文 / tool content / args),尾部 keepRecent 原样", () => {
    const long = "A".repeat(2000);
    const mk = (role: Message["role"], content: string | null, extra: Partial<Message> = {}): Message =>
      ({ role, content, ...extra });
    const messages: Message[] = [
      mk("system", "sys"),                                          // 0
      mk("user", `<tool_result>${long}</tool_result>`),            // 1 老,截
      mk("tool", long, { tool_call_id: "c1", name: "t" }),         // 2 老 tool,整体截
      mk("assistant", null, {                                       // 3 老,args 截
        tool_calls: [{ id: "c2", type: "function", function: { name: "f", arguments: JSON.stringify({ path: long }) } }],
      }),
      mk("user", "短消息"),                                         // 4 老但短,不变
    ];
    for (let i = 5; i < 14; i++) messages.push(mk("user", `recent${i}`)); // 5..13 尾部
    messages.push(mk("user", `<tool_result>${long}</tool_result>`));      // 14 尾部,长但原样

    compressHistoryTags(messages); // keepRecent=10, maxLen=800 默认

    expect(messages[1].content).toContain("...[Truncated]...");
    expect(messages[1].content!.length).toBeLessThan(2000);
    expect(messages[2].content).toContain("...[Truncated]...");
    expect(messages[3].tool_calls![0].function.arguments).toContain("...[Truncated]...");
    expect(messages[4].content).toBe("短消息");
    expect(messages[14].content).not.toContain("...[Truncated]..."); // 尾部不动
    expect(messages.length).toBe(15);
  });
});

describe("trimOverflow", () => {
  it("从头弹到 user 边界,保 system,dbIdx 平行 splice,返回 popped + newLiveFrom", () => {
    const huge = "B".repeat(200000);
    const messages: Message[] = [
      { role: "system", content: "sys" },                                  // 0 保留
      { role: "user", content: huge },                                     // 1 idx0 弹
      { role: "assistant", content: "a1" },                                // 2 idx1 弹
      { role: "tool", content: "t2", tool_call_id: "c", name: "f" },       // 3 idx2 弹
      { role: "user", content: "u3" },                                     // 4 idx3 ← 停
      { role: "assistant", content: "a4" },                                // 5 idx4
      { role: "tool", content: "t5", tool_call_id: "c", name: "f" },       // 6 idx5
      { role: "user", content: "u6" },                                     // 7 idx6
      { role: "assistant", content: "a7" },                                // 8 idx7
      { role: "tool", content: "t8", tool_call_id: "c", name: "f" },       // 9 idx8
      { role: "user", content: "u9" },                                     // 10 idx9
    ];
    const dbIdx: (number | null)[] = [null, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
    const { popped, newLiveFromTurnIdx } = trimOverflow(messages, dbIdx, 1000);

    expect(popped).toBe(3);
    expect(newLiveFromTurnIdx).toBe(3);
    expect(messages[0].role).toBe("system");
    expect(messages[1].role).toBe("user");
    expect(messages[1].content).toBe("u3");
    expect(dbIdx[1]).toBe(3);
    expect(messages.length).toBe(8);
  });
});

describe("messagesCost", () => {
  it("> 0", () => {
    expect(messagesCost([{ role: "user", content: "hi" }])).toBeGreaterThan(0);
  });
});
