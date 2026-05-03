# v2 TODO

接 agent (Phase 4) 之前先把基础功能补齐到和老 PyQt 版对齐。

## 文件树相关

- [x] **右键菜单**:重命名 / 删除 / 复制 / 粘贴 / 剪切 / 在资源管理器中显示 — `ec61325`
- [ ] **右键菜单(项目级)**:自动修复本项目命名 / 统一音频长度 / 添加到混音台
- [ ] **粘贴与移动**:外部文件拖入文件树、节点之间 drag-and-drop
- [x] **音频时长显示**:WAV 行末 mm:ss — `ec61325`
- [ ] **同目录时长不一致染色** — 接 `/tools/get_duration_summary`
- [ ] **多选 + 批量操作**

## 编辑器内嵌功能

- [ ] **MIDI 人声 WAV 对照轨道**:MIDI 预览里加载同名/同位置的 vocal WAV,在 magenta 多轨之外加一条波形轨,solo/mute,共播放头(参考 `editors.py:993` 的 MidiPreview)
- [ ] **音频波形「渲染节奏」**:读同名 `_Beat.csv` 在波形上叠节拍线 + 节拍器声(参考 `editors.py:852` 的 `_toggle_beat_render`)
- [ ] **音频波形「渲染结构」**:读同名 `_Structure.csv` 在波形上叠段落 marker(参考 `editors.py:911` 的 `_toggle_structure_render`)
- [ ] **混音台窗口**:多轨同步播放 + 增益 + solo/mute(参考 `mix_console.py`,可能要做成独立 window 或独立路由)

## 工具栏 / 全局

- [ ] 工具栏「自动修复命名」「统一音频长度」按钮接通(目前是空 stub,sidecar 已有 fixers)
- [x] 键盘快捷键:F2 重命名、Delete 删除、Ctrl+C/V/X — `ec61325`
- [x] 全局禁选文本(VS Code 式) — `ec61325`

## 推进顺序(草案)

**Round 1 — 高频小工程**
- 音频时长显示
- 右键菜单基础项(重命名 / 删除 / 资源管理器 / 复制 / 粘贴)
- F2 / Delete 快捷键

**Round 2 — QC 工作流核心**
- 渲染节奏 / 结构(canvas 叠层)
- 自动修复命名 / 统一音频长度(右键 + 工具栏)

**Round 3 — 中等工程**
- MIDI 人声 WAV 对照轨
- 混音台

**Round 4 — 收尾**
- drag-and-drop
- 多选 + 批量
