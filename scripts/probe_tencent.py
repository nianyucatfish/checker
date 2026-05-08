"""
腾讯文档 API 探针脚本 — 验证凭证和共享是否到位。

用法:
    venv/Scripts/python.exe scripts/probe_tencent.py

不会改任何东西,只读。读完就把表头打出来。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import urllib.request
import urllib.error
from sidecar.config import get_config


def call(method: str, url: str, headers: dict, body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return 0, repr(e)


def main() -> int:
    cfg = get_config()
    t = cfg.tencent_docs
    missing = [
        k for k, v in {
            "client_id": t.client_id,
            "access_token": t.access_token,
            "open_id": t.open_id,
            "spreadsheet_id": t.spreadsheet_id,
        }.items() if not v
    ]
    if missing:
        print(f"[!] 缺字段: {missing}")
        return 1

    headers = {
        "Client-Id": t.client_id,
        "Access-Token": t.access_token,
        "Open-Id": t.open_id,
        "Content-Type": "application/json",
    }

    # 候选 endpoint(从命名规律猜的)。哪条返回 200 就用哪条。
    base = "https://docs.qq.com"
    sid = t.spreadsheet_id
    sheet_id = t.sheet_id or "default"
    candidates = [
        ("GET", f"{base}/openapi/spreadsheet/v3/{sid}"),
        ("GET", f"{base}/openapi/spreadsheet/v2/files/{sid}"),
        ("GET", f"{base}/openapi/sheet/v3/{sid}"),
        ("GET", f"{base}/openapi/sheet/v2/{sid}"),
        ("GET", f"{base}/openapi/drive/v2/files/{sid}"),
        ("GET", f"{base}/openapi/spreadsheet/v3/{sid}/sheets/{sheet_id}"),
        ("GET", f"{base}/openapi/spreadsheet/v2/files/{sid}/sheets/{sheet_id}"),
        ("GET", f"{base}/openapi/sheet/v3/{sid}/sheets/{sheet_id}/range/A1:Z3"),
        # 旧版 v1 dop-api(公开链接专用,不需 token)对照
        ("GET", f"{base}/dop-api/opendoc?id={sid}&tab={t.sheet_id}&outformat=1&normal=1"),
    ]

    for method, url in candidates:
        # dop-api 不带 OAuth header,清空
        h = headers if "/openapi/" in url else {"User-Agent": "Mozilla/5.0"}
        status, body = call(method, url, h)
        snippet = body[:300].replace("\n", " ")
        print(f"[{status}] {method} {url}")
        print(f"      -> {snippet}")
        print()
        if status == 200:
            print("=" * 60)
            print(f"[OK] {url} 通了!完整响应前 2KB:")
            print(body[:2000])
            return 0

    print("[!] 没有 endpoint 返回 200")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
