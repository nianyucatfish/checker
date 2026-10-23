# 本机接口约定（protocol 1）

连接信息由运行中的桌面软件写入 `external-agent.json`，每次启动更换随机端口、实例 ID 和 token。文件默认位置：

- macOS：`~/Library/Application Support/AudioQC/external-agent.json`
- Windows：`%APPDATA%\AudioQC\external-agent.json`（程序本体仍可在便携目录）
- Linux：`${XDG_CONFIG_HOME:-~/.config}/AudioQC/external-agent.json`

可用环境变量 `CHECKER_EXTERNAL_CONNECTION` 指定文件位置。CLI 只允许连接 127.0.0.1，不跟随重定向，不走系统 HTTP 代理。运行实例不匹配或连接失败时重新确认软件状态，不沿用旧 token。

HTTP 请求需 `Authorization: Bearer <token>`；只支持本机调用，不接受浏览器 Origin。token 不等同模型 API Key 或分工表密钥，也不应复制进提示词。

| 请求 | 用途 |
| --- | --- |
| GET /v1/status | 协议、实例、工作区、外部会话与执行状态 |
| GET /v1/workflow | 当前软件实际生效的提示词、知识索引和内容哈希版本 |
| GET /v1/tools?name=… | 工具及 JSON Schema；不传 name 返回全部 |
| POST /v1/sessions | `{workspace?:绝对路径}`，创建外部执行会话并设置工作区 |
| POST /v1/calls | `{session_id, request_id, name, args}`，异步执行一次工具 |
| GET /v1/jobs/调用ID?session_id=… | 读取状态、是否等待人工及最终结果 |
| POST /v1/jobs/调用ID/cancel | `{session_id}`，标记取消请求，已开始的写入仍先执行完 |
| POST /v1/sessions/close | `{session_id}`，无运行中工具时结束会话 |

`request_id` 在会话内幂等。同一 ID 和不同参数会被拒绝。CLI 等待超时只返回进行中的 job，不取消、不重跑。工具本身可能返回 `{ok:false,...}`，不能只看 HTTP 或 job 的 completed 状态判断质检通过。

人工确认由外部 Agent 在当前对话中直接提问，等待用户真实回答。界面定位、混音和节拍叠层的结果沿用内置工具的成功／失败反馈。

`api_*` 仅暴露现有 `/tools/` 路由，不开放模型请求、密钥设置、原始全表查看等内部管理端点。查询时读 schema，例如 `api_check_song` 的参数为 `{"query":{"song_path":"…"}}`（以运行时 schema 为准）。

## 执行与限制

外部会话会阻止内置 Agent 发起新任务，但不阻止质检员在界面中手动编辑文件。不要同时操作同一份交付数据。工作区被界面切换后，调用会报错，需结束会话后重新连接正确目录。未完成的写入不会因为取消就回滚。

高级工具的开关及权限与软件一致。接口具备调用者本机用户的文件权限，不是文件系统沙箱。旧版软件没有该接口，需要升级至包含此功能的安装包。

一次应用运行最多保留 1000 条外部调用记录；任务结束及时关闭会话。软件重启后调用记录和连接凭证失效，歌曲进度及文件修改仍保留（工具调用记录由外部 Agent 保存）。发生中断时先检查文件及进度，再决定是否重试。
