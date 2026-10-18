import { describe, expect, it } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { readAdvancedToolsPreference, writeAdvancedToolsPreference } from "./agentPreferences";

describe("application advanced tools preference", () => {
  it("defaults off and restores both on and off across restarts", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "checker-preferences-"));
    try {
      const file = path.join(dir, "agent-preferences.json");
      expect(readAdvancedToolsPreference(file)).toBe(false);
      writeAdvancedToolsPreference(file, true);
      expect(readAdvancedToolsPreference(file)).toBe(true);
      writeAdvancedToolsPreference(file, false);
      expect(readAdvancedToolsPreference(file)).toBe(false);
    } finally { fs.rmSync(dir, { recursive: true }); }
  });
});
