import { test } from "node:test";
import assert from "node:assert/strict";
import { detachDmg } from "./detach-dmg.mjs";

test("retries busy image before succeeding", async () => {
  let attempts = 0, waits = 0;
  await detachDmg("/mount", async () => { if (++attempts < 3) throw Error("busy"); }, async () => { waits++; });
  assert.equal(attempts, 3); assert.equal(waits, 2);
});
test("falls back to force and propagates permanent detach failures", async () => {
  const calls = [];
  await assert.rejects(detachDmg("/mount", async (cmd, args) => {
    calls.push(args); throw Error("still busy");
  }, async () => {}), /still busy/);
  assert.equal(calls.length, 7);
  assert.deepEqual(calls.at(-1), ["detach", "-force", "/mount"]);
});
