import * as fs from "node:fs";
import * as path from "node:path";

export function readTaskPrompt(file: string): string {
  try { return fs.readFileSync(file, "utf8"); }
  catch (e) { if ((e as NodeJS.ErrnoException).code === "ENOENT") return ""; throw e; }
}

export function writeTaskPrompt(file: string, text: unknown): string {
  if (typeof text !== "string" || text.length > 100_000) throw new Error("任务提示词必须为文本且不超过 100000 字符");
  const value = text.trim();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(`${file}.tmp`, value, "utf8");
  fs.renameSync(`${file}.tmp`, file);
  return value;
}

export function injectTaskPrompt(system: string, task: string): string {
  if (!task.trim()) return system;
  const split = system.indexOf("\n\n");
  const position = split < 0 ? system.length : split;
  return `${system.slice(0, position)}\n\n当前任务：\n${task.trim()}\n\n${system.slice(position).trimStart()}`.trimEnd();
}
