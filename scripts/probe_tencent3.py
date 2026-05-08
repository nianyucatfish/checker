"""第三轮探针: 找读单元格的正确 endpoint。"""

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


def is_real_ok(status: int, body: str) -> bool:
    if status != 200:
        return False
    try:
        j = json.loads(body)
    except Exception:
        return True
    code = j.get("code", 0)
    return code in (0, None)


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

    # 各种可能的 endpoint 形态
    rng = "A1:AK3"
    cands = [
        f"{base}/openapi/spreadsheet/v3/{sid}/sheets/{sheet_id}",
        f"{base}/openapi/spreadsheet/v3/{sid}?sheet_id={sheet_id}",
        f"{base}/openapi/spreadsheet/v3/{sid}/values?sheetId={sheet_id}&range={rng}",
        f"{base}/openapi/spreadsheet/v3/{sid}/cells?sheetId={sheet_id}&range={rng}",
        f"{base}/openapi/spreadsheet/v3/{sid}/data?sheetId={sheet_id}&range={rng}",
        f"{base}/openapi/spreadsheet/v3/{sid}/sheets/{sheet_id}/values?range={rng}",
        f"{base}/openapi/spreadsheet/v3/{sid}/sheets/{sheet_id}/cells?range={rng}",
        f"{base}/openapi/spreadsheet/v3/{sid}/sheets/{sheet_id}/data?range={rng}",
        f"{base}/openapi/spreadsheet/v3/{sid}/sheets/{sheet_id}/range?range={rng}",
        f"{base}/openapi/spreadsheet/v3/{sid}/range/{sheet_id}!{rng}",
        f"{base}/openapi/spreadsheet/v3/{sid}/values/{sheet_id}!{rng}",
        f"{base}/openapi/spreadsheet/v3/files/{sid}/sheets/{sheet_id}",
        f"{base}/openapi/spreadsheet/v3/files/{sid}/sheets/{sheet_id}/values?range={rng}",
        # V2 variants
        f"{base}/openapi/spreadsheet/v2/{sid}/sheets/{sheet_id}",
        f"{base}/openapi/spreadsheet/v2/files/{sid}/sheets/{sheet_id}",
        f"{base}/openapi/spreadsheet/v2/files/{sid}/sheets/{sheet_id}/range/{rng}",
        f"{base}/openapi/spreadsheet/v2/files/{sid}/sheets/{sheet_id}/cells?range={rng}",
        f"{base}/openapi/spreadsheet/v2/files/{sid}/sheets/{sheet_id}/values?range={rng}",
        # sheet (单数)
        f"{base}/openapi/sheet/v3/{sid}/sheets/{sheet_id}",
        f"{base}/openapi/sheet/v3/{sid}/sheets/{sheet_id}/range/{rng}",
    ]

    for url in cands:
        status, body = call("GET", url, headers)
        marker = "✓" if is_real_ok(status, body) else "✗"
        snippet = body[:200].replace("\n", " ")
        print(f"{marker} [{status}] {url}")
        print(f"        {snippet}")
        if is_real_ok(status, body):
            print()
            print("=" * 60)
            print(f"[FOUND] {url}")
            print(body[:4000])
            return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
