"""sidecar.review_log — append-only event stream for state-machine exits.

Per 脑暴 §10.4 / §10.5 / §10.9, this is the *thin state* the agent system 持久化:
- 每态退出写一行 JSONL(chat_id + song + state + result + summary + details + ts)
- 同一文件双用:
    - 跨 chat 知识库 query (audit_get_prior_review,无 chat_id 过滤)
    - 本 chat workflow 进度 UI(按 chat_id 过滤)

文件位置:`<repo_root>/cache/review_log.jsonl`(同 sheet_cache.json 同级)。

Schema(每行一个 JSON object):
    {
      "chat_id": str,      # Electron main 注入,sidecar 不验证内容
      "song": str,
      "state": str,        # "1.4" / "2.3" / "G-08" / etc
      "result": str,       # pass | fail | cancel | skipped
      "summary": str,      # ≤30 字物理快照(脑暴 GA `<summary>` 协议)
      "details": dict,     # 结构化:{items: [...], note: str, ...}
      "timestamp": str,    # ISO 8601 UTC
    }

Concurrency: append-only 写,行内不会被穿插(单次 write 是 atomic for short lines)。
跨 chat 并发写多 chat 各自一个 sidecar process 时也安全(底层 OS 文件 append 语义)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sidecar import paths


def _log_path() -> Path:
    """review log 路径；开发期仍在 repo cache，打包可由 CHECKER_LOG_DIR 注入。"""
    return paths.review_log_path()


def append(
    *,
    chat_id: str,
    song: str,
    state: str,
    result: str,
    summary: str = "",
    details: dict | None = None,
    timestamp: str | None = None,
) -> None:
    """写一行到 review log。

    - timestamp 默认当前 UTC ISO 8601(带 Z 后缀)
    - details 默认 {}
    - 父目录不存在时自动建
    """
    entry = {
        "chat_id": chat_id,
        "song": song,
        "state": state,
        "result": result,
        "summary": summary,
        "details": details or {},
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def iter_entries() -> Iterator[dict]:
    """流式读全部条目;文件不存在则空生成器。

    损坏行(JSON 解析失败 / 缺字段)skip 不抛 —— 下游 agent / UI 见到的就是
    "干净的过去"。坏行的修复留给开发者人工处理。
    """
    path = _log_path()
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def get_prior_review(song_name: str, *, chat_id: str | None = None) -> list[dict]:
    """按 song 过滤事件流,可选按 chat_id 二次过滤。返回时序倒序(最近的在前)。

    使用场景:
    - chat_id=None:跨 chat 知识库("这首歌之前别的 chat 走到哪 / fail 过啥")
    - chat_id=具体值:本 chat 进度 UI 数据源

    注:返回 list 而不是 generator —— 调用方通常要排序 / 计数 / 序列化,
    一次性物化更简单;review_log 文件预期不会爆量(每首歌每态 ~1 行)。
    """
    # Reverse ingestion order first: multiple events can share the same clock resolution
    # on CI, and a later append must still rank ahead of an earlier one on timestamp ties.
    out = [e for e in reversed(list(iter_entries())) if e.get("song") == song_name]
    if chat_id is not None:
        out = [e for e in out if e.get("chat_id") == chat_id]
    out.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return out
