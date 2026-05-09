"""
MCP server for the checker sidecar.

Stdio transport. Electron main spawns this as a subprocess; agent calls
flow through MCP client → here → existing sidecar domain layers
(assignment_sheet / checker / fixers).

Run standalone for debugging:
    python -m sidecar.mcp_server

Tool inventory (see doc/操作清单.md, 18 态状态树 + G-XX 全局):
- audit.*  : run_check / run_workspace_check / get_prior_review
- sheet.*  : list_my_pending / get_song_meta(写路径 mark_accepted / write_baidu_link 待 α-2 补)
- fs.*     : song_exists  (G-03 入口前置 gate)
- fix.*    : propose_rename_plan / execute_plan(union ops) /
             propose_csv_header_rewrite / propose_csv_time_zero_pad

Boundaries (must hold):
- reviewer_name 永远不在 tool args / results 中(assignment_sheet 内部读 config)
- _rows 系列工具不在这里暴露,只留 /dev/* 给 dev panel
- write_text op 只允许 .csv/.txt/.md 等文本扩展(脑暴 §8 边界,fixers._WRITE_TEXT_ALLOWED_EXTS)
- 所有工具同步返回 dict;MCP server 自行 JSON 序列化
"""

from __future__ import annotations

import logging
import os
import re
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

from sidecar import assignment_sheet, checker, fixers, review_log
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


def _find_song_root(file_or_dir: str, workspace_root: str) -> str:
    """从 workspace_root 下的某个内部路径,推回它所在的 song folder(workspace 直接子目录)。

    audit_run_workspace_check 用 —— check_workspace 返回的 path 是 song 内文件
    路径,需要回归到 song folder 这一级才能按歌分组。
    """
    norm = os.path.normpath(os.path.abspath(file_or_dir))
    root_norm = os.path.normpath(os.path.abspath(workspace_root))
    rel = os.path.relpath(norm, root_norm)
    parts = rel.split(os.sep)
    if not parts or parts[0] in ("", "."):
        return root_norm
    return os.path.join(root_norm, parts[0])


@mcp.tool()
def audit_run_workspace_check(workspace_root: str) -> dict[str, Any]:
    """Run audit on every song folder under the workspace root.

    G-01 多歌循环用:agent 拿到 sheet_list_my_pending 队列后可以调这个一次扫
    所有歌再按 song 分桶 prioritize,比逐首 audit_run_check 省 round-trip。

    Returns:
        {
          "by_song": {song_root_path: {"errors": [...], "by_code": {code: count}}},
          "total_errors": int,
        }
    """
    by_path = checker.check_workspace(workspace_root)
    by_song: dict[str, dict] = {}
    total = 0
    for path, errs in by_path.items():
        song_dir = _find_song_root(path, workspace_root)
        bucket = by_song.setdefault(song_dir, {"errors": [], "by_code": {}})
        for e in errs:
            bucket["errors"].append(e.to_dict())
            bucket["by_code"][e.code] = bucket["by_code"].get(e.code, 0) + 1
            total += 1
    return {"by_song": by_song, "total_errors": total}


@mcp.tool()
def audit_get_prior_review(song_name: str) -> dict[str, Any]:
    """Query review_log.jsonl for prior workflow entries about this song.

    跨 chat 知识库查询(无 chat_id 过滤,脑暴 §10.5)。用法:agent 在新 chat
    开始验收某首歌前调这个看"上次走到哪 / fail 在哪 / 用户给过啥反馈"。

    Returns:
        {
          "entries": [
            {"chat_id", "song", "state", "result", "summary", "details", "timestamp"},
            ...
          ]   # 时序倒序(最近的在前);空 list = 这首歌从没验收过
        }
    """
    return {"entries": review_log.get_prior_review(song_name)}


# ============================================================
#  sheet.* (分工表)
# ============================================================

@mcp.tool()
def sheet_get_song_meta(song_name: str) -> dict[str, Any]:
    """Fetch one song's metadata + missing-required-fields from the assignment sheet.

    Used by state 1.1 to verify the song's row is filled in (扒曲信息 / 风格标签等).
    Only finds songs assigned to the current reviewer (身份隐藏 boundary preserved
    via internal config; 不允许通过此工具枚举他人负责的歌).

    Returns:
        {
          "meta": {row_index, song_name, owner, original_singer, backing,
                   backing_gender, pan_owner_link, pan_mix_link},
          "missing_required_fields": [str, ...]   # 必填但空的字段名
        }

    Failure modes:
        - song not found / not in scope → {"ok": false, "code": "SONG_NOT_FOUND"}
        - tencent docs api down → {"ok": false, "code": "SHEET_FETCH_FAILED"}
    """
    try:
        meta = assignment_sheet.get_song_meta(song_name)
    except TencentSheetError as e:
        msg = str(e)
        # 区分 "不在范围" 与 "API 挂"
        if "不在当前用户的验收范围" in msg or "分工表为空" in msg:
            return {"ok": False, "code": "SONG_NOT_FOUND", "message": msg}
        return {
            "ok": False,
            "code": "SHEET_FETCH_FAILED",
            "message": f"{e} (http_status={e.http_status}, api_code={e.api_code})",
        }
    d = asdict(meta)
    missing = d.pop("missing_required_fields")
    return {"meta": d, "missing_required_fields": missing}


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
#  fs.* (文件系统查询)
# ============================================================

