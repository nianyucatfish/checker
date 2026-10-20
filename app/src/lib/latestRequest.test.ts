import { expect, it } from "vitest";
import { LatestRequest } from "./latestRequest";
it("ignores an older directory response even when it finishes last", async () => {
  const gate = new LatestRequest();
  let finishOld!: (value: string[]) => void;
  let displayed: string[] = [];
  const old = gate.start("directory");
  const pending = new Promise<string[]>(resolve => { finishOld = resolve; }).then(rows => { if (old()) displayed = rows; });
  const latest = gate.start("directory");
  if (latest()) displayed = ["current-file.wav"];
  finishOld([]); await pending;
  expect(displayed).toEqual(["current-file.wav"]);
});
it("invalidates all pending directory and scan results when switching workspace", () => {
  const gate = new LatestRequest(); const directory = gate.start("dir"); const scan = gate.start("scan");
  gate.reset(); const newDirectory = gate.start("dir");
  expect(directory()).toBe(false); expect(scan()).toBe(false); expect(newDirectory()).toBe(true);
});
it("allows independent directory requests to finish", () => {
  const gate = new LatestRequest(); const a = gate.start("a"); const b = gate.start("b");
  expect(a()).toBe(true); expect(b()).toBe(true);
});
