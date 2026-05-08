# Agent 架构脑暴

> 状态:**讨论中**,非正式方案。多轮迭代后再决定落地方案。
> 起点:2026-05-07 与用户对话整理。

---

## 0. 愿景(用户原话整理)

- 人类在软件上能做的基本操作,agent 基本都要能做
- 精细操作暂不做(例:播放台拖动进度条)
- 必须能做的:打开混音台、添加歌曲、打开文件、渲染节奏、渲染结构……
- 流程中会出现 agent 难以独立完成的判断(例:节奏是否对齐),需要 agent **唤起 UI 让人类 check**,人类反馈后继续
- 人类 check UI:**三态**
  - `pass`(没问题)
  - `fail` + 结构化反馈(发现的问题字段 + 自由备注)
  - `cancel`(整个流程停下)

---

## 1. 框架选型

### 候选(已讨论)
| 方案 | 评价 |
|---|---|
| MCP (Model Context Protocol) | 当前最接近"标准"。OpenAI/Google/Anthropic 都接了。工具与 provider 解耦 |
| Anthropic SDK 原生 tool use | 零中间层,延迟最低,功能最全 |
| Pydantic AI | 轻量,多 provider,prompt 不抢戏 |
| LangChain / LlamaIndex | 不推荐 —— 抽象重,prompt 黑盒 |
| Anthropic Agent SDK | 内置 MCP / subagent / 文件 bash 工具,适合"造 Claude Code 同款" |

### 决定:直接上 MCP(2026-05-08 修订)

**sidecar 工具走 MCP,UI / playback / human 留 IPC**(混合架构)。

理由:
- 协议已稳定:Anthropic SDK / Claude Code / Claude Desktop / Cline / Cursor 都支持
- sidecar 现在就是 HTTP API,翻成 MCP server 主要是 schema 包装,不是大改
- 跳过"先 native 后 MCP"少一次迁移工作
- bonus:外部客户端(Claude Desktop)也能直连 sidecar,联调方便

但 **UI 工具不走 MCP**:`ui.open_panel` / `playback.play` / `request_human_check` 必须操控 renderer,走 MCP 反而要让 sidecar 反向通知 renderer,绕一大圈。直接 IPC 简单一个数量级。

具体落地:
- sidecar 加 `sidecar/mcp_server.py`,用 `mcp` Python SDK 把现有 FastAPI 工具包一层
- Electron main 用 `@modelcontextprotocol/sdk` 起 MCP client,subprocess spawn sidecar
- 现有 `/tools/*` HTTP 端点保留给开发期 dev panel(或删,看实测要不要)

> 历史(已撤回):初稿是"两步走 —— 短期 Anthropic SDK 原生 tool use,中期 MCP"。本次直接跳过中间态。

---

## 2. 架构分层

### 工具不再全在 sidecar
现有 sidecar(Python)管文件/数据。新加的 UI 控制必须能命令到 renderer。

**架构(2026-05-08 修订:MCP + IPC 混合)**:

```
agent (Electron main)
  ├─ MCP client → sidecar MCP server   (fs.* / sheet.*)
  ├─ IPC (webContents.send) → renderer  (ui.* / playback.*)
  └─ in-process                          (human.*, escalate, cancel)
```

各路解释:
- **MCP** 给 sidecar:数据/文件类工具,标准协议,可被任何 MCP 客户端复用(Claude Desktop 也能直接调)
- **IPC** 给 renderer:UI 控制类工具,绕开 MCP 反向通知的复杂度
- **in-process** 给 main 自处理:human-in-loop / cancel 信道,延迟最低

> 撤回:初稿"两条路"列了"sidecar 起反向 websocket 推 renderer",现在确定不走这条 —— UI 工具直接 IPC,不需要 sidecar 介入。

### 工具按域分四类
- `fs.*` / `sheet.*` —— 数据层(已有)
- `ui.*` —— 切换视图、打开面板、定位行/文件、加载混音台
- `human.*` —— check / confirm / pick(让人类二选一)
- `playback.*` —— play/pause/load,**不暴露 seek**(用户明确说精细操作不做)

---

## 3. Human-in-the-loop 原语

### 工具签名(草稿,2026-05-08 升级:单 string → items list)

