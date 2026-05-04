"""
sidecar.checker — 在 logic_checker 之上加一层结构化错误适配。

老的 LogicChecker 输出 dict[path -> list[str]]，本模块提供：
- check_song_folder(song_path) -> dict[path -> list[CheckError]]
- check_workspace(root) -> dict[path -> list[CheckError]]

实现方式：先调老接口拿字符串，再用 _parse_error_string 解析回 CheckError。
保持 logic_checker.py 本身不变（避免影响老 PyQt UI），过渡期单文件适配。

后续阶段（plan 里的 Phase 0 后段）会让 logic_checker 直接发 CheckError，本文件届时可缩成
一个再导出。
"""

import os
import re
from typing import Dict, List

from logic_checker import LogicChecker
from sidecar.errors import CheckError, ErrorCode, FixHint


# 标签到 ErrorCode 的映射（按优先匹配顺序）
_TAG_TO_CODE = {
    "命名错误": ErrorCode.FOLDER_NAME_PATTERN,
    "缺失目录": ErrorCode.MISSING_DIR,
    "多余项目": ErrorCode.EXTRA_ITEM,
    "多余文件夹": ErrorCode.EXTRA_FOLDER,
    "多余文件/格式错误": ErrorCode.EXTRA_FILE_OR_FORMAT,
    "多余文件": ErrorCode.EXTRA_FILE,
    "缺失文件": ErrorCode.MISSING_FILE,
    "类型错误": ErrorCode.TYPE_WRONG,
    "音频格式错误": ErrorCode.WAV_FORMAT_WRONG,
    "无法读取WAV": ErrorCode.WAV_READ_FAILED,
    "音频时长过短": ErrorCode.WAV_DURATION_TOO_SHORT,
    "时长不一致": ErrorCode.WAV_DURATION_INCONSISTENT,
    "伴唱文件错误": ErrorCode.BG_COMBO_INVALID,
    "内容错误": ErrorCode.FILE_EMPTY,
    "读取错误": ErrorCode.FILE_READ_FAILED,
    "表头错误": ErrorCode.CSV_HEADER_WRONG,
    "列数错误": ErrorCode.CSV_COLUMN_COUNT_WRONG,
    "时间格式错误": ErrorCode.CSV_TIME_FORMAT_WRONG,
    "格式错误": ErrorCode.MIX_PROJ_INST_NAME_EMPTY,
    "命名冗余": ErrorCode.MIX_PROJ_NUM_REDUNDANT,
    "命名缺失": ErrorCode.MIX_PROJ_NUM_MISSING,
}

_TAG_PATTERN = re.compile(r"^\[([^\]]+)\]\s*(.*)$", re.DOTALL)


def _machine_fixable(code: str) -> bool:
    """根据 code 判断该类错误是否原则上可由规则/agent 自动修复。"""
    return code in (
        ErrorCode.FOLDER_NAME_PATTERN,
        ErrorCode.EXTRA_FILE,
        ErrorCode.EXTRA_FILE_OR_FORMAT,
        ErrorCode.MISSING_FILE,           # 可能在工作区其他位置存在 orphan
        ErrorCode.MISSING_DIR,            # 创建空目录或挪动子项
        ErrorCode.CSV_HEADER_WRONG,
        ErrorCode.CSV_TIME_FORMAT_WRONG,  # mm:ss 缺零之类
        ErrorCode.MIX_PROJ_INST_NAME_EMPTY,
        ErrorCode.MIX_PROJ_NUM_REDUNDANT,
        ErrorCode.MIX_PROJ_NUM_MISSING,
        ErrorCode.BG_COMBO_INVALID,       # 通常是命名问题
    )


