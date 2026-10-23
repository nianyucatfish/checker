# 外部 Agent 接入 Audio QC

外部 Agent（例如 Codex）可以通过本机 HTTP 接口或随 Skill 提供的 CLI 使用软件的质检能力，不需要配置内置模型，也不需要客户端安装 MCP。软件内部继续共用现有工具实现。

从 v2.0.0-beta.3 起支持此接口，Release 下载页附带 `Audio-QC-Agent-Skill-<版本>.zip`，其中包含可直接安装的 `audio-qc/` 文件夹和独立 CLI。

## 使用方式

1. 打开包含外部接口功能的 Audio QC，等待启动完成。
2. 将仓库的 `skills/audio-qc` 文件夹复制到外部 Agent 的技能目录。例如 Codex：`~/.agents/skills/audio-qc`。重新加载技能后可用 `$audio-qc` 发起任务。
3. 让 Agent 读取当前流程并执行指定范围的质检。需要试听或人工确认时，使用软件的文件预览、试听和外部 Agent 对话中的人工确认。

## 各平台安装 Skill

先打开支持外部接口的 Audio QC，下载配套的 Skill ZIP 并解压。源代码用户可直接使用仓库中的 `skills/audio-qc`。CLI 需要 Python 3.9+，只用标准库；内置模型配置可以留空。不要只复制 `SKILL.md`，`scripts/` 和 `references/` 也要保留。

### 通用方式：直接让 Agent 阅读说明

无需安装，直接告诉能读取本机文件并运行命令的 Agent：

> 阅读 `/路径/audio-qc/SKILL.md`，按照其中的说明，使用 CLI 完成质检。

替换为真实路径，例如 macOS 的 `/Users/你的用户名/Downloads/audio-qc/SKILL.md`，或 Windows 的 `C:\Users\你的用户名\Downloads\audio-qc\SKILL.md`。软件与 Agent 应在同一台电脑上运行；试听和预览在 Audio QC 中操作，人工确认在当前 Agent 对话中回答。

### Codex：本地桌面端、CLI、IDE 扩展

将整个 `audio-qc` 文件夹复制到个人技能目录：

| 系统 / 执行环境 | 安装后的入口 |
| --- | --- |
| macOS / Linux | `~/.agents/skills/audio-qc/SKILL.md` |
| Windows 原生 | `%USERPROFILE%\.agents\skills\audio-qc\SKILL.md` |

当前官方推荐个人目录为 `.agents/skills`；本教程按此路径安装。若只希望当前项目可用，也可放到项目的 `.agents/skills/audio-qc/`，同一份技能选一个位置即可。

安装后选择 `audio-qc` 技能并描述任务。CLI / IDE 可输入 `$audio-qc`，例如：

> $audio-qc 检查当前工作目录中的歌曲，先检查文件结构与命名。

Codex 会自动发现技能变动；若列表未更新，重启客户端。在桌面界面中也可选择技能或直接点名让它使用 `audio-qc`。

