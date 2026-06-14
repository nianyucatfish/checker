"""
MCP server for the checker sidecar.

Stdio transport. Electron main spawns this as a subprocess; agent calls
flow through MCP client → here → existing sidecar domain layers
(assignment_sheet / checker / fixers).

Run standalone for debugging:
    python -m sidecar.mcp_server

Tool inventory (see doc/prompts/agent_workflow.md):
- state_tree.*: read (auto-init) / update —— 18 态 markdown 进度本
- audit.*  : list_errors (MISSING_FILE 附带 workspace 候选清单)
- read_text_file: 读纯文本,line_range 取片段,>8KB 自动 head+tail 截断
- sheet.*  : list_my_pending / get_song_meta(写路径 mark_accepted / write_baidu_link 待补,依赖腾讯文档写 API)
- fs.*     : list_dir
- fix.*    : execute_plan(union ops: rename / delete / move / copy / write_text / text_edit;agent 自构造,不再有 propose_* 包装)

Boundaries (must hold):
- reviewer_name 永远不在 tool args / results 中(assignment_sheet 内部读 config)
- _rows 系列工具不在这里暴露,只留 /dev/* 给 dev panel
- write_text op 只允许 .csv/.txt/.md 等文本扩展(脑暴 §8 边界,fixers._WRITE_TEXT_ALLOWED_EXTS)
- 所有工具同步返回 dict;MCP server 自行 JSON 序列化
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
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

from sidecar import assignment_sheet, checker, fixers, state_tree
from sidecar import workspace as _ws
from sidecar.config import get_config
from sidecar.tencent_sheet import TencentSheetError

logger = logging.getLogger("sidecar.mcp")


# ============================================================
#  server 实例
# ============================================================

mcp = FastMCP("checker-sidecar")


# ============================================================
#  system.* (内部 — Electron main 启动 / 切工作区时调)
# ============================================================

@mcp.tool()
def system_set_workspace(root: str) -> dict[str, Any]:
    """Set current workspace root for relative-path resolution. Called by Electron main when workspace changes; LLM should not call this. root="" → 清空."""
    _ws.set_workspace(root or None)
    return {"ok": True, "current": _ws.get_workspace()}


# ============================================================
#  state_tree.* (进度本)
# ============================================================

@mcp.tool()
def state_tree_read(song: str) -> dict[str, Any]:
    """Read state markdown at cache/state_tree/<song>.md. **Auto-creates initial md (18 态全 `[ ]`) if missing** —— 进歌第一件事就调它,既看进度又确保文件就位。Scope=song,同一首歌的所有 chat 共享一份 md。
    Returns {path, text, created} (created=true 表示这次首建)."""
    try:
        existed = state_tree.md_path(song).exists()
        state_tree.init_state_tree(song)  # 幂等;已存在不动
        text = state_tree.read_state_tree(song)
    except state_tree.StateTreeError as e:
        return {"ok": False, "code": "INVALID_PATH", "message": str(e)}
    return {
        "path": str(state_tree.md_path(song)),
        "text": text,
        "created": not existed,
    }


@mcp.tool()
def state_tree_update(
    song: str,
    state_id: str,
    done: bool,
    note: str | None = None,
) -> dict[str, Any]:
    """Flip one state line's checkbox / note.
    `done` 语义严格: **true 仅当本态所有 audit 错误已消除 + 无任何遗留**,不是"看过/做了一些 = true"。
    任何"缺/未消/无法修/失败/待用户/待补"等未解决问题 → `done=false`,note 写遗留原因,推下一态(1.7 复检阶段统一处理)。
    `done=true` 同时 note 含未消除字眼是常见错误,会被人工识别为标错。
    `note`: None 不动现有,空串清空,非空替换(支持 `{{file:path:start:end}}` 服务端展开).
    Valid state_id: 1.1-1.7 / 2.1-2.8 / 3.1-3.3. **Requires state_tree_read first to init md.**
    Returns {path, text} 含更新后全文. On failure: {ok: false, code: "INVALID_STATE"|"INVALID_PATH"|"NOT_INITIALIZED"|"MD_CORRUPT", message: str}."""
    try:
        text = state_tree.update_state_tree(song, state_id, done, note)
    except state_tree.StateTreeError as e:
        msg = str(e)
        if "不在白名单" in msg:
            code = "INVALID_STATE"
        elif "非法字符" in msg or "不能为空" in msg:
            code = "INVALID_PATH"
        else:
            code = "MD_CORRUPT"
        return {"ok": False, "code": code, "message": msg}
    except FileNotFoundError as e:
        return {"ok": False, "code": "NOT_INITIALIZED", "message": str(e)}
    return {"path": str(state_tree.md_path(song)), "text": text}


# ============================================================
#  audit.* (查文件)
# ============================================================

@mcp.tool()
def audit_list_errors(song_path: str) -> dict[str, Any]:
    """List all QC errors for one song folder. MISSING_FILE 错误项附带 `candidates`: 工作区其他位置同名文件路径清单(嵌套目录 / 错的子目录 / 别的歌都搜),agent 据此直接构造 Move / Copy.
    Check codes: FOLDER_NAME / MISSING_DIR / MISSING_FILE / EXTRA_FILE / WAV_FORMAT (96k/24bit/2ch) / WAV_DURATION_TOO_SHORT (≥180s) / CROSS_DIR_DURATION_INCONSISTENT (exact-frame, covers both intra-dir and cross-dir) / CSV_HEADER / CSV_TIME_FORMAT / MIX_PROJ_NAME / BG_COMBO_INVALID.
    song_path 可绝对可相对(相对会基于当前 workspace 解析). Returns {errors: [...], by_code: {code: count}}; **path 不存在 → {ok: false, code: "SONG_PATH_NOT_FOUND"}**(不要把空 errors 解读成"没问题")."""
    try:
        song_path = _ws.resolve(song_path)
    except _ws.WorkspaceNotSet as e:
        return {"ok": False, "code": "WORKSPACE_NOT_SET", "message": str(e)}
    if not os.path.isdir(song_path):
        return {"ok": False, "code": "SONG_PATH_NOT_FOUND", "message": f"song_path 不存在或不是目录: {song_path}"}
    by_path = checker.check_song_folder(song_path)
    workspace_root = _ws.get_workspace() or os.path.dirname(os.path.normpath(os.path.abspath(song_path)))
    errors: list[dict] = []
    by_code: dict[str, int] = {}
    for path, errs in by_path.items():
        for e in errs:
            d = e.to_dict()
            if e.code == "MISSING_FILE":
                filename = (e.expected or {}).get("filename", "")
                if filename:
                    d["candidates"] = _find_candidates(filename, workspace_root, song_path)
            errors.append(d)
            by_code[e.code] = by_code.get(e.code, 0) + 1
    return {"errors": errors, "by_code": by_code}


def _find_candidates(filename: str, workspace_root: str, song_path: str) -> list[dict]:
    """工作区里 basename == filename 的所有文件路径,按"本歌内 / 跨歌"分类。

    返回 [{path, scope}]:scope = "this_song" | "other_song"。
    跨歌候选 agent 不能自动 Move/Copy,要先在 chat 里向用户确认(见 workflow §1.3)。

    扫描范围 = workspace_root,跳过 .git / venv / node_modules / cache 等明显非工作区目录。
    """
    if not os.path.isdir(workspace_root):
        return []
    song_norm = os.path.normcase(os.path.normpath(os.path.abspath(song_path)))
    skip_dirs = {".git", "venv", ".venv", "node_modules", "cache", "__pycache__", ".idea", ".vscode"}
    hits: list[dict] = []
    for dirpath, dirnames, fnames in os.walk(workspace_root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        if filename not in fnames:
            continue
        full = os.path.join(dirpath, filename)
        # 落在 song_path 内 → this_song(嵌套或错的子目录);否则跨歌
        full_norm = os.path.normcase(os.path.normpath(os.path.abspath(full)))
        if full_norm == song_norm or full_norm.startswith(song_norm + os.sep):
            scope = "this_song"
        else:
            scope = "other_song"
        hits.append({"path": full, "scope": scope})
    return hits


def _find_song_root(file_or_dir: str, workspace_root: str) -> str:
    """从 workspace_root 下的某个内部路径,推回它所在的 song folder(workspace 直接子目录)。

    `_affected_song_dirs` 用 —— 跑 audit 前后 diff 时按 song folder 分桶。
    """
    norm = os.path.normpath(os.path.abspath(file_or_dir))
    root_norm = os.path.normpath(os.path.abspath(workspace_root))
    rel = os.path.relpath(norm, root_norm)
    parts = rel.split(os.sep)
    if not parts or parts[0] in ("", "."):
        return root_norm
    return os.path.join(root_norm, parts[0])


# ============================================================
#  sheet.* (分工表)
# ============================================================

@mcp.tool()
def sheet_get_song_meta(song_name: str, row_index: int | None = None) -> dict[str, Any]:
    """Fetch song meta (28 fields) for 1.1 完整性 check. PII 字段(人名/链接)已 sidecar 打码,写消息直接用打码版即可。
    Scope: only songs in current reviewer's range (身份隐藏 boundary).
    Key fields for 1.1: missing_required_fields, invalid_format_fields, derived.backing_count.
    歌名撞车(同 reviewer 下同 song_name 多行) → {ok: false, code: "AMBIGUOUS_SONG", candidates: [{row_index, song_name, owner, original_singer}]}, 用 candidates 里的 row_index 再调一次。
    On failure: {ok: false, code: "SONG_NOT_FOUND" | "AMBIGUOUS_SONG" | "SHEET_FETCH_FAILED", message: str}."""
    try:
        meta = assignment_sheet.get_song_meta(song_name, row_index=row_index)
    except assignment_sheet.AmbiguousSongError as e:
        return {
            "ok": False,
            "code": "AMBIGUOUS_SONG",
            "message": str(e),
            "candidates": e.candidates,
        }
    except TencentSheetError as e:
        msg = str(e)
        # 区分 "不在范围" 与 "API 挂"
        if "不在当前用户的验收范围" in msg or "分工表为空" in msg or "row_index=" in msg:
            return {"ok": False, "code": "SONG_NOT_FOUND", "message": msg}
        return {
            "ok": False,
            "code": "SHEET_FETCH_FAILED",
            "message": f"{e} (http_status={getattr(e, 'http_status', '?')}, api_code={getattr(e, 'api_code', '?')})",
        }
    return asdict(meta)


@mcp.tool()
def sheet_list_my_pending() -> dict[str, Any]:
    """List songs in current reviewer's range that are not yet marked accepted.
    Reviewer name read from config, NEVER appears in args/results (身份隐藏 boundary).
    Returns {songs: [{row_index, song_name, owner}, ...]}; on failure: {ok: false, code: "SHEET_FETCH_FAILED", message: str}."""
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

def _list_tree(path: str, max_depth: int, _cur: int = 0) -> dict[str, Any]:
    try:
        entries = os.scandir(path)
    except (FileNotFoundError, NotADirectoryError, PermissionError) as e:
        return {"name": os.path.basename(path) or path, "error": str(e)}
    dirs: list[dict] = []
    files: list[dict] = []
    for ent in entries:
        if ent.is_dir():
            if _cur + 1 < max_depth:
                dirs.append(_list_tree(ent.path, max_depth, _cur + 1))
            else:
                dirs.append({"name": ent.name, "truncated": True})
        else:
            try:
                size = ent.stat().st_size
            except OSError:
                size = -1
            files.append({"name": ent.name, "size": size})
    dirs.sort(key=lambda d: d["name"])
    files.sort(key=lambda f: f["name"])
    return {"name": os.path.basename(path) or path, "dirs": dirs, "files": files}


@mcp.tool()
def fs_list_dir(path: str, max_depth: int = 2) -> dict[str, Any]:
    """List a directory tree, default depth 2 (一首歌 song folder + 5 子目录 + 文件,一次拿全).
    Use cases: (1) song folder 全貌 默认深度 2 够;(2) 1.2 双文件夹合并 / 1.3 找 orphan → 显式 path 为工作区根, max_depth=1 列同级 song folders 再按需下钻;(3) 比对两个目录冲突 → 各调一次自己 diff names.
    path 可绝对可相对(相对基于当前 workspace 解析;空串 "" 默认 = 当前 workspace 根). Returns nested {name, dirs: [...recursive], files: [{name, size}]}; 超 max_depth 的目录标 {name, truncated: true};不存在/无权限 → {name, error: str}."""
    try:
        if not path:
            path = _ws.get_workspace() or ""
            if not path:
                return {"ok": False, "code": "WORKSPACE_NOT_SET", "message": "empty path needs workspace set"}
        else:
            path = _ws.resolve(path)
    except _ws.WorkspaceNotSet as e:
        return {"ok": False, "code": "WORKSPACE_NOT_SET", "message": str(e)}
    return _list_tree(path, max_depth=max(1, max_depth))


# ============================================================
#  read_text_file (读纯文本)
# ============================================================

_READ_TEXT_AUTO_TRUNCATE_BYTES = 8 * 1024
_READ_TEXT_HEAD_TAIL_LINES = 30


@mcp.tool()
def read_text_file(path: str, line_range: list[int] | None = None) -> dict[str, Any]:
    """Read a plain-text file. 默认全文;传 `line_range=[start, end]`(1-based 闭区间)取片段;>8KB 自动 head+tail 截断,中间塞 `[omitted N lines]`. 1.6 CSV 用这个读 Structure / 乐器音源对照表(短)+ Beat.csv (长,用 range).
    path 可绝对可相对(相对基于当前 workspace 解析). Returns {path, content, total_lines, truncated, omitted_lines}; on failure: {ok: false, code: "FILE_NOT_FOUND"|"READ_FAILED"|"WORKSPACE_NOT_SET", message: str}."""
    try:
        path = _ws.resolve(path)
    except _ws.WorkspaceNotSet as e:
        return {"ok": False, "code": "WORKSPACE_NOT_SET", "message": str(e)}
    if not os.path.isfile(path):
        return {"ok": False, "code": "FILE_NOT_FOUND", "message": path}
    try:
        with open(path, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "code": "READ_FAILED", "message": str(e)}

    lines = text.splitlines(keepends=True)
    total = len(lines)

    if line_range is not None:
        if not (isinstance(line_range, (list, tuple)) and len(line_range) == 2):
            return {"ok": False, "code": "READ_FAILED", "message": f"line_range 必须是 [start, end]:{line_range!r}"}
        start, end = int(line_range[0]), int(line_range[1])
        start = max(1, start)
        end = min(total, end)
        sliced = lines[start - 1:end]
        return {
            "path": path,
            "content": "".join(sliced),
            "total_lines": total,
            "truncated": True,
            "line_range": [start, end],
        }

    if len(raw) <= _READ_TEXT_AUTO_TRUNCATE_BYTES:
        return {"path": path, "content": text, "total_lines": total, "truncated": False}

    # 自动截断:head N + omitted + tail N
    n = _READ_TEXT_HEAD_TAIL_LINES
    if total <= 2 * n:
        # 行少但 bytes 大(单行超长):按行 split 仍按全文返回比强切更安全
        return {"path": path, "content": text, "total_lines": total, "truncated": False}
    head = "".join(lines[:n])
    tail = "".join(lines[total - n:])
    omitted = total - 2 * n
    content = head + f"\n[omitted {omitted} lines —— 用 line_range=[{n + 1}, {total - n}] 取中段]\n" + tail
    return {
        "path": path,
        "content": content,
        "total_lines": total,
        "truncated": True,
        "omitted_lines": omitted,
    }


# ============================================================
#  fix.* (改文件)
# ============================================================

# auto 模式下,execute 必须能查到一次同样 ops 的 simulate 调用。simulate hash 集合
# 模块级保留,5min TTL 自动清。confirm 模式不查这个集合(用户在 loop 中,卡片做 gate)。
_SIMULATE_TTL_SEC = 300
_simulate_cache: dict[str, float] = {}


def _ops_hash(ops: list[dict]) -> str:
    """ops 列表的稳定 hash —— canonical JSON + sha256 前 16 hex。
    sort_keys 让 dict 字段顺序无关;list 顺序保留(不同顺序的 plan 视为不同 plan)。"""
    canonical = json.dumps(ops, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _gc_simulate_cache() -> None:
    now = time.time()
    expired = [k for k, t in _simulate_cache.items() if now - t > _SIMULATE_TTL_SEC]
    for k in expired:
        _simulate_cache.pop(k, None)


def _affected_song_dirs(ops: list[dict], workspace_root: str) -> set[str]:
    """ops 涉及的所有 song folder(workspace 直接子目录)。diff 前后只 audit 这几个,省。"""
    dirs: set[str] = set()
    for op in ops:
        for key in ("src", "dst", "path", "dst_dir"):
            v = op.get(key)
            if v:
                dirs.add(_find_song_root(v, workspace_root))
    return dirs


def _snapshot_by_code(song_paths: set[str]) -> dict[str, int]:
    """对给定 song folders 跑 audit,聚合 by_code 计数。不存在的目录跳过。"""
    snap: dict[str, int] = {}
    for sp in song_paths:
        if not os.path.isdir(sp):
            continue
        by_path = checker.check_song_folder(sp)
        for _, errs in by_path.items():
            for e in errs:
                snap[e.code] = snap.get(e.code, 0) + 1
    return snap


def _diff_by_code(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """after - before;0 的 key 不出现。负数 = 错误减少,正数 = 错误新增。"""
    delta: dict[str, int] = {}
    for k in set(before) | set(after):
        d = after.get(k, 0) - before.get(k, 0)
        if d != 0:
            delta[k] = d
    return delta


@mcp.tool()
def fix_execute_plan(
    approved_ops: list[dict],
    workspace_root: str = "",
    simulate: bool = False,
) -> dict[str, Any]:
    """Execute file-system ops, or dry-run when simulate=True. **Op shape**: `{"type": "<op>", ...}` (字段名是 `type`,不是 `op`!). Op types: rename {type, src, dst} / delete {type, path} (送回收站) / move {type, src, dst_dir} / copy {type, src, dst_dir} (源保留) / text_edit {type, path, old_string, new_string, replace_all?} (精确替换,old_string 默认必须唯一) / write_text {type, path, content} (整文件重写,CSV/txt/md 等白名单扩展).
    workspace_root 可省 → 用 sidecar 当前 workspace;ops 里 src/dst/path/dst_dir 可绝对可相对(相对基于 workspace_root 解析).
    ALL paths must be inside workspace_root; any out-of-workspace → 整批拒.
    simulate=True: 干跑,返 would_execute + would_conflict + ops_hash,不碰磁盘.
    simulate=False: auto 模式下服务端校验"先 simulate 过同 ops"——没查到 → 拒,返 SIMULATE_REQUIRED;confirm 模式不查(用户卡片已是 gate).
    Execute 后返 by_code_delta (-X: n / +Y: m 表示该 code 错误减少 n / 新增 m),agent 据此自我反馈是否符合预期.
    Returns simulate: {simulated, would_execute, would_conflict, predicted_path_updates, ops_hash}; execute: {executed, errors, path_updates, by_code_delta}; on auto-mode rejection: {ok: false, code: "SIMULATE_REQUIRED", ops_hash, message}; on workspace miss: {ok: false, code: "WORKSPACE_NOT_SET"}."""
    if not workspace_root:
        workspace_root = _ws.get_workspace() or ""
    if not workspace_root:
        return {"ok": False, "code": "WORKSPACE_NOT_SET", "message": "workspace_root not provided and sidecar has no current workspace"}
    # 把 ops 里所有 path 字段解析成绝对(相对就基于 workspace_root);兼容 `op` 别名
    # (模型常把 `type` 写成 `op`,容错处理避免死循环)
    normalized_ops: list[dict] = []
    for op in approved_ops:
        new_op = dict(op)
        if "op" in new_op and "type" not in new_op:
            new_op["type"] = new_op.pop("op")
        for key in ("src", "dst", "path", "dst_dir"):
            v = new_op.get(key)
            if isinstance(v, str) and v and not os.path.isabs(v):
                new_op[key] = str(os.path.normpath(os.path.join(workspace_root, v)))
        normalized_ops.append(new_op)
    approved_ops = normalized_ops
    h = _ops_hash(approved_ops)

    if simulate:
        sim = fixers.simulate_ops(approved_ops, workspace_root=workspace_root)
        _gc_simulate_cache()
        _simulate_cache[h] = time.time()
        return {
            "simulated": True,
            "would_execute": sim.would_execute,
            "would_conflict": sim.would_conflict,
            "predicted_path_updates": sim.predicted_path_updates,
            "ops_hash": h,
        }

    mode = get_config().preferences.execution_mode
    if mode == "auto":
        _gc_simulate_cache()
        if h not in _simulate_cache:
            return {
                "ok": False,
                "code": "SIMULATE_REQUIRED",
                "ops_hash": h,
                "message": "auto 模式下,真执行前必须先用同样 approved_ops 调一次 simulate=True",
            }

    affected = _affected_song_dirs(approved_ops, workspace_root)
    before = _snapshot_by_code(affected)
    result = fixers.execute_ops(approved_ops, workspace_root=workspace_root)
    after = _snapshot_by_code(affected)
    return {
        "executed": result.executed,
        "errors": result.errors,
        "path_updates": result.path_updates,
        "by_code_delta": _diff_by_code(before, after),
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
