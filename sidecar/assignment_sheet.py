"""
分工表领域查询 —— agent 工具调到这一层。

身份隐藏
========
所有"我的"过滤都从 `config.user.reviewer_name` 读,**不通过工具参数从 LLM 传入**。
LLM 看不到当前用户姓名,这条边界是隐私设计的关键 —— 不需要加密,因为 agent
prompt / tool args / tool results 全程都不会出现"杨航"这个字符串。

列号(1-based,跟 A1 notation 对齐)见 memory/project_assignment_sheet.md。
"""

from __future__ import annotations

from dataclasses import dataclass

from sidecar.config import get_config
from sidecar.tencent_sheet import TencentSheetError, get_client


# ============================================================
#  列号常量(1-based)
# ============================================================

COL_SONG_NAME = 1               # 歌名(主键)
COL_OWNER = 2                   # 扒曲负责人
COL_ORIGINAL_SINGER = 3         # 原唱
COL_BACKING = 27                # 伴唱
COL_BACKING_GENDER = 28         # 伴唱性别
COL_PAN_OWNER_LINK = 29         # 扒曲/整首提交链接位置
COL_PAN_REVIEW_LINK = 30        # 审核人最终提交位置
COL_PAN_MIX_LINK = 31           # 录混提交链接位置
COL_PAN_MIX_REVIEW_LINK = 32    # 录混提交链接位置验收
COL_REVIEWER = 33               # 验收负责人(过滤 key)
COL_ACCEPTED = 34               # 是否验收(写入目标)

ACCEPTED_VALUE = "1"            # 已验收的标记值;非此值都视为"未验收"

# 启动时校验表头是否漂移,防止学姐改了列序我们读错列写错值
_EXPECTED_HEADERS = {
    COL_SONG_NAME: "歌名",
    COL_OWNER: "扒曲负责人",
    COL_REVIEWER: "验收负责人",
    COL_ACCEPTED: "是否验收",
}


# ============================================================
#  返回模型
# ============================================================


@dataclass
class PendingSong:
    """list_my_pending 的返回项。

    row_index: sheet 里的 1-based 行号(表头是 1,首条数据是 2)。
               写入"是否验收=1"时定位用,LLM 拿到这个值后透传回 mark_accepted 工具即可。
    """
    row_index: int
    song_name: str
    owner: str  # 扒曲负责人


# ============================================================
#  helpers
# ============================================================


def _cell(row: list[str], col_1based: int) -> str:
    """安全取列。

    腾讯返回的行末尾连续空 cell 可能被截掉,直接 row[i] 会越界;
    顺手 strip,因为表里有少量"前导空格""全角空格"的脏数据。
    """
    if 0 <= col_1based - 1 < len(row):
        return (row[col_1based - 1] or "").strip()
    return ""


def _validate_headers(header_row: list[str]) -> None:
    """启动时跑一次,列序漂了立刻让 agent 工具不可用,而不是默默读错列。"""
    drifts: list[str] = []
    for col, expected in _EXPECTED_HEADERS.items():
        actual = _cell(header_row, col)
        if actual != expected:
            drifts.append(f"col {col}: expected '{expected}', got '{actual}'")
    if drifts:
        raise TencentSheetError(
            "sheet schema drift detected: " + "; ".join(drifts)
        )


# ============================================================
#  查询函数(暴露给 agent 工具)
# ============================================================


def list_my_pending() -> list[PendingSong]:
    """列出当前用户被分配但未验收的所有歌。

    工具签名上**不带 reviewer 参数**,LLM 调它时不需要也不能指定"是谁"。
    sidecar 内部读 config.user.reviewer_name 作为过滤 key。

    判断条件:
        列 33 (验收负责人) == config.user.reviewer_name
        AND 列 34 (是否验收) != "1"

    返回顺序按 sheet 里的原始行序(行号升序)。
    """
    reviewer = get_config().user.reviewer_name.strip()
    if not reviewer:
        raise TencentSheetError(
            "user.reviewer_name not configured in config.toml; "
            "cannot determine current user"
        )

    rows = get_client().fetch_all()
    if not rows:
        return []

    _validate_headers(rows[0])

    out: list[PendingSong] = []
    # rows[1:] 是数据行;sheet 行号 = enumerate 起点 2(因为 rows[0] 是 row 1)
    for row_index, row in enumerate(rows[1:], start=2):
        if _cell(row, COL_REVIEWER) != reviewer:
            continue
        if _cell(row, COL_ACCEPTED) == ACCEPTED_VALUE:
            continue
        song_name = _cell(row, COL_SONG_NAME)
        if not song_name:
            continue  # 整行没歌名,当空行跳过
        out.append(PendingSong(
            row_index=row_index,
            song_name=song_name,
            owner=_cell(row, COL_OWNER),
        ))
    return out


def _list_my_rows(*, accepted: bool) -> tuple[list[str], list[dict]]:
    """整行版"我的歌"过滤器,共用核心。

    accepted=True 返回已验收(col 34 == "1"),False 返回待验收(其它)。
    任何分支都过滤掉空歌名行(被截断的尾部脏数据)。
    """
    reviewer = get_config().user.reviewer_name.strip()
    if not reviewer:
        raise TencentSheetError(
            "user.reviewer_name not configured in config.toml; "
            "cannot determine current user"
        )
    rows = get_client().fetch_all()
    if not rows:
        return ([], [])
    _validate_headers(rows[0])
    headers = list(rows[0])
    out: list[dict] = []
    for row_index, row in enumerate(rows[1:], start=2):
        if _cell(row, COL_REVIEWER) != reviewer:
            continue
        is_accepted = _cell(row, COL_ACCEPTED) == ACCEPTED_VALUE
        if is_accepted != accepted:
            continue
        if not _cell(row, COL_SONG_NAME):
            continue
        out.append({"row_index": row_index, "cells": list(row)})
    return (headers, out)


def list_my_pending_rows() -> tuple[list[str], list[dict]]:
    """整行版"我的待验收歌"—— (headers, [{row_index, cells}, ...])。

    给开发者 UI / debug 弹窗用,展示完整 37 列。
    agent 工具应该用 list_my_pending(返回精简的 PendingSong),不要把整行
    塞进 LLM 上下文(浪费 token)。
    """
    return _list_my_rows(accepted=False)


def list_my_accepted_rows() -> tuple[list[str], list[dict]]:
    """整行版"我的已验收歌"—— 给开发者复盘 / 抽查用。

    判定: col 34 (是否验收) == "1"。其它任何文字(包括"已审核"等状态注释)
    都视为未完成验收,不会出现在这里。
    """
    return _list_my_rows(accepted=True)
