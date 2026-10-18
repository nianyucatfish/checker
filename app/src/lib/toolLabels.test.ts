import { describe, expect, it } from "vitest";
import { toolActionLabel } from "./toolLabels";

describe("tool action labels", () => {
  it("describes browser and code actions without exposing scripts", () => {
    expect(toolActionLabel("web_scan", { tabs_only: true })).toBe("扫描浏览器标签");
    expect(toolActionLabel("web_scan", {})).toBe("查看浏览器页面");
    expect(toolActionLabel("web_execute_js", { code: "secret" })).toBe("执行浏览器操作");
    expect(toolActionLabel("code_run", {})).toBe("运行代码");
  });
  it("uses filenames for both path separators and tolerates missing args", () => {
    expect(toolActionLabel("file_read", { path: "/a/progress.md" })).toBe("阅读 progress.md 文件");
    expect(toolActionLabel("file_patch", { path: "C:\\a\\test.txt" })).toBe("修改 test.txt 文件");
    expect(toolActionLabel("read_text_file", null)).toBe("阅读文件");
    expect(toolActionLabel("fs_list_dir", { path: "/a/data/" })).toBe("查看 data 目录");
  });
  it("distinguishes previews and toggles and falls back for unknown tools", () => {
    expect(toolActionLabel("fix_execute_plan", { simulate: true })).toBe("预览文件修复方案");
    expect(toolActionLabel("playback_toggle_beat_render", { on: false })).toBe("关闭节拍辅助");
    expect(toolActionLabel("new_tool", {})).toBe("调用工具：new_tool");
  });
});
