/** Authenticated loopback API. The bridge delegates to the desktop's existing tools. */
import * as http from "node:http";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import { randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import Ajv from "ajv";

export type ExternalTool = { name: string; description?: string; parameters: Record<string, unknown> };
export interface ExternalBridge {
  status(): unknown;
  workflow(): unknown;
  tools(): Promise<ExternalTool[]>;
  open(chatId: string, workspace?: string): Promise<void>;
  run(chatId: string, name: string, args: Record<string, unknown>): Promise<unknown>;
  cancel(chatId: string): void;
  pendingHuman(chatId: string): boolean;
  internalBusy(): boolean;
}
type Session = { id: string; chatId: string; expires: number; workspace?: string };
type Job = { id: string; session_id: string; name: string; status: string; result?: unknown; error?: string; cancel_requested?: boolean; created_at: number };
class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }
const ajv = new Ajv({ strict: false, validateFormats: false });

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, v]) => [key, canonical(v)]));
  return value;
}

export async function startExternalAgent(bridge: ExternalBridge, discoveryPath: string) {
  const token = randomBytes(32).toString("hex");
  const instanceId = randomUUID();
  let session: Session | undefined;
  let opening = false;
  let busy: Job | undefined;
  let closed = false;
  const jobs = new Map<string, Job>();
  const requests = new Map<string, { fingerprint: string; job: Job }>();
  const active = () => {
    if (session && session.expires < Date.now() && !busy) session = undefined;
    return !!session || opening;
  };
  function requireSession(id: unknown): Session {
    active();
    if (!session || id !== session.id) throw new ApiError(409, "外部会话已过期或不存在，请重新打开会话");
    session.expires = Date.now() + 30 * 60_000;
    return session;
  }
  async function readBody(req: http.IncomingMessage) {
    const buffers: Buffer[] = []; let bytes = 0;
    for await (const b of req) {
      bytes += b.length;
      if (bytes > 1024 * 1024) throw new ApiError(413, "请求过大");
      buffers.push(b);
    }
    try {
      const body = JSON.parse(Buffer.concat(buffers).toString() || "{}");
      if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error();
      return body;
    } catch { throw new ApiError(400, "请求必须为 JSON object"); }
  }
  const viewJob = (job: Job) => ({ ...job, waiting_for_human: job.status === "running" && !!session && bridge.pendingHuman(session.chatId) });
  const server = http.createServer(async (req, res) => {
    const send = (code: number, data: unknown) => {
      if (!res.destroyed) res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" }).end(JSON.stringify(data));
    };
    try {
      // No browser-origin access or DNS rebinding; possession of the local discovery file is required.
      if (req.headers.origin || !/^127\.0\.0\.1:\d+$/.test(req.headers.host || "")) throw new ApiError(403, "仅允许本机 CLI 连接");
      const supplied = Buffer.from(req.headers.authorization || "");
      const expected = Buffer.from(`Bearer ${token}`);
      if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) throw new ApiError(401, "本机连接凭证无效");
      const url = new URL(req.url || "/", "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/v1/status") return send(200, { protocol: 1, instance_id: instanceId, external_session: active() ? { id: session?.id, busy: !!busy } : null, desktop: bridge.status() });
      if (req.method === "GET" && url.pathname === "/v1/workflow") return send(200, bridge.workflow());
      if (req.method === "GET" && url.pathname === "/v1/tools") {
        const tools = await bridge.tools(); const name = url.searchParams.get("name");
        if (name && !tools.some(t => t.name === name)) throw new ApiError(404, "未知工具");
        return send(200, { tools: name ? tools.filter(t => t.name === name) : tools });
      }
      if (req.method === "POST" && url.pathname === "/v1/sessions") {
        if (active() || busy || bridge.internalBusy()) throw new ApiError(409, "已有 Agent 正在使用工作区，请结束当前会话后重试");
        const body = await readBody(req);
        if (body.workspace !== undefined && typeof body.workspace !== "string") throw new ApiError(400, "workspace 必须是路径文本");
        if (active() || busy || bridge.internalBusy()) throw new ApiError(409, "工作区正在使用中");
        opening = true;
        const next = { id: randomUUID(), chatId: `external_${randomUUID()}`, expires: Date.now() + 30 * 60_000, workspace: body.workspace };
        try { await bridge.open(next.chatId, body.workspace); session = next; }
        finally { opening = false; }
        return send(201, { session_id: next.id, chat_id: next.chatId, idle_timeout_seconds: 1800 });
      }
      if (req.method === "POST" && url.pathname === "/v1/calls") {
        const body = await readBody(req); const current = requireSession(body.session_id);
        if (typeof body.name !== "string" || !body.args || typeof body.args !== "object" || Array.isArray(body.args)) throw new ApiError(400, "需要 name 和 args object");
        if (typeof body.request_id !== "string" || !/^[\w.-]{1,100}$/.test(body.request_id)) throw new ApiError(400, "需要唯一 request_id，重试时复用相同值");
        const key = `${current.id}:${body.request_id}`;
        const fingerprint = JSON.stringify(canonical([body.name, body.args]));
        const prior = requests.get(key);
        if (prior) {
          if (prior.fingerprint !== fingerprint) throw new ApiError(409, "request_id 已用于不同参数");
          return send(200, viewJob(prior.job));
        }
        if (busy) throw new ApiError(409, "上一个工具尚未完成，请等待结果");
        const tool = (await bridge.tools()).find(t => t.name === body.name);
        if (!tool) throw new ApiError(404, "工具不存在或高级工具未开启");
        const validate = ajv.compile(tool.parameters);
        if (!validate(body.args)) throw new ApiError(400, `参数不符合工具 schema: ${ajv.errorsText(validate.errors)}`);
        // Recheck after async discovery so concurrent POSTs cannot both start a write.
        if (busy || session !== current) throw new ApiError(409, "工作区或会话状态已变化");
        if (jobs.size >= 1000) throw new ApiError(429, "本次应用运行的调用记录已满，请重启软件后重试");
        const job: Job = { id: randomUUID(), session_id: current.id, name: body.name, status: "running", created_at: Date.now() };
        jobs.set(job.id, job); requests.set(key, { fingerprint, job }); busy = job;
        void Promise.resolve().then(() => bridge.run(current.chatId, body.name, body.args)).then(
          result => { job.result = result; job.status = "completed"; },
          error => { job.error = error instanceof Error ? error.message : "工具执行失败"; job.status = "failed"; },
        ).finally(() => { busy = undefined; current.expires = Date.now() + 30 * 60_000; });
        return send(202, viewJob(job));
      }
      const jobMatch = /^\/v1\/jobs\/([\w-]+)(\/cancel)?$/.exec(url.pathname);
      if (jobMatch) {
        const job = jobs.get(jobMatch[1]);
        if (!job) throw new ApiError(404, "调用记录不存在");
        if (req.method === "POST" && jobMatch[2]) {
          const body = await readBody(req); const current = requireSession(body.session_id);
          if (job.session_id !== current.id) throw new ApiError(403, "调用不属于当前会话");
          job.cancel_requested = true;
          // Human waiting can be safely cancelled. Other operations finish before unlocking;
          // cancelling a network await must not pretend an already-started write was rolled back.
          if (job === busy && job.name === "human_check") bridge.cancel(current.chatId);
          return send(200, viewJob(job));
        }
        if (req.method === "GET" && !jobMatch[2]) {
          const current = requireSession(url.searchParams.get("session_id"));
          if (job.session_id !== current.id) throw new ApiError(403, "调用不属于当前会话");
          return send(200, viewJob(job));
        }
      }
      if (req.method === "POST" && url.pathname === "/v1/sessions/close") {
        const body = await readBody(req); requireSession(body.session_id);
        if (busy) throw new ApiError(409, "工具仍在执行，请先等待完成或取消人工确认");
        session = undefined; return send(200, { ok: true });
      }
      throw new ApiError(404, "接口不存在");
    } catch (e) { send(e instanceof ApiError ? e.status : 500, { ok: false, message: e instanceof Error ? e.message : "接口错误" }); }
  });
  server.requestTimeout = 15_000;
  server.headersTimeout = 10_000;
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const port = (server.address() as netAddress).port;
  const discovery = { protocol: 1, instance_id: instanceId, url: `http://127.0.0.1:${port}`, token, pid: process.pid };
  await fs.mkdir(path.dirname(discoveryPath), { recursive: true });
  const temp = `${discoveryPath}.${instanceId}.tmp`;
  await fs.writeFile(temp, JSON.stringify(discovery), { mode: 0o600 });
  await fs.rename(temp, discoveryPath);
  return {
    active,
    async close() {
      if (closed) return; closed = true;
      if (session) bridge.cancel(session.chatId);
      server.closeAllConnections(); server.close();
      try {
        const saved = JSON.parse(await fs.readFile(discoveryPath, "utf8"));
        if (saved.instance_id === instanceId) await fs.unlink(discoveryPath);
      } catch { /* Another instance may now own discovery. */ }
    },
    discovery,
  };
}
type netAddress = { port: number };
