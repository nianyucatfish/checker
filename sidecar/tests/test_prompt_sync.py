import os
import io
import json
from types import SimpleNamespace
import pytest
from sidecar import prompt_sync


def test_valid_bundle_cache_and_failure_preserves_old(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt_sync, 'cache_path', lambda: tmp_path / 'prompts.json')
    monkeypatch.setattr(prompt_sync, 'get_config', lambda: SimpleNamespace(tencent_docs=SimpleNamespace(sheet_server_url='https://example.com/api', sheet_key='private')))
    bundle = {'version': 'v1', 'files': {name: '# 质检流程：逐首核对' for name in prompt_sync.FILES}}
    class Opener:
        def open(self, request, timeout):
            assert request.get_header('Authorization') == 'Bearer private'
            return io.BytesIO(json.dumps(bundle).encode())
    monkeypatch.setattr('urllib.request.build_opener', lambda *a: Opener())
    assert prompt_sync.pull() == bundle
    assert prompt_sync.cached() == bundle
    if os.name != "nt":
        assert (tmp_path / 'prompts.json').stat().st_mode & 0o077 == 0
    bundle['files'].pop('agent_system.md')
    with pytest.raises(ValueError): prompt_sync.pull()
    assert prompt_sync.cached()['version'] == 'v1'
    assert len(prompt_sync.cached()['files']) == 3