```python
class CheckItem(TypedDict):
    id: str                  # 稳定标识,如 "alignment" / "content_match" / "vocal_noise" / "quality"
    label: str               # 给人类看的中文短语,如 "对齐没问题" / "命名↔内容对应"
    default_pass: bool       # UI 默认是否勾上(按经验值给默认,降低人工点击)

class CheckItemResult(TypedDict):
    id: str
    passed: bool             # 用户是否勾上
    note: Optional[str]      # 单项备注(可选,常见于"对齐"打 ✗ 时写"副歌晚 0.2s")

request_human_check(
    state: str,              # 当前状态机态 ID,如 "2.3" / "2.5";让 UI / review_log 知道在哪
    reason: str,             # 给人类看的整体提示:"混音台 session 1 — 请检查这 4 项"
    ui_state: dict,          # 切换 UI 的指令:{ "panel": "mixer", "song": "...", "preload": [...] }
    items: list[CheckItem],  # 本次要勾选的项;长度 1 = 退化为单项 check 卡
) -> {
    "result": "pass" | "fail" | "cancel",
    # pass: 所有 items 都勾上;fail: 至少一项没勾;cancel: 整个 workflow 中止
    "items": list[CheckItemResult],   # 逐项结果,fail 时 caller 据此知道"哪几项 fail"
    "note": str,                      # 整体自由文本备注(独立于 items 各自的 note)
}
```

**为什么从单 `expected_check: str` 升级**:操作清单 2.3 / 2.4 / 2.6 / 2.7 都出现"一次混音台 session 看多件事"的需求,单 string 表达不了 4 项独立勾选,会被迫拆成 4 张卡 → 4 轮 LLM round-trip,违反 §4.1 粒度。

**`items` 长度 1 退化**:1.1-03 / 1.5-02 / 2.2-03 等仍是单项 check,直接传 `items=[CheckItem(id="...", label="...", default_pass=False)]`,UI 渲染上可特化为"两按钮 pass/fail",不强制走多项 checklist。

**`state` 字段**:让 review_log 写入时拿得到态 ID,同时给 UI 决定要不要切面板(脑暴 §10.9 工作流进度面板按 state 高亮 checklist)。

**`ui_state` schema**(对应 §7 第 5 个 open question):
- `panel: "mixer" | "midi_editor" | "csv_editor" | "wav_main" | "sheet_view"`
- `song: str`(歌曲文件夹名)
- `preload: dict`(各 panel 自己定:mixer 要 mode + tracks;midi_editor 要 midi_path + ref_wav;...)
- 太自由 LLM 会乱填 → 在 mcp_server 端给 `ui_state` 一个 discriminated union,不在 schema 里就拒绝(§4.4 硬约束)

### 实现方式:阻塞式(首选)
- tool handler 里 `await event`,直到 renderer 推回结果
- LLM 看到的就是普通同步工具,prompt 干净
- Electron main 维护 pending check 注册表,renderer 点 button 后 IPC 回灌触发 resolve

### 不选:非阻塞 + pending_id
- 复杂得多
- 只在"agent 同时挂多个 check 等待"时才需要 —— 你这场景一次一个 check,不需要

### `cancel` 的语义
- 不只是"这次 check 取消",而是"**整个 agent session 中止**"
- tool 返 `{"result": "cancel"}` 后,agent 系统提示词要约束它直接结束(或调 `escalate_to_human`)
- **额外**:全局 cancel 信道(快捷键/按钮)能在任何时刻打断当前 tool call,不只是 human_check

---

## 4. 工具编排原则(7 条)

### 4.1 工具粒度按"人类任务"切,不按"原子操作"切
**最关键的一条**。

- 反面:`open_panel("mixer")` → `clear_tracks()` → `add_track(p1)` → ... 5 个 round-trip,每步都可能错位
- 正面:`load_song_into_mixer(song_name)` 一次 call,内部代码搞定全部副作用

LLM 每次 tool call 都是决策机会,**机会越多出错率越高**。粒度对齐"操作员脑里的一个动作"。

