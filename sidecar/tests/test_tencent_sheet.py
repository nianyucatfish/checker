"""Tencent Docs client edge cases."""

import pytest

from sidecar.tencent_sheet import TencentSheetClient, TencentSheetError


def _client() -> TencentSheetClient:
    return TencentSheetClient(
        spreadsheet_id="sid",
        sheet_id="tab",
        client_id="cid",
        access_token="token",
        open_id="openid",
    )


def test_fetch_row_count_reports_metadata_api_error(monkeypatch):
    def fake_get(_url, _headers):
        return b'{"code":400006,"message":"Authentication Internal Error"}', 200

    monkeypatch.setattr("sidecar.tencent_sheet._http_get_body", fake_get)

    with pytest.raises(TencentSheetError) as exc:
        _client()._fetch_row_count()

    assert exc.value.api_code == 400006
    assert "Authentication Internal Error" in str(exc.value)


def test_fetch_all_fetches_header_separately(monkeypatch, tmp_path):
    client = _client()
    calls = []

    monkeypatch.setattr(client, "_fetch_row_count", lambda: 3)
    monkeypatch.setattr("sidecar.tencent_sheet._disk_cache_path", lambda: tmp_path / "sheet_cache.json")

    def fake_fetch_range(a1_range):
        calls.append(a1_range)
        if a1_range == "A1:AK1":
            return [["歌名", "扒曲负责人"]]
        if a1_range == "A2:AK3":
            return [["望春风", "张三"], ["月亮", "李四"]]
        raise AssertionError(f"unexpected range: {a1_range}")

    monkeypatch.setattr(client, "_fetch_range_uncached", fake_fetch_range)

    rows = client.fetch_all(force=True)

    assert calls == ["A1:AK1", "A2:AK3"]
    assert rows == [
        ["歌名", "扒曲负责人"],
        ["望春风", "张三"],
        ["月亮", "李四"],
    ]


def test_disk_cache_path_uses_cache_override(tmp_path, monkeypatch):
    from sidecar import tencent_sheet

    monkeypatch.setenv("CHECKER_CACHE_DIR", str(tmp_path))
    assert tencent_sheet._disk_cache_path() == tmp_path / "sheet_cache.json"


def test_get_client_missing_config_raises_not_configured(monkeypatch):
    """凭证缺失 → TencentNotConfiguredError(TencentSheetError 子类,mcp 映射 SHEET_NOT_CONFIGURED)。"""
    from sidecar import config as sidecar_config
    from sidecar import tencent_sheet

    monkeypatch.setattr(sidecar_config, "_cached", sidecar_config.Config())
    monkeypatch.setattr(tencent_sheet, "_client", None)

    with pytest.raises(tencent_sheet.TencentNotConfiguredError) as exc:
        tencent_sheet.get_client()

    assert isinstance(exc.value, TencentSheetError)
    assert "client_id" in str(exc.value)
