import httpx
import pytest
from fastapi.testclient import TestClient
from sidecar import config, model_catalog
from sidecar.api import app

@pytest.mark.parametrize('endpoint,expected', [
    ('https://example.com', 'https://example.com/v1/models'),
    ('https://example.com/v1', 'https://example.com/v1/models'),
    ('https://example.com/api/v1/chat/completions', 'https://example.com/api/v1/models'),
    ('https://example.com/v1/messages', 'https://example.com/v1/models'),
])
def test_models_url(endpoint, expected):
    assert model_catalog.models_url(endpoint) == expected


def transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(model_catalog.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))


def test_openai_ids_and_auth(monkeypatch):
    def handler(request):
        assert request.headers['Authorization'] == 'Bearer private-test'
        assert str(request.url) == 'https://example.com/v1/models'
        return httpx.Response(200, json={'data': [{'id': 'b'}, {'id': 'a'}, {'id': 'a'}, {'other': 'ignored'}]})
    transport(monkeypatch, handler)
    assert model_catalog.fetch_models('https://example.com/v1', 'private-test', 'openai') == ['a', 'b']


def test_anthropic_pagination(monkeypatch):
    calls = []
    def handler(request):
        assert request.headers['x-api-key'] == 'private-test'
        assert request.headers['anthropic-version']
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={'data': [{'id': 'model-a'}], 'has_more': True, 'last_id': 'model-a'})
        assert request.url.params['after_id'] == 'model-a'
        return httpx.Response(200, json={'data': [{'id': 'model-b'}], 'has_more': False})
    transport(monkeypatch, handler)
    assert model_catalog.fetch_models('https://example.com', 'private-test', 'anthropic') == ['model-a', 'model-b']


def test_list_uses_unsaved_values_and_does_not_change_config(tmp_path, monkeypatch):
    path = tmp_path / 'config.toml'
    path.write_text('', encoding='utf-8')
    monkeypatch.setenv('CHECKER_CONFIG', str(path))
    monkeypatch.setattr(config, '_cached', None)
    monkeypatch.setattr(model_catalog, 'fetch_models', lambda **kwargs: ['model-a'])
    with TestClient(app) as client:
        response = client.post('/config/llm/models', json={'endpoint': 'https://example.com', 'api_key': 'draft-secret', 'protocol': 'openai'})
        assert response.json() == {'models': ['model-a']}
        assert path.read_text(encoding='utf-8') == ''
        assert client.get('/config/llm').json()['model'] == ''
        assert client.post('/config/llm/models', json={}).status_code == 400