> **反方观点(GA 哲学)**:Generic Agent 走的是相反路线 —— **9 个最小原子工具 + LLM 智能组合 + SOP 兜底**,目标是 token 极致省(GA 自称是 Claude Code 的 35% 消耗)。
>
> 我们没采这条的原因:
> - 我们的工作流**固定**(每首歌验收流程一样),不需要 LLM 在运行时现场组合
> - 状态机已经把"骨架"写死(§4.2),agent 只在每个状态内做局部决策,自由度本来就低
> - 我们的领域**封闭**(音乐质检 ≠ 开放浏览器任务),任务粒度的工具能完全覆盖,不会出现"找不到合适的复合工具,只能用原子工具拼"
> - token 不是当前主要瓶颈,**正确性 / 可预测性**才是
>
> 但 GA 的**分层记忆 / 上下文压缩**思路对我们仍然适用 —— 例如 tool docstring 按需加载、歌曲元数据不一次塞完。这部分到第 5 节"现有工具封装评估"和未来记忆设计时再谈。

### 4.2 工作流骨架用代码写死,工具填空
不让 agent 自由发挥整个验收流程。每首歌 happy path 固定:
```
load → auto_check → [if errors: human_check_errors]
     → human_check_rhythm → human_check_mix → mark_accepted
```
- 在 Electron main 写成确定性 state machine
- agent 只在每个状态内部决策(例:auto_check 报错时分诊该怎么修)
- 好处:token 省、行为可预测、bug 易定位

### 4.3 每个 action 配一个 query
agent 看不见 UI,所有状态变更后必须能"读回来确认"。
- `load_song_into_mixer` ↔ `get_mixer_state`
- `mark_accepted` ↔ `get_song_status`

否则 agent 会脑补当前状态。

### 4.4 写工具内置 precondition 检查
**不靠 prompt 约束安全边界,prompt 是软约束,代码是硬约束**。

例:`mark_accepted(song_name)` 内部硬性检查 —— 这首歌当前 session 里有没有 `request_human_check` 返过 `pass`?没有就拒绝:
```json
{"ok": false, "code": "NEEDS_HUMAN_VERIFY", "message": "..."}
```

### 4.5 错误返回要"可操作"
```json
{"ok": false, "code": "MIXER_BUSY", "message": "...",
 "recoverable": true, "suggestion": "call close_mixer first"}
```
有 `suggestion` agent 一次重试就能修;没有的话它会乱试三轮然后放弃。

### 4.6 不用 subagent,但留 escape hatch
- 这个场景 single agent 够用,不需要多 agent 编排
- 但准备一个 `escalate_to_human(reason)` 工具,agent 卡死时调它,流程暂停等人接管
- 比让 agent 死循环或瞎写好得多
- **subagent 收益的等价替代方案见 §10.8**(state exit message + `<summary>` 软隔离)

### 4.7 全局 `cancel` 信道
- 人类随时能打断当前 tool call(尤其 `request_human_check` 长 await 时)
- 不是 agent 工具,是 Electron 层的中断信号
- tool handler 收到后立刻 reject

---

## 5. 现有工具封装评估

### 好的部分
- 三层分得干净:`tencent_sheet.py`(传输/缓存)→ `assignment_sheet.py`(领域)→ `api.py`(HTTP)。接 agent 时跳过 HTTP 直接调领域层即可
- `assignment_sheet.py:5-8` "身份隐藏"边界关键 —— LLM 看不到 reviewer_name,这条要保住
- Pydantic schemas 在 `sidecar/schemas.py` 现成,转 tool definition 几乎零成本

### 接 agent 前要补的
1. **A/B 类标记没显式化**:现在靠 GET/POST 区分,Python 函数层没标记。建议加 `@tool(write=True)` 装饰器或维护白名单,write 工具走 confirm 流程
2. **错误返回不统一**:`HTTPException(400, detail=...)` 是给前端的;agent 要的是结构化 `{ok, error_code, message}`。建议在领域函数层返 `Result` dataclass,api.py 和 agent 都从这层取
3. **工具 docstring 是中文长篇**:LLM 需要简短英文 + 参数语义 + 失败模式。要么写 `__llm_doc__`,要么 docstring 改成 agent-friendly 的双语短描述
4. **没有 token 预算意识**:`fetch_all` 返 270 行 × 37 列,直接给 agent 会爆上下文。`list_my_pending`(精简)和 `list_my_pending_rows`(完整)的分流对了 —— 但要硬性禁掉给 agent 暴露 `_rows` 系列

---

## 6. 落地顺序(草稿)

