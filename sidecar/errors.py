"""
结构化错误模型。

CheckError 是 logic_checker 报错的新载荷，从字符串升级为带元数据的对象。
保留 __str__ 返回 message，使老的 PyQt UI 在 f"...{e}" 这类格式化里行为不变。

设计原则（详见 plan: 错误驱动的修复）：
- 每条错误自带 expected/fix_hints，agent 拿到一条 error 就能修，不需要读规范文档
- code 是 stable 的机器可读标识，便于前端聚类、agent 模式匹配
- expected 因 code 而异，schema 见每个 code 的注释
"""

from dataclasses import dataclass, field


class ErrorCode:
    """错误代码常量。新增条目时同步在 logic_checker 报错站点和此处。"""

    # === 文件夹 / 结构 ===
    FOLDER_NAME_PATTERN = "FOLDER_NAME_PATTERN"
    MISSING_DIR = "MISSING_DIR"
    EXTRA_ITEM = "EXTRA_ITEM"
    EXTRA_FOLDER = "EXTRA_FOLDER"
    EXTRA_FILE = "EXTRA_FILE"
    EXTRA_FILE_OR_FORMAT = "EXTRA_FILE_OR_FORMAT"
    MISSING_FILE = "MISSING_FILE"
    TYPE_WRONG = "TYPE_WRONG"

    # === WAV ===
    WAV_FORMAT_WRONG = "WAV_FORMAT_WRONG"
    WAV_READ_FAILED = "WAV_READ_FAILED"
    WAV_DURATION_TOO_SHORT = "WAV_DURATION_TOO_SHORT"
    WAV_DURATION_INCONSISTENT = "WAV_DURATION_INCONSISTENT"
    CROSS_DIR_DURATION_INCONSISTENT = "CROSS_DIR_DURATION_INCONSISTENT"

    # === 伴唱组合 ===
    BG_COMBO_INVALID = "BG_COMBO_INVALID"

    # === 文件内容 ===
    FILE_EMPTY = "FILE_EMPTY"
    FILE_READ_FAILED = "FILE_READ_FAILED"

    # === CSV ===
    CSV_HEADER_WRONG = "CSV_HEADER_WRONG"
    CSV_COLUMN_COUNT_WRONG = "CSV_COLUMN_COUNT_WRONG"
    CSV_TIME_FORMAT_WRONG = "CSV_TIME_FORMAT_WRONG"
    CSV_LABEL_INVALID = "CSV_LABEL_INVALID"

    # === 混音工程原文件 ===
    MIX_PROJ_NAME_WRONG = "MIX_PROJ_NAME_WRONG"
    MIX_PROJ_INST_NAME_EMPTY = "MIX_PROJ_INST_NAME_EMPTY"
    MIX_PROJ_NUM_REDUNDANT = "MIX_PROJ_NUM_REDUNDANT"
    MIX_PROJ_NUM_MISSING = "MIX_PROJ_NUM_MISSING"

    # === 兜底 ===
    OTHER = "OTHER"


class FixHint:
    """fix_hints 字段常用的提示标识，agent 可据此选择修复策略。可自由组合。"""

    FULLWIDTH_TO_HALFWIDTH = "fullwidth_to_halfwidth_punctuation"
    NORMALIZE_WHITESPACE = "normalize_whitespace"
    DASH_TO_UNDERSCORE = "dash_to_underscore"
    CASE_NORMALIZE = "case_normalize"
    FUZZY_MATCH_WHITELIST = "fuzzy_match_against_whitelist"
    SEARCH_ORPHAN_NEARBY = "search_orphan_with_similar_name"
    CANNOT_MACHINE_FIX = "cannot_machine_fix"


@dataclass
class CheckError:
    """单条结构化错误。

    str(error) 返回 message，便于在老 PyQt UI 的 f-string 格式化里直接用。
    to_dict() 给 sidecar API / agent prompt 使用。
    """

    code: str
    path: str
    message: str
    severity: str = "error"
    expected: dict = field(default_factory=dict)
    fix_hints: list = field(default_factory=list)
    machine_fixable: bool = False

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
            "expected": dict(self.expected),
            "fix_hints": list(self.fix_hints),
            "machine_fixable": self.machine_fixable,
        }
