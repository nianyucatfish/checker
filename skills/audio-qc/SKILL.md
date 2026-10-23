---
name: audio-qc
description: 通过正在运行的 Audio QC 桌面软件检查音频交付文件、查询分工表、处理质检问题和进度，并打开试听界面及人工确认。用于用户要求执行或继续歌曲质检时。
---

使用本目录的 `scripts/qc.py` 连接软件；需要 Python 3.9+，无需额外依赖，也不需要配置 Audio QC 的内置模型。

## 接入

以下 `QC` 表示 `python3 <本 Skill 目录>/scripts/qc.py`（Windows 可用 `py`）。先运行 `QC status` 确认软件已启动。连接文件默认自动发现；特殊安装可在命令前添加 `--connection-file <文件路径>`。连接失败时让用户打开支持外部接口的新版 Audio QC，不自行连接公网或尝试其他端口。

开始质检前运行 `QC workflow`，读取返回的 **system_prompt**、启用时的 **advanced_prompt** 及版本。这是与内置 Agent 共用的当前质检流程和知识索引，按其执行；不要用自己记忆中的流程替代，也不要读取源码里的另一份流程作为准则。新任务、软件重启或长时间暂停后重新读取，版本变化时以新内容为准。用户指定局部检查时保留其范围。

运行 `QC session open --workspace <歌曲文件夹的上一级目录>`，记下 `session_id`。该会话锁住内置 Agent 的执行入口；同一时刻只运行一个工具。结束后始终运行 `QC session close --session <ID>` 释放占用。会话空闲 30 分钟后过期；未完成调用不会因空闲而释放执行锁。

## 工具

- `QC tools`：查看当前可用工具；`QC tools <名称>`：读取精确参数 schema。
- `QC call <名称> --session <ID> --args '<JSON>' --wait 10`：提交调用。
- `QC job get <调用ID> --session <ID> --wait 30`：读取进度或最终结果。
- `QC job cancel <调用ID> --session <ID>`：申请停止等待。已经开始的操作先执行完再释放锁，取消不代表回滚。

工具参数可用 `--args @文件.json` 或 `--args -` 从 stdin 读取，避免命令行转义。输出为 JSON。调用返回 `status=running` 时继续查询同一个 job，不能把超时当失败后重新执行。提交前 CLI 会向 stderr 输出 request_id；网络中断后使用相同 `--request-id` 和完全相同参数重试，获取原调用，避免重复写入。

先用 `sheet_query` 筛选、分页查询，`sheet_get_song_meta` 查详情。检查使用 `audit_list_errors`；进度使用 `state_tree_read/update`；修复使用 `fix_execute_plan`，按实时流程规定模拟及确认。不要将上一首歌或上一页的结果套用到其他歌曲。

需要用户查看或试听时，在当前对话提醒用户切回 Audio QC，混音台可从工具栏打开。

试听使用 `ui_open_file`、`mix_load_song` 和 `playback_toggle_*`。人工判断由你在当前对话中直接询问用户，准备好试听／预览界面并说明歌曲、步骤、问题和选项后，等待用户实际回答再推进。根据用户真实回答更新质检进度。

需要常规工具之外的文件、音频、CSV 或 MIDI 能力时，查询 `api_*` 工具的 schema；这些直接复用软件现有 HTTP 操作，参数以 `query` 和 `body` 分组。优先使用流程规定的专用工具，避免绕过修复流程。Python、浏览器等高级工具只有在软件开启“解锁全部能力”后才可用，不自行修改开关或绕过限制。

面向用户使用返回的实际中文表头，保留姓名打码。分工表及模型密钥由软件保管，不读取配置文件或将连接文件、token 放进对话。接口细节及限制按需阅读 [references/interface.md](references/interface.md)。