1. 列**人类操作清单** —— 把现在 UI 上能做的所有事过一遍
2. 据此定 **tool inventory**(粒度按 4.1)
3. 写**状态机骨架**(4.2),agent 暂时只在一两个状态里跑
4. 实现 **`request_human_check`**(阻塞式 + 三态返回)
5. 跑通**一首歌端到端**
6. 再加 `escalate` / `cancel` / 多歌循环

---

## 7. Open questions(待讨论)

- [ ] UI 控制工具走 MCP 还是直接 Electron IPC?(倾向 IPC)
- [ ] Agent 跑在 Electron main 还是 sidecar?(倾向 main —— 离 UI 近,IPC 直)
- [ ] Anthropic API key 怎么管?(用户自己填到 config.toml?系统级 keychain?)
- [ ] 多歌循环时,是 agent 自己决定下一首,还是 state machine 喂?(倾向后者 —— 决定性强)
- [~] `request_human_check` 的 `ui_state` 怎么 schema 化?(2026-05-08 §3 部分答:走 discriminated union,`panel` 字段决定 `preload` schema;mcp_server 端硬校验。具体每个 panel 的 preload 还要按操作清单 2.x 各态展开)
- [ ] Cost / token budget:典型一首歌验收 agent 大概要烧多少 token?要不要预估
- [ ] Agent 失败 / API 报错时的 fallback —— 静默 retry?提示用户?
- [ ] 流式输出(streaming):agent 边想边给用户看,还是等 turn 结束一次性给?
- [ ] 历史 trace / 可观测性:每个 tool call 要不要写日志,方便事后复盘 agent 决策

---

## 8. 不做(明确 out of scope)

- 多 agent 编排 / agent 之间通信
- 播放台拖动进度条 / 精细鼠标操作
- agent **无约束**自主写文件内容(LLM 幻觉编数据风险高)。**约束版可做**:默认走 fixer 工具(确定性写),**例外**允许 agent 调 `write_csv` / `write_text`,但必须满足:
  - 文件类型白名单:工作区 CSV / 文本文件,**不**含分工表
  - 强制 confirm 流程,UI 弹卡片显示 diff,人确认才落盘
  - 不允许写表格之外的格式(WAV / MIDI / 二进制等)
- agent 跨 session 记忆(每次新对话从干净状态开始)

---

## 9. 参考项目(study, not adopt wholesale)

落地前打算扒以下项目,挑能直接借鉴的模式 —— **不是抄整套**,只看对应模块。

| 项目 | 看什么 | 与我们关系 |
|---|---|---|
| **Cline**(VSCode 扩展) | LLM 在 extension host,UI 在 webview,工具按域 dispatch 到 host vs webview | **架构最接近** —— "VSCode 版 Electron main 当 router",可直接对照学 |
| **Claude Code 本体** | per-tool human permission(write 工具一律弹确认)、hooks / MCP 注册、system prompt 结构 | 我们 §4.4 "硬约束"思路同源;闭源,只能从外部观察(`/export` 看对话原始 JSON、API log 看 tool_use block) |
| **Anthropic "Building effective agents"** 博客 | single-agent + tools 几种 pattern 的官方推荐 | 比追代码更对路,落地前先读 |
| **opencode / Goose** | Claude Code 风格的开源 clone,agent loop + tool registry 模块化 | 看具体实现细节,但架构跟我们不完全对齐(它们都是 CLI / 终端,我们是 GUI) |
| **GA(lsdefine/GenericAgent)** | 四层记忆架构 L1-L4、上下文压缩流水线、自我进化 NL→SOP→code | **设计哲学与我们对立**(原子工具 vs 任务粒度,见 §4.1 反方观点),但记忆 / 压缩思路可借鉴 —— **源码机制详见 §9.1** |
| **smol-agents**(HuggingFace) | 极简 ReAct loop 实现 | 用来摸最小机制,debug 思路时参照 |

**优先级**:Cline 源码 + Anthropic 博客 > 其他。等到 §6 第 1 步"列人类操作清单"做完后,选 Cline 一个具体场景对照画我们的工具 dispatch 表。

### 9.1 GA 省 token 机制详解(源码扒读 2026-05-08)

GA 自称比 Claude Code 省 65% token,是**多层叠加**效果(源码在仓库根 `GenericAgent/`)。按对 token 影响从大到小:

1. **极简工具集**——只有 8 个(`code_run` / `ask_user` / `web_scan` / `web_execute_js` / `file_patch` / `file_read` / `file_write` / `update_working_checkpoint`,见 `ga.py:277-510`)。`code_run` 跑 Python 脚本包揽 ls / grep / find / mv / cp 一切,tool schema 本身极小

