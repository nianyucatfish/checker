import { beforeEach, describe, expect, it, vi } from "vitest";
import * as path from "node:path";
import * as fs from "node:fs";
import * as os from "node:os";

vi.mock("./db", () => ({
  createSessionWithId: vi.fn(), getSession: vi.fn(() => null), hydrateSession: vi.fn(),
  appendMessage: vi.fn(() => ({ id: "m", turn_index: 0 })), appendPart: vi.fn(),
  updateSessionSong: vi.fn(), updateSessionAdvancedTools: vi.fn(), updateSessionLiveFrom: vi.fn(),
}));
import { AgentRunner } from "./agent";
import { createSessionWithId, appendMessage, updateSessionLiveFrom, updateSessionSong } from "./db";

function setup() {
  const mcp = {
    listTools: vi.fn(async () => ({ tools: ["audit_list_errors", "state_tree_read", "fix_execute_plan"].map(
      name => ({ name, inputSchema: { type: "object" } }),
    ) })),
    callTool: vi.fn(async () => ({ content: [{ type: "text", text: '{"errors":[]}' }] })),
  };
  const runner = new AgentRunner(mcp as any, () => null, path.resolve("../doc/prompts"), "", {} as any);
  runner.setWorkspace("/workspace");
  const internal = runner as any;
  internal.resolveSongFolder = vi.fn(async (song: string) => ({ folder: song }));
  internal.buildWorkspaceHint = vi.fn(async () => "工作区: /workspace");
  return { runner, internal, mcp };
}

beforeEach(() => vi.clearAllMocks());

