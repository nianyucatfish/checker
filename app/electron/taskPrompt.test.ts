import { describe, expect, it } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { injectTaskPrompt, readTaskPrompt, writeTaskPrompt } from "./taskPrompt";

describe("current task prompt", () => {
  it("injects after introduction and omits the entire section when empty", () => {
    expect(injectTaskPrompt("介绍\n\n手册", "批量检查")).toBe("介绍\n\n当前任务：\n批量检查\n\n手册");
    expect(injectTaskPrompt("介绍\n\n手册", " \n ")).toBe("介绍\n\n手册");
  });
  it("persists edits, including clearing, and rejects invalid values", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "checker-task-"));
    try {
      const file = path.join(dir, "current-task.txt");
      expect(readTaskPrompt(file)).toBe("");
      expect(writeTaskPrompt(file, " 任务 ")).toBe("任务");
      expect(readTaskPrompt(file)).toBe("任务");
      writeTaskPrompt(file, "");
      expect(readTaskPrompt(file)).toBe("");
      expect(() => writeTaskPrompt(file, {})).toThrow();
      expect(() => writeTaskPrompt(file, "x".repeat(100001))).toThrow();
    } finally { fs.rmSync(dir, { recursive: true }); }
  });
});
