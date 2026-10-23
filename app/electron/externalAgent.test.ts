import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import { spawn } from "node:child_process";
import { startExternalAgent, type ExternalBridge } from "./externalAgent";

let directory: string;
let api: Awaited<ReturnType<typeof startExternalAgent>>;
let bridge: ExternalBridge;
let finish: (x: unknown) => void;
let human = false;
const schema = { type: "object", properties: { value: { type: "string" } }, required: ["value"], additionalProperties: false };
beforeEach(async () => {
  directory = await mkdtemp(path.join(os.tmpdir(), "audio-qc-external-"));
  bridge = { status: () => ({ workspace: "/test", model_required: false }), workflow: () => ({ version: "same-as-desktop", system_prompt: "共享流程", files: {} }),
    tools: async () => ["check", "human_check"].map(name => ({ name, parameters: schema })), open: vi.fn(async () => {}),
    run: vi.fn(async () => new Promise(resolve => { finish = resolve; })),
    cancel: vi.fn(() => finish({ ok: false, code: "USER_CANCELLED" })), pendingHuman: () => human, internalBusy: () => false };
  api = await startExternalAgent(bridge, path.join(directory, "connection.json"));
});
afterEach(async () => { human = false; await api.close(); await rm(directory, { recursive: true, force: true }); });
function request(route: string, body?: unknown, headers = {}) {
  return fetch(api.discovery.url + route, { headers: { Authorization: `Bearer ${api.discovery.token}`, "Content-Type": "application/json", ...headers },
    ...(body === undefined ? {} : { method: "POST", body: JSON.stringify(body) }) });
}
async function session() { const r: any = await (await request("/v1/sessions", {})).json(); return r.session_id; }
async function done(id: string, sid: string) {
  let out: any;
  await vi.waitFor(async () => { out = await (await request(`/v1/jobs/${id}?session_id=${sid}`)).json(); expect(out.status).not.toBe("running"); });
  return out;
}
it("requires local authentication, refuses browser origins, writes private discovery and removes it on close", async () => {
  expect((await request("/v1/status", undefined, { Authorization: "Bearer bad" })).status).toBe(401);
  expect((await request("/v1/status", undefined, { Origin: "https://example.com" })).status).toBe(403);
  if (process.platform !== "win32") expect((await stat(path.join(directory, "connection.json"))).mode & 0o077).toBe(0);
  expect(JSON.parse(await readFile(path.join(directory, "connection.json"), "utf8")).instance_id).toBe(api.discovery.instance_id);
  await api.close();
  await expect(readFile(path.join(directory, "connection.json"))).rejects.toThrow();
});
it("serializes sessions and tools, validates arguments, deduplicates retries without repeating writes", async () => {
  const sid = await session();
  expect((await request("/v1/sessions", {})).status).toBe(409);
  const call = { session_id: sid, request_id: "first", name: "check", args: { value: "song" } };
  expect((await request("/v1/calls", { ...call, args: {} })).status).toBe(400);
  const job: any = await (await request("/v1/calls", call)).json();
  const retry: any = await (await request("/v1/calls", call)).json();
  expect(retry.id).toBe(job.id);
  expect((await request("/v1/calls", { ...call, args: { value: "different" } })).status).toBe(409);
  expect((await request("/v1/calls", { ...call, request_id: "second" })).status).toBe(409);
  expect((await request("/v1/sessions/close", { session_id: sid })).status).toBe(409);
  finish({ ok: true });
  expect((await done(job.id, sid)).result.ok).toBe(true);
  expect(bridge.run).toHaveBeenCalledTimes(1);
  expect((await request("/v1/sessions/close", { session_id: sid })).status).toBe(200);
  expect(api.active()).toBe(false);
});
it("cancels human waiting but keeps the lock for already-started writes until completion", async () => {
  const sid = await session(); human = true;
  let job: any = await (await request("/v1/calls", { session_id: sid, request_id: "human", name: "human_check", args: { value: "confirm" } })).json();
  expect(job.waiting_for_human).toBe(true);
  await request(`/v1/jobs/${job.id}/cancel`, { session_id: sid });
  expect((await done(job.id, sid)).result.code).toBe("USER_CANCELLED");
  job = await (await request("/v1/calls", { session_id: sid, request_id: "write", name: "check", args: { value: "write" } })).json();
  await request(`/v1/jobs/${job.id}/cancel`, { session_id: sid });
  expect(bridge.cancel).toHaveBeenCalledTimes(1);
  expect((await request("/v1/sessions/close", { session_id: sid })).status).toBe(409);
  finish({ executed: true });
  const completed = await done(job.id, sid);
  expect(completed.cancel_requested).toBe(true);
  expect(completed.result.executed).toBe(true);
});
it("does not let overlapping session opens both acquire the desktop", async () => {
  let release!: () => void;
  bridge.open = vi.fn(() => new Promise<void>(resolve => { release = resolve; }));
  const first = request("/v1/sessions", {});
  await vi.waitFor(() => expect(bridge.open).toHaveBeenCalledTimes(1));
  expect((await request("/v1/sessions", {})).status).toBe(409);
  release(); expect((await first).status).toBe(201);
});
it("runs the standalone CLI without third-party Python packages or a model", async () => {
  const python = process.env.TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const result = await new Promise<{ code: number | null; output: string }>((resolve, reject) => {
    const process = spawn(python, [path.resolve("../skills/audio-qc/scripts/qc.py"), "--connection-file", path.join(directory, "connection.json"), "status"]);
    let output = ""; process.stdout.on("data", b => output += b); process.stderr.on("data", b => output += b);
    process.on("error", reject); process.on("close", code => resolve({ code, output }));
  });
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).desktop.model_required).toBe(false);
});
