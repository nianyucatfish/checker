"""sidecar.state_tree — per-song markdown state tree.

Workflow.md 的"单一真相":每首歌一个 markdown,落在 `<repo_root>/cache/state_tree/<song_slug>.md`。
**Scope = song**(2026-05-16 用户拍板,见 memory `project_state_tree_song_scoped`):
同一首歌即便换了 chat / 重启 app,进度仍在;chat 是临时的,song 的 todo 是持久的。

agent 通过 `state_tree_read` / `update` 推进进度,前端按 md 渲染 checkbox。

格式(workflow.md §状态树):
    # {song_slug}

    ## 进度
    - [ ] 1.1 分工表完整性
    - [ ] 1.2 文件夹命名 + 5 目录结构
    ...
    - [ ] 3 收尾:上传网盘 + 填链接 + 标记验收

状态值:
- `[x]` —— 完成
- `[ ]` —— 未完成(可选 ` — note` 解释原因)

`running` / `cancel` 不写进 md(瞬态)。
"""

from __future__ import annotations

import re
from pathlib import Path

from sidecar import paths


# 15 态白名单 + 标题。顺序即写入顺序。
_STATES: list[tuple[str, str]] = [
    ("1.1", "分工表完整性"),
    ("1.2", "文件夹命名 + 5 目录结构"),
    ("1.3", "各目录文件齐全度"),
    ("1.4", "文件命名归一化"),
    ("1.5", "WAV 物理格式 / 时长"),
    ("1.6", "CSV 简单格式"),
    ("1.7", "复检"),
    ("2.1", "三方对照:音源表 ↔ 混音工程文件 ↔ 分轨"),
    ("2.3", "混音台 session 1(分轨 + 总轨)"),
    ("2.4", "混音台 session 2(源文件 + 总轨)"),
    ("2.5", "MIDI vs WAV 对齐"),
    ("2.6", "渲染节奏"),
    ("2.7", "渲染结构"),
    ("2.8", "音频质量通听"),
    ("3", "收尾:上传网盘 + 填链接 + 标记验收"),
]

_VALID_STATE_IDS = frozenset(sid for sid, _ in _STATES)

# `{{file:<path>:<start>:<end>}}` 占位符。path 不含冒号(Windows 盘符例外 — 见 _expand);
# start/end 是 1-based 闭区间行号。
_FILE_REF_RE = re.compile(r"\{\{file:(?P<spec>[^}]+)\}\}")
# 单条引用展开后体积上限,超出截断 + 标注。防止 agent 引爆 md。
_FILE_REF_MAX_CHARS = 4000


def _expand_file_ref(spec: str) -> str:
    """spec = "path:start:end"。Windows 盘符如 C:\\foo:1:5 也能解析(右起切两次冒号)。

    异常一律降级为内联标注,不抛 —— note 写入流程不该因引用问题失败。
    """
    parts = spec.rsplit(":", 2)
    if len(parts) != 3:
        return f"[ref_error: spec='{spec}' 不是 path:start:end 形式]"
    raw_path, start_s, end_s = parts
    try:
        start = int(start_s)
        end = int(end_s)
    except ValueError:
        return f"[ref_error: start/end 非整数:'{start_s}'/'{end_s}']"
    if start < 1 or end < start:
        return f"[ref_error: 行号无效 start={start} end={end}]"
    path = Path(raw_path)
    if not path.is_absolute():
        # 开发模式基于仓库根；打包后由 Electron 注入只读 resource root。
        path = (paths.resource_root() / path).resolve()
    if not path.is_file():
        return f"[ref_error: 文件不存在:{path}]"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError as e:
        return f"[ref_error: 读取失败:{e}]"
    snippet = "\n".join(lines[start - 1 : end])
    if len(snippet) > _FILE_REF_MAX_CHARS:
        snippet = snippet[:_FILE_REF_MAX_CHARS] + f"\n[...truncated, total {len(snippet)} chars]"
    rel = raw_path
    return f"<!-- {{{{file:{spec}}}}} expanded -->\n```\n{snippet}\n```\n<!-- end {rel}:{start}-{end} -->"


def _expand_file_refs(text: str) -> str:
    """note / 文本字段服务端展开 {{file:path:start:end}}。无引用则原样返回。"""
    if "{{file:" not in text:
        return text
    return _FILE_REF_RE.sub(lambda m: _expand_file_ref(m.group("spec")), text)

# 匹配一行:`- [ ] 1.4 文件命名归一化 — note here`
# group(1) = " " or "x";group(2) = "1.4"(顶层态如 "3" 不带小数点);
# group(3) = "文件命名归一化"(到 — 为止 / 行尾);group(4) = note(可选)
_LINE_RE = re.compile(
    r"^- \[([ x])\] (\d+(?:\.\d+)?) (.+?)(?:\s—\s(.*))?$"
)


class StateTreeError(ValueError):
    """state_tree 输入错(state_id 非法 / 文件结构损坏等)。"""


def _cache_root() -> Path:
    """兼容旧测试钩子；实际 durable root 统一由 sidecar.paths 选择。"""
    return paths.data_dir()


def _sanitize(component: str) -> str:
    """路径段做最小安全处理:去前后空白 / 拒 path 分隔符。

    song_slug 通常 = 歌曲文件夹名(本来就是合法路径段),不应含 / \\,出现就 raise
    (避免目录穿越)。
    """
    s = component.strip()
    if not s:
        raise StateTreeError(f"路径段不能为空:{component!r}")
    if "/" in s or "\\" in s or s in (".", ".."):
        raise StateTreeError(f"路径段含非法字符:{component!r}")
    return s


