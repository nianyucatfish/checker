"""sidecar.assignment_sheet 单测 —— validators / 派生事实 / get_song_meta 完整流程。

不打 Tencent API,通过 monkeypatch 注入 fake config + fake sheet client。
"""

from dataclasses import asdict

import pytest

from sidecar import assignment_sheet as asheet


# ============================================================
#  pure validators(无需 fixture)
# ============================================================


@pytest.mark.parametrize("name", [
    "张三", "李四", "王五六", "欧阳娜娜", "阿迪力江·阿不都拉",
])
def test_validate_chinese_name_ok(name):
    assert asheet._validate_chinese_name(name) is None


@pytest.mark.parametrize("name", [
    "TODO", "asdf", "暂定12", "John Smith", "张三 1",
])
def test_validate_chinese_name_unusual(name):
    assert asheet._validate_chinese_name(name) == "name_format_unusual"


def test_validate_chinese_name_empty_returns_none():
    assert asheet._validate_chinese_name("") is None
    assert asheet._validate_chinese_name("   ") is None


@pytest.mark.parametrize("url", [
    "https://pan.baidu.com/s/1abc",
    "http://pan.baidu.com/s/abc?pwd=xyz",
    "HTTPS://PAN.BAIDU.COM/S/X",  # 大小写
])
def test_validate_baidu_pan_url_ok(url):
    assert asheet._validate_baidu_pan_url(url) is None


def test_validate_baidu_pan_url_not_url():
    assert asheet._validate_baidu_pan_url("看群里") == "not_url"
    assert asheet._validate_baidu_pan_url("pan.baidu.com/s/1") == "not_url"


def test_validate_baidu_pan_url_wrong_domain():
    assert asheet._validate_baidu_pan_url("https://drive.google.com/x") == "not_baidu_pan"
    assert asheet._validate_baidu_pan_url("https://example.com") == "not_baidu_pan"


def test_validate_baidu_pan_url_empty_returns_none():
    assert asheet._validate_baidu_pan_url("") is None


@pytest.mark.parametrize("v", ["张三", "李四"])
def test_validate_backing_one_person(v):
    assert asheet._validate_backing_persons(v) is None


@pytest.mark.parametrize("v", ["张三/李四", "张三、李四", "张三,李四", "张三 李四"])
def test_validate_backing_two_persons_various_separators(v):
    assert asheet._validate_backing_persons(v) is None


def test_validate_backing_three_persons_rejected():
    assert asheet._validate_backing_persons("张三/李四/王五") == "more_than_two_persons"


def test_validate_backing_garbage_part():
    assert asheet._validate_backing_persons("张三/asdf") == "name_format_unusual"


def test_validate_backing_empty_returns_none():
    assert asheet._validate_backing_persons("") is None


@pytest.mark.parametrize("v,expected", [
    ("", 0),
    ("张三", 1),
    ("张三/李四", 2),
    ("张三、李四", 2),
    ("张三 李四", 2),
    ("张三/李四/王五", 2),  # 顶配封顶
])
def test_parse_backing_count(v, expected):
    assert asheet._parse_backing_count(v) == expected


def test_expected_backing_files_zero():
    assert asheet._expected_backing_files("song", 0) == []


def test_expected_backing_files_one():
    files = asheet._expected_backing_files("望春风", 1)
    assert "望春风_BG.wav" in files
    assert "望春风_BG(干声).wav" in files
    assert "望春风_BG_midi.mid" in files


def test_expected_backing_files_two():
    files = asheet._expected_backing_files("望春风", 2)
    assert "望春风_BG_A.wav" in files
    assert "望春风_BG_A(干声).wav" in files
    assert "望春风_BG_B.wav" in files
    assert "望春风_BG_B(干声).wav" in files
    assert "望春风_BG_midi.mid" in files
    assert "望春风_BG.wav" not in files


# ============================================================
#  get_song_meta 集成测试(fake config + fake sheet client)
# ============================================================


def _build_headers() -> list[str]:
    """构造满足 _validate_headers 的最小表头(37 列)。"""
    h = [""] * 37
    h[0] = "歌名"           # col 1
    h[1] = "扒曲负责人"      # col 2
    h[32] = "验收负责人"     # col 33
    h[33] = "是否验收"       # col 34
    return h


