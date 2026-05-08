# 状态 1.4: 文件命名归一化

> 双用途文档:既是开发者读的设计文档,也是 agent runtime 在状态切到 1.4 时
> 注入到 system prompt 末尾的"当前任务说明"。
> 内容应该既可读又简洁,LLM 看完就能知道在这个状态下该干嘛。

## 任务

修复歌曲文件夹及内部文件的命名问题,让 audit_run_check 不再报
`FOLDER_NAME_PATTERN` / `EXTRA_FILE` / `EXTRA_FILE_OR_FORMAT` /
`BG_COMBO_INVALID` / `MIX_PROJ_NAME_WRONG` / `MIX_PROJ_INST_NAME_EMPTY` /
`MIX_PROJ_NUM_REDUNDANT` / `MIX_PROJ_NUM_MISSING` 这几类错误。

## 命名规范(节选自 数据要求.md)

### 歌曲文件夹
格式: `{作者}_{歌曲名}_{扒谱者}` 或录混版本 `{歌手名}_{歌曲名}_{交付人名}(混)`

### 分轨 wav(`分轨wav/` 目录下)
- BASS / DR / GTR / PNO / OTHER 任选(若有): `{歌曲名}_{类型}.wav`
- 主唱: `{歌曲名}_Vocal_A.wav` / `_Vocal_A(干声).wav` / `_Vocal_B.wav` / `_Vocal_B(干声).wav`
- 伴唱(单伴唱): `{歌曲名}_BG.wav` / `_BG(干声).wav`
- 伴唱(双伴唱): `_BG_A.wav` / `_BG_A(干声).wav` / `_BG_B.wav` / `_BG_B(干声).wav`

### 总轨 wav(`总轨wav/` 目录下)
- `{歌曲名}_Mix_A.wav` / `_Mix_B.wav`

### MIDI(`midi/` 目录下)
- `{歌曲名}_Vocal_midi.mid`(必有)
- `{歌曲名}_BG_midi.mid`(若有伴唱)
- `{歌曲名}_Mix_midi.mid`(必有)

### CSV(`csv/` 目录下)
- `{歌曲名}_Beat.csv` / `{歌曲名}_Structure.csv`

### 混音工程原文件(`混音工程原文件/` 目录下)
- DAW 项目文件:`.flp` / `.logicx` / `.cpr` 等
- 未合并音频:
  - 单音源单轨道:`{歌曲名}_{乐器名}.wav`(如 `玫瑰_弦乐组.wav`)
  - 单音源多轨道:`{歌曲名}_{乐器}{音源序号}_{轨道序号}.wav`
  - 多音源单轨道:`{歌曲名}_{乐器}{音源序号}.wav`(单音源时也可省略序号)

> 命名共同约束:所有非汉字字符均用英文输入法(括号 / 下划线等),不允许全角

## 推荐序列

1. 调 `audit_run_check(song_path)` —— 看 by_code 里有哪些命名相关错误
2. 调 `fix_propose_rename_plan(song_path)` —— 拿规则生成的批量改名建议
3. 评估建议:
   - 大部分情况下 propose 输出可以全收
   - 如果有规则没识别的特殊情况(比如歌曲名里有"-"该改"_"),自构造 RenameOp 补充
   - 如果有多余文件该删而不是改名(比如临时备份文件),自构造 DeleteOp
4. 提交合并后的 ops 给 `ui_show_confirm_card(ops)` —— 等用户审批
5. 用户批准后调 `fix_execute_plan(approved_ops, workspace_root)` 落盘
6. 复跑 `audit_run_check` 确认上述类型错误清零

## 在此状态下不应使用的工具(软约束)

- `mix.*` / `playback.*`(那是 2.x 听感阶段)
- `sheet.write_baidu_link` / `sheet.mark_accepted`(那是 3.x 收尾)
- `human.check`(命名问题不需要主观判断,本状态只在 confirm 卡里跟用户互动)

## 完成判定(给状态机用)

`audit_run_check` 复检时 by_code 里以下 8 个 code 的计数都为 0:
- FOLDER_NAME_PATTERN
- EXTRA_FILE / EXTRA_FILE_OR_FORMAT
- BG_COMBO_INVALID
- MIX_PROJ_NAME_WRONG / MIX_PROJ_INST_NAME_EMPTY
- MIX_PROJ_NUM_REDUNDANT / MIX_PROJ_NUM_MISSING

满足后状态机自动 advance 到 1.5。

## 边界 / 可能踩坑

- **跨 1.4 / 1.3 的边界**:有些"缺失文件"是因为命名错被识别成多余文件。先做 1.3
  的 move,再做 1.4 的 rename;状态机已经按这个顺序。所以本状态进入时,文件都
  应该在它该在的目录里,只是名字不对
- **混音工程文件的乐器命名是中文**:不要尝试翻译成英文,数据要求明确写"都用中文"
- **propose_rename_plan 的输出可能有 conflict**(目标文件名重复):看 conflicts 列表,人
  工帮 LLM 决定哪个保留哪个改成 _2 之类
- **如果用户在 confirm 卡里 reject 了部分 op**:不要重新 propose,接受用户判断,直接
  下一步 audit 复检看缺啥再决定