def _hints_for(code: str, body: str) -> list:
    """根据 code 与消息体给出常用 fix_hints。保守一点，不知道就空。"""
    hints = []
    if code in (ErrorCode.EXTRA_FILE, ErrorCode.EXTRA_FILE_OR_FORMAT, ErrorCode.FOLDER_NAME_PATTERN):
        # 命名归一化路径上的常见救济
        hints.extend([
            FixHint.FULLWIDTH_TO_HALFWIDTH,
            FixHint.NORMALIZE_WHITESPACE,
            FixHint.DASH_TO_UNDERSCORE,
            FixHint.CASE_NORMALIZE,
            FixHint.FUZZY_MATCH_WHITELIST,
        ])
    elif code == ErrorCode.MISSING_FILE:
        hints.append(FixHint.SEARCH_ORPHAN_NEARBY)
    elif code == ErrorCode.WAV_DURATION_TOO_SHORT:
        hints.append(FixHint.CANNOT_MACHINE_FIX)
    elif code == ErrorCode.WAV_FORMAT_WRONG:
        hints.append(FixHint.CANNOT_MACHINE_FIX)  # 重新导出由人完成
    return hints


# 一些 code 的 expected 解析器：从 message body 抽取结构化字段
def _expected_missing_file(body: str, path: str) -> dict:
    # 老格式："[缺失文件] {filename}" 或 "[缺失文件] 缺少 {something}"
    m = re.match(r"(?:缺少\s*)?(.+)$", body.strip())
    if m:
        return {"filename": m.group(1).strip(), "in_dir": path}
    return {"in_dir": path}


def _expected_extra_file(body: str) -> dict:
    return {"filename": body.strip()}


def _expected_wav_format() -> dict:
    return {"samplerate": 96000, "channels": 2, "subtype": "PCM_24"}


def _expected_duration_too_short(body: str) -> dict:
    # 例: "0.123s < 180s"
    m = re.match(r"([\d.]+)s\s*<\s*([\d.]+)s", body.strip())
    if m:
        return {"actual_seconds": float(m.group(1)), "min_seconds": float(m.group(2))}
    return {"min_seconds": 180.0}


def _expected_csv_time_format(body: str) -> dict:
    # 例: "第 N 行 X 应为mm:ss格式"
    m = re.match(r"第(\d+)行\s+(\S+)", body.strip())
    if m:
        return {"line_no": int(m.group(1)), "value": m.group(2), "pattern": r"^\d{2}:\d{2}$"}
    return {"pattern": r"^\d{2}:\d{2}$"}


def _expected_csv_column_count(body: str) -> dict:
    m = re.match(r"第(\d+)行不是2列|第(\d+)行应为(\d+)列", body.strip())
    if m:
        if m.group(1):
            return {"line_no": int(m.group(1)), "expected_columns": 2}
        return {"line_no": int(m.group(2)), "expected_columns": int(m.group(3))}
    return {}


def _expected_folder_name_pattern() -> dict:
    return {"pattern": r"^(.+?)_(.+?)_(.+?)$", "components": ["作者", "歌曲名", "扒谱者"]}


def _parse_error_string(path: str, msg: str) -> CheckError:
    """把老的字符串错误解析为 CheckError。"""
    m = _TAG_PATTERN.match(msg)
    if not m:
        return CheckError(
            code=ErrorCode.OTHER,
            path=path,
            message=msg,
            machine_fixable=False,
        )
    tag, body = m.group(1).strip(), m.group(2).strip()
    code = _TAG_TO_CODE.get(tag, ErrorCode.OTHER)

    expected: dict = {}
    if code == ErrorCode.MISSING_FILE:
        expected = _expected_missing_file(body, os.path.dirname(path) if not os.path.isdir(path) else path)
    elif code == ErrorCode.EXTRA_FILE:
        expected = _expected_extra_file(body)
    elif code == ErrorCode.EXTRA_FILE_OR_FORMAT:
        expected = _expected_extra_file(body)
    elif code == ErrorCode.WAV_FORMAT_WRONG:
        expected = _expected_wav_format()
    elif code == ErrorCode.WAV_DURATION_TOO_SHORT:
        expected = _expected_duration_too_short(body)
    elif code == ErrorCode.CSV_TIME_FORMAT_WRONG:
        expected = _expected_csv_time_format(body)
    elif code == ErrorCode.CSV_COLUMN_COUNT_WRONG:
        expected = _expected_csv_column_count(body)
    elif code == ErrorCode.FOLDER_NAME_PATTERN:
        expected = _expected_folder_name_pattern()
    elif code == ErrorCode.WAV_DURATION_INCONSISTENT:
        expected = {"tolerance_seconds": 0.02}
    elif code == ErrorCode.CROSS_DIR_DURATION_INCONSISTENT:
        expected = {"folders": ["分轨wav", "总轨wav", "混音工程原文件"]}

    return CheckError(
        code=code,
        path=path,
        message=msg,
        expected=expected,
        fix_hints=_hints_for(code, body),
        machine_fixable=_machine_fixable(code),
    )


