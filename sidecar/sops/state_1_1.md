# 状态 1.1: 分工表完整性

> 本态启动前,状态机已经调过 `sheet_get_song_meta(song_name)`,完整 `SongMeta`
> (含 missing_required_fields / invalid_format_fields / derived)已注入到你的
> context。人名 / 链接字段值已打码。完整 schema 见
> `sidecar/assignment_sheet.py::SongMeta`。

## 任务

看一眼 meta,判断字段够不够 + 内容看着对不对。

- 字段齐 + 无明显异常 → 直接结束本态(pass,推进 1.2)
- 缺关键字段 或 有可疑值(参考 `missing_required_fields` / `invalid_format_fields`,
  也可自行判断)→ 调 `human_check` 升级用户去补,补完后再 pass

## 工具

- `human_check(state="1.1", items=[{"id":"filled","label":"已找扒曲补完字段"}], reason="...", ui_state=...)`
  —— 阻塞式升级用户

## 完成判定

| 触发 | result |
|---|---|
| 直接结束(无 human_check) | pass |
| `human_check` 返 pass | pass |
| `human_check` 返 cancel | cancel |

⚠ 1.1 **没有 fail** —— 没"判错"语义,只有"等用户补 / 走人"。

## 数据要点

- `original_singer`(公开艺人,如"周杰伦")不打码;其他人名是"杨xx"格式打码值,
  链接是前缀 + `***`。写消息直接用打码版即可,用户从分工表 UI 自己看真名
- `derived`(`backing_count` / `expected_backing_files` / `has_pan_*_link`)给下游态
  cross-ref 用,1.1 不消费;状态机自动写 review_log,**你不用调任何 meta 工具**
- 字段名用 sidecar 列名(`original_singer` / `pan_review_link` 等),不要翻成
  "原唱艺人姓名"等用户在 sheet 找不到的字符串
