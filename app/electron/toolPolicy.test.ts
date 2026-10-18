import { describe, expect, it } from "vitest";
import { ADVANCED_TOOLS, toolAllowed } from "./toolPolicy";

describe("advanced tool gate", () => {
  it("gates advanced tools and revokes them on close", () => {
    for (const tool of ADVANCED_TOOLS) {
      expect(toolAllowed(tool, false)).toBe(false);
      expect(toolAllowed(tool, true)).toBe(true);
      expect(toolAllowed(tool, false)).toBe(false);
    }
  });
  it("keeps internal tools hidden and exposes all specialist tools without song selection", () => {
    expect(toolAllowed("system_set_workspace", true)).toBe(false);
    expect(toolAllowed("audit_list_errors", false)).toBe(true);
    expect(toolAllowed("fs_list_dir", false)).toBe(true);
    expect(toolAllowed("human_check", false)).toBe(true);
    expect(toolAllowed("fix_execute_plan", false)).toBe(true);
  });
});