2. **工具 schema 按需重发**(`llmcore.py:722-726`):tools 跟上轮一样 → 只发 `### Tools: still active, ready to call`,**不重发完整 schema**。重置触发器:每 10 轮 / 累计 >9000 chars / LLM 主动 `[NextWillSummary]` tag / JSON 解析失败

3. **历史 tag 截断**(`llmcore.py:26-57` `compress_history_tags`):老消息(超出最近 10 条)的 `<thinking>` / `<tool_use>` / `<tool_result>` 截到 800 字符,`<history>` / `<key_info>` 整段塞 `[...]`。每 5 次调用触发一次,context >3x window 强制更狠

4. **`<summary>` 协议 —— 逼 LLM 自压缩**(`llmcore.py:714-720`,**最聪明的一招**)。系统提示强制 LLM 每轮在 `<summary>` 标签写 30 字物理快照,而 `compress_history_tags` 只截 thinking / tool_use / tool_result,**不截 summary** —— LLM 自己产出的摘要自然变成"长期保留的工作记忆",老 thinking 被裁掉也不丢关键信息。漏写还会被 prompt 警告(`ga.py:451` `[DANGER] 上一轮遗漏了<summary>`)

5. **内容自动瘦身**:
   - 代码块 >6 行只展示前 5 行 + 省略计数(`agent_loop.py:99-111`)
   - tool args 路径只留 basename,args 截到 120 字符(`agent_loop.py:113-118`)
   - `file_read` 结果硬裁到 20000 字符(`ga.py:415`)

6. **L1-L4 分层按需加载**:
   - L1 (`assets/insight_fixed_structure.txt`) + L2 (`memory/global_mem_insight.txt`) **只在每 10 turn 注入**,不是每轮(`ga.py:528-532`)
   - L3 SOP(`memory/*_sop.md`:plan_sop / vision_sop / web_setup_sop / ...)**默认不加载**,LLM 用 `file_read` 主动取;读了之后系统追加 prompt 逼"提取关键点 update working memory"(`ga.py:413-414`)
   - L4 (`memory/L4_raw_sessions/`) 完全不进 context
   - working memory `self.working['key_info']` + `related_sop` 每轮 anchor prompt 注入(`ga.py:435-443`),LLM 忘了哪 SOP 就 prompt 再点一句"有不清晰的请再读 X"

7. **Prompt caching**(基础设施级):最后 2 条 user 消息 + system + tools 末尾元素全打 `cache_control`,`prompt-caching-2024-07-31` beta header 开(`llmcore.py:283-294, 505, 572, 580-582`)

**隐藏架构决定**(`agent_loop.py:95`):`agent_runner_loop` 每轮只发新消息,累积历史在 `BaseSession.history` 集中处理。loop 跟 session 解耦,让 token 优化集中实现不散落各处。

#### 对我们可借鉴的取舍

| 机制 | 抄 | 不抄 |
|---|---|---|
| `<summary>` 协议(LLM 自压缩) | ✅ 几乎 free,**最值得抄** | |
| 工具 schema 按需重发 | ✅ 实现成本低 | |
| 历史 tag 截断(对应 §10.3 auto-compact) | ✅ | |
| Prompt caching | ✅ Anthropic SDK 一行配置 | |
| tool result 硬裁(`file_read` → 20k) | ✅ 跟 §5 "token 预算意识" 对齐 | |
| 8 个超原子工具 + `code_run` | | ❌ 我们走任务粒度(§4.1 反方观点) |
| L1-L4 分层 | | ❌ 跨 chat 数据走工具查 review log,不进 prompt(§10.5) |
| `start_long_term_update` 自我进化 | | ❌ SOP 是 state machine 写死代码(§4.2) |

---

## 10. 会话与记忆

会话管理走**通用聊天机器人形态**(ChatGPT / Claude.ai / Cursor chat 风格),**不**按工作流边界自动切 session —— 用户视角是"多个命名 chat,自主新建/切换/继续",workflow 是 chat 里的一个动作,不反过来。

### 10.1 Session = chat(用户控制)

