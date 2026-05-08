"""
MCP server for the checker sidecar.

Stdio transport. Electron main spawns this as a subprocess; agent calls
flow through MCP client → here → existing sidecar domain layers
(assignment_sheet / checker / fixers).

Run standalone for debugging:
    python -m sidecar.mcp_server

Tool inventory: see doc/工具清单.md. This file currently implements only the
read-path slice (audit.run_check + sheet.list_my_pending) per implementation
priority step 1. Other tools (fix.* / sheet.write_* / sheet.mark_accepted)
land in subsequent slices.

Boundaries (must hold):
- reviewer_name never appears in tool args / results (assignment_sheet 内部读 config)
- _rows 系列工具不在这里暴露,只留 /dev/* 给 dev panel
- 所有工具同步返回 dict;MCP server 自行 JSON 序列化
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

# 显式延迟导入 mcp:让本文件在没装 mcp 的环境里仍能被 import 检查 / 测试基础逻辑。
try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "mcp Python SDK 未安装。装一下:pip install 'mcp>=1.0'\n"
        f"原始错误:{e}"
    )

from sidecar import assignment_sheet, checker, fixers
from sidecar.tencent_sheet import TencentSheetError

logger = logging.getLogger("sidecar.mcp")


# ============================================================
#  server 实例
# ============================================================

mcp = FastMCP("checker-sidecar")


# ============================================================
#  audit.* (查文件)
# ============================================================

@mcp.tool()
def audit_run_check(song_path: str) -> dict[str, Any]:
    """Run full auto-check on one song folder.

    Args:
        song_path: absolute path to the song folder.

    Returns:
        {
          "errors": [CheckError, ...],     # flattened, every entry has its own .path
          "by_code": {ErrorCode: count},   # quick triage for the agent
        }

    CheckError schema is stable; see sidecar/errors.py. Structurally:
        {code, severity, path, message, expected, fix_hints, machine_fixable}

    Failure modes:
        - non-existent path: returns errors=[] and by_code={} (silent;
          state machine should have G-03'd before this).
    """
    by_path = checker.check_song_folder(song_path)
    errors: list[dict] = []
    by_code: dict[str, int] = {}
    for path, errs in by_path.items():
        for e in errs:
            errors.append(e.to_dict())
            by_code[e.code] = by_code.get(e.code, 0) + 1
    return {"errors": errors, "by_code": by_code}


# ============================================================
#  sheet.* (分工表)
# ============================================================

@mcp.tool()
def sheet_list_my_pending() -> dict[str, Any]:
    """List songs assigned to the current reviewer that are not yet accepted.

    Reviewer identity is read from config (身份隐藏 boundary, 见 §5).
    The agent never receives the reviewer name in args or results.

    Returns:
        {
          "songs": [{"row_index": int, "song_name": str, "owner": str}, ...]
        }

    Failure modes:
        - tencent docs API down / cache miss + offline: returns
          {"ok": false, "code": "SHEET_FETCH_FAILED", "message": "..."}
    """
    try:
        rows = assignment_sheet.list_my_pending()
    except TencentSheetError as e:
        return {
            "ok": False,
            "code": "SHEET_FETCH_FAILED",
            "message": f"{e} (http_status={e.http_status}, api_code={e.api_code})",
        }
    return {"songs": [asdict(r) for r in rows]}


# ============================================================
#  fix.* (改文件)
# ============================================================

@mcp.tool()
def fix_propose_rename_plan(song_path: str) -> dict[str, Any]:
    """Propose rule-driven batch renames for one song folder.

    Output is a *suggestion list* the agent can take whole / partial / discard;
    not a forced batch. Agent typically combines this with self-authored ops.

    Returns:
        {
          "ops": [{"type": "rename", "src": str, "dst": str, "kind": str}, ...],
          "conflicts": [str, ...]   # 目标重名 / 已存在等冲突描述
        }
    """
    plan = fixers.build_autofix_plan([song_path])
    return {
        "ops": [
            {"type": "rename", "src": op.src, "dst": op.dst, "kind": op.kind}
            for op in plan.ops
        ],
        "conflicts": plan.conflicts,
    }


@mcp.tool()
def fix_execute_plan(approved_ops: list[dict], workspace_root: str) -> dict[str, Any]:
    """Execute a list of file-system ops approved by the user via confirm card.

    Op types(see doc/工具清单.md fix.* schema):
      - rename:     {"type": "rename",     "src": str, "dst": str}
      - delete:     {"type": "delete",     "path": str}        # send2trash
      - move:       {"type": "move",       "src": str, "dst_dir": str}
      - create_dir: {"type": "create_dir", "path": str}

    Path whitelist: every src/dst/path/dst_dir must be inside `workspace_root`;
    any out-of-workspace ref → whole batch rejected (fail-fast).

    NOTE: confirm-card hash gating is enforced by Electron main IPC layer
    before this tool is called; sidecar trusts the caller. When agent talks
    directly to MCP without the IPC bridge (dev only), there is no human gate.

    Returns:
        {
          "executed": [...],         # 成功执行的 ops(rename 后是真实落盘路径)
          "errors": [...],           # 失败 op 描述(包括路径越界)
          "path_updates": {old: new} # rename / move 引发的路径变更映射
        }
    """
    result = fixers.execute_ops(approved_ops, workspace_root=workspace_root)
    return {
        "executed": result.executed,
        "errors": result.errors,
        "path_updates": result.path_updates,
    }


# ============================================================
#  entry point
# ============================================================

def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # FastMCP.run() 默认起 stdio transport,Electron main 用 @modelcontextprotocol/sdk
    # 起 client + spawn 此 subprocess 即可。
    mcp.run()


if __name__ == "__main__":
    main()