# 默认所有 23 项必填字段都填合法值;tests 通过 overrides 关闭/破坏特定字段。
_DEFAULTS = {
    "song_name":              "望春风",
    "owner":                  "张三",
    "original_singer":        "李四",
    "original_singer_gender": "男",
    "genre":                  "流行",
    "emotion":                "伤感",
    "era":                    "1990s",
    "transcribe_type":        "完整扒带",
    "transcribe_reviewer":    "审核人",
    "mix_owner":              "录混人",
    "mentor":                 "导师",
    "difficulty":             "中等",
    "tempo_changes":          "否",
    "four_pieces":            "齐全",
    "microphone":             "Neumann U87",
    "sound_card":             "Apollo Twin",
    "recording_software":     "Pro Tools",
    "mixer":                  "Behringer",
    "monitoring":             "Yamaha NS10",
    "vocal_a":                "歌甲",
    "vocal_a_gender":         "男",
    "a_score":                "8",
    "vocal_b":                "歌乙",
    "vocal_b_gender":         "女",
    "b_score":                "7",
    "backing":                "",
    "backing_gender":         "",
    "pan_review_link":        "https://pan.baidu.com/s/1xyz",
    "pan_mix_review_link":    "",
    "reviewer":               "杨航",
    "accepted":               "",
}


def _complete_row(**overrides) -> list[str]:
    """所有 23 项必填都填合法值;overrides 调整指定字段(可设空 / 设非法值测验证)。

    col → idx 映射:col N → idx N-1
    """
    f = {**_DEFAULTS, **overrides}
    row = [""] * 37
    row[0]  = f["song_name"]              # col 1
    row[1]  = f["owner"]                  # col 2
    row[2]  = f["original_singer"]        # col 3
    row[3]  = f["original_singer_gender"] # col 4
    row[4]  = f["genre"]                  # col 5
    row[5]  = f["emotion"]                # col 6
    row[6]  = f["era"]                    # col 7
    row[7]  = f["transcribe_type"]        # col 8
    row[8]  = f["transcribe_reviewer"]    # col 9
    row[9]  = f["mix_owner"]              # col 10
    row[10] = f["mentor"]                 # col 11
    row[11] = f["difficulty"]             # col 12
    row[12] = f["tempo_changes"]          # col 13
    row[13] = f["four_pieces"]            # col 14
    # idx 14 / col 15 留空(反引号占位)
    row[15] = f["microphone"]             # col 16
    row[16] = f["sound_card"]             # col 17
    row[17] = f["recording_software"]     # col 18
    row[18] = f["mixer"]                  # col 19
    row[19] = f["monitoring"]             # col 20
    row[20] = f["vocal_a"]                # col 21
    row[21] = f["vocal_a_gender"]         # col 22
    row[22] = f["a_score"]                # col 23
    row[23] = f["vocal_b"]                # col 24
    row[24] = f["vocal_b_gender"]         # col 25
    row[25] = f["b_score"]                # col 26
    row[26] = f["backing"]                # col 27
    row[27] = f["backing_gender"]         # col 28
    # idx 28 / col 29 留空(扒曲方写入,reviewer ACL)
    row[29] = f["pan_review_link"]        # col 30
    # idx 30 / col 31 留空(录混方写入)
    row[31] = f["pan_mix_review_link"]    # col 32
    row[32] = f["reviewer"]               # col 33
    row[33] = f["accepted"]               # col 34
    return row


@pytest.fixture
def fake_sheet(monkeypatch):
    """注入 fake config (reviewer=杨航) + fake sheet client。

    返回 set_rows(data_rows) —— 设置除表头外的数据行。
    """
    from sidecar import config as sidecar_config
    from sidecar import tencent_sheet

    fake_cfg = sidecar_config.Config()
    fake_cfg.user.reviewer_name = "杨航"
    monkeypatch.setattr(sidecar_config, "_cached", fake_cfg)

    class FakeClient:
        _cache = None

        def fetch_all(self, *, force=False):
            return self._cache or []

    fake = FakeClient()
    monkeypatch.setattr(tencent_sheet, "_client", fake)

    headers = _build_headers()

    def set_rows(data_rows):
        fake._cache = [headers] + data_rows

    return set_rows


