import { afterEach, expect, it, vi } from "vitest";
import { backendToolCatalog, callBackendTool } from "./externalBackend";
import Ajv from "ajv";
afterEach(() => vi.unstubAllGlobals());
it("exposes existing tool schemas, resolving body references, while excluding secrets and model routes", async () => {
  const schema = { components: { schemas: { Input: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } } }, paths: {
    "/tools/check": { get: { parameters: [{ name: "song", in: "query", required: true, schema: { type: "string" } }] } },
    "/tools/write": { post: { requestBody: { required: true, content: { "application/json": { schema: { $ref: "#/components/schemas/Input" } } } } } },
    "/config/tencent": { get: {} }, "/chat": { post: {} }, "/sheet/view": { get: {} },
  } };
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => schema })));
  const catalog = await backendToolCatalog("http://127.0.0.1:1");
  expect([...catalog.keys()]).toEqual(["api_check", "api_write"]);
  const validate = new Ajv({ strict: false }).compile(catalog.get("api_write")!.tool.parameters);
  expect(validate({ body: {} })).toBe(false);
  expect(validate({ body: { path: "song.csv" } })).toBe(true);
  const query = new Ajv({ strict: false }).compile(catalog.get("api_check")!.tool.parameters);
  expect(query({})).toBe(false);
  expect(query({ query: { song: "A" } })).toBe(true);
});
it("returns backend failures without presenting them as successful QC", async () => {
  const fetcher = vi.fn(async (_url: URL, _init?: unknown) => ({ ok: false, status: 400, json: async () => ({ detail: "bad path" }) }));
  vi.stubGlobal("fetch", fetcher);
  const result: any = await callBackendTool("http://127.0.0.1:1", { path: "/tools/check", method: "GET" }, { query: { path: "中文 A" } });
  expect(result.ok).toBe(false);
  expect(fetcher.mock.calls[0]![0].searchParams.get("path")).toBe("中文 A");
});
