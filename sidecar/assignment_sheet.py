"""
分工表领域查询 — agent 工具调到这一层。

身份隐藏 + PII 边界
==================
1) reviewer_name 从 config 读,不通过工具参数从 LLM 传入(身份隐藏)
2) (2026-05-09 加强) 人名 / 链接字段值由后端打码后再返:
   - 人名 → "首字 + xx" (例:张三 → 张xx,欧阳娜娜 → 欧xx)
   - 链接 → 前 30 字符 + *** (例:https://pan.baidu.com/s/1aBcDe***)
   - 例外:`original_singer`(公开艺人名)不打码;非 PII 字段(性别 / 评分 / 风格 / 设备等)不打码
   LLM 看到打码值,**不应试图还原**。需要让用户找谁就直接说"杨xx"(用户从分工表 UI 自己看是谁)。
   sidecar 内部仍持有 raw 真值,供 filename 校验等不经 LLM 的检查使用。

列号常量(1-based,跟 A1 notation 对齐)见 memory/project_assignment_sheet.md。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from sidecar.config import get_config
from sidecar.tencent_sheet import TencentSheetError, get_client


class AmbiguousSongError(TencentSheetError):
    """同 reviewer 下同 song_name 多行匹配,需要 row_index 消歧。

    携带 candidates:[{row_index, song_name, owner, original_singer}],owner 已打码。
    """

    def __init__(self, song_name: str, candidates: list[dict]):
        super().__init__(
            f"歌 '{song_name}' 有 {len(candidates)} 行匹配,需要 row_index 消歧"
        )
        self.song_name = song_name
        self.candidates = candidates


# ============================================================
#  列号常量(1-based)
# ============================================================

COL_SONG_NAME = 1
COL_OWNER = 2
COL_ORIGINAL_SINGER = 3
COL_ORIGINAL_SINGER_GENDER = 4
COL_GENRE = 5
COL_EMOTION = 6                # 表头脏空格,_cell strip 后匹配
COL_ERA = 7
COL_TRANSCRIBE_TYPE = 8
COL_TRANSCRIBE_REVIEWER = 9
COL_MIX_OWNER = 10
COL_MENTOR = 11
COL_DIFFICULTY = 12
COL_TEMPO_CHANGES = 13
COL_FOUR_PIECES = 14
# col 15 跳过(memory 笔记"真表头疑被吞")
COL_MICROPHONE = 16
COL_SOUND_CARD = 17
COL_RECORDING_SOFTWARE = 18
COL_MIXER = 19
COL_MONITORING = 20
COL_VOCAL_A = 21
COL_VOCAL_A_GENDER = 22
COL_A_SCORE = 23
COL_VOCAL_B = 24
COL_VOCAL_B_GENDER = 25
COL_B_SCORE = 26
COL_BACKING = 27
COL_BACKING_GENDER = 28
COL_PAN_OWNER_LINK = 29        # ACL: reviewer 视角下读 col 30
COL_PAN_REVIEW_LINK = 30
COL_PAN_MIX_LINK = 31          # ACL: reviewer 视角下读 col 32
COL_PAN_MIX_REVIEW_LINK = 32
COL_REVIEWER = 33
COL_ACCEPTED = 34

ACCEPTED_VALUE = "1"

_EXPECTED_HEADERS = {
    COL_SONG_NAME: "歌名",
    COL_OWNER: "扒曲负责人",
    COL_REVIEWER: "验收负责人",
    COL_ACCEPTED: "是否验收",
}


# ============================================================
#  PII 打码
# ============================================================

def _mask_name(name: str) -> str:
    """人名打码:首字符 + 'xx'。空字符串保持空。

    例:
        张三 → 张xx
        欧阳娜娜 → 欧xx (复姓只保留首字,可读性 vs 精确性 取舍)
        阿迪力江·阿不都拉 → 阿xx
    """
    n = name.strip()
    if not n:
        return ""
    return n[0] + "xx"


def _normalize_zero(value: str) -> str:
    """录混方常用 "0" 表示"没录混交付"(单份),业务上等同空。

    专给 pan_mix_link 列用 —— 其他列里 "0" 可能是真数字(如评分),不要全表 normalize。
    """
    return "" if value.strip() == "0" else value


def _mask_url(url: str) -> str:
    """链接打码:**仅对格式合法的 URL 打码**,非 URL(空 / "0" / 乱填值)原样返回。

    - 空 → 原样空串
    - 不是 http(s):// 开头 → 原样返(让 reviewer 一眼看到"录混填了个 0"这种垃圾值,
      而不是被打码成 "0***" 误以为是合法链接被打了)
    - 合法 URL → 保留前 30 字符 + '***'(scheme+host+path 前缀,LLM 能识别厂商但拿不到 share key/pwd)
    """
    u = url.strip()
    if not u:
        return ""
    if not (u.lower().startswith("http://") or u.lower().startswith("https://")):
        return u
    if len(u) <= 30:
        return u + "***"
    return u[:30] + "***"


_BACKING_SEP_RE = re.compile(r"([/、,,\s]+)")


def _mask_backing(value: str) -> str:
    """伴唱字段(可能多人)逐 part 打码,保留原分隔符。

    例: 张三/李四 → 张xx/李xx
        张三、李四 → 张xx、李xx
    """
    v = value.strip()
    if not v:
        return ""
    parts = _BACKING_SEP_RE.split(v)
    out = []
    for p in parts:
        if not p:
            continue
        if _BACKING_SEP_RE.fullmatch(p):
            out.append(p)
        else:
            out.append(_mask_name(p))
    return "".join(out)


# ============================================================
#  形式 validators
# ============================================================

# 中文人名(含中点 · 用于少数民族 / 维语转写名)
_CHINESE_NAME_RE = re.compile(r"^[一-鿿·]{1,15}$")

# 多人姓名分隔符
_PERSON_SEP_RE = re.compile(r"[/、,,\s]+")


def _validate_chinese_name(value: str) -> str | None:
    """中文姓名;空字符串不报(由 missing 检查覆盖)。允许中点 ·。"""
    if not value.strip():
        return None
    if not _CHINESE_NAME_RE.match(value.strip()):
        return "name_format_unusual"
    return None


def _validate_baidu_pan_url(value: str) -> str | None:
    """合法 URL + pan.baidu.com 域。"""
    if not value.strip():
        return None
    v = value.strip().lower()
    if not (v.startswith("http://") or v.startswith("https://")):
        return "not_url"
    if "pan.baidu.com" not in v:
        return "not_baidu_pan"
    return None


def _validate_backing_persons(value: str) -> str | None:
    """伴唱字段:1-2 人,任一个 part 都要是中文名。0 人(空)接受。"""
    if not value.strip():
        return None
    parts = [p for p in _PERSON_SEP_RE.split(value.strip()) if p]
    if len(parts) > 2:
        return "more_than_two_persons"
    for p in parts:
        if not _CHINESE_NAME_RE.match(p):
            return "name_format_unusual"
    return None


# ============================================================
#  必填字段 + validator 映射
# ============================================================

# 必填 23 项(2026-05-09 用户敲定:除选填外其他必填,歌手 AB 都要)。
# 选填 5 项:a_score / b_score / backing / backing_gender / pan_mix_link
# 改清单只改这,不改 SOP。
_REQUIRED_META_FIELDS = [
    "owner",                   # col 2  扒曲负责人
    "original_singer",         # col 3  原唱
    "original_singer_gender",  # col 4  原唱性别
    "genre",                   # col 5  流派
    "emotion",                 # col 6  情感
    "era",                     # col 7  年代
    "transcribe_type",         # col 8  扒带类型
    "transcribe_reviewer",     # col 9  扒带类型审核人
    "mix_owner",               # col 10 录混负责人
    "mentor",                  # col 11 指导老师
    "difficulty",              # col 12 扒曲难度
    "tempo_changes",           # col 13 是否有速度变化
    "four_pieces",             # col 14 四大件
    "microphone",              # col 16 话筒
    "sound_card",              # col 17 声卡
    "recording_software",      # col 18 录音软件
    "mixer",                   # col 19 调音台
    "monitoring",              # col 20 监听
    "vocal_a",                 # col 21 主唱 a
    "vocal_a_gender",          # col 22 a 性别
    "vocal_b",                 # col 24 主唱 b
    "vocal_b_gender",          # col 25 b 性别
    "pan_review_link",         # col 30 审核人最终提交位置(扒曲百度链接)
]

# field name → validator function。无 validator 的字段不做形式检查,只查空。
_FIELD_VALIDATORS = {
    # 中文姓名(各种角色)
    "owner":                _validate_chinese_name,
    "original_singer":      _validate_chinese_name,
    "transcribe_reviewer":  _validate_chinese_name,
    "mix_owner":            _validate_chinese_name,
    "mentor":               _validate_chinese_name,
    "vocal_a":              _validate_chinese_name,
    "vocal_b":              _validate_chinese_name,
    # 伴唱多人特殊
    "backing":              _validate_backing_persons,
    # 链接
    "pan_review_link":      _validate_baidu_pan_url,
    "pan_mix_link":         _validate_baidu_pan_url,
}


# ============================================================
#  派生事实
# ============================================================

def _parse_backing_count(value: str) -> int:
    """空 → 0;1 part → 1;>= 2 parts → 2(顶配封顶)。"""
    if not value.strip():
        return 0
    parts = [p for p in _PERSON_SEP_RE.split(value.strip()) if p]
    return min(len(parts), 2)


def _expected_backing_files(song_name: str, backing_count: int) -> list[str]:
    """根据 backing_count 算 BG 期望文件清单(含干声 + midi)。

    数据要求.md:
    - 单伴唱(count=1): _BG.wav + _BG(干声).wav + _BG_midi.mid
    - 双伴唱(count=2): _BG_A.wav + _BG_A(干声).wav + _BG_B.wav + _BG_B(干声).wav + _BG_midi.mid
    - 无伴唱(count=0): []
    """
    if backing_count == 0:
        return []
    if backing_count == 1:
        return [
            f"{song_name}_BG.wav",
            f"{song_name}_BG(干声).wav",
            f"{song_name}_BG_midi.mid",
        ]
    return [
        f"{song_name}_BG_A.wav",
        f"{song_name}_BG_A(干声).wav",
        f"{song_name}_BG_B.wav",
        f"{song_name}_BG_B(干声).wav",
        f"{song_name}_BG_midi.mid",
    ]


# ============================================================
#  返回模型
# ============================================================

@dataclass
class PendingSong:
    """list_my_pending 的返回项。owner 字段已打码。"""
    row_index: int
    song_name: str
    owner: str       # 打码:首字 + xx (例:杨xx)


@dataclass
class DerivedFacts:
    """1.1 解析的派生事实,给下游态做 cross-ref。"""
    backing_count: int                 # 0 / 1 / 2
    expected_backing_files: list[str]  # 算自 song_name + backing_count
    has_pan_review_link: bool          # 扒曲百度链接是否填了(col 30)
    has_pan_mix_link: bool             # 录混百度链接是否填了(col 32)


@dataclass
class SongMeta:
    """1.1 验收用 + 下游交叉引用用。

    *** PII 边界 (2026-05-09) ***
    人名 / 链接字段值由 sidecar 后端打码:
    - 人名 → "首字 + xx" (例:杨xx)
    - 链接 → 前 30 字符 + ***
    例外:original_singer(公开艺人)不打码;非 PII 字段(性别 / 评分 / 风格 /
    年代 / 难度 / 设备 等)不打码,直接返。

    LLM 看到打码值,**不应试图还原**。给用户写消息直接用打码版,
    用户从分工表 UI 自己看真名 / 真链接。
    """
    row_index: int
    song_name: str

    # === 角色字段(全打码 except original_singer)===
    owner: str
    original_singer: str
    transcribe_reviewer: str
    mix_owner: str
    mentor: str
    vocal_a: str
    vocal_b: str
    backing: str

    # === 性别 / 元数据 / 评分 / 设备(无 PII,不打码)===
    original_singer_gender: str
    vocal_a_gender: str
    vocal_b_gender: str
    backing_gender: str
    a_score: str
    b_score: str
    genre: str
    emotion: str
    era: str
    transcribe_type: str
    difficulty: str
    tempo_changes: str
    four_pieces: str
    microphone: str
    sound_card: str
    recording_software: str
    mixer: str
    monitoring: str

    # === 链接(打码)===
    pan_review_link: str
    pan_mix_link: str

    # === 校验产物 + 派生事实 ===
    missing_required_fields: list[str]
    invalid_format_fields: list[dict]
    derived: DerivedFacts


# ============================================================
#  helpers
# ============================================================

def _cell(row, col_1based: int) -> str:
    """安全取列。腾讯返回行末空 cell 可能截掉,直接 row[i] 越界。"""
    if 0 <= col_1based - 1 < len(row):
        return (row[col_1based - 1] or "").strip()
    return ""


def _validate_headers(header_row) -> None:
    """启动时跑一次,列序漂了立刻让 agent 工具不可用。"""
    drifts = []
    for col, expected in _EXPECTED_HEADERS.items():
        actual = _cell(header_row, col)
        if actual != expected:
            drifts.append(f"col {col}: expected '{expected}', got '{actual}'")
    if drifts:
        raise TencentSheetError("sheet schema drift detected: " + "; ".join(drifts))


def _load_rows() -> list[list[str]]:
    fixture = get_config().agent_sandbox.sheet_fixture_path.strip()
    if fixture:
        path = Path(fixture)
        if not path.is_file():
            raise TencentSheetError(f"agent_sandbox.sheet_fixture_path 不存在:{fixture}")
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.reader(f))
    return get_client().fetch_all()


# ============================================================
#  查询函数(暴露给 agent 工具)
# ============================================================

def list_my_pending() -> list[PendingSong]:
    """列出当前 reviewer 待验收的歌。owner 字段返打码版。"""
    reviewer = get_config().user.reviewer_name.strip()
    if not reviewer:
        raise TencentSheetError(
            "user.reviewer_name not configured in config.toml; cannot determine current user"
        )

    rows = _load_rows()
    if not rows:
        return []
    _validate_headers(rows[0])

    out = []
    for row_index, row in enumerate(rows[1:], start=2):
        if _cell(row, COL_REVIEWER) != reviewer:
            continue
        if _cell(row, COL_ACCEPTED) == ACCEPTED_VALUE:
            continue
        song_name = _cell(row, COL_SONG_NAME)
        if not song_name:
            continue
        out.append(PendingSong(
            row_index=row_index,
            song_name=song_name,
            owner=_mask_name(_cell(row, COL_OWNER)),
        ))
    return out


def get_song_meta(song_name: str, row_index: int | None = None) -> SongMeta:
    """拉本歌的分工表 meta + missing/invalid + 派生事实(1.1 验收用)。

    返回的 SongMeta 中人名 / 链接字段值已打码;original_singer 与非 PII 字段不打码。
    sidecar 内部 raw 真值仅在本函数局部变量里使用,不返回上层。

    Args:
        song_name: 歌名(分工表 col 1)。
        row_index: 可选;歌名撞车时用 row_index 锁定唯一一行。撞车且未给 row_index → AmbiguousSongError。

    Raises:
        TencentSheetError: 配置缺 reviewer_name / song_name 不在当前用户范围 / API 挂等。
        AmbiguousSongError: 同 song_name 多行匹配且未传 row_index。
    """
    reviewer = get_config().user.reviewer_name.strip()
    if not reviewer:
        raise TencentSheetError(
            "user.reviewer_name not configured in config.toml; cannot determine current user"
        )

    rows = _load_rows()
    if not rows:
        raise TencentSheetError(f"分工表为空,无法定位歌 '{song_name}'")
    _validate_headers(rows[0])

    target = song_name.strip()
    matches: list[tuple[int, list[str]]] = []
    for ri, row in enumerate(rows[1:], start=2):
        if _cell(row, COL_REVIEWER) != reviewer:
            continue
        if _cell(row, COL_SONG_NAME) != target:
            continue
        matches.append((ri, row))

    if not matches:
        raise TencentSheetError(f"歌 '{song_name}' 不在当前用户的验收范围内")
    if len(matches) > 1 and row_index is None:
        # 撞车:同名多行。拿打码后的 owner / original_singer 给上层做消歧提示
        candidates = [
            {
                "row_index": ri,
                "song_name": _cell(r, COL_SONG_NAME),
                "owner": _mask_name(_cell(r, COL_OWNER)),
                "original_singer": _cell(r, COL_ORIGINAL_SINGER),  # 不打码
            }
            for ri, r in matches
        ]
        raise AmbiguousSongError(song_name=song_name, candidates=candidates)
    if row_index is not None:
        sel = next((m for m in matches if m[0] == row_index), None)
        if sel is None:
            raise TencentSheetError(f"row_index={row_index} 不在歌 '{song_name}' 的匹配行内")
        row_index_use, row = sel
    else:
        row_index_use, row = matches[0]

    # raw 真值(局部用,**不返**给 LLM)
    raw = {
        "song_name":              _cell(row, COL_SONG_NAME),
        "owner":                  _cell(row, COL_OWNER),
        "original_singer":        _cell(row, COL_ORIGINAL_SINGER),
        "original_singer_gender": _cell(row, COL_ORIGINAL_SINGER_GENDER),
        "genre":                  _cell(row, COL_GENRE),
        "emotion":                _cell(row, COL_EMOTION),
        "era":                    _cell(row, COL_ERA),
        "transcribe_type":        _cell(row, COL_TRANSCRIBE_TYPE),
        "transcribe_reviewer":    _cell(row, COL_TRANSCRIBE_REVIEWER),
        "mix_owner":              _cell(row, COL_MIX_OWNER),
        "mentor":                 _cell(row, COL_MENTOR),
        "difficulty":             _cell(row, COL_DIFFICULTY),
        "tempo_changes":          _cell(row, COL_TEMPO_CHANGES),
        "four_pieces":            _cell(row, COL_FOUR_PIECES),
        "microphone":             _cell(row, COL_MICROPHONE),
        "sound_card":             _cell(row, COL_SOUND_CARD),
        "recording_software":     _cell(row, COL_RECORDING_SOFTWARE),
        "mixer":                  _cell(row, COL_MIXER),
        "monitoring":             _cell(row, COL_MONITORING),
        "vocal_a":                _cell(row, COL_VOCAL_A),
        "vocal_a_gender":         _cell(row, COL_VOCAL_A_GENDER),
        "a_score":                _cell(row, COL_A_SCORE),
        "vocal_b":                _cell(row, COL_VOCAL_B),
        "vocal_b_gender":         _cell(row, COL_VOCAL_B_GENDER),
        "b_score":                _cell(row, COL_B_SCORE),
        "backing":                _cell(row, COL_BACKING),
        "backing_gender":         _cell(row, COL_BACKING_GENDER),
        "pan_review_link":        _cell(row, COL_PAN_REVIEW_LINK),       # col 30
        "pan_mix_link":           _normalize_zero(_cell(row, COL_PAN_MIX_REVIEW_LINK)),   # col 32; "0" 视作空(录混占位)
    }

    # 1) Missing
    missing = [f for f in _REQUIRED_META_FIELDS if not raw.get(f, "").strip()]

    # 2) Invalid format(跑 raw 真值)
    invalid = []
    for f, validator in _FIELD_VALIDATORS.items():
        reason = validator(raw[f])
        if reason:
            invalid.append({"field": f, "reason": reason})

    # 3) Derived(用 raw 真值算)
    backing_count = _parse_backing_count(raw["backing"])
    derived = DerivedFacts(
        backing_count=backing_count,
        expected_backing_files=_expected_backing_files(raw["song_name"], backing_count),
        has_pan_review_link=bool(raw["pan_review_link"].strip()),
        has_pan_mix_link=bool(raw["pan_mix_link"].strip()),
    )

    # 4) 应用 PII 打码,组装 SongMeta
    return SongMeta(
        row_index=row_index_use,
        song_name=raw["song_name"],
        owner=_mask_name(raw["owner"]),
        original_singer=raw["original_singer"],   # 公开艺人,不打码
        transcribe_reviewer=_mask_name(raw["transcribe_reviewer"]),
        mix_owner=_mask_name(raw["mix_owner"]),
        mentor=_mask_name(raw["mentor"]),
        vocal_a=_mask_name(raw["vocal_a"]),
        vocal_b=_mask_name(raw["vocal_b"]),
        backing=_mask_backing(raw["backing"]),
        original_singer_gender=raw["original_singer_gender"],
        vocal_a_gender=raw["vocal_a_gender"],
        vocal_b_gender=raw["vocal_b_gender"],
        backing_gender=raw["backing_gender"],
        a_score=raw["a_score"],
        b_score=raw["b_score"],
        genre=raw["genre"],
        emotion=raw["emotion"],
        era=raw["era"],
        transcribe_type=raw["transcribe_type"],
        difficulty=raw["difficulty"],
        tempo_changes=raw["tempo_changes"],
        four_pieces=raw["four_pieces"],
        microphone=raw["microphone"],
        sound_card=raw["sound_card"],
        recording_software=raw["recording_software"],
        mixer=raw["mixer"],
        monitoring=raw["monitoring"],
        pan_review_link=_mask_url(raw["pan_review_link"]),
        pan_mix_link=_mask_url(raw["pan_mix_link"]),
        missing_required_fields=missing,
        invalid_format_fields=invalid,
        derived=derived,
    )


def _list_my_rows(*, accepted: bool):
    """整行版"我的歌"过滤器,共用核心。dev panel 用,**不**给 agent。"""
    reviewer = get_config().user.reviewer_name.strip()
    if not reviewer:
        raise TencentSheetError(
            "user.reviewer_name not configured in config.toml; cannot determine current user"
        )

    rows = _load_rows()
    if not rows:
        return ([], [])
    _validate_headers(rows[0])
    headers = list(rows[0])
    out = []
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


def list_my_pending_rows():
    """整行版 dev panel 用 -- 不暴露给 agent。"""
    return _list_my_rows(accepted=False)


def list_my_accepted_rows():
    """整行版 dev panel 用 -- 不暴露给 agent。"""
    return _list_my_rows(accepted=True)