describe("multi-song conversations", () => {
  it("uses current task edits on subsequent requests in an existing chat", async () => {
    const { runner, internal } = setup();
    const snapshots: any[] = [];
    internal.callLlm = vi.fn(async (messages: any[]) => {
      snapshots.push(structuredClone(messages));
      return { message: { role: "assistant", content: "完成" }, usage: {}, finishReason: "stop", cachedTokens: 0 };
    });
    runner.setTaskPrompt("每批 5 首");
    await runner.send("task", "继续");
    expect(snapshots[0][0].content.indexOf("当前任务：\n每批 5 首"))
      .toBeLessThan(snapshots[0][0].content.indexOf("下面是完整工作手册"));
    runner.setTaskPrompt("");
    await runner.send("task", "继续");
    expect(snapshots[1][0].content).not.toContain("当前任务：");
  });
  it("interrupts a pending LLM immediately and ignores its late response", async () => {
    const { runner, internal } = setup();
    let finish!: (value: any) => void;
    internal.callLlm = vi.fn(() => new Promise(resolve => { finish = resolve; }));
    internal.emit = vi.fn();
    const run = runner.send("cancel-llm", "test");
    await vi.waitFor(() => expect(internal.callLlm).toHaveBeenCalled());
    const signal = internal.callLlm.mock.calls[0][2] as AbortSignal;
    runner.cancel("cancel-llm");
    await run;
    expect(signal.aborted).toBe(true);
    expect(internal.getOrCreate("cancel-llm").running).toBe(false);
    finish({ message: { role: "assistant", content: "late" }, usage: {} });
    await Promise.resolve();
    expect(internal.emit.mock.calls.some(([e]: any[]) => e.type === "assistant_text" || e.type === "error")).toBe(false);
    expect(internal.emit.mock.calls.some(([e]: any[]) => e.type === "turn_done")).toBe(true);
  });

  it.each(["audit_list_errors", "ui_open_file"])("interrupts pending %s and pairs remaining tool calls", async (name) => {
    const { runner, internal, mcp } = setup();
    const pending = new Promise(() => {});
    mcp.callTool.mockImplementation(() => pending as any);
    internal.uiTools.openFile = vi.fn(() => pending);
    internal.callLlm = vi.fn(async () => ({
      message: { role: "assistant", content: "", tool_calls: [1, 2].map(id => ({
        id: String(id), type: "function", function: { name, arguments: '{"path":"a"}' },
      })) }, usage: {}, finishReason: "tool_calls", cachedTokens: 0,
    }));
    const run = runner.send("cancel-tool", "test");
    const tool = name === "ui_open_file" ? internal.uiTools.openFile : mcp.callTool;
    await vi.waitFor(() => expect(tool).toHaveBeenCalledTimes(1));
    const signal = name === "audit_list_errors" ? (mcp.callTool.mock.calls[0] as any)[2].signal : null;
    runner.cancel("cancel-tool");
    await run;
    if (signal) expect(signal.aborted).toBe(true);
    const state = internal.getOrCreate("cancel-tool");
    expect(state.running).toBe(false);
    expect(state.messages.filter((m: any) => m.role === "tool").map((m: any) => [m.tool_call_id, JSON.parse(m.content).code]))
      .toEqual([["1", "CANCELLED"], ["2", "CANCELLED"]]);
    expect(tool).toHaveBeenCalledTimes(1);
    expect(internal.callLlm).toHaveBeenCalledTimes(1);
  });

  it("preserves history, tool-call pairing, summaries and turn count across song changes in one round", async () => {
    const { runner, internal, mcp } = setup();
    const state = internal.getOrCreate("batch");
    const base = state.baseSystem;
    const call = (id: string, name: string, args: object) => ({
      id, type: "function", function: { name, arguments: JSON.stringify(args) },
    });
    const snapshots: any[] = [];
    internal.callLlm = vi.fn(async (messages: any[]) => {
      snapshots.push(structuredClone(messages));
      return {
        message: snapshots.length === 1
          ? { role: "assistant", content: "<summary>检查 A 和 B</summary>", tool_calls: [
            call("a", "start_qc", { song: "歌手_A_人" }),
            call("b", "start_qc", { song: "歌手_B_人" }),
            call("audit-a", "audit_list_errors", { song_path: "歌手_A_人" }),
            call("audit-b", "audit_list_errors", { song_path: "歌手_B_人" }),
          ] }
          : { role: "assistant", content: "已检查两首。<summary>A 和 B 检查完成</summary>" },
        usage: {}, finishReason: "stop", cachedTokens: 0,
      };
    });

    await runner.send("batch", "一起检查 A 和 B");

    expect(state.baseSystem).toBe(base);
    expect(state.song).toBe("歌手_B_人");
    expect(state.turn).toBe(2);
    expect(state.summaryTrail).toHaveLength(2);
    expect(updateSessionLiveFrom).not.toHaveBeenCalled();
    expect(updateSessionSong).toHaveBeenCalledWith("batch", "歌手_A_人");
    expect(updateSessionSong).toHaveBeenCalledWith("batch", "歌手_B_人");
    const history = snapshots[1];
    expect(history.some((m: any) => m.role === "user" && m.content === "一起检查 A 和 B")).toBe(true);
    const assistant = history.findIndex((m: any) => m.tool_calls?.length);
    expect(history.slice(assistant + 1, assistant + 5).map((m: any) => m.tool_call_id))
      .toEqual(["a", "b", "audit-a", "audit-b"]);
    expect(mcp.callTool.mock.calls.map((args: any) => args[0].arguments.song_path))
      .toEqual(["歌手_A_人", "歌手_B_人"]);
  });

  it("loads the same complete SOP in new and restored conversations regardless of legacy phase/song", async () => {
    const { internal } = setup();
    const fresh = internal.getOrCreate("fresh");
    const restored = internal.rebuildState("old", {
      session: { phase: "B", song: "歌手_B_人", live_from_turn_index: 0, advanced_tools: 0 },
      messages: [{ role: "user", turn_index: 1, parts: [
        { type: "text", content: { text: "之前检查了 A" } },
      ] }],
    });
    expect(restored.baseSystem).toBe(fresh.baseSystem);
    expect(fresh.baseSystem).toContain("### 2.5 MIDI vs WAV 对齐");
    expect(fresh.baseSystem).toContain("# SOP 索引");
    expect(fresh.baseSystem).not.toContain("baidu_download_archive.md");
    expect(fresh.baseSystem).not.toContain("/skills/baidu-netdisk/SKILL.md");
    expect(fresh.baseSystem).not.toContain("/sops/baidu_download.md");
    expect(fresh.baseSystem).not.toContain("/lib/macljqCtrl.py");
    expect(fresh.baseSystem).not.toContain("{{knowledge_root}}");
    expect(fresh.baseSystem).not.toContain("song 已锁定");
    expect(restored.messages).toContainEqual({ role: "user", content: "之前检查了 A" });
    const names = (await internal.buildTools(fresh)).map((t: any) => t.function.name);
    expect(names).toEqual(expect.arrayContaining(["audit_list_errors", "state_tree_read", "human_check", "fix_execute_plan"]));
  });

  it("resolves index paths against the explicit runtime knowledge directory", () => {
    const knowledgeDir = fs.mkdtempSync(path.join(os.tmpdir(), "checker-knowledge-"));
    try {
      fs.writeFileSync(path.join(knowledgeDir, "INDEX.md"), "SOP: {{knowledge_root}}/sops/example.md");
      const runner = new AgentRunner({} as any, () => null, path.resolve("../doc/prompts"), "", {} as any, knowledgeDir);
      const state = (runner as any).getOrCreate("runtime");
      expect(state.baseSystem).toContain(`${knowledgeDir}/sops/example.md`);
      expect(state.baseSystem).not.toContain("{{knowledge_root}}");
    } finally {
      fs.rmSync(knowledgeDir, { recursive: true });
    }
  });
});


