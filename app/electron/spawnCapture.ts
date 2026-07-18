import { spawn } from "node:child_process";

// 跑一次性子进程,收集 stdout,超时就 kill。进程出错 / 超时都返回已收到的 stdout(可能为空串),
// 不抛 —— 调用方按内容判断成败。仅用于短命令(如读剪贴板的 powershell),不适合长驻进程。
export function spawnCaptureWithTimeout(
  cmd: string,
  args: string[],
  timeoutMs: number,
): Promise<string> {
  return new Promise<string>((resolve) => {
    let stdout = "";
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      resolve(stdout);
    };
    let proc;
    try {
      proc = spawn(cmd, args, { windowsHide: true });
    } catch {
      return finish(); // spawn 同步抛(命令不存在等)→ 空串
    }
    proc.stdout?.on("data", (d: Buffer) => (stdout += d.toString("utf-8")));
    proc.on("error", finish);
    proc.on("close", finish);
    setTimeout(() => {
      if (done) return;
      try { proc.kill(); } catch { /* ignore */ }
      finish();
    }, timeoutMs);
  });
}