def md_path(song: str) -> Path:
    """状态树 md 的绝对路径。文件存不存在不查 —— 调用方按需 init / read。

    Scope = song(不再 keyed by chat_id):同一首歌的所有 chat 共享一份 md。
    """
    return _cache_root() / "state_tree" / f"{_sanitize(song)}.md"


def _initial_content(song: str) -> str:
    lines = [f"# {song}", "", "## 进度"]
    for sid, title in _STATES:
        lines.append(f"- [ ] {sid} {title}")
    return "\n".join(lines) + "\n"


def _migrate_legacy_content(text: str) -> str:
    """把旧 17 态进度本迁成 15 态；新格式原样返回。"""
    lines = text.splitlines()
    if not any(
        (match := _LINE_RE.match(line)) and match.group(2) in ("3.1", "3.2", "3.3")
        for line in lines
    ):
        return text
    legacy: dict[str, tuple[bool, str | None]] = {}
    kept: list[str] = []
    i = 0
    while i < len(lines):
        match = _LINE_RE.match(lines[i])
        if not match or match.group(2) not in ("3.1", "3.2", "3.3"):
            line = lines[i]
            if match and match.group(2) == "2.1" and match.group(3) != dict(_STATES)["2.1"]:
                old_note = f";旧 note:{match.group(4)}" if match.group(4) else ""
                line = (
                    "- [ ] 2.1 三方对照:音源表 ↔ 混音工程文件 ↔ 分轨"
                    f" — 流程升级,新增分轨三方齐全性待补查{old_note}"
                )
            kept.append(line)
            i += 1
            continue

        state_id = match.group(2)
        note_lines = [match.group(4)] if match.group(4) else []
        i += 1
        while i < len(lines) and not _LINE_RE.match(lines[i]) and not lines[i].startswith("#"):
            note_lines.append(lines[i].removeprefix("  "))
            i += 1
        legacy[state_id] = (match.group(1) == "x", "\n".join(note_lines).strip() or None)

    all_done = len(legacy) == 3 and all(done for done, _ in legacy.values())
    details = []
    for state_id in ("3.1", "3.2", "3.3"):
        if state_id not in legacy:
            details.append(f"{state_id}旧记录缺失")
            continue
        done, note = legacy[state_id]
        details.append(f"{state_id}{'已完成' if done else '未完成'}" + (f"({note})" if note else ""))
    suffix = "" if all_done and not any(note for _, note in legacy.values()) else f" — 旧进度迁移:{';'.join(details)}"
    kept.append(f"- [{'x' if all_done else ' '}] 3 收尾:上传网盘 + 填链接 + 标记验收{suffix}")
    return "\n".join(kept) + "\n"


def init_state_tree(song: str) -> Path:
    """建文件并迁移旧格式,返回路径。幂等。父目录自动创建。"""
    p = md_path(song)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_initial_content(song), encoding="utf-8")
    else:
        text = p.read_text(encoding="utf-8")
        migrated = _migrate_legacy_content(text)
        if migrated != text:
            p.write_text(migrated, encoding="utf-8")
    return p


def read_state_tree(song: str) -> str:
    """读全文。文件不存在 → FileNotFoundError(调用方决定是否 init)。"""
    return md_path(song).read_text(encoding="utf-8")


def update_state_tree(
    song: str,
    state_id: str,
    done: bool,
    note: str | None = None,
) -> str:
    """改一行,返回更新后的全文。

    - `done` 控制 `[x]` / `[ ]`
    - `note`:None 不动现有 note;空字符串清空;非空字符串替换(支持 `{{file:...}}` 展开)

    state_id 不在白名单 → StateTreeError;文件未 init → FileNotFoundError;
    md 里找不到该 state 行 → StateTreeError(md 被外部破坏)。
    """
    if state_id not in _VALID_STATE_IDS:
        raise StateTreeError(
            f"state_id {state_id!r} 不在白名单:{sorted(_VALID_STATE_IDS)}"
        )

    p = md_path(song)
    text = p.read_text(encoding="utf-8")
    lines = text.split("\n")

    found = False
    for i, line in enumerate(lines):
        m = _LINE_RE.match(line)
        if not m or m.group(2) != state_id:
            continue
        check = "x" if done else " "
        title = m.group(3)
        existing_note = m.group(4)

        if note is None:
            new_note = existing_note
        elif note == "":
            new_note = None
        else:
            new_note = _expand_file_refs(note)

        # 找到当前 state 块的范围 [i, block_end):state 行 + 后续所有续行(展开后
        # note 可能跨多行,前一次写入会在 state 行后追加缩进/空行)。下一条 `- [` 行
        # 或 `#`/`##` heading 视为块结束。
        block_end = i + 1
        while block_end < len(lines):
            ln = lines[block_end]
            if _LINE_RE.match(ln) or ln.startswith("#"):
                break
            block_end += 1

        if new_note:
            note_lines = new_note.split("\n")
            head = note_lines[0]
            replacement = [f"- [{check}] {state_id} {title} — {head}"]
            for t in note_lines[1:]:
                replacement.append(f"  {t}" if t else "")
            lines[i:block_end] = replacement
        else:
            lines[i:block_end] = [f"- [{check}] {state_id} {title}"]
        found = True
        break

    if not found:
        raise StateTreeError(
            f"md 里找不到 state {state_id} 行,文件可能被破坏:{p}"
        )

    new_text = "\n".join(lines)
    p.write_text(new_text, encoding="utf-8")
    return new_text
