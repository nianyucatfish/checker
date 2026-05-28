"""Agent sandbox integration tests."""

from pathlib import Path

from sidecar import assignment_sheet, config


def _headers() -> list[str]:
    h = [""] * 37
    h[0] = "歌名"
    h[1] = "扒曲负责人"
    h[32] = "验收负责人"
    h[33] = "是否验收"
    return h


def test_sheet_fixture_bypasses_tencent(monkeypatch, tmp_path):
    fixture = tmp_path / "sheet.csv"
    row = [""] * 37
    row[0] = "Agent测试"
    row[1] = "张三"
    row[2] = "测试歌手"
    row[3] = "男"
    row[4] = "流行"
    row[5] = "平静"
    row[6] = "2020s"
    row[7] = "完整扒带"
    row[8] = "李四"
    row[9] = "张三"
    row[10] = "王五"
    row[11] = "中等"
    row[12] = "否"
    row[13] = "齐全"
    row[15] = "Neumann U87"
    row[16] = "Apollo Twin"
    row[17] = "Pro Tools"
    row[18] = "Yamaha MG10"
    row[19] = "Yamaha HS5"
    row[20] = "赵六"
    row[21] = "男"
    row[23] = "孙七"
    row[24] = "女"
    row[29] = "https://pan.baidu.com/s/test-owner"
    row[32] = "测试员"
    fixture.write_text(
        ",".join(_headers()) + "\n" + ",".join(row) + "\n",
        encoding="utf-8-sig",
    )

    fake_cfg = config.Config()
    fake_cfg.user.reviewer_name = "测试员"
    fake_cfg.agent_sandbox.sheet_fixture_path = str(fixture)
    monkeypatch.setattr(config, "_cached", fake_cfg)

    def fail_get_client():
        raise AssertionError("Tencent client should not be used when sheet fixture is configured")

    monkeypatch.setattr(assignment_sheet, "get_client", fail_get_client)

    pending = assignment_sheet.list_my_pending()
    assert [p.song_name for p in pending] == ["Agent测试"]

    meta = assignment_sheet.get_song_meta("Agent测试")
    assert meta.song_name == "Agent测试"
    assert meta.owner == "张xx"
    assert meta.original_singer == "测试歌手"
    assert meta.missing_required_fields == []


def test_create_agent_sandbox_script_contains_no_real_credentials():
    script = Path("scripts/create_agent_sandbox.py")
    text = script.read_text(encoding="utf-8")
    assert "sheet_fixture_path" in text
    assert "api_key = \\\"\\\"" in text
    assert "access_token = \\\"\\\"" in text
