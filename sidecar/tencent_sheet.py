"""
腾讯文档 V3 spreadsheet 客户端 + 进程内缓存。

设计要点
========
- **整表 bulk 缓存**:首次请求时分页拉满全部 37 列 × ≤ 1000 行,存内存。
  之后所有领域查询都从内存读,API 用量极低(典型 4 次 / 次冷启动)。
- **不设 TTL**:缓存只在三种情况更新 ——
    1. 显式 `fetch_all(force=True)`(开发者菜单"强制刷新")
    2. 写入后 `fetch_row(idx)`(单行重拉,只覆盖一行)
    3. 磁盘 cache 文件被删 / spreadsheet_id 变更
- **磁盘 cache**:`<repo>/cache/sheet_cache.json` 持久保存最近一次拉取结果。
  sidecar 重启 / `reset_client()` 后:fetch_all(force=False) 优先读盘,
  没有再去 API。开发期反复重启 sidecar 不会烧 API 配额。
- **不假设排序 / 行数**:`fetch_all` 边拉边判最后一块,直到 chunk 短于预期。

API 限制
========
- 单次 GET range:行 ≤ 1000,列 ≤ 200,总 cells ≤ 10000
- 37 列 × 270 行 = 9990 cells,所以分页用 270 行 / chunk
- 每天 200 次调用配额(用户当前等级)

Endpoint
========
GET /openapi/spreadsheet/v3/files/{spreadsheet_id}/{sheet_id}/{a1_range}
Headers: Client-Id / Access-Token / Open-Id
响应 envelope 实测有变体,这里都尝试解一下:
  - {"data": {"gridData": {...}}, "ret": 0, "msg": "Succeed"}     (官方文档)
  - {"gridData": {...}}                                            (无包裹,旧 probe 见过)
  - {"code": 4xxxxx, "message": "..."}                             (错误也有用 ret/msg 的)
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from sidecar.config import get_config


# 表的固定形状
_TOTAL_COLS = 37
_LAST_COL_LETTER = "AK"   # 第 37 列,A1 notation 用
_CHUNK_ROWS = 270         # 单次拉的最大行数(37 * 270 = 9990 cells, 留一点裕度)
_MAX_ROWS_HARD_CAP = 5000  # 防御性兜底,防止表长意外炸成无限循环

_BASE_URL = "https://docs.qq.com/openapi/spreadsheet/v3/files"
_HTTP_TIMEOUT_SEC = 30
_HTTP_MAX_RETRIES = 2  # 总尝试次数 = 1 首发 + 1 重试


def _http_get_body(url: str, headers: dict[str, str]) -> tuple[bytes, int]:
    """GET 一个 URL,返回 (body_bytes, http_status)。

    封装两层鲁棒性,专治腾讯 V3 的两个常见症状:
      1. **IncompleteRead**: 服务端 chunked-encoding 末帧没发,但 body 通常已完整。
         此时 `urllib` 把已读字节塞进 e.partial,我们直接用,不当错。
      2. **偶发 5xx / 网络抖动**: 重试一次。

    HTTPError 仍然返回(body + 状态码),交给上层判 api error;真正网络层
    URLError 才抛 TencentSheetError。
    """
    last_exc: Exception | None = None
    for attempt in range(_HTTP_MAX_RETRIES):
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
                http_status = resp.status
                try:
                    body = resp.read()
                except http.client.IncompleteRead as e:
                    # body 通常仍是完整 JSON,直接用 partial
                    body = e.partial
                return body, http_status
        except urllib.error.HTTPError as e:
            # 4xx / 5xx 也带 body,交给 caller 判 ret/code
            try:
                body = e.read()
            except http.client.IncompleteRead as ie:
                body = ie.partial
            return body, e.code
        except urllib.error.URLError as e:
            last_exc = e
            if attempt + 1 < _HTTP_MAX_RETRIES:
                time.sleep(0.3 * (attempt + 1))
                continue
            raise TencentSheetError(f"network error: {e.reason}") from e
        except http.client.IncompleteRead as e:
            # 极少数情况 IncompleteRead 从 with 块外抛(连接级别),也兜底
            if e.partial:
                return e.partial, 200
            last_exc = e
            if attempt + 1 < _HTTP_MAX_RETRIES:
                time.sleep(0.3 * (attempt + 1))
                continue
            raise TencentSheetError(f"incomplete read with no partial data") from e
    # 理论不可达
    raise TencentSheetError(f"http get exhausted retries: {last_exc!r}")


def _disk_cache_path() -> Path:
    """磁盘 cache 文件位置: <repo_root>/cache/sheet_cache.json

    放仓库内方便开发期手动查看 / 删除;.gitignore 已包含 cache/。
    打包后如要换路径,改这一处即可(不影响调用方)。
    """
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "cache" / "sheet_cache.json"


class TencentSheetError(Exception):
    """凭证 / 网络 / 表结构等异常的统一类型。

    携带 http_status 和 api_code,上层(api.py / agent 工具)能据此分流:
      - http 401 / api 400006 → 凭证过期,提示用户重置 token
      - http 429 / api 400007 → 限频,稍后重试
      - 其它 → 通用错误信息
    """

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        api_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.api_code = api_code


# ============================================================
#  Client
# ============================================================


@dataclass
class TencentSheetClient:
    spreadsheet_id: str
    sheet_id: str
    client_id: str
    access_token: str
    open_id: str

    _cache: Optional[list[list[str]]] = field(default=None, init=False, repr=False)
    _fetched_at: Optional[datetime] = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # ------- public -------

    @property
    def fetched_at(self) -> Optional[datetime]:
        """上次完成 bulk fetch 的时间。UI 角标用('上次同步: 14:32')。"""
        return self._fetched_at

    def fetch_all(self, *, force: bool = False) -> list[list[str]]:
        """拉满整表,缓存到 self._cache;返回 list[list[str]],索引 0 = 表头(row 1)。

        优先级:内存 cache > 磁盘 cache > API 拉取。
        force=True 直接打 API,跳过两层 cache(开发者菜单"强制刷新"用)。

        非 thread-safe-on-result:返回的是内部 list 的引用,调用方别就地改它。
        """
        with self._lock:
            if self._cache is not None and not force:
                return self._cache
            if not force:
                # 没内存 cache,先看磁盘有没有上次保存的;命中就直接装入内存
                disk = self._load_disk_cache()
                if disk is not None:
                    self._cache = disk["rows"]
                    self._fetched_at = disk["fetched_at"]
                    return self._cache
            # 真去 API:先查真实行数,避免末块 range 越界(API 返 400001)
            row_count = self._fetch_row_count()
            collected: list[list[str]] = []
            start = 1
            while start <= row_count:
                end = min(start + _CHUNK_ROWS - 1, row_count)
                a1 = f"A{start}:{_LAST_COL_LETTER}{end}"
                chunk = self._fetch_range_uncached(a1)
                collected.extend(chunk)
                if not chunk or len(chunk) < (end - start + 1):
                    break  # 提前到尾(空尾行)
                start = end + 1
            self._cache = collected
            self._fetched_at = datetime.now()
            self._save_disk_cache()
            return collected

    def _fetch_row_count(self) -> int:
        """从 spreadsheet metadata 拿当前 sheet 的真实行数。

        每次 bulk fetch 前 1 次额外 API,换"分页边界永远精确"——比"试错碰 400001"
        干净,且 metadata 调用本身很轻。
        """
        url = f"https://docs.qq.com/openapi/spreadsheet/v3/{self.spreadsheet_id}"
        body_bytes, _ = _http_get_body(url, self._headers())
        body = body_bytes.decode("utf-8", errors="replace")
        try:
            j = json.loads(body)
        except json.JSONDecodeError as e:
            raise TencentSheetError(f"metadata non-JSON: {body[:200]}") from e
        # 这个 endpoint 实测直接返 {properties: [...]},不带 data/ret 包裹
        props = j.get("properties") or (j.get("data") or {}).get("properties") or []
        for p in props:
            if p.get("sheetId") == self.sheet_id:
                rc = int(p.get("rowCount") or p.get("rowTotal") or 0)
                if rc <= 0:
                    raise TencentSheetError(f"sheet {self.sheet_id}: rowCount=0")
                return rc
        raise TencentSheetError(
            f"sheet_id {self.sheet_id} not found in spreadsheet metadata"
        )

    def fetch_row(self, row_index_1based: int) -> list[str]:
        """单行重拉(写入"是否验收=1"后调用,让缓存里那行变最新)。

        row_index_1based: sheet 里的 1-based 行号(表头是 1,第一首歌是 2)。
        """
        if row_index_1based < 1:
            raise ValueError(f"row_index_1based must be >= 1, got {row_index_1based}")
        a1 = f"A{row_index_1based}:{_LAST_COL_LETTER}{row_index_1based}"
        rows = self._fetch_range_uncached(a1)
        if not rows:
            return []
        row = rows[0]
        # 同步进 _cache(如果之前拉过) + 持久化磁盘,防止重启丢这次单行更新
        with self._lock:
            if self._cache is not None:
                idx = row_index_1based - 1
                while len(self._cache) <= idx:
                    self._cache.append([])
                self._cache[idx] = row
                self._save_disk_cache()
        return row

    def invalidate(self) -> None:
        """清整张表缓存。下次 fetch_all 会重拉。"""
        with self._lock:
            self._cache = None
            self._fetched_at = None

    # ------- internal disk cache -------

    def _load_disk_cache(self) -> dict | None:
        """读盘 cache。spreadsheet_id / sheet_id 不匹配视为不可用(配置变了)。

        任何 IO/JSON 错误都返 None,让上层走 API。失败不抛错 —— cache 是优化,
        不是正确性来源。
        """
        path = _disk_cache_path()
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                j = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        if j.get("spreadsheet_id") != self.spreadsheet_id:
            return None
        if j.get("sheet_id") != self.sheet_id:
            return None
        rows = j.get("rows")
        if not isinstance(rows, list):
            return None
        fetched_at_str = j.get("fetched_at")
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str) if fetched_at_str else None
        except (TypeError, ValueError):
            fetched_at = None
        return {"rows": rows, "fetched_at": fetched_at}

    def _save_disk_cache(self) -> None:
        """写盘 cache。tmp + replace 原子写;失败 silently swallow。

        在 _lock 之内调用 —— 序列化 self._cache 需要稳定快照。文件写入是本地
        操作,通常 < 50ms,不至于阻塞太久。
        """
        if self._cache is None:
            return
        path = _disk_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "spreadsheet_id": self.spreadsheet_id,
                        "sheet_id": self.sheet_id,
                        "fetched_at": (
                            self._fetched_at.isoformat() if self._fetched_at else None
                        ),
                        "rows": self._cache,
                    },
                    f,
                    ensure_ascii=False,
                )
            tmp.replace(path)
        except OSError:
            pass  # cache 非关键,出错不打断主流程

    # ------- internal HTTP -------

    def _headers(self) -> dict[str, str]:
        return {
            "Client-Id": self.client_id,
            "Access-Token": self.access_token,
            "Open-Id": self.open_id,
        }

    def _fetch_range_uncached(self, a1_range: str) -> list[list[str]]:
        url = f"{_BASE_URL}/{self.spreadsheet_id}/{self.sheet_id}/{a1_range}"
        body_bytes, http_status = _http_get_body(url, self._headers())
        body = body_bytes.decode("utf-8", errors="replace")

        try:
            j = json.loads(body)
        except json.JSONDecodeError as e:
            raise TencentSheetError(
                f"non-JSON response (http {http_status}): {body[:200]}"
            ) from e

        # 错误判定:同时容忍 code(旧)和 ret(官方文档)字段
        api_code_raw = j.get("code") if j.get("code") is not None else j.get("ret")
        if api_code_raw not in (None, 0):
            msg = j.get("message") or j.get("msg") or "(no message)"
            raise TencentSheetError(
                f"api error {api_code_raw}: {msg}",
                http_status=http_status,
                api_code=int(api_code_raw),
            )
        if http_status >= 400:
            raise TencentSheetError(
                f"http {http_status}: {body[:200]}",
                http_status=http_status,
            )

        return _extract_rows(j)


def _extract_rows(j: dict) -> list[list[str]]:
    """从响应 JSON 抽出 list[list[str]]。

    腾讯 V3 cellValue 是 oneof 风格,根据格式只填一个字段:
      - text     -> 普通文本
      - number   -> 数字格式(分工表"是否验收"列就是数字 1/0,不是字符串!)
      - location -> 地点 cell,name 是显示名
      - select   -> 下拉 / 多选标签;value 是 option id 列表,options 给 id→text 映射;
                   单选取首项,多选用 "/" 拼接(如"情感"列 = "激昂/愤怒")
    我们统一展平成字符串。number 类型的整数特意转成不带小数点的形式
    ("1" 而不是 "1.0"),保持和用户在浏览器里看到的字面值一致 —— 后续判定
    `cell == "1"` 才能命中。
    """
    grid = (j.get("data") or {}).get("gridData") or j.get("gridData") or {}
    rows = grid.get("rows") or []
    out: list[list[str]] = []
    for r in rows:
        values = r.get("values") or []
        cells: list[str] = []
        for v in values:
            cv = v.get("cellValue") or {}
            text = cv.get("text") or ""
            if not text:
                if "number" in cv and cv["number"] is not None:
                    n = cv["number"]
                    # 整数值不带小数点:1.0 -> "1",1.5 -> "1.5"
                    if isinstance(n, bool):
                        text = "1" if n else "0"
                    elif isinstance(n, (int, float)) and float(n).is_integer():
                        text = str(int(n))
                    else:
                        text = str(n)
                elif isinstance(cv.get("location"), dict):
                    text = cv["location"].get("name", "")
                elif isinstance(cv.get("select"), dict):
                    sel = cv["select"]
                    selected_ids = sel.get("value") or []
                    options = sel.get("options") or []
                    id_to_text = {o.get("id"): o.get("text", "") for o in options}
                    picked = [id_to_text.get(sid, "") for sid in selected_ids]
                    picked = [p for p in picked if p]
                    text = "/".join(picked)
            cells.append(text or "")
        out.append(cells)
    return out


# ============================================================
#  模块级单例
# ============================================================


_client: Optional[TencentSheetClient] = None
_client_lock = threading.Lock()


def get_client() -> TencentSheetClient:
    """取(或懒构建)进程内单例。配置不全时抛 TencentSheetError。"""
    global _client
    with _client_lock:
        if _client is None:
            cfg = get_config().tencent_docs
            missing = [
                k for k, v in {
                    "client_id": cfg.client_id,
                    "access_token": cfg.access_token,
                    "open_id": cfg.open_id,
                    "spreadsheet_id": cfg.spreadsheet_id,
                    "sheet_id": cfg.sheet_id,
                }.items() if not v
            ]
            if missing:
                raise TencentSheetError(
                    "tencent_docs config incomplete; missing fields: "
                    + ", ".join(missing)
                )
            _client = TencentSheetClient(
                spreadsheet_id=cfg.spreadsheet_id,
                sheet_id=cfg.sheet_id,
                client_id=cfg.client_id,
                access_token=cfg.access_token,
                open_id=cfg.open_id,
            )
        return _client


def reset_client() -> None:
    """清单例;config reload / 单元测试用。"""
    global _client
    with _client_lock:
        _client = None
