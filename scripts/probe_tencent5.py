"""第五轮: 用真正的 endpoint 读表头。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import urllib.request
import urllib.error
from sidecar.config import get_config


def call(url: str, headers: dict) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def main() -> int:
    cfg = get_config()
    t = cfg.tencent_docs
    headers = {
        "Client-Id": t.client_id,
        "Access-Token": t.access_token,
        "Open-Id": t.open_id,
    }

    # 真endpoint: /openapi/spreadsheet/v3/files/{fileId}/{sheetId}/{range}
    base = f"https://docs.qq.com/openapi/spreadsheet/v3/files/{t.spreadsheet_id}/{t.sheet_id}"

    # 先读前 3 行,前 37 列(AK = 第 37 列)。range 用 A1 notation。
    # 试 raw + encoded 两种
    for rng in ["A1:AK3", quote("A1:AK3", safe=""), "A1:AK1"]:
        url = f"{base}/{rng}"
        status, body = call(url, headers)
        print(f"[{status}] {url}")
        # 拣 code
        try:
            j = json.loads(body)
            code = j.get("code")
            if code == 0:
                print()
                print("=== HEADERS (first 3 rows) ===")
                print(json.dumps(j, ensure_ascii=False, indent=2)[:3000])
                return 0
            else:
                print(f"  body code={code} msg={j.get('message')}")
        except Exception:
            print(f"  body: {body[:300]}")

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
