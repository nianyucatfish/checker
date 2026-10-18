import * as fs from "node:fs";
import * as path from "node:path";

export function readAdvancedToolsPreference(file: string): boolean {
  try { return JSON.parse(fs.readFileSync(file, "utf8")).advancedTools === true; }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") console.warn("Unable to read advanced tools preference", error);
    return false;
  }
}

export function writeAdvancedToolsPreference(file: string, enabled: boolean): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(`${file}.tmp`, JSON.stringify({ advancedTools: enabled }), "utf8");
  fs.renameSync(`${file}.tmp`, file);
}
