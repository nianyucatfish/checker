"""Read assignment snapshots over verified HTTPS; never follow credential-bearing redirects."""
import json
import threading
import time
from datetime import datetime
import urllib.error
import urllib.parse
import urllib.request
from sidecar.tencent_sheet import TencentSheetError

DEFAULT_SHEET_SERVER = 'https://118.25.191.236/api/checker/v1'


def validate_server_url(value):
    p = urllib.parse.urlsplit(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError('分工表服务器必须使用 HTTPS 地址，且不能包含账号、密码、查询参数或片段')
    return value.rstrip('/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RemoteSheetClient:
    def __init__(self, url, key):
        self.url = validate_server_url(url)
        self.key = key
        self.spreadsheet_id = 'server-snapshot'
        self.sheet_id = 'assignment'
        self._cache = None
        self._fetched_at = None
        self._loaded = 0
        self._lock = threading.Lock()
        self.sync_ok = None
        self.updated_at = None

    @property
    def fetched_at(self): return self._fetched_at

    def fetch_all(self, *, force=False):
        with self._lock:
            if not force and self._cache is not None and time.monotonic() - self._loaded < 300:
                return self._cache
            req = urllib.request.Request(self.url + '/sheet', headers={'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json'})
            try:
                opener = urllib.request.build_opener(NoRedirect())
                with opener.open(req, timeout=20) as response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024: raise ValueError('response too large')
                payload = json.loads(raw)
                rows = payload['rows']
                if not isinstance(rows, list) or not 2 <= len(rows) <= 5000: raise ValueError('invalid rows')
                if any(not isinstance(row, list) or len(row) != 37 or any(not isinstance(c, str) for c in row) for row in rows):
                    raise ValueError('invalid dimensions')
                from sidecar.assignment_sheet import _validate_headers
                _validate_headers(rows[0])
            except urllib.error.HTTPError as e:
                messages = {401: '表格密钥无效，请在设置中更新', 429: '分工表请求过于频繁，请稍后重试', 503: '服务器分工表暂不可用'}
                raise TencentSheetError(messages.get(e.code, '分工表服务器请求失败'), http_status=e.code) from None
            except (OSError, ValueError, KeyError, TypeError):
                raise TencentSheetError('无法通过 HTTPS 获取有效分工表，请检查服务器地址、证书及网络') from None
            self._cache = rows
            self._fetched_at = datetime.now()
            self._loaded = time.monotonic()
            self.updated_at = payload.get('updated_at')
            self.sync_ok = payload.get('sync_ok')
            return self._cache

    def fetch_row(self, row_index_1based):
        rows = self.fetch_all(force=True)
        if not 1 <= row_index_1based <= len(rows): raise TencentSheetError('分工表行号超出范围')
        return rows[row_index_1based - 1]

    def invalidate(self):
        with self._lock:
            self._cache = None
            self._loaded = 0
            self._fetched_at = None
