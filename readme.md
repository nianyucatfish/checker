# Audio QC

桌面端音频质检工具:从腾讯文档分工表拉"我负责验收"的歌 → 在工作区里挨个查文件命名 / 时长 / 节奏 / 结构 / 混音 → 写回"已验收"标记。当前 v2 架构 = Electron + React 前端 + Python FastAPI sidecar,Phase 4 接入 Anthropic agent。

## 高级工具（默认关闭）

聊天输入框中的“解锁全部能力”全局保存，切换会话和重启后保留。开启后才向模型展示 `code_run`、`web_scan`、`web_execute_js`、`file_read`、`file_write`、`file_patch`；关闭后新的高级调用会被执行层拒绝，运行中的脚本可点击停止中断。

这些原子能力复用 GenericAgent。Python 脚本使用 sidecar 自身的解释器，默认工作目录和临时脚本目录为开发仓库的 `temp/`（打包版为缓存目录下的 `temp/`），可通过 `CHECKER_TEMP_DIR` 覆盖。运行后自动清理脚本，其他临时产物保留。`file_read` 可读文本；`file_write` 和 `file_patch` 只修改 `temp/` 内的临时文本，歌曲交付文件继续通过 fixer。浏览器复用已连接的 GA 桥接；未安装扩展时可手动加载 `plugin/browser-bridge/`。

专用工具优先规则见 `doc/prompts/advanced_tools.md`：文件修复继续走 fixer，状态更新继续走状态树，人工判断仍需确认。开关有执行层校验，但脚本本身不是操作系统沙箱；禁止绕过流程属于模型行为约束，不是文件系统隔离。桌面控制配套与 SOP 见下方索引说明；目前不包含截图到模型的传输。

## Agent 会话与歌曲进度

一个会话可以处理单曲、多首歌或指定检查步骤。所有会话加载同一份完整质检手册，基础质检工具从开始就可用；`start_qc` 用于解析歌曲名称和定位界面，工具通过参数明确指定目标歌曲。切歌保留消息、摘要和轮数，上下文只在超过容量时裁剪。

每首歌仅保存一份 `state_tree/<歌曲文件夹名>/progress.md`，开发版位于 `cache/`，打包版位于应用数据目录（可用 `CHECKER_DATA_DIR` 覆盖）。进度跨会话共享，没有 tasks 目录；读取时自动迁移旧的 `<歌曲文件夹名>.md`，旧文件保留作为备份，新文件存在时以新文件为准。

## SOP / Skill 索引

启用个人扩展时，开发者菜单的“编辑当前任务提示词”可设置全局任务，保存到应用 userData 下的 current-task.txt。非空时插在系统介绍之后，以“当前任务：”标识；留空保存会移除该段。新旧会话均从下一次模型请求生效，重启后保留，不改写历史消息。开发环境的文件在 tmp/electron-user-data/current-task.txt。本地批次资料放 local_data/byd300（不入 Git，不随应用打包）。

公共 `sidecar/knowledge/INDEX.md` 随完整手册注入所有会话的系统提示词；索引中的路径在启动时转换为实际安装位置的绝对路径，正文按需使用 `read_text_file` 或 `file_read` 读取。新增 SOP / skill 后更新索引并重启应用。公共知识目录随 sidecar 打包；个人知识位于 local_extensions/knowledge，不提交、不随用户版打包。

个人扩展包含百度网盘专用 macOS skill、下载 SOP、GA 浏览器 SOP 和电脑操作 SOP，用户版不包含这些内容。百度网盘任务优先读取专用 skill；新版 CLI 支持 snapshot/actions/act/set-value，下载流程待客户端实测。Swift CLI 需要 Swift 和 macOS 辅助功能权限，不依赖 PyObjC；GA 备用控制库另需 Python 系统控制依赖。文档索引本身不会自动提供这些能力。

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

推荐使用独立 Conda 环境（在仓库根目录执行）：

```bash
conda env create -f environment.yml  # 首次创建
conda activate checker
cd app
npm install
npm run dev
```

环境创建后，也可以在 `app/` 直接执行 `npm run dev:conda`。开发版 Python 选择顺序：`CHECKER_PYTHON` 指定的绝对路径 → 当前非 base Conda 环境 → 仓库 `venv/` → `.venv/`。HTTP 服务、MCP 和 `code_run` 共用所选解释器；打包版仍使用内置 Python。

验证环境：`conda run -n checker python -m pytest sidecar/tests/ -q`（仓库根目录）。模型密钥、腾讯文档登录等业务配置需单独配置，不包含在 Conda 环境中。

前置:`venv` 装好 sidecar 依赖 (`pip install -r sidecar/requirements.txt`),`config.toml` 从 `config.example.toml` 复制改。

```bash
cd app
npm install     # 首次
npm run dev     # Vite + Electron 一起起,Electron main 自动 spawn sidecar
```

详见 `app/README.md` 和 `CLAUDE.md`。

## 老版本(PyQt v1)

v1 单体 PyQt 版本现保留在 git history (tag/branch 视情况而定),工作目录已全面切到 v2。

## 一份代码与个人扩展

通用质检、高级工具和会话改进共用一份代码。`local_extensions/` 被 Git 忽略，开发版检测到 `local_extensions/knowledge/INDEX.md` 时加载个人知识并显示任务提示词入口。打包版默认不加载个人扩展，也不注入已有的 current-task.txt。需要个人安装版时，将扩展保存在安装包外，并设置 `CHECKER_LOCAL_EXTENSIONS` 为扩展根目录。

用户版构建只复制公共 sidecar 和公共 prompts。包检查会拒绝 local_extensions、百度网盘 skill/SOP 及 current-task.txt。不要通过删除目录或回退公共代码切换版本。高级工具首次使用默认关闭，开关保存在应用数据目录的 agent-preferences.json，旧会话自身的开关不再覆盖应用设置。

## 从服务器读取分工表

设置 → 分工表 · 用户：填写 HTTPS 服务器、管理员下发的表格密钥及验收负责人姓名，保存后可点击“测试已保存配置”。当前服务地址为 https://118.25.191.236/api/checker/v1。密钥默认隐藏，可点击小眼睛查看，不进入 agent 上下文。用户无需配置腾讯开发者账号。

服务端项目独立维护于 /Users/catfish/fd-lamt/checker-server，负责定时读取腾讯 Open API 并发布只读快照。客户端内存缓存 5 分钟；开发者菜单可强制重新拉取。服务器凭证及共享密钥不进入用户版安装包。

### 浏览器插件源码

GA 浏览器插件位于 [plugin/browser-bridge](plugin/browser-bridge/README.md)，基于 [GenericAgent](https://github.com/lsdefine/GenericAgent)，保留 MIT 许可证。该目录可直接作为 Chrome 已解压扩展加载；来源记录、修改说明及更新方式见目录中的 README。后续发布流程应使用这份源码。

### macOS 首次打开说明

DMG 内附 [首次打开说明.txt](packaging/macos/首次打开说明.txt)，用三个步骤说明打开终端、执行命令和启动应用。该说明是普通 UTF-8 文本，不含可执行脚本。仅对来自可信渠道的安装包使用说明中的操作；正常启动时无需处理。

分工表和质检提示词请求优先直连服务器，连接异常时再尝试系统代理；两个路径都验证 HTTPS 证书并拒绝重定向。不会更改系统代理配置或 GitHub 的代理设置。
