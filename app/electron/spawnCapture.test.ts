import { describe, it, expect } from "vitest";
import { spawnCaptureWithTimeout } from "./spawnCapture";

const NODE = process.execPath; // 用 node 自己当被 spawn 的命令,跨平台稳定

describe("spawnCaptureWithTimeout", () => {
  it("收集子进程 stdout", async () => {
    const out = await spawnCaptureWithTimeout(NODE, ["-e", "process.stdout.write('hello')"], 5000);
    expect(out).toBe("hello");
  });

  it("超时则 kill 并返回已收到的内容", async () => {
    // 先打印 partial,再 sleep 远超 timeout → 应被 kill,拿到 partial
    const out = await spawnCaptureWithTimeout(
      NODE,
      ["-e", "process.stdout.write('partial'); setTimeout(()=>{}, 10000)"],
      300,
    );
    expect(out).toBe("partial");
  });

  it("命令不存在 → 空串,不抛", async () => {
    const out = await spawnCaptureWithTimeout("definitely_not_a_real_cmd_xyz", [], 1000);
    expect(out).toBe("");
  });
});
