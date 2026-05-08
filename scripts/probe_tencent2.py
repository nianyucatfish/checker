"""
腾讯文档 API 探针 - 第二轮: 已知 metadata 通,这次读 sheet 内容。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

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
    headers = {
        "Client-Id": t.client_id,
        "Access-Token": t.access_token,
        "Open-Id": t.open_id,
        "Content-Type": "application/json",
    }
    base = "https://docs.qq.com"
    sid = t.spreadsheet_id
    sheet_id = t.sheet_id

    # 1) 重新打印 metadata,看清楚 sheet 标题
    print("=== metadata ===")
    status, body = call("GET", f"{base}/openapi/spreadsheet/v3/{sid}", headers)
    print(f"[{status}]", body)
    print()

    # 2) 试几个读 range 的 endpoint 形态
    range_str = "A1:AK3"  # 头三行,前 37 列(AK = 第 37 列)
    candidates = [
        ("GET", f"{base}/openapi/spreadsheet/v3/{sid}/sheets/{sheet_id}/range/{range_str}"),
        ("GET", f"{base}/openapi/spreadsheet/v3/{sid}/sheets/{sheet_id}/values/{range_str}"),
        ("GET", f"{base}/openapi/spreadsheet/v3/{sid}/values/{sheet_id}!{range_str}"),
        ("GET", f"{base}/openapi/spreadsheet/v3/{sid}/sheets/{sheet_id}?range={range_str}"),
        ("GET", f"{base}/openapi/spreadsheet/v3/{sid}/sheets/{sheet_id}"),
        ("GET", f"{base}/openapi/spreadsheet/v2/files/{sid}/sheets/{sheet_id}/range/{range_str}"),
        ("GET", f"{base}/openapi/spreadsheet/v2/files/{sid}/sheets/{sheet_id}"),
    ]
    for method, url in candidates:
        status, body = call(method, url, headers)
        snippet = body[:400]
        print(f"[{status}] {method} {url}")
        print(f"      -> {snippet}")
        print()
        if status == 200:
            print("=" * 60)
            print(f"[OK] 完整响应前 4KB:")
            print(body[:4000])
            return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
