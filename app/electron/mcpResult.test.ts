import { describe, it, expect } from "vitest";
import { mcpResultText } from "./mcpResult";

describe("mcpResultText", () => {
  it("取第一段 text payload", () => {
    expect(mcpResultText([{ type: "text", text: '{"ok":true}' }])).toBe('{"ok":true}');
  });
  it("多段时只取第一段", () => {
    expect(mcpResultText([{ text: "first" }, { text: "second" }])).toBe("first");
  });
  it("无 text payload → JSON.stringify 兜底", () => {
    expect(mcpResultText([{ type: "image", data: "x" }])).toBe('[{"type":"image","data":"x"}]');
    expect(mcpResultText([])).toBe("[]");
    expect(mcpResultText(null)).toBe("null");
  });
  it("配合 JSON.parse 还原结构", () => {
    const out = JSON.parse(mcpResultText([{ text: '{"dirs":[{"name":"a"}]}' }]));
    expect(out.dirs[0].name).toBe("a");
  });
});