来源：[OpenAI 官方：Build skills](https://learn.chatgpt.com/docs/build-skills)。

### Claude Code：终端、IDE、本地桌面 Code 会话

将整个 `audio-qc` 文件夹复制到：

| 系统 / 执行环境 | 安装后的入口 |
| --- | --- |
| macOS / Linux | `~/.claude/skills/audio-qc/SKILL.md` |
| Windows 原生 | `%USERPROFILE%\.claude\skills\audio-qc\SKILL.md` |

也可只对项目安装到 `<项目>/.claude/skills/audio-qc/`。重新打开本地会话后输入：

> /audio-qc 检查当前工作目录中的歌曲，先检查文件结构与命名。

桌面应用请使用 **Code 的本地会话**；远端 SSH 或云端环境中的“本机”指远端环境，不能直接连接你电脑上的 Audio QC。

来源：[Claude Code 官方：Skills](https://code.claude.com/docs/en/skills)、[桌面应用说明](https://code.claude.com/docs/en/desktop)。

### Claude 网页聊天 / 桌面 Chat / Cowork

Claude 账号内上传技能的官方入口是 **Customize → Skills → + → Create skill → Upload a skill**，上传包含 `audio-qc/` 文件夹的 ZIP 并启用；需启用代码执行功能，组织账号可能由管理员控制。此入口与 Claude Code 的本地文件夹安装是两种方式。

**安装成功不代表可以连接 Audio QC。** 网页聊天和云端执行环境无法直接访问用户电脑的 `127.0.0.1`；Cowork 的隔离环境也需要额外核实本机文件及服务访问能力，目前本项目未验证兼容。当前推荐使用上面的 Claude Code 本地方式，不将网页上传列为可直接质检的方案。

来源：[Claude 官方：Use skills in Claude](https://support.claude.com/en/articles/12512180-use-skills-in-claude)。

### Windows WSL、远程或云端 Agent

WSL 内的技能路径使用 Linux 主目录，例如 `~/.agents/skills/audio-qc/` 或 `~/.claude/skills/audio-qc/`，不是 Windows 用户目录。但当前 CLI 自动查找的是所在环境的连接文件；WSL 与 Windows 的目录及本机网络边界不同，**不能仅复制 Skill 就保证连接**。Windows 用户优先使用原生本地 Agent；WSL、容器和远程环境暂不列为已验证方案。`--connection-file` 只能指定连接文件，不能自动解决网络隔离。

外部 Agent 自行管理对话历史与上下文压缩。Skill 仅提供工具用法并读取软件当前质检流程；

## CLI 示例

CLI 需要 Python 3.9+ 标准库。Windows 可将示例中的 `python3` 替换为 `py`：

```bash
python3 skills/audio-qc/scripts/qc.py status
python3 skills/audio-qc/scripts/qc.py workflow
python3 skills/audio-qc/scripts/qc.py session open --workspace "/path/to/workspace"
python3 skills/audio-qc/scripts/qc.py tools
python3 skills/audio-qc/scripts/qc.py tools audit_list_errors
python3 skills/audio-qc/scripts/qc.py call audit_list_errors --session SESSION_ID --args '{"song_path":"歌手_歌曲_扒曲人"}' --wait 10
python3 skills/audio-qc/scripts/qc.py job get JOB_ID --session SESSION_ID --wait 30
python3 skills/audio-qc/scripts/qc.py session close --session SESSION_ID
```

开始前先读 `workflow` 的返回值，再调用工具。`running` 表示尚未完成，应继续查询相同 JOB_ID；等待超时不会自动重跑操作。结束会话可释放内置 Agent 的执行入口，已保存的歌曲进度不受影响。

## 与内置 Agent 一致的内容

- `workflow` 导出的是桌面端当前实际生效的完整质检流程和公共知识索引，人工确认在当前对话中进行。服务器刷新后随内置 Agent 一起变化，Skill 不维护另一份流程。
- `version` 是生效内容的 SHA-256，`source` 标识 bundled 或 published；可据此核对当前检查依据。
- `tools` 从当前工具目录生成，按软件的高级工具开关过滤，返回真实参数 schema。
- 分工表分页、中文表头及姓名打码沿用现有实现。模型和表格密钥不通过接入接口返回。
- 检查、修复和状态树共用内置实现及歌曲进度文件。界面定位、混音台、节拍／结构叠层仍使用原界面反馈。
- 人工确认由外部 Agent 在当前对话中直接提问并等待用户回答。

## 工具覆盖

| 类别 | 工具 |
| --- | --- |
| 分工表 | sheet_query、sheet_get_song_meta |
| 歌曲定位与目录 | start_qc、fs_list_dir、read_text_file |
| 检查与修复 | audit_list_errors、fix_execute_plan |
| 进度 | state_tree_read、state_tree_update |
| 试听和人工判断 | ui_open_file、mix_load_song、playback_toggle_beat_render、playback_toggle_structure_render |
| 高级能力 | 开关启用时提供 code_run、web_scan、web_execute_js、file_read、file_write、file_patch |
| 更多编辑和音频能力 | 自动导出 `/tools/` 路由为 api_*：CSV、文本、MIDI 写入、MIDI 平移、音频元数据／波形／时长、补齐、重命名、复制、移动及删除等 |

`api_*` 参数按 `query`、`body` 分组，应先读运行时 schema。流程已有专用工具时优先使用专用工具，不绕过修复模拟或人工确认。管理端点、模型请求及密钥配置不属于导出范围。

## 会话与本机连接

接口只监听 `127.0.0.1` 的随机端口，不新增公网服务。每次运行生成连接凭证，写入用户配置目录的 `AudioQC/external-agent.json`；Unix 下文件权限为 0600。CLI 自动发现并直接连接，拒绝重定向及非本机地址。

连接文件位置和完整 REST 约定见 [Skill 接口参考](../skills/audio-qc/references/interface.md)。该文件包含访问 token，不要粘贴到对话、日志或提交到仓库。

外部会话对 Agent 执行入口使用独占锁，同一时刻只能运行一个工具；内置 Agent 有任务时不允许打开外部会话。网络中断后使用同一 request_id 与相同参数获取原调用，避免重复修改。已开始的操作会先执行完，不将“取消等待”当作回滚。

质检员手动编辑不在这个锁的范围内，不要与 Agent 同时修改相同数据。若切换工作区，外部调用将报错，需重新连接。程序退出、窗口未就绪、会话空闲过期等情况都应先确认实际状态，再重试。

## 本地开发验证

正常开发启动方式见 [开发与扩展指南](开发与扩展指南.md)。为避免影响已安装软件，可用独立端口和数据目录启动开发版本：

```bash
# 先在 app/ 运行 npm run build:app，再回到仓库根目录
CHECKER_PYTHON=/absolute/path/to/python \
CHECKER_APP_DATA_DIR=/tmp/audio-qc-dev-data \
CHECKER_SIDECAR_PORT=18775 \
CHECKER_EXTERNAL_CONNECTION=/tmp/audio-qc-dev-connection.json \
app/node_modules/.bin/electron app

python3 skills/audio-qc/scripts/qc.py --connection-file /tmp/audio-qc-dev-connection.json status
```

以上目录仅为示例，请换成自己的临时目录。开发验证与发布安装包分别进行；