- `AgentSidebar.tsx`(目前是 §13 占位)长成 chat list:新建 / 重命名 / 删除 / 切换
- 每个 chat 各自独立 LLM 上下文,跨 chat 不携带历史
- Chat 数据本地 JSON 持久化(类似 Cursor / Claude Desktop 的 chat history),路径建议 `cache/chats/<chat_id>.json`

### 10.2 Workflow 触发

- "验收一批歌"是用户在 chat 里发的**指令**(自然语言)或工具栏按钮(本质也是发同款 prompt)
- 同一个 chat 里用户也能切话题(单独问 "X 歌为什么报错" / 问代码 / 闲聊),agent 不约束
- state machine 是 chat 内的一个工具,不是 chat 容器

### 10.3 Context 管理

- chat 过长走**通用 auto-compact**(老消息 LLM summarize,新消息保留原文)—— 不做 workflow-aware 特殊压缩
- 行为参考 Claude Code 的 auto-compact;Anthropic SDK 没自带,得自己写 `summarize_old_turns(history) -> compressed_message`

### 10.4 续 / 中断

- 续 = 用户在 sidebar 切回旧 chat,LLM 看到完整(或压缩后的)历史
- **state machine:模板写死代码,进度持久 append-only 事件流**(2026-05-08 修订):
  - ✅ **持久**:每态退出写一行到 `cache/review_log.jsonl`(加 `chat_id` 字段),resume 时 replay 出"上次走到哪"
  - ❌ **不持久**:文件树状态、分工表快照、UI 当前选中 等派生状态(都是 lazy 重读)
  - 🔁 **Resume 对账**:replay 事件流 → 跟现实(文件 / sheet)比对 → 不一致**先问用户**"log 说 X,但 Y 不一致,要重做这步还是接着走?"
  - 不开第二个文件 —— `review_log.jsonl` 兼任 §10.5 跨 chat 知识库 + 本 chat 进度 UI 数据源(按 chat_id 过滤)
- §3 / §4.7 里 `cancel` 的语义微调:中止的是**当前 workflow 执行**,不是关 chat。chat 本身永远在,用户随时能继续打字

> 撤回:初稿"state machine 设计成无状态 / 简单 > 智能"那条彻底改写 —— 用户提出要"像 Claude Code plan 模式但可恢复"的进度 UI(§10.9)后,纯无状态做不到,改成"只持久 append-only 事件,不持久派生快照"的最薄状态层。简单 vs 智能取舍换边了。

### 10.5 跨 chat 数据 = 工具查询,不是 LLM 记忆

§8 "agent 跨 session 记忆 = 不做" 仍然成立(指 LLM 上下文**不**跨 chat 携带)。但**结构化数据可跨 chat 查**:

- 分工表 —— 已有
- **review log**(新增):每态退出的事件流,JSONL 写到 `cache/review_log.jsonl`,每行字段 `{chat_id, song, state, result, summary, details, timestamp}`
- 工具 `get_prior_review(song_name)` 让 agent 在新 chat 主动查"这首歌之前怎么样"(跨 chat,**不**按 chat_id 过滤)
- 同一文件,本 chat 的 workflow 进度 UI 也读它(**按** chat_id 过滤)—— 一份双用,见 §10.4 / §10.9

区分清楚:这是"**知识库**",不是"**记忆**"。

### 10.6 Trace / observability

- 每个 tool call / state 转移 / human check 结果写 JSONL 到 `cache/agent_trace_<chat_id>.jsonl`
- 用途:**人工 debug 复盘**,不喂回 agent
- 这条是 §7 第 9 个 open question 的正式落地

### 10.7 数据落盘格式(草稿)

| 文件 | 用途 | 跨 chat? |
|---|---|---|
| `cache/chats/<chat_id>.json` | chat 本体(messages 数组) | N/A |
| `cache/review_log.jsonl` | **状态退出事件流** + 验收结论 / 人类反馈,字段含 `chat_id` | 是(无 chat_id 过滤 = 跨 chat 知识库;按 chat_id 过滤 = 本 chat 进度 UI 数据源)|
| `cache/agent_trace_<chat_id>.jsonl` | tool call trace | 否,debug 用 |
| ~~`cache/state_machine_<chat_id>.json`~~ | **不做**(state machine 模板在代码;进度落 review_log) | — |

### 10.8 State 隔离 + exit message(代替真 subagent)

