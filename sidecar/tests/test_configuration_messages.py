import pytest
from fastapi.testclient import TestClient
from sidecar import config, tencent_sheet
from sidecar.api import app

@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / 'config.toml'
    path.write_text('', encoding='utf-8')
    monkeypatch.setenv('CHECKER_CONFIG', str(path))
    monkeypatch.setattr(config, '_cached', None)
    monkeypatch.setattr(tencent_sheet, '_client', None)
    return TestClient(app)

def test_missing_sheet_configuration_is_actionable(client):
    r = client.get('/sheet/view')
    assert r.status_code == 400
    assert '表格密钥' in r.json()['detail']
    assert 'client_id' not in r.text and 'TencentNotConfiguredError' not in r.text

@pytest.mark.parametrize('endpoint,body', [('/agent/completion', {'messages': []}), ('/chat', {'messages': []})])
def test_missing_model_configuration_is_actionable(client, endpoint, body):
    r = client.post(endpoint, json=body)
    assert r.status_code == 400
    assert '设置' in r.json()['detail'] and 'API 密钥' in r.json()['detail']
    assert 'llm.endpoint' not in r.text
