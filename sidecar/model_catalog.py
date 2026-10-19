"""Read provider model IDs without requiring or changing a selected model."""
import time
from urllib.parse import urlsplit, urlunsplit
import httpx
from sidecar import llm_providers as llm


def models_url(endpoint):
    url = urlsplit(endpoint.strip())
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('请填写有效的模型服务地址，不要包含账号、密码或查询参数。')
    path = url.path.rstrip('/')
    for suffix in ('/chat/completions', '/messages', '/models'):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    if not path.endswith('/v1'): path += '/v1'
    return urlunsplit((url.scheme, url.netloc, path + '/models', '', ''))


def fetch_models(endpoint, api_key, protocol):
    url = models_url(endpoint)
    headers = llm.anthropic_headers(api_key) if protocol == 'anthropic' else llm.openai_headers(api_key)
    models = set()
    cursor = None
    seen = set()
    deadline = time.monotonic() + 25
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        for _ in range(10):
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise ValueError('获取模型列表超时，请稍后重试或手动填写模型名称。')
            params = {'limit': 1000} if protocol == 'anthropic' else {}
            if cursor: params['after_id'] = cursor
            with client.stream('GET', url, headers=headers, params=params, timeout=min(10, remaining)) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2_000_000: raise ValueError('模型列表过大，请手动填写模型名称。')
                import json
                payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
                raise ValueError('该服务未返回有效的模型列表，请手动填写模型名称。')
            for item in payload['data']:
                if isinstance(item, dict) and isinstance(item.get('id'), str) and item['id'].strip(): models.add(item['id'])
            if not payload.get('has_more'): return sorted(models)
            cursor = payload.get('last_id')
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError('该服务的模型列表分页异常，请手动填写模型名称。')
            seen.add(cursor)
    raise ValueError('模型列表分页过多，请手动填写模型名称。')