讨论过"全局 agent + 子 agent 分解 check"。我们场景(封闭工作流、同质工具、串行执行)**真 subagent 性价比低**:获得的隔离收益小于 spawn 成本(每个子 agent 多一轮 system prompt + UI 路由复杂 + 人机交互链路变长)。改为在 state machine 内**软实现** subagent 的关键收益。

**核心做法**:每个 state 退出强制产出**结构化 exit_message**:

```python
exit_message = {
  "state": "naming_check",
  "result": "passed" | "failed" | "skipped",
  "summary": str,    # <30 字,人类可读
  "details": dict,   # 结构化字段(改名清单 / issue list / 用户反馈等)
}
```

- 全局视图只看 exit_message,**不**直接看 state 内部 tool 历史
- exit_message 同步写入 `review_log.jsonl`(§10.5)—— 既做"全局上下文压缩"又做"可观测"

**配合抄 GA 的 `<summary>` 协议**(§9.1 第 4 条):state 内每轮强制 LLM 在 `<summary>` 标签写 30 字物理快照,history 压缩时 `<summary>` 不被截断,所有 summary 自然累积成 `exit_message.summary` 的素材。

**等价收益对比**:

| | 我们这套 | 真 subagent |
|---|---|---|
| 上下文隔离 | ✅ 软(summary 压缩 + state 切换清史) | ✅ 硬(完全独立 conversation) |
| 可解释分解 | ✅ exit_message 类似函数返回值 | ✅ |
| LLM 调用开销 | 同 1 个对话 | +N 个 system prompt 加载 |
| 并行 / 换模型 | ❌(暂不需要) | ✅ |

**升级 hatch**:实测下来某个 check 产物爆炸(比如批量扫全工作区文件)→ **那一个** check 升级成真 subagent,单点升级不全盘改架构。

### 10.9 Workflow 进度面板(2026-05-08 新增)

承接 §10.4 的"持久事件流"基建,具体落到 UI:

- **载体**:agent 侧栏(`v2_验收清单.md` §13 当前为占位)的常驻面板,chat list 下方
- **数据源**:`cache/review_log.jsonl` 按当前 `chat_id` 过滤
- **渲染**:18 态状态机模板(写死代码,详见 `doc/操作清单.md` 总览) × 当前 chat 的事件流 → checklist
  - ✓ 已完成 / ● 进行中 / ○ 待执行 / ✗ 失败
  - 每行可展开看 exit_message 的 `summary` / `details`
- **affordances**:
  - 取消整个 workflow(对应 §4.7 全局 cancel)
  - 跳过当前态(强制写一行 `result=skipped` 推进下一态;限非关键态 —— 关键态:`1.7 复检` / `3.3 标记已验收`,实测时再收紧)
  - 重做当前态(回退一格,删该态最近一条 review_log 记录,重新进入)

**跟 Claude Code plan 模式的差别**:

| | Claude Code plan | 本项目 workflow 节点 |
|---|---|---|
| 步骤来源 | LLM 临时草拟 | 14 态状态机模板,固定 |
| 持久化 | 当前对话 in-memory | 落盘(`review_log.jsonl`),跨 session 可恢复 |
| 用户介入 | 批准 / 改 / 拒 | 看进度,中途 cancel,fail 后可重试单态 |
| 显示载体 | chat 内的一段渲染 | agent 侧栏常驻面板 |

**为什么是侧栏不在 chat 内**:chat 主要承载自然语言交互;workflow 是结构化 / 可视化的工作面板,塞 chat 里会被聊天滚动顶飞,反而看不见进度。

---

## 11. 本轮新增(2026-05-07 对话)

- §4.1 加 "反方观点 (GA 哲学)" 段,记录"为什么不走原子工具路线"
- §9 参考项目表新建(Cline / Claude Code / GA 等的取舍说明)
- §10 **会话与记忆方案敲定**:走通用 chat 形态,workflow = chat 内动作
  - 用户明确反对了 agent-centric 的"按工作流切 session"路线(初稿提过的"一批待验收 = 一 session"撤回)
  - state machine 设计成无状态工具(简化,不做断点恢复)
  - 跨 chat 数据走工具查询(review log),**不**走 LLM 记忆
