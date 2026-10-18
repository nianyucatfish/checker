import { describe, expect, it, vi } from "vitest";
import * as path from "node:path";
import * as fs from "node:fs";
import * as os from "node:os";
import { readAdvancedToolsPreference, writeAdvancedToolsPreference } from "./agentPreferences";
vi.mock("./db", () => ({
  createSessionWithId: vi.fn(), getSession: vi.fn(), hydrateSession: vi.fn(),
  appendMessage: vi.fn(() => ({ id: "m", turn_index: 0 })), appendPart: vi.fn(),
  updateSessionSong: vi.fn(), updateSessionAdvancedTools: vi.fn(),
  updateSessionLiveFrom: vi.fn(),
}));
import { AgentRunner } from "./agent";
import { updateSessionAdvancedTools } from "./db";

describe("advanced tool integration", () => {
  it("hides by default, persists enablement and rejects stale calls after disabling", async () => {
    const mcp = {
      listTools: vi.fn(async () => ({ tools: ["code_run", "web_scan", "web_execute_js", "file_read", "file_write", "file_patch", "system_set_workspace", "audit_list_errors"].map(name => ({ name, inputSchema: { type: "object" } })) })),
      callTool: vi.fn(async () => ({ content: [{ type: "text", text: '{"ok":true}' }] })),
    };
    const runner = new AgentRunner(mcp as any, () => null, path.resolve("../doc/prompts"), "", {} as any);
    const internals = runner as any;
    const state = internals.getOrCreate("test");
    const names = async () => (await internals.buildTools(state)).map((t: any) => t.function.name);
    expect(await names()).not.toContain("code_run");
    runner.setAdvancedTools("test", true);
    expect(updateSessionAdvancedTools).toHaveBeenCalledWith("test", true);
    expect(await names()).toEqual(expect.arrayContaining(["code_run", "web_scan", "web_execute_js", "file_read", "file_write", "file_patch", "human_check"]));
    expect(await names()).not.toContain("system_set_workspace");
    await internals.executeTool(state, "code_run", { script: "print(1)" });
    expect(mcp.callTool).toHaveBeenCalledTimes(1);
    runner.setAdvancedTools("test", false);
    expect(await internals.executeTool(state, "code_run", {})).toMatchObject({ code: "TOOL_DISABLED" });
    expect(await internals.executeTool(state, "file_write", {})).toMatchObject({ code: "TOOL_DISABLED" });
    expect(mcp.callTool).toHaveBeenCalledTimes(1);
    expect(await names()).toContain("audit_list_errors");
    expect(await names()).not.toContain("code_run");
  });
});

describe("shared advanced tools setting", () => {
  it("overrides old per-chat values and survives runner restart", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "checker-runner-"));
    try {
      const file = path.join(dir, "preferences.json");
      const make = () => new AgentRunner({} as any, () => null, path.resolve("../doc/prompts"), "", {} as any, undefined, {
        advancedTools: readAdvancedToolsPreference(file),
        persistAdvancedTools: enabled => writeAdvancedToolsPreference(file, enabled),
      });
      const runner = make(); const internals = runner as any;
      const old = internals.getOrCreate("old");
      runner.setAdvancedTools("first", true);
      expect(old.advancedTools).toBe(true);
      expect(internals.getOrCreate("new").advancedTools).toBe(true);
      expect(runner.hydrate("new").advancedTools).toBe(true);
      const restarted = make();
      expect(restarted.hydrate("old").advancedTools).toBe(true);
      restarted.setAdvancedTools("old", false);
      expect(make().hydrate("new").advancedTools).toBe(false);
    } finally { fs.rmSync(dir, { recursive: true }); }
  });
  it("loads personal knowledge only when an extension is explicitly supplied", () => {
    const publicRunner = new AgentRunner({} as any, () => null, path.resolve("../doc/prompts"), "", {} as any);
    expect((publicRunner as any).systemPrompt).not.toContain("baidu_download_archive.md");
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "checker-extension-"));
    fs.writeFileSync(path.join(dir, "INDEX.md"), "Personal SOP: {{knowledge_root}}/baidu_download_archive.md");
    const personalRunner = new AgentRunner({} as any, () => null, path.resolve("../doc/prompts"), "", {} as any, undefined, {
      localKnowledgeDir: dir,
    });
    expect((personalRunner as any).systemPrompt).toContain(`${dir}/baidu_download_archive.md`);
    fs.rmSync(dir, { recursive: true });
  });
});

describe("published prompt updates", () => {
  it("updates existing chats while retaining local task injection and knowledge", () => {
    const runner = new AgentRunner({} as any, () => null, path.resolve("../doc/prompts"), "", {} as any);
    const internals = runner as any;
    const state = internals.getOrCreate("published");
    runner.setTaskPrompt("private local task");
    runner.applyPublishedPrompts({ "agent_system.md": "new introduction", "agent_workflow.md": "new workflow", "advanced_tools.md": "new tools" });
    expect(state.baseSystem).toContain("new introduction\n\nnew workflow");
    expect(state.baseSystem).toContain("# SOP 索引");
    expect(internals.taskPrompt).toBe("private local task");
    expect(internals.advancedPrompt).toBe("new tools");
    expect(() => runner.applyPublishedPrompts({ "agent_system.md": "broken" })).toThrow();
    expect(state.baseSystem).toContain("new workflow");
  });
});
