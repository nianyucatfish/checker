import { expect, it } from "vitest";
import { waitForAgent } from "./agentReadiness";

it("waits for delayed startup before hydrating the advanced tools setting", async () => {
  let finish!: () => void;
  const startup = new Promise<void>(resolve => { finish = resolve; });
  let runner: { advanced: boolean } | null = null;
  let hydrated = false;
  const hydrate = waitForAgent(startup, () => runner).then(value => { hydrated = true; return value.advanced; });
  await Promise.resolve();
  expect(hydrated).toBe(false);
  runner = { advanced: true };
  finish();
  expect(await hydrate).toBe(true);
});

it("serves simultaneous window initialization and toggle requests after startup", async () => {
  let finish!: () => void;
  const startup = new Promise<void>(resolve => { finish = resolve; });
  let runner: object | null = null;
  const calls = [waitForAgent(startup, () => runner), waitForAgent(startup, () => runner)];
  runner = {};
  finish();
  expect(await Promise.all(calls)).toEqual([runner, runner]);
});

it("reports an initialization failure if startup ended without an agent", async () => {
  await expect(waitForAgent(Promise.resolve(), () => null)).rejects.toThrow("智能体启动失败");
});