# 哪些 code 在同 path 内适合聚合。这些都是按"行 / 项"逐条 add_error 的高频
# 报错点(参考 logic_checker.py:670/710/717/781),原样转发会让前端面板被几百条
# 同质消息刷屏。其他 code(多余文件、命名错误等)每条都是不同信息,不聚合。
_AGGREGATABLE_CODES = frozenset({
    ErrorCode.CSV_COLUMN_COUNT_WRONG,
    ErrorCode.CSV_TIME_FORMAT_WRONG,
})


def _format_line_summary(line_nos: List[int]) -> str:
    """把行号列表压成简短摘要。

    1 处   -> '第 3 行'
    2-4 处 -> '第 3、5、7 行'
    ≥5 处  -> '第 3、5、7 行...等共 N 处'
    """
    n = len(line_nos)
    if n == 0:
        return ""
    if n == 1:
        return f"第 {line_nos[0]} 行"
    if n <= 4:
        return f"第 {'、'.join(str(x) for x in line_nos)} 行"
    head = '、'.join(str(x) for x in line_nos[:3])
    return f"第 {head} 行...等共 {n} 处"


def _aggregate(errs: List[CheckError]) -> List[CheckError]:
    """同一 path 内把高频按行报错聚合成一条;其他 code 原样保留。

    聚合产物的 expected 带 line_nos[]/total 等字段,agent 仍可据此修复。
    """
    if not errs:
        return errs
    keep: List[CheckError] = []
    buckets: Dict[str, List[CheckError]] = {}
    for e in errs:
        if e.code in _AGGREGATABLE_CODES:
            buckets.setdefault(e.code, []).append(e)
        else:
            keep.append(e)

    for code, group in buckets.items():
        if len(group) == 1:
            keep.append(group[0])
            continue
        line_nos = sorted({
            ln for g in group
            if (ln := g.expected.get("line_no")) is not None
        })
        summary = _format_line_summary(line_nos)
        if code == ErrorCode.CSV_COLUMN_COUNT_WRONG:
            ec = group[0].expected.get("expected_columns", 2)
            tail = "不是 2 列" if ec == 2 else f"应为 {ec} 列"
            msg = f"[列数错误] {summary}{tail}"
            expected = {
                "line_nos": line_nos,
                "expected_columns": ec,
                "total": len(group),
            }
        elif code == ErrorCode.CSV_TIME_FORMAT_WRONG:
            msg = f"[时间格式错误] {summary}不符 mm:ss 格式"
            values = [g.expected.get("value") for g in group if g.expected.get("value")]
            expected = {
                "line_nos": line_nos,
                "values": values,
                "pattern": r"^\d{2}:\d{2}$",
                "total": len(group),
            }
        else:
            # 防御:新增 code 进 _AGGREGATABLE_CODES 但忘了写聚合分支时退化为原样。
            keep.extend(group)
            continue
        keep.append(CheckError(
            code=code,
            path=group[0].path,
            message=msg,
            severity=group[0].severity,
            expected=expected,
            fix_hints=group[0].fix_hints,
            machine_fixable=group[0].machine_fixable,
        ))
    return keep


def check_song_folder(song_path: str) -> Dict[str, List[CheckError]]:
    """对单首歌做全量检查，返回结构化错误。"""
    raw = LogicChecker.check_song_folder(song_path)
    out: Dict[str, List[CheckError]] = {}
    for path, msgs in raw.items():
        parsed = [_parse_error_string(path, m) for m in msgs]
        out[path] = _aggregate(parsed)
    return out


def check_workspace(root_dir: str) -> Dict[str, List[CheckError]]:
    """对整个工作区做全量检查。"""
    out: Dict[str, List[CheckError]] = {}
    if not os.path.isdir(root_dir):
        return out
    for name in sorted(os.listdir(root_dir)):
        song = os.path.join(root_dir, name)
        if not os.path.isdir(song):
            continue
        out.update(check_song_folder(song))
    return out
