# v2 TODO

接 agent (Phase 4) 之前先把基础功能补齐到和老 PyQt 版对齐。

## 文件树相关

- [x] **右键菜单**:重命名 / 删除 / 复制 / 粘贴 / 剪切 / 在资源管理器中显示 — `ec61325`
- [x] **右键菜单(项目级)**:自动修复本项目命名 / 统一音频长度 — `ec61325`(混音台见下)
- [x] **音频时长显示**:WAV 行末 mm:ss — `ec61325`
- [x] **同目录时长不一致染色**:整数帧比较,与错误扫描同精度 — `ec61325`
- [x] **外部文件系统同步**:chokidar 监视 + 节流批量推送 → 增量重拉 — _本次_
- [ ] **粘贴与移动**:外部文件拖入文件树、节点之间 drag-and-drop
- [ ] **多选 + 批量操作**

## 编辑器内嵌功能

- [x] **音频波形「渲染结构」**:读同名 `_Structure.csv` 在波形上叠段落 marker(参考 `editors.py:911`) — _本次_
- [x] **音频波形「渲染节奏」**:读同名 `_Beat.csv` 在波形上叠节拍线 + Web Audio 节拍器(参考 `editors.py:852`、`audio_player.py:_build_click_waveform`) — _本次_
- [ ] **MIDI 人声 WAV 对照轨道**:MIDI 预览里加载同名/同位置的 vocal WAV,在 magenta 多轨之外加一条波形轨,solo/mute,共播放头(参考 `editors.py:993` 的 MidiPreview)
- [ ] **混音台**:多轨同步播放 + 增益 + solo/mute(参考 `mix_console.py`,可能要做成独立 window 或独立路由)

## 工具栏 / 全局

- [x] 工具栏结构对齐老 PyQt:文件↓/扫描/混音台/帮助↓ — `(待 commit)`
- [x] 键盘快捷键:F2 重命名、Delete 删除、Ctrl+C/V/X — `ec61325`
- [x] 全局禁选文本(VS Code 式) — `ec61325`

> 老 PyQt 没有"批量自动修复命名 / 批量统一时长"的工具栏入口,所以新版工具栏也不放这两个按钮;
> 单首歌的修复仍走文件树右键。

## 推进顺序(草案)

**Round 1 — 高频小工程** ✅
**Round 2 — QC 工作流核心** ✅
- 渲染结构(canvas 叠层,纯视觉,简单)
- 渲染节奏(canvas 叠层 + Web Audio 节拍器,中等)

**Round 3 — 中等工程**(下一步)
- MIDI 人声 WAV 对照轨
- 混音台

**Round 4 — 收尾**
- drag-and-drop
- 多选 + 批量
