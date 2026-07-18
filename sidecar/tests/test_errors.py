"""CheckError 模型与序列化的基本测试。"""

from sidecar.errors import CheckError, ErrorCode, FixHint


def test_str_returns_message():
    e = CheckError(code=ErrorCode.MISSING_FILE, path="/a/b", message="[缺失文件] foo.wav")
    assert str(e) == "[缺失文件] foo.wav"
    # f-string 拼接行为
    assert f"prefix {e}" == "prefix [缺失文件] foo.wav"


def test_to_dict_round_trip():
    e = CheckError(
        code=ErrorCode.WAV_FORMAT_WRONG,
        path="/a/b.wav",
        message="[音频格式错误] (采样率 44100 != 96000)",
        expected={"samplerate": 96000},
        fix_hints=[FixHint.CANNOT_MACHINE_FIX],
        machine_fixable=False,
    )
    d = e.to_dict()
    assert d["code"] == "WAV_FORMAT_WRONG"
    assert d["expected"]["samplerate"] == 96000
    assert d["fix_hints"] == ["cannot_machine_fix"]
    assert d["machine_fixable"] is False


def test_default_collections_are_independent():
    """两个 CheckError 不应共享 default_factory 创建的 list/dict（不可变默认陷阱）。"""
    a = CheckError(code=ErrorCode.OTHER, path="/a", message="a")
    b = CheckError(code=ErrorCode.OTHER, path="/b", message="b")
    a.fix_hints.append("x")
    a.expected["k"] = "v"
    assert b.fix_hints == []
    assert b.expected == {}
