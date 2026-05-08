# 状态 2.3: 混音台 session 1 — 分轨 + 总轨

> agent runtime 在状态切到 2.3 时把本文件注入 system prompt 末尾。

## 任务

引导用户在混音台里**一次性听完 4 件事**:对齐 / 命名↔内容对应 / 主唱静默段杂音 /
通听质量。然后通过 `human_check` 拿回三态结果。本状态自己**不做听感判断** —— 这是
人耳的事,agent 只负责"把场景搭好,把卡片弹出来,把结果记下来"。

## 4 个检查项

每项都是 ✓ / ✗ 二态。`human_check.items` 传 4 个 CheckItem:

1. **alignment** — 各分轨之间、分轨与总轨之间是否时间对齐?(听首尾 + 强拍位置)
2. **content_match** — 文件名对得上声音内容吗?(比如标 BASS 的轨听起来真是 bass 而不是 GTR)
3. **vocal_noise** — 主唱(Vocal_A / Vocal_B)的静默段(无人声段)有没有杂音 / 喘气漏录?
4. **quality** — 整体扒谱质量(分轨该有的有 / 该没的没;人声 / 伴奏的相对响度合理)

> 用户审 4 项时给的整体 result 规则:全 ✓ → result=pass,任一 ✗ → result=fail,
> agent 收到 fail 要看 `items` 字段判断哪几项挂,据此决定下一步(返工提示 / 跳过 / 升级)。

## 推荐序列

1. 调 `mix_load_song(song_path, mode="stems_plus_master")` —— 1 个工具搞定:
   开混音台 / 清旧轨 / 加载分轨wav 全部 + 总轨wav 全部 / 准备播放
2. 调 `mix_show()` —— 把混音台窗口前置(如果之前被遮住)
3. 调 `human_check(state="2.3", reason=..., items=[四项], ui_state=...)` —— 阻塞等用户
4. 收到 result:
   - `pass` —— 状态机 advance 到 2.4(混音台 session 2)
   - `fail` —— 看 items 哪些 ✗:
     - 命名↔内容不对应:可能是 1.4 漏改的命名,提示"1.4 没修干净,要回滚到 1.4 吗?"
     - 静默杂音 / 通听质量:**agent 不能自动修**(不能写音频),调 `human_notify(...)`
       提示用户去找扒曲负责人重做,然后 cancel 整个 workflow
     - 对齐挂:同上,人工返工
   - `cancel` —— 整个 workflow 中止

## 在此状态下不应使用的工具

- `fix.*`(命名 / 删 / 移 / 建目录都跟 2.3 无关)
- `audit_run_check`(已经在 1.7 复检过,不需要再扫)
- `playback.toggle_*`(那是 2.6 / 2.7 单独的态)

## 完成判定(给状态机用)

`human_check(state="2.3", ...)` 返回 `result=pass`(4 项全 ✓)。
若 `result=fail`,根据 `items` 内容,状态机要么:
- 标记当前 chat 这首歌"内容质量不达标 → workflow 中止 + human_notify",
- 或回滚到 1.4(只在"命名↔内容"挂时考虑)。

## 边界 / 可能踩坑

- **不要尝试自己听音频**:agent 没听觉能力,任何"我觉得这段听起来..."都是幻觉。判断
  全部交 human_check
- **mix_load_song 要等加载完才弹卡**:如果 ok=false 或 tracks_loaded=0,先报告
  "混音台加载失败 [原因]",不要弹 check 卡
- **fail 后不要急着重新 audit**:本状态的 fail 跟文件结构无关,不会通过 audit 修。
  绝大部分 fail 应该走"通知扒曲负责人 + cancel" 路径
- **统一音频长度(等长补 0)是 human-only**:如果用户在 check 卡里写"让我先统一一下时长",
  你只该回答"请右键混音台 → 统一音频长度;完成后再点 pass / fail",不要尝试调任何
  pad / trim 工具(根本不暴露)
