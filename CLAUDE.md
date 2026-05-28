# Audio QC — Claude Code 项目说明

## 项目概览

桌面端音频质检工具,流程:从腾讯文档分工表拉"我负责验收的歌" → 在工作区里挨个查文件命名 / 时长 / 节奏 / 结构 / 混音 → 写回"已验收"标记。架构 = Electron + Python sidecar,Phase 4 接入 Anthropic agent 自动化人工流程。老的 PyQt 单体已在 v2 切换完成后归档至 git history。

## 架构

```
仓库根/
├── app/          前端 — Electron + React (Vite + Tailwind + Monaco)
├── sidecar/      后端 — Python FastAPI 服务,端口 8775(含质检 core: logic_checker / checker / fixers)
├── doc/          设计文档 / SOP / 脑暴
├── scripts/      一次性探针(probe_tencent*.py)
└── cache/        运行时缓存(gitignore)
```

**v2 数据流**:Electron renderer ↔ Electron main(IPC)↔ sidecar(HTTP /tools/*)↔ 文件系统 / 腾讯文档。

## 常用命令

```bash
# 前端 dev(Vite + Electron 一起起)
cd app && npm run dev

# sidecar dev(独立跑)
python -m sidecar.serve   # 默认 127.0.0.1:8775, /docs 看 OpenAPI

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

5. **质检 core 逻辑全在 `sidecar/logic_checker.py`**:命名 / 时长 / CSV 校验的真规则在这。`sidecar/checker.py` 和 `sidecar/fixers.py` 包它做 v2 编排,不要把规则散到 UI 层。

## Agent 接入(Phase 4,进行中)

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

- 主开发分支 `v2`,Phase 4(agent 接入)进行中:`app/electron/agent.ts` AgentRunner + MCP 工具 + db 持久化已搭好,workflow.md 18 态在迭代 SOP
- `main` 是发布分支,PR 提到 `main`

## 配置

`config.toml`(从 `config.example.toml` 复制改),不进 git。字段:`[anthropic]` / `[tencent_docs]` / `[user]` / `[preferences]`。sidecar 启动找配置顺序:`CHECKER_CONFIG` 环境变量 → 仓库根 `config.toml` → 平台 app config 目录。

## 给 Claude 的工作偏好

- 回复简洁,不重复显而易见的内容
- 改前先读相关代码,别凭印象改
- 涉及 agent / tool 设计时,**用 GA(Generic Agent)的术语做参照系**(原子工具集 / SOP / L1-L4 记忆 / 上下文压缩),用户对 GA 整体熟悉,但具体机制该解释还是要解释

### MCP 工具 docstring 风格(GA-style)

`sidecar/mcp_server.py` 里的 `@mcp.tool()` docstring 直接做 LLM 工具 description,改 docstring = 改注入 LLM 的工具说明。规则:

1. **1-2 句话**:第一句"做什么 + 返回啥",第二句(可选)关键警告 / 触发场景 / 关键字段
2. **不写 Args section**:类型签名已自解释。仅当参数有非自明约束(互斥 / 默认 / 特殊格式)才补一行
3. **Returns 折成 1 行内联**:`Returns {key1, key2, ...}`,不罗列 dataclass 全部字段。LLM 收到 dict 自己看 key
4. **关键 ErrorCode / Op 类型 / key fields 写在 desc 里**:LLM 看一眼就知能查 / 能改 / 能拿什么,无需翻外部文档
5. **Failure 写到 1 行**:`On failure: {ok: false, code: "X" | "Y", message: str}`,不另起 Failure modes section
6. **不写"agent 不会主动做的边界"**:像"不应试图还原打码"/"不要绕过 confirm" 这种,agent 看不到对应工具就做不出来,写出来反而显得 prompt 怪
7. **关键警告用 CAPS / 短句**:`NEVER pass approved_ops without confirm card hash` 这种值得加;常规说明用普通中文
8. **场景型工具用 `(1)(2)(3)`**:多触发场景的工具(如未来的 `state_tree_update`)用编号列表

参考范本:`sidecar/mcp_server.py` 现有 10 个工具均按此风格,写新工具直接照抄。
