# Audio QC — Claude Code 项目说明

## 项目概览

桌面端音频质检工具,流程:从腾讯文档分工表拉"我负责验收的歌" → 在工作区里挨个查文件命名 / 时长 / 节奏 / 结构 / 混音 → 写回"已验收"标记。当前 v2 重构,从老的 PyQt 单体迁到 Electron + Python sidecar 架构,Phase 4 计划接入 Anthropic agent 自动化人工流程。

## 架构

```
仓库根/
├── app/          v2 前端 — Electron + React (Vite + Tailwind + Monaco)
├── sidecar/      v2 后端 — Python FastAPI 服务,端口 8765
├── doc/          设计文档 / 验收清单
├── scripts/      一次性探针(probe_tencent*.py)
├── cache/        运行时缓存(gitignore)
└── *.py          老 PyQt 单体(main.py / main_window.py / ...),逐步被 v2 替换,新功能不要往老代码加
```

**v2 数据流**:Electron renderer ↔ Electron main(IPC)↔ sidecar(HTTP /tools/*)↔ 文件系统 / 腾讯文档。

## 常用命令

```bash
# 前端 dev(Vite + Electron 一起起)
cd app && npm run dev

# sidecar dev(独立跑)
python -m sidecar.serve   # 默认 127.0.0.1:8765, /docs 看 OpenAPI

# sidecar 测试(目标:全绿,71 passed)
pytest sidecar/tests/

# 前端类型检查
cd app && npm run typecheck

# 打包(Electron + sidecar 一起)
cd app && npm run build
```

## 关键文件

| 路径 | 作用 |
|---|---|
| `sidecar/api.py` | FastAPI 路由,工具按 `/tools/*` (GET=读 / POST=写) |
| `sidecar/assignment_sheet.py` | 分工表领域层,**身份隐藏边界** |
| `sidecar/tencent_sheet.py` | 腾讯文档 V3 客户端 + 缓存 |
| `sidecar/checker.py` / `fixers.py` | 命名 / 时长 / CSV 等质检与自动修 |
| `sidecar/schemas.py` | Pydantic 请求 / 响应模型 |
| `sidecar/config.py` | `config.toml` 加载,字段见 `config.example.toml` |
| `app/electron/main.ts` | Electron main,管 IPC / sidecar 子进程 / 剪贴板 |
| `app/src/components/Explorer.tsx` | 文件树 — 多选 / 拖放 / 剪贴板逻辑全在这 |
| `app/src/components/MixConsole.tsx` | 混音台窗口 |
| `doc/agent_架构脑暴.md` | **agent 接入设计稿,多轮讨论中,落地前必读** |
| `doc/v2_验收清单.md` | v2 接 agent 前的人工验收清单(15 节)|

## 不能踩的雷(critical invariants)

1. **身份隐藏**:`assignment_sheet.py` 内部读 `config.user.reviewer_name`,**不通过工具参数从 LLM 传入**。reviewer 姓名永远不能进 agent prompt / tool args / tool results。这是隐私设计的核心边界,未来加 agent 工具时也要保住。

2. **不要把 `_rows` 系列暴露给 agent**:`list_my_pending_rows` / `list_my_accepted_rows` 返回完整 37 列 × N 行,只给开发者 UI / debug 用。给 agent 的应该是 `list_my_pending`(精简的 `PendingSong` dataclass),不然上下文爆掉。

3. **写工具走 confirm 流程**:`config.preferences.execution_mode` 默认 `"confirm"`。任何写操作(POST 端点 / 文件改动 / 表格写回)在 agent 模式下要给用户确认卡,不要静默执行。

4. **分工表列号写死在常量**:`COL_REVIEWER = 33` / `COL_ACCEPTED = 34` 等。启动时 `_validate_headers` 校验表头,**漂了立刻 raise** 不要默默读错列。学姐改了列序就让工具不可用。

5. **老 PyQt 代码处于维护态**:根目录的 `main.py` / `main_window.py` / `mix_console.py` 等是 v1,新功能进 `app/` + `sidecar/`,不要往老代码加东西。

## Agent 接入(Phase 4,设计中)

完整脑暴见 `doc/agent_架构脑暴.md`。核心决定:

- **架构**:MCP + IPC 混合 —— sidecar 工具走 MCP server,UI / playback / human 走 IPC
- agent 跑在 Electron main(离 UI 近,IPC 直)
- 工具按域分:`fs.*` / `sheet.*` → MCP 到 sidecar;`ui.*` / `playback.*` → IPC 到 renderer;`human.*` 走 main 自处理
- **工具粒度按"人类任务"切**(`load_song_into_mixer` 一次完成),不按原子操作切
- 工作流骨架代码写死(state machine),agent 只在每个状态内决策
- Human-in-the-loop:`request_human_check(reason, ui_state, expected_check)` 阻塞式,三态返回 `pass` / `fail+反馈` / `cancel`
- 全局 cancel 信道是 Electron 层中断,不是 agent 工具
- **agent 写文件**:默认走 fixer 工具(确定性);例外允许 `write_csv` / `write_text`,但仅限工作区 CSV / 文本文件 + 强制 diff confirm,**不**含分工表 / 二进制 / 音频
- **session = chat**(像 ChatGPT),用户管,workflow 是 chat 内动作
- **state exit message** 结构化产出 + GA `<summary>` 协议做软隔离(替代真 subagent)

设计未定的 open questions 列在脑暴文档第 7 节,落地前会再讨论几轮。

## 当前分支状态

- 主开发分支 `v2`,Phase 2(UI 打磨)末尾
- 验收清单 `doc/v2_验收清单.md` 走完 §1–§16 + 清理 §17 调试日志后进 Phase 4
- `main` 是发布分支,PR 提到 `main`

## 配置

`config.toml`(从 `config.example.toml` 复制改),不进 git。字段:`[anthropic]` / `[tencent_docs]` / `[user]` / `[preferences]`。sidecar 启动找配置顺序:`CHECKER_CONFIG` 环境变量 → 仓库根 `config.toml` → 平台 app config 目录。

## 给 Claude 的工作偏好

- 回复简洁,不重复显而易见的内容
- 改前先读相关代码,别凭印象改
- 写工具 / API 加 docstring 时要"双语短描述",中文长篇放设计文档,工具描述要 LLM-friendly
- 涉及 agent / tool 设计时,**用 GA(Generic Agent)的术语做参照系**(原子工具集 / SOP / L1-L4 记忆 / 上下文压缩),用户对 GA 整体熟悉,但具体机制该解释还是要解释