describe("external agent tool reuse", () => {
  it("shares the active QC flow without imposing built-in conversation or confirmation policies", () => {
    const { runner, internal } = setup();
    const before = runner.externalWorkflow();
    expect(before.system_prompt).toContain("人工确认在当前对话中进行");
    expect(before.system_prompt).not.toContain("human_check");
    expect(before.system_prompt).not.toContain("不要求每轮");
    expect(before.system_prompt).not.toContain("每轮回复最后一行写 <summary>");
    expect(internal.systemPrompt).toContain("每轮回复最后一行写 <summary>");
    expect(before.system_prompt).toContain(before.files["agent_workflow.md"]);
    runner.applyPublishedPrompts({ "agent_system.md": "服务器系统提示", "agent_workflow.md": "服务器当前流程", "advanced_tools.md": "高级工具说明" });
    const after = runner.externalWorkflow();
    expect(after.system_prompt).toContain(internal.systemPrompt);
    expect(after.system_prompt).toContain("服务器当前流程");
    expect(after.system_prompt).toContain(after.knowledge);
    expect(after.version).not.toBe(before.version);
    expect(after.source).toBe("published");
  });
  it("leaves human confirmation to the external host instead of opening desktop cards", async () => {
    const { runner, internal } = setup();
    internal.emit = vi.fn();
    expect((await runner.externalTools()).map(t => t.name)).not.toContain("human_check");
    expect(await runner.externalCall("external-human", "human_check", {})).toMatchObject({ ok: false, code: "HOST_CONFIRMATION_REQUIRED" });
    expect(internal.emit).not.toHaveBeenCalled();
  });
  it("executes existing tools without calling any model or creating sidebar conversation events", async () => {
    const { runner, internal, mcp } = setup();
    internal.callLlm = vi.fn(() => { throw new Error("must not call a model"); });
    internal.emit = vi.fn();
    const result = await runner.externalCall("external-test", "audit_list_errors", { song_path: "test" });
    expect(result).toEqual({ errors: [] });
    expect(mcp.callTool).toHaveBeenCalled();
    expect(internal.callLlm).not.toHaveBeenCalled();
    expect(internal.emit).not.toHaveBeenCalled();
    expect(createSessionWithId).not.toHaveBeenCalled();
    expect(appendMessage).not.toHaveBeenCalled();
    expect(runner.externalBusy()).toBe(false);
  });
  it("applies the same global advanced-tool switch to external discovery and execution", async () => {
    const { runner, mcp } = setup();
    mcp.listTools.mockResolvedValue({ tools: ["code_run", "system_set_workspace", "audit_list_errors"].map(name => ({ name, inputSchema: { type: "object" } })) });
    expect((await runner.externalTools()).map(t => t.name)).not.toContain("code_run");
    expect((await runner.externalTools()).map(t => t.name)).not.toContain("system_set_workspace");
    expect(await runner.externalCall("external-off", "code_run", {})).toMatchObject({ ok: false, code: "TOOL_DISABLED" });
    runner.setAdvancedTools("external-off", true);
    expect((await runner.externalTools()).map(t => t.name)).toContain("code_run");
  });
});