def test_all_required_filled_passes(fake_sheet):
    """所有必填都填合法值 → missing/invalid 全空。"""
    fake_sheet([_complete_row()])
    meta = asheet.get_song_meta("望春风")
    assert meta.song_name == "望春风"
    assert meta.missing_required_fields == []
    assert meta.invalid_format_fields == []


@pytest.mark.parametrize("field", [
    "owner", "original_singer", "original_singer_gender",
    "genre", "emotion", "era",
    "transcribe_type", "transcribe_reviewer", "mix_owner", "mentor",
    "difficulty", "tempo_changes", "four_pieces",
    "microphone", "sound_card", "recording_software", "mixer", "monitoring",
    "vocal_a", "vocal_a_gender", "vocal_b", "vocal_b_gender",
    "pan_review_link",
])
def test_each_required_field_when_empty_appears_in_missing(fake_sheet, field):
    """逐一关闭 23 项必填字段,确认每个都能被 missing 检查抓到。"""
    fake_sheet([_complete_row(**{field: ""})])
    meta = asheet.get_song_meta("望春风")
    assert field in meta.missing_required_fields, (
        f"{field} 应该报 missing,实际 missing = {meta.missing_required_fields}"
    )


@pytest.mark.parametrize("field", [
    "a_score", "b_score", "backing", "backing_gender", "pan_mix_link",
])
def test_each_optional_field_when_empty_does_not_appear_in_missing(fake_sheet, field):
    """选填字段空时**不**该出现在 missing 里。"""
    # 注意 pan_mix_link 在 row 里走 pan_mix_review_link 列(col 32)
    override_key = "pan_mix_review_link" if field == "pan_mix_link" else field
    fake_sheet([_complete_row(**{override_key: ""})])
    meta = asheet.get_song_meta("望春风")
    assert field not in meta.missing_required_fields


@pytest.mark.parametrize("field", [
    "owner", "original_singer", "transcribe_reviewer", "mix_owner",
    "mentor", "vocal_a", "vocal_b",
])
def test_role_field_validates_chinese_name(fake_sheet, field):
    """所有"角色"字段都应该跑 chinese_name validator,catch 非中文垃圾值。"""
    fake_sheet([_complete_row(**{field: "TODO"})])
    meta = asheet.get_song_meta("望春风")
    bad = [f for f in meta.invalid_format_fields if f["field"] == field]
    assert len(bad) == 1
    assert bad[0]["reason"] == "name_format_unusual"


def test_get_song_meta_pan_link_wrong_domain(fake_sheet):
    fake_sheet([_complete_row(pan_review_link="https://drive.google.com/x")])
    meta = asheet.get_song_meta("望春风")
    bad = [f for f in meta.invalid_format_fields if f["field"] == "pan_review_link"]
    assert len(bad) == 1
    assert bad[0]["reason"] == "not_baidu_pan"


def test_get_song_meta_derived_backing_count_1(fake_sheet):
    fake_sheet([_complete_row(backing="王五")])
    meta = asheet.get_song_meta("望春风")
    assert meta.derived.backing_count == 1
    assert "望春风_BG.wav" in meta.derived.expected_backing_files


def test_get_song_meta_derived_backing_count_2(fake_sheet):
    fake_sheet([_complete_row(backing="王五/赵六")])
    meta = asheet.get_song_meta("望春风")
    assert meta.derived.backing_count == 2
    assert "望春风_BG_A.wav" in meta.derived.expected_backing_files
    assert "望春风_BG_B.wav" in meta.derived.expected_backing_files


def test_get_song_meta_derived_no_backing(fake_sheet):
    fake_sheet([_complete_row()])  # backing 默认空
    meta = asheet.get_song_meta("望春风")
    assert meta.derived.backing_count == 0
    assert meta.derived.expected_backing_files == []


def test_get_song_meta_derived_link_flags(fake_sheet):
    fake_sheet([_complete_row(
        pan_review_link="https://pan.baidu.com/s/1",
        pan_mix_review_link="",
    )])
    meta = asheet.get_song_meta("望春风")
    assert meta.derived.has_pan_review_link is True
    assert meta.derived.has_pan_mix_link is False


