/** IPC calls can arrive while the window is open but MCP is still starting. */
export async function waitForAgent<T>(startup: Promise<void>, current: () => T | null): Promise<T> {
  await startup;
  const runner = current();
  if (!runner) throw new Error("智能体启动失败，请重启软件；若仍然失败，请将日志反馈给开发者。");
  return runner;
}
