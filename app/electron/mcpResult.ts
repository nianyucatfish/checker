// MCP callTool 结果解包 —— 从 content(TextContent[] 等)里取第一段 text payload。
// agent.ts 的 executeTool 和 listWorkspaceDirs 都要这步,抽出来去重 + 可单测。

/** 取 MCP 结果 content 数组里第一段 text;没有 text payload 就 JSON.stringify 兜底。 */
export function mcpResultText(content: unknown): string {
  return Array.isArray(content) && content.length > 0 && "text" in content[0]
    ? (content[0] as { text: string }).text
    : JSON.stringify(content);
}