- 仓库根新建 `CLAUDE.md`(Claude Code 启动时自动加载的项目级 context)—— 项目级上下文已兜底,但 memory 系统在本机环境 EPERM,user / feedback 类记忆暂时缺位
- §9.1 新增:**GA 源码扒读**,梳理了 7 类省 token 机制(极简工具 / schema 按需重发 / 历史截断 / `<summary>` 协议 / 内容瘦身 / L1-L4 按需加载 / prompt caching)+ 可借鉴清单
- §10.8 新增:**state exit message + `<summary>` 软隔离**作为真 subagent 的等价替代
  - 用户提议过全局 agent + 子 agent 分解 check 任务,讨论后认为我们场景(封闭工作流 / 同质工具 / 串行)真 subagent 性价比低
  - 改为 state machine 内每个 state 退出产出结构化 exit_message,配合 GA `<summary>` 协议做软隔离
  - 留升级 hatch:某 check 产物爆炸时单点升级,不全盘改
- §1 / §2 / §8 实质修订(2026-05-08):
  - **直接上 MCP**(原"两步走"撤回):sidecar 工具走 MCP server,UI / playback / human 留 IPC,混合架构
  - **agent 写文件**:从"完全禁止"改为"约束版允许"——工作区 CSV / 文本,白名单 + diff confirm,不含分工表
  - 仍未做:LLM 自由生成文件内容(幻觉风险)、agent 写音频/MIDI 等二进制
- §10 / 操作清单 实质修订(2026-05-08 第二批):
  - §10.4 撤回"state machine 设计成无状态"原案 —— 用户提出要"Claude Code plan 模式但可恢复"的进度 UI 后,改成"模板写死代码 + 进度落 append-only 事件流"
  - §10.5 / §10.7 `review_log.jsonl` 加 `chat_id` 字段,一份双用(跨 chat 知识库 + 本 chat 进度 UI 数据源),不开第二个文件
  - §10.9 新增:**workflow 进度面板** —— Claude Code plan 模式的"模板固定 + 可恢复"变体,侧栏常驻
  - `doc/操作清单.md` 附录加 4 行(A-08 写 review_log / A-09 跳过当前态 / A-10 重做当前态 / A-11 resume 时事件流 vs 现实对账)
- **状态树敲定(2026-05-08 第三批):**
  - **18 态 per-song 状态树**敲定 —— 前置 gate + Block 1 (1.1–1.7) + Block 2 (2.1–2.8) + Block 3 (3.1–3.3,park);详见 `doc/操作清单.md` 总览
  - **入口前置 gate** 改为**硬性二元**:歌曲目录在不在本地工作区,缺 = 整首中止,**不出现"缺这些歌,要继续吗"**(那是多歌层的事,不是单歌)
  - **混音台拆两 session**(2.3 / 2.4):分轨+总轨一次、源文件+总轨一次,因为一次性加全部太多;分别打 4 项 / 2 项勾
  - **节奏 / 结构 toggle 拆两态**(2.6 / 2.7):各自"开 toggle → 听 → 关 toggle",中间不并存(避免视觉干扰)
  - **混音工程文件命名** 合并到 1.4(原本散在多处),统一走 `fix.propose_rename_plan`
  - **MIDI 对齐范围 = 仅 Vocal_midi + BG_midi**;Mix_midi **存在性** 在 1.3 检,**不做对齐**(`流程.md` "重点关注 vocal")
  - **关键态收窄到 2 个**:`1.7 复检` / `3.3 标记已验收`,其他态默认可 skip,实测后再收紧
  - **agent 不能写音频**:1.5 WAV 类全部 `human-only`,agent 只在 check 卡 reason 里点名,统一时长由人在混音台听后排版(2.3-07)
  - `doc/操作清单.md` 全量重构成 `G-XX` / `1.x-XX` / `2.x-XX` / `3.x-XX` ID 体系
- **§3 `request_human_check` schema 升级(2026-05-08):**
  - 单 `expected_check: str` → `items: list[CheckItem]`,每项含 `id` / `label` / `default_pass`
  - 返回值加 `items: list[CheckItemResult]`,fail 时 caller 拿得到"哪几项没过"
  - 新增 `state` 字段(状态机态 ID),让 review_log 与 §10.9 进度面板能定位
  - `ui_state` 走 discriminated union(`panel` 决定 `preload` schema),mcp_server 硬校验,部分答 §7 第 5 个 open question
  - `items` 长度 1 退化为单项 check 卡,1.1 / 1.5 / 2.2 等单项场景不变形