def test_get_song_meta_pii_is_masked(fake_sheet):
    """*** PII 边界回归:角色字段 + 链接字段值打码,original_singer 不打码 ***"""
    fake_sheet([_complete_row(
        owner="张三",
        original_singer="周杰伦",            # 公开艺人,不打码
        transcribe_reviewer="张审核",
        mix_owner="李录混",
        mentor="王导师",
        vocal_a="歌甲特殊",
        vocal_b="歌乙特殊",
        backing="王伴一/赵伴二",
        pan_review_link="https://pan.baidu.com/s/SECRET_LINK_AABBCCDDEE",
    )])
    meta = asheet.get_song_meta("望春风")
    flat = repr(asdict(meta))

    # 角色字段:真值不出现,打码版出现
    assert "张三" not in flat
    assert "张xx" in flat                  # owner masked
    assert "张审核" not in flat
    assert "李录混" not in flat
    assert "王导师" not in flat
    assert "歌甲特殊" not in flat
    assert "歌乙特殊" not in flat
    assert "王伴一" not in flat
    assert "赵伴二" not in flat
    assert "王xx/赵xx" in flat              # backing 打码后保留分隔符

    # 例外:original_singer 是公开艺人,真值原样出现
    assert "周杰伦" in flat

    # 链接:真 share key 不出现,prefix + *** 出现
    assert "SECRET_LINK_AABBCCDDEE" not in flat
    assert "https://pan.baidu.com/s/SECRET" in flat   # prefix 30 chars 保留
    assert "***" in flat


def test_mask_name_basic():
    assert asheet._mask_name("张三") == "张xx"
    assert asheet._mask_name("欧阳娜娜") == "欧xx"          # 复姓只保留首字
    assert asheet._mask_name("阿迪力江·阿不都拉") == "阿xx"   # 转写名
    assert asheet._mask_name("") == ""
    assert asheet._mask_name("   ") == ""                   # 全空白


def test_mask_url_basic():
    short = "https://x.com/a"
    assert asheet._mask_url(short) == short + "***"
    long = "https://pan.baidu.com/s/1aBcDeFgHiJkLmNoPq?pwd=xyz"
    masked = asheet._mask_url(long)
    assert masked.startswith("https://pan.baidu.com/s/")
    assert masked.endswith("***")
    assert "pwd=xyz" not in masked
    assert asheet._mask_url("") == ""


def test_mask_backing_preserves_separator():
    assert asheet._mask_backing("张三/李四") == "张xx/李xx"
    assert asheet._mask_backing("张三、李四") == "张xx、李xx"
    assert asheet._mask_backing("张三 李四") == "张xx 李xx"
    assert asheet._mask_backing("张三") == "张xx"
    assert asheet._mask_backing("") == ""


def test_pending_song_owner_is_masked(fake_sheet):
    """list_my_pending 返回项的 owner 也打码。"""
    fake_sheet([_complete_row(owner="赵某某")])
    songs = asheet.list_my_pending()
    assert len(songs) == 1
    assert songs[0].owner == "赵xx"
    assert songs[0].owner != "赵某某"


def test_get_song_meta_song_not_in_reviewer_scope(fake_sheet):
    """别的 reviewer 的歌 → 抛 TencentSheetError(SONG_NOT_FOUND 语义)。"""
    from sidecar.tencent_sheet import TencentSheetError
    fake_sheet([_complete_row(
        song_name="月亮代表我的心",
        reviewer="另一个人",
    )])
    with pytest.raises(TencentSheetError, match="不在当前用户的验收范围"):
        asheet.get_song_meta("月亮代表我的心")


def test_get_song_meta_reads_col_30_not_col_29(fake_sheet):
    """显式验证:扒曲方写到 col 29 → reviewer 视角应只看 col 30 → 漏填会报 missing。"""
    row = _complete_row(pan_review_link="")  # col 30 留空
    row[28] = "https://pan.baidu.com/扒曲方写的"  # col 29 (idx 28) 写满
    fake_sheet([row])
    meta = asheet.get_song_meta("望春风")
    assert "pan_review_link" in meta.missing_required_fields
