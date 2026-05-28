# Audio QC

桌面端音频质检工具:从腾讯文档分工表拉"我负责验收"的歌 → 在工作区里挨个查文件命名 / 时长 / 节奏 / 结构 / 混音 → 写回"已验收"标记。当前 v2 架构 = Electron + React 前端 + Python FastAPI sidecar,Phase 4 接入 Anthropic agent。

## 目录

```
checker/
├── app/          Electron + React (Vite + Tailwind + Monaco)
├── sidecar/      Python FastAPI 服务,端口 8775
├── doc/          设计文档 / 验收清单
├── scripts/      一次性探针
└── config.example.toml
```

## 跑起来(dev)

前置:`venv` 装好 sidecar 依赖 (`pip install -r sidecar/requirements.txt`),`config.toml` 从 `config.example.toml` 复制改。

```bash
cd app
npm install     # 首次
npm run dev     # Vite + Electron 一起起,Electron main 自动 spawn sidecar
```

详见 `app/README.md` 和 `CLAUDE.md`。

## 老版本(PyQt v1)

v1 单体 PyQt 版本现保留在 git history (tag/branch 视情况而定),工作目录已全面切到 v2。
