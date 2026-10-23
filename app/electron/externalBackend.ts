/** Expose only existing /tools routes; never expose model or spreadsheet credentials. */
import type { ExternalTool } from "./externalAgent";
export async function backendToolCatalog(base: string) {
  const response = await fetch(`${base}/openapi.json`, { signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error("无法读取后端工具 schema");
  const schema = await response.json() as any;
  const tools = new Map<string, { tool: ExternalTool; path: string; method: string }>();
  for (const [route, ops] of Object.entries(schema.paths) as any) {
    if (!route.startsWith("/tools/")) continue;
    for (const [method, op] of Object.entries(ops) as any) {
      if (!["get", "post"].includes(method)) continue;
      const query: any = { type: "object", properties: {}, required: [], additionalProperties: false };
      for (const p of op.parameters || []) {
        if (p.in !== "query") continue;
        query.properties[p.name] = p.schema;
        if (p.required) query.required.push(p.name);
      }
      const parameters: any = { type: "object", properties: { query }, additionalProperties: false,
        components: schema.components, required: query.required.length ? ["query"] : [] };
      if (op.requestBody) {
        parameters.properties.body = op.requestBody.content["application/json"].schema;
        if (op.requestBody.required) parameters.required.push("body");
      }
      const name = `api_${route.slice("/tools/".length)}`;
      tools.set(name, { path: route, method: method.toUpperCase(), tool: { name,
        description: op.description || op.summary || route, parameters } });
    }
  }
  return tools;
}
export async function callBackendTool(base: string, entry: { path: string; method: string }, args: Record<string, unknown>) {
  const url = new URL(base + entry.path);
  for (const [k, v] of Object.entries((args.query || {}) as Record<string, unknown>)) {
    if (Array.isArray(v)) for (const item of v) url.searchParams.append(k, String(item));
    else if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
  }
  // Writes finish before releasing the external execution lock, even when cancellation was requested.
  const response = await fetch(url, { method: entry.method,
    ...(entry.method === "POST" ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(args.body || {}) } : {}) });
  const result = await response.json();
  return response.ok ? result : { ok: false, code: "BACKEND_ERROR", status: response.status, result };
}