@mcp.tool()
def fs_song_exists(workspace_root: str, song_name: str) -> dict[str, Any]:
    """Check whether a song folder exists under the workspace root.

    G-03 入口前置 gate 用。state machine 进任何 1.x 态前先调这个;不存在 →
    整首跳过(not_started),不进入 state tree —— 不算 fail,只是没数据可走。

    Returns:
        {"exists": bool, "song_path": str}
    """
    song_path = os.path.join(workspace_root, song_name)
    return {"exists": os.path.isdir(song_path), "song_path": song_path}


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

    Op types(see doc/操作清单.md fix.* schema):
      - rename:     {"type": "rename",     "src": str, "dst": str}
      - delete:     {"type": "delete",     "path": str}        # send2trash
      - move:       {"type": "move",       "src": str, "dst_dir": str}
      - create_dir: {"type": "create_dir", "path": str}
      - write_text: {"type": "write_text", "path": str, "content": str}  # CSV/文本仅

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


# 1.6 用:CSV mm:ss 零填充正则。匹配 m:ss / mm:s / m:s,跳过已是 mm:mm 的。
# (?<![\d:]) 防误匹配 "abc1:23" 中的 "1:23";(?![\d:]) 防误匹配 "1:234" 的 "1:23"。
_CSV_TIME_PATTERN = re.compile(r"(?<![\d:])(\d{1,2}):(\d{1,2})(?![\d:])")


@mcp.tool()
def fix_propose_csv_header_rewrite(csv_path: str) -> dict[str, Any]:
    """Propose a write_text op fixing the CSV header for known fixed-header file types.

    1.6 自动修用。支持的文件类型(per 数据要求.md / logic_checker):
    - `*_Beat.csv`:        header 应为 `TIME,LABEL`
    - `乐器音源对照表.csv`: header 应为 `乐器,音源`
    - `*_Structure.csv`:   header 是内容驱动(段落标签 mix),**不**自动修,返回 skipped

    Returns:
        {
          "ops": [{"type": "write_text", "path": str, "content": str}, ...],
          "skipped": str | None,   # 没 op 时解释原因(已正确 / Structure / 未知类型)
        }
        失败时:{"ok": false, "code": "FILE_NOT_FOUND" | "READ_FAILED", "message": str}
    """
    if not os.path.isfile(csv_path):
        return {"ok": False, "code": "FILE_NOT_FOUND", "message": csv_path}
    name = os.path.basename(csv_path)
    if name.endswith("_Beat.csv"):
        expected_header = "TIME,LABEL"
    elif name == "乐器音源对照表.csv":
        expected_header = "乐器,音源"
    elif name.endswith("_Structure.csv"):
        return {
            "ops": [],
            "skipped": "Structure.csv 表头是内容驱动(Intro/Verse/Chorus/Bridge/Outro 选取),"
                       "非固定字符串,无法自动修;请人工核对段落标签是否在允许集",
        }
    else:
        return {"ops": [], "skipped": f"未知 CSV 类型: {name}"}

    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
    except OSError as e:
        return {"ok": False, "code": "READ_FAILED", "message": str(e)}

    current_header = lines[0].strip() if lines else ""
    if current_header == expected_header:
        return {"ops": [], "skipped": "header 已正确"}

    body = "".join(lines[1:]) if len(lines) > 1 else ""
    new_content = expected_header + "\n" + body
    return {"ops": [{"type": "write_text", "path": csv_path, "content": new_content}]}


@mcp.tool()
def fix_propose_csv_time_zero_pad(csv_path: str) -> dict[str, Any]:
    """Propose a write_text op zero-padding all `n:nn` / `nn:n` / `n:n` mm:ss values.

    1.6 自动修用。纯字符串处理,不依赖 checker 报错位置 —— 扫整个文件,把所有
    单数字的 minutes/seconds 补零成两位。已经是 mm:ss 的不动。

    适用 Beat.csv / Structure.csv 等含时间列的文件。乐器音源对照表无时间格式
    → 自然 fixes=0,不返 op(graceful no-op)。

    Returns:
        {
          "ops": [{"type": "write_text", "path": str, "content": str}],   # 0 处修时为 []
          "fixes": int,   # 实际改了多少处时间值
        }
        失败时:{"ok": false, "code": "FILE_NOT_FOUND" | "READ_FAILED", "message": str}
    """
    if not os.path.isfile(csv_path):
        return {"ok": False, "code": "FILE_NOT_FOUND", "message": csv_path}
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except OSError as e:
        return {"ok": False, "code": "READ_FAILED", "message": str(e)}

    fixes = [0]

    def _pad(m: re.Match) -> str:
        mm, ss = m.group(1), m.group(2)
        new_mm = mm.zfill(2)
        new_ss = ss.zfill(2)
        if new_mm != mm or new_ss != ss:
            fixes[0] += 1
        return f"{new_mm}:{new_ss}"

    new_text = _CSV_TIME_PATTERN.sub(_pad, text)
    if fixes[0] == 0:
        return {"ops": [], "fixes": 0}
    return {
        "ops": [{"type": "write_text", "path": csv_path, "content": new_text}],
        "fixes": fixes[0],
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
