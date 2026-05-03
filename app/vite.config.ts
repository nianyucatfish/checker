import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import electron from "vite-plugin-electron";
import renderer from "vite-plugin-electron-renderer";

// Node 包不要打进 bundle:
// - chokidar 间接依赖 fsevents (Mac 原生模块,rollup 打包会失败)
// - electron 是 runtime,只能 require
const NODE_EXTERNALS = ["electron", "chokidar", "fsevents"];

export default defineConfig({
  plugins: [
    react(),
    electron([
      {
        entry: "electron/main.ts",
        onstart(args) {
          // 仅插件启 Electron，不让 concurrently 重复启动
          args.startup();
        },
        vite: {
          build: { outDir: "dist-electron", rollupOptions: { external: NODE_EXTERNALS } },
        },
      },
      {
        entry: "electron/preload.ts",
        onstart({ reload }) {
          reload();
        },
        vite: {
          build: { outDir: "dist-electron", rollupOptions: { external: NODE_EXTERNALS } },
        },
      },
    ]),
    renderer(),
  ],
  server: { port: 5173 },
});
