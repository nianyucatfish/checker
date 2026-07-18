import { defineConfig } from "vitest/config";

// 独立于 vite.config.ts —— 不带 electron 插件,纯 node 跑纯逻辑模块的单测。
// 只测不依赖 electron / better-sqlite3 运行时的纯模块(compaction / 工具函数 等)。
export default defineConfig({
  test: {
    include: ["electron/**/*.test.ts", "src/**/*.test.ts"],
    environment: "node",
  },
});
