"""sidecar.workspace — current workspace root (set by Electron via /agent/workspace).

工具在拿到 path/song_path 这类参数时通过 `resolve(p)` 解析:
- 绝对路径 → 原样返回
- 相对路径 → 拼接 current workspace,resolve 后返回(若 workspace 未设置则拒)

这样 LLM 不需要拼绝对路径,直接传 song folder name 这种短形式即可,省 token、防笔误。
"""

from __future__ import annotations

import os
from pathlib import Path


_current: str | None = None


def set_workspace(root: str | None) -> None:
    global _current
    _current = root if root else None


def get_workspace() -> str | None:
    return _current


def resolve(p: str) -> str:
    """把 path 解析成绝对路径。

    - 绝对路径 → normalize 后返回(不强制在 workspace 内,审计 / 工程文件等场景可能例外)
    - 相对路径 → 必须 workspace 已设置,拼接 + resolve
    - 空串 → 原样返回(让上层报参数错)
    """
    if not p:
        return p
    if os.path.isabs(p):
        return os.path.normpath(p)
    if not _current:
        raise WorkspaceNotSet(f"path is relative but no workspace set: {p!r}")
    return str((Path(_current) / p).resolve())


class WorkspaceNotSet(RuntimeError):
    pass
