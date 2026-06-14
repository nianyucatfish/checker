import { describe, it, expect } from "vitest";
import { setDumpLlmContext, isDumpLlmContextOn, logLlmPrompt, logLlmResponse } from "./agentDebug";

// 会话日志落盘(GA 风格 tmp/agent_contexts/<chat>.log)已由真实 app 验证;这里只测纯逻辑:
// 开关 set/get + 关闭时 prompt/response 记录都是 no-op(不写不抛)。
describe("dump 开关", () => {
  it("set/get 往返", () => {
    setDumpLlmContext(true);
    expect(isDumpLlmContextOn()).toBe(true);
    setDumpLlmContext(false);
    expect(isDumpLlmContextOn()).toBe(false);
  });
  it("关闭时 logLlmPrompt / logLlmResponse 不写不抛", () => {
    setDumpLlmContext(false);
    expect(() => logLlmPrompt("c", 1, null, [{ role: "user", content: "x" }], [])).not.toThrow();
    expect(() =>
      logLlmResponse("c", 1, null, {
        content: "hi",
        toolCalls: [],
        finishReason: "stop",
        promptTokens: 10,
        completionTokens: 5,
        cachedTokens: 0,
      }),
    ).not.toThrow();
  });
});
