"""Fetch authenticated, published prompt bundles; keep last good cache on failure."""
import json
import os
from pathlib import Path
import tempfile
import urllib.request
from sidecar import paths
from sidecar.config import get_config
from sidecar.remote_sheet import NoRedirect, validate_server_url

FILES = {'agent_system.md', 'agent_workflow.md', 'advanced_tools.md'}


def validate(bundle):
    if not isinstance(bundle, dict) or not isinstance(bundle.get('version'), str): raise ValueError('未找到已发布版本')
    files = bundle.get('files')
    if not isinstance(files, dict) or set(files) != FILES: raise ValueError('提示词文件不完整')
    if any(not isinstance(s, str) or not s.strip() or len(s.encode()) > 300000 for s in files.values()): raise ValueError('提示词内容无效')
    return {'version': bundle['version'], 'files': files}


def cache_path(): return paths.cache_dir() / 'remote-prompts.json'


def cached():
    try: return validate(json.loads(cache_path().read_text()))
    except (OSError, ValueError, TypeError): return None


def pull():
    cfg = get_config().tencent_docs
    if not cfg.sheet_server_url or not cfg.sheet_key: raise ValueError('请先配置分工表服务器与表格密钥')
    url = validate_server_url(cfg.sheet_server_url) + '/prompts'
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + cfg.sheet_key})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
        raw = response.read(1000001)
    if len(raw) > 1000000: raise ValueError('提示词响应过大')
    bundle = validate(json.loads(raw))
    p = cache_path();p.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=p.parent, delete=False) as f:
            temp_name = f.name;json.dump(bundle, f, ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.replace(temp_name, p)
    finally:
        if temp_name and os.path.exists(temp_name): os.unlink(temp_name)
    return bundle
