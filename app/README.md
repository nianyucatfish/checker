# Audio QC — Electron 前端 (v2)

Phase 2 MVP：Electron + Vite + React + TypeScript，与 Python sidecar 联动。

## 启动

前置：项目根目录的 venv 已装好 sidecar 依赖（见 `../sidecar/requirements.txt`）。

```bash
cd app
npm install        # 首次
npm run dev        # 仅启动 Vite renderer（http://localhost:5173）
npm run electron:dev  # 同时跑 Vite + Electron 主窗口
```

启动后 Electron 主进程会：
1. 用项目根的 `venv/Scripts/python.exe` (Win) / `venv/bin/python` (Mac) spawn `python -m sidecar.serve --port 8765`
2. 等端口监听就绪
3. 加载 renderer

关闭窗口会自动 kill sidecar。

## 当前能做什么（MVP）

- 三栏布局（文件树 / 中央工作区 / Agent 侧栏占位）
- 选择工作区文件夹 → 列出歌曲
- 点"全量扫描"调 `/tools/check_workspace`，每首歌按错误数着色
- 点歌曲查看其结构化错误（带 code、message、路径）

## TODO（按计划推进）

- 文件夹内文件树（虚拟滚动）
- CSV 编辑器（AG Grid）+ 文本编辑器（Monaco）
- 波形 / MIDI / 混音台（Phase 3）
- Agent 聊天（Phase 4）
