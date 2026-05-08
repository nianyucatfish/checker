"""第四轮: endpoint 找到了 /sheets/{sheet_id},探 range 参数怎么传。"""

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
    except Exception as e:
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
    base = f"https://docs.qq.com/openapi/spreadsheet/v3/{t.spreadsheet_id}/sheets/{t.sheet_id}"

    # 已确定 endpoint 是这个,只是缺 range 参数。试 query string 各种 key 名。
    cands = [
        f"{base}?range=A1:AK1",
        f"{base}?range=A1:E1",
        f"{base}?rangeStr=A1:E1",
        f"{base}?rangeSize=A1:E1",
        f"{base}?cellRange=A1:E1",
        f"{base}?startRow=1&endRow=1&startCol=1&endCol=37",
        f"{base}?startRow=0&endRow=0&startCol=0&endCol=36",
        f"{base}?startRowIndex=0&endRowIndex=1&startColumnIndex=0&endColumnIndex=37",
        f"{base}?row=1&col=1&numRow=1&numCol=37",
        f"{base}?rangeSize.startRow=1&rangeSize.endRow=1&rangeSize.startCol=1&rangeSize.endCol=37",
        # 也许是 POST + body
    ]
    for url in cands:
        status, body = call("GET", url, headers)
        ok = (status == 200 and '"code":0' in body or ('"properties"' not in body and 'message' not in body))
        marker = "?" if status == 200 else "✗"
        snippet = body[:200].replace("\n", " ")
        print(f"{marker} [{status}] {url}")
        print(f"        {snippet}")
        # 看到响应里没有 "Validate error" / "Not Found" 就当做候选
        if status == 200:
            try:
                j = json.loads(body)
                if j.get("code", -1) == 0 or "values" in body or "data" in j:
                    print()
                    print("=" * 60)
                    print(f"[POSSIBLE] {url}")
                    print(body[:4000])
                    return 0
            except Exception:
                pass

    # 最后试 POST + body
    print("\n--- POST 尝试 ---")
    post_cands = [
        ({"range": "A1:E1"},),
        ({"rangeSize": {"startRow": 1, "endRow": 1, "startCol": 1, "endCol": 37}},),
        ({"startRow": 1, "endRow": 1, "startCol": 1, "endCol": 37},),
    ]
    for (body_dict,) in post_cands:
        status, body = call("POST", base, headers, body_dict)
        snippet = body[:200].replace("\n", " ")
        print(f"  [{status}] POST body={body_dict}")
        print(f"        {snippet}")

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
