"""适配器把 logic_checker 的字符串错误解析为 CheckError 的测试。"""

from sidecar.checker import _aggregate, _format_line_summary, _parse_error_string
from sidecar.errors import CheckError, ErrorCode, FixHint


def test_missing_file():
    e = _parse_error_string("/x/分轨wav/foo.wav", "[缺失文件] 望春风_Vocal_A.wav")
    assert e.code == ErrorCode.MISSING_FILE
    assert e.expected["filename"] == "望春风_Vocal_A.wav"
    assert e.machine_fixable is True
    assert FixHint.SEARCH_ORPHAN_NEARBY in e.fix_hints


def test_extra_file_keeps_filename():
    e = _parse_error_string("/x/分轨wav/y.wav", "[多余文件] y_extra.wav")
    assert e.code == ErrorCode.EXTRA_FILE
    assert e.expected["filename"] == "y_extra.wav"
    assert e.machine_fixable is True


def test_wav_format_carries_canonical_spec():
    e = _parse_error_string("/x/y.wav", "[音频格式错误] (采样率 44100 != 96000; 声道 1 != 2)")
    assert e.code == ErrorCode.WAV_FORMAT_WRONG
    assert e.expected == {"samplerate": 96000, "channels": 2, "subtype": "PCM_24"}
    # 重导音频 agent 改不了
    assert e.machine_fixable is False
    assert FixHint.CANNOT_MACHINE_FIX in e.fix_hints


def test_duration_too_short_extracts_numbers():
    e = _parse_error_string("/x/y.wav", "[音频时长过短] 60.123s < 180s")
    assert e.code == ErrorCode.WAV_DURATION_TOO_SHORT
    assert e.expected["actual_seconds"] == 60.123
    assert e.expected["min_seconds"] == 180.0


def test_folder_name_pattern():
    e = _parse_error_string("/workspace/望春风by张三", "[命名错误] 文件夹须为 '作者_歌曲名_扒谱者'")
    assert e.code == ErrorCode.FOLDER_NAME_PATTERN
    assert "pattern" in e.expected
    assert e.machine_fixable is True


def test_csv_column_count():
    e = _parse_error_string("/x/foo_Beat.csv", "[列数错误] 第3行不是2列")
    assert e.code == ErrorCode.CSV_COLUMN_COUNT_WRONG
    assert e.expected == {"line_no": 3, "expected_columns": 2}


def test_csv_time_format():
    e = _parse_error_string("/x/foo_Structure.csv", "[时间格式错误] 第5行 12:5 应为mm:ss格式")
    assert e.code == ErrorCode.CSV_TIME_FORMAT_WRONG
    assert e.expected == {
        "line_no": 5,
        "value": "12:5",
        "pattern": r"^\d{2}:\d{2}$",
    }
    assert e.machine_fixable is True


def test_unknown_tag_falls_back_to_other():
    e = _parse_error_string("/x/y", "[新错误类型] 还没收录的标签")
    assert e.code == ErrorCode.OTHER
    assert e.machine_fixable is False


def test_no_tag_at_all():
    e = _parse_error_string("/x/y", "纯文本错误")
    assert e.code == ErrorCode.OTHER
    assert str(e) == "纯文本错误"


def test_bg_combo_invalid():
    e = _parse_error_string("/x/分轨wav", "[伴唱文件错误] 使用 BG 形式时，必须同时包含 BG 和 BG(干声)")
    assert e.code == ErrorCode.BG_COMBO_INVALID
    assert e.machine_fixable is True


# ----- 聚合 (避免问题面板被几百条同质 CSV 错刷屏) -----


def test_format_line_summary():
    assert _format_line_summary([]) == ""
    assert _format_line_summary([3]) == "第 3 行"
    assert _format_line_summary([3, 5, 7]) == "第 3、5、7 行"
    assert _format_line_summary([3, 5, 7, 9]) == "第 3、5、7、9 行"
    s = _format_line_summary([3, 5, 7, 9, 11, 13])
    assert "3、5、7" in s and "共 6 处" in s


def test_aggregate_csv_column_count_2cols():
    p = "/x/foo_Beat.csv"
    errs = [
        _parse_error_string(p, f"[列数错误] 第{i}行不是2列")
        for i in [3, 5, 7, 9, 11, 100]
    ]
    out = _aggregate(errs)
    assert len(out) == 1
    assert out[0].code == ErrorCode.CSV_COLUMN_COUNT_WRONG
    assert "等共 6 处" in out[0].message
    assert "不是 2 列" in out[0].message
    assert out[0].expected["total"] == 6
    assert out[0].expected["line_nos"] == [3, 5, 7, 9, 11, 100]
    assert out[0].expected["expected_columns"] == 2


def test_aggregate_csv_column_count_short_lists_inline():
    """≤4 处时直接列出全部行号,不显示'共 N 处'(信息更具体)"""
    p = "/x/foo_Beat.csv"
    errs = [
        _parse_error_string(p, f"[列数错误] 第{i}行不是2列") for i in (3, 5, 7)
    ]
    out = _aggregate(errs)
    assert len(out) == 1
    assert out[0].message == "[列数错误] 第 3、5、7 行不是 2 列"


def test_aggregate_csv_column_count_n_cols_structure():
    p = "/x/foo_Structure.csv"
    errs = [
        _parse_error_string(p, f"[列数错误] 第{i}行应为3列") for i in (4, 5)
    ]
    out = _aggregate(errs)
    assert len(out) == 1
    assert "应为 3 列" in out[0].message
    assert out[0].expected["expected_columns"] == 3


def test_aggregate_csv_time_format():
    p = "/x/foo_Structure.csv"
    errs = [
        _parse_error_string(p, f"[时间格式错误] 第{i}行 {v} 应为mm:ss格式")
        for i, v in [(5, "12:5"), (8, "1:23"), (12, "00:7"), (15, "0:45"), (20, "3:5")]
    ]
    out = _aggregate(errs)
    assert len(out) == 1
    assert out[0].code == ErrorCode.CSV_TIME_FORMAT_WRONG
    assert "等共 5 处" in out[0].message
    assert "mm:ss" in out[0].message
    assert out[0].expected["values"] == ["12:5", "1:23", "00:7", "0:45", "3:5"]


def test_aggregate_single_does_not_collapse():
    """只有 1 条的话保留原始 message(信息更具体,不需要聚合)"""
    p = "/x/foo_Beat.csv"
    errs = [_parse_error_string(p, "[列数错误] 第3行不是2列")]
    out = _aggregate(errs)
    assert len(out) == 1
    assert out[0].message == "[列数错误] 第3行不是2列"  # 原 message 保留


def test_aggregate_keeps_other_codes_intact():
    """非聚合 code(多余文件 / 命名错误等)每条信息独立,不应合并"""
    p = "/x/foo"
    errs = [
        CheckError(code=ErrorCode.EXTRA_FILE, path=p, message="[多余文件] a.wav"),
        CheckError(code=ErrorCode.EXTRA_FILE, path=p, message="[多余文件] b.wav"),
        CheckError(code=ErrorCode.EXTRA_FILE, path=p, message="[多余文件] c.wav"),
    ]
    out = _aggregate(errs)
    assert len(out) == 3
