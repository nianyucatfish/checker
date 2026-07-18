// 烟测脚本:从 Node 直接拉 sidecar.mcp_server,验证 stdio 通道 + 工具列表。
// 不依赖 Electron。跑法:
//   node app/scripts/smoke_mcp.mjs
// 期望输出:连上 + 列出 5 个工具 + 简单调一个工具看返回。

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..", "..");
const py = process.platform === "win32"
  ? path.join(projectRoot, "venv", "Scripts", "python.exe")
  : path.join(projectRoot, "venv", "bin", "python");

const transport = new StdioClientTransport({
  command: py,
  args: ["-X", "utf8", "-m", "sidecar.mcp_server"],
  cwd: projectRoot,
  env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  stderr: "inherit",
});

const client = new Client(
  { name: "smoke-test", version: "0.0.1" },
  { capabilities: {} }
);

console.log("[smoke] connecting...");
await client.connect(transport);
console.log("[smoke] connected");

const { tools } = await client.listTools();
console.log(`[smoke] ${tools.length} tools:`);
for (const t of tools) {
  console.log(`  - ${t.name}`);
}

console.log("[smoke] calling sheet_list_my_pending...");
const res = await client.callTool({ name: "sheet_list_my_pending", arguments: {} });
const structured = res.structuredContent ?? JSON.parse(res.content[0].text);
console.log(`[smoke] got ${structured.songs?.length ?? 0} pending songs`);
if (structured.songs?.[0]) {
  console.log(`[smoke] sample: ${JSON.stringify(structured.songs[0])}`);
}

await client.close();
console.log("[smoke] done");
