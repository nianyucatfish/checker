import os
import io
import json
from types import SimpleNamespace
import urllib.error
import urllib.request
import pytest
from fastapi.testclient import TestClient
from sidecar import config, tencent_sheet
from sidecar.api import app
from sidecar.remote_sheet import RemoteSheetClient, validate_server_url, NoRedirect


def payload():
    from sidecar.assignment_sheet import _EXPECTED_HEADERS
    header = [''] * 37
    for col, value in _EXPECTED_HEADERS.items(): header[col - 1] = value
    return {'rows': [header, ['song'] + [''] * 36], 'sync_ok': True, 'updated_at': '2026-10-16T00:00:00Z'}


def test_verified_https_only_and_no_redirect():
    for url in ['http://example.com', 'https://user:pass@example.com', 'https://example.com?key=x', 'https://example.com/#x']:
        with pytest.raises(ValueError): validate_server_url(url)
    assert validate_server_url('https://example.com/api/') == 'https://example.com/api'
    assert NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example') is None


def test_authorization_and_cache(monkeypatch):
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert request.get_header('Authorization') == 'Bearer private-key'
            assert 'private-key' not in request.full_url
            return io.BytesIO(json.dumps(payload()).encode())
    monkeypatch.setattr('urllib.request.build_opener', lambda *a: Opener())
    c = RemoteSheetClient('https://example.com/api', 'private-key')
    assert len(c.fetch_all()) == 2
    c.fetch_all()
    assert len(calls) == 1
    c.fetch_all(force=True)
    assert len(calls) == 2
    c.invalidate()
    c.fetch_all()
    assert len(calls) == 3


def test_failed_auth_does_not_reuse_old_cache(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, 'secret-body', {}, None)
    monkeypatch.setattr('urllib.request.build_opener', lambda *a: Opener())
    c = RemoteSheetClient('https://example.com/api', 'private-key')
    c._cache = payload()['rows']
    with pytest.raises(tencent_sheet.TencentSheetError) as e: c.fetch_all(force=True)
    assert e.value.http_status == 401 and 'secret-body' not in str(e.value)


def test_settings_restore_saved_key_for_visibility_toggle_and_reset_client(tmp_path, monkeypatch):
    path = tmp_path / 'config.toml'
    path.write_text('')
    monkeypatch.setenv('CHECKER_CONFIG', str(path))
    monkeypatch.setattr(config, '_cached', None)
    monkeypatch.setattr(tencent_sheet, '_client', None)
    c = TestClient(app)
    r = c.post('/config/tencent', json={'sheet_server_url': 'https://example.com/api', 'sheet_key': 'private-key', 'reviewer_name': '测试员'})
    assert r.status_code == 200 and r.json()['sheet_key_set']
    assert r.json()['sheet_key'] == 'private-key'
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
    first = tencent_sheet.get_client()
    r = c.post('/config/tencent', json={'reviewer_name': '测试员'})
    assert config.get_config().tencent_docs.sheet_key == 'private-key'
    assert tencent_sheet.get_client() is not first
    assert c.get('/config/tencent').json()['sheet_key'] == 'private-key'
    assert c.post('/config/tencent', json={'sheet_server_url': 'http://example.com'}).status_code == 400
    assert c.post('/config/tencent', json={'sheet_key': ''}).status_code == 200
    assert tencent_sheet.missing_config_fields() == ['sheet_key']


def test_table_view_marks_owner_and_acceptance_without_requiring_reviewer(tmp_path, monkeypatch):
    from sidecar import assignment_sheet as sheet
    path = tmp_path / 'config.toml'
    path.write_text('[user]\nreviewer_name = "测试员"\n', encoding='utf-8')
    monkeypatch.setenv('CHECKER_CONFIG', str(path))
    monkeypatch.setattr(config, '_cached', None)
    rows = payload()['rows']
    rows[1][sheet.COL_REVIEWER - 1] = '测试员'
    rows[1][sheet.COL_ACCEPTED - 1] = '1'
    other = ['other'] + [''] * 36
    rows.append(other)
    monkeypatch.setattr(sheet, '_load_rows', lambda: rows)
    c = TestClient(app)
    view = c.get('/sheet/view').json()
    assert len(view['items']) == 2 and view['reviewer_set']
    assert view['items'][0]['mine'] and view['items'][0]['accepted']
    assert not view['items'][1]['mine'] and not view['items'][1]['accepted']
    path.write_text('')
    view = c.get('/sheet/view').json()
    assert len(view['items']) == 2 and not view['reviewer_set']
    assert not any(row['mine'] for row in view['items'])


def test_server_prefers_direct_connection_and_uses_proxy_fallback(monkeypatch):
    from sidecar.remote_sheet import read_server_resource
    calls = []
    class Opener:
        def __init__(self, direct): self.direct = direct
        def open(self, request, timeout):
            if self.direct: raise urllib.error.URLError('network unavailable')
            return io.BytesIO(b'ok')
    def build(*handlers):
        direct = any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {} for h in handlers)
        calls.append(direct)
        assert any(isinstance(h, NoRedirect) for h in handlers)
        return Opener(direct)
    monkeypatch.setattr('urllib.request.build_opener', build)
    assert read_server_resource('https://example.com', 100) == b'ok'
    assert calls == [True, False]


@pytest.mark.parametrize('status', [301, 302, 401, 403, 429])
def test_auth_errors_and_redirects_do_not_trigger_proxy_retry(monkeypatch, status):
    from sidecar.remote_sheet import read_server_resource
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(1)
            raise urllib.error.HTTPError('https://example.com', status, 'error', {}, None)
    monkeypatch.setattr('urllib.request.build_opener', lambda *args: Opener())
    with pytest.raises(urllib.error.HTTPError): read_server_resource('https://example.com', 100)
    assert len(calls) == 1
