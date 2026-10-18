export const ADVANCED_TOOLS = new Set([
  "code_run", "web_scan", "web_execute_js", "file_read", "file_write", "file_patch",
]);

export function toolAllowed(name: string, advanced: boolean): boolean {
  if (name.startsWith("system_")) return false;
  if (ADVANCED_TOOLS.has(name)) return advanced;
  return true;
}
