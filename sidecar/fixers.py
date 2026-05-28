"""
sidecar.fixers — 文件系统写操作核心(rename / move / copy / delete / pad / trim)。

设计原则:
- 纯函数,异常以返回值/exception 形式表达
- 不接 UI,UI 层(electron main / sidecar API)自己决定确认 / 进度提示
"""
import os
import shutil
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf
from send2trash import send2trash

from sidecar.logic_checker import LogicChecker


@dataclass
class RenameOp:
    src: str
    dst: str
    kind: str

    def to_dict(self) -> dict:
        return {"src": self.src, "dst": self.dst, "kind": self.kind}


@dataclass
class AutofixPlan:
    ops: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)


@dataclass
class AutofixResult:
    executed: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    path_updates: dict = field(default_factory=dict)


@dataclass
class SimulateResult:
    would_execute: list = field(default_factory=list)
    would_conflict: list = field(default_factory=list)
    predicted_path_updates: dict = field(default_factory=dict)


@dataclass
class PadResult:
    padded: int = 0
    max_duration: Optional[float] = None
    error: Optional[str] = None


def safe_rename(src, dst):
    """重命名 src → dst，处理 case-only rename（Windows/macOS 大小写不敏感文件系统）。"""
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    if os.path.normcase(src) == os.path.normcase(dst) and src != dst:
        tmp = dst + ".__case_swap__"
        os.rename(src, tmp)
        try:
            os.rename(tmp, dst)
        except Exception:
            try:
                os.rename(tmp, src)
            except Exception:
                pass
            raise
    else:
        os.rename(src, dst)
    return dst


def collect_top_level_wavs(target_dirs):
    """收集多个目录下的一级 WAV 文件（不递归）。"""
    out = []
    for d in target_dirs:
        if not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for name in names:
            if not name.lower().endswith(".wav"):
                continue
            fp = os.path.join(d, name)
            if os.path.isfile(fp):
                out.append(fp)
    return out


def collect_song_folders(workspace_root):
    """列出工作区下所有歌曲文件夹（一级目录）。"""
    if not workspace_root or not os.path.isdir(workspace_root):
        return []
    try:
        names = sorted(os.listdir(workspace_root))
    except Exception:
        return []
    return [
        os.path.join(workspace_root, name)
        for name in names
        if os.path.isdir(os.path.join(workspace_root, name))
    ]


def build_autofix_plan(song_paths):
    """构建自动修复重命名计划。

    返回 AutofixPlan(ops=可执行操作, conflicts=因冲突跳过的描述)。
    """
    raw_ops = []
    for song_path in song_paths:
        raw_ops.extend(LogicChecker.propose_simple_renames(song_path))

    deduped = []
    seen_pairs = set()
    for op in raw_ops:
        src = os.path.normpath(op["src"])
        dst = os.path.normpath(op["dst"])
        if src == dst:
            continue
        pair = (src, dst)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        deduped.append({**op, "src": src, "dst": dst})

    src_set = {op["src"] for op in deduped}
    src_casefold_set = {os.path.normcase(s) for s in src_set}

    target_counts = {}
    for op in deduped:
        k = os.path.normcase(op["dst"])
        target_counts[k] = target_counts.get(k, 0) + 1

    duplicate_targets = {k for k, c in target_counts.items() if c > 1}
    plan = AutofixPlan()
    for k in sorted(duplicate_targets):
        plan.conflicts.append(f"目标重名，已跳过: {k}")

    for op in deduped:
        dst = op["dst"]
        dst_key = os.path.normcase(dst)
        if dst_key in duplicate_targets:
            continue
        if os.path.exists(dst) and dst_key not in src_casefold_set:
            plan.conflicts.append(f"目标已存在，已跳过: {dst}")
            continue
        plan.ops.append(RenameOp(src=op["src"], dst=op["dst"], kind=op["kind"]))

    plan.ops.sort(
        key=lambda op: (
            1 if op.kind == "song_folder" else 0,
            -op.src.count(os.sep),
        )
    )
    return plan


def execute_autofix_plan(ops):
    """执行重命名计划，处理过程中产生的路径变更级联。

    例：先把 A/B/file.wav 改名为 A/B/file_new.wav，再把 A/B/ 改名为 A/B_new/，
    第二步执行时 src 应解析为已被改过的当前路径。path_updates 记录这种映射。
    """
    result = AutofixResult()

    for op in ops:
        original_src = op.src
        current_src = result.path_updates.get(original_src, original_src)
        original_dst = op.dst
        current_dst = result.path_updates.get(original_dst, original_dst)
        if current_src == current_dst:
            continue
        try:
            safe_rename(current_src, current_dst)
            executed = RenameOp(src=current_src, dst=current_dst, kind=op.kind)
            result.executed.append(executed)
            for old, mapped in list(result.path_updates.items()):
                if mapped == current_src:
                    result.path_updates[old] = current_dst
            result.path_updates[original_src] = current_dst
        except Exception as e:
            result.errors.append(
                f"重命名失败 {os.path.basename(current_src)} → "
                f"{os.path.basename(current_dst)}: {e}"
            )

    return result


# ============================================================
#  Agent 写路径:接受 LLM 自构造的 dict-style ops,统一执行
#
#  Op 类型(对应 doc/工具清单.md fix.* schema):
#    {"type": "rename",     "src": str, "dst": str}
#    {"type": "delete",     "path": str}                 # send2trash,文件/目录皆可
#    {"type": "move",       "src": str, "dst_dir": str}
#    {"type": "create_dir", "path": str}
#
#  路径白名单:所有路径必须在 workspace_root 下;否则整批拒绝(快失败,
#  避免半执行后 LLM 拿到 partial state 难收拾)。
#
#  precondition 哈希校验:不在本层。confirm 卡哈希校验是 Electron main 的事,
#  这里只负责"批准后的纯执行"。
# ============================================================


class PathOutsideWorkspaceError(ValueError):
    """有 op 引用了 workspace_root 之外的路径,整批拒绝。"""

    def __init__(self, path: str, workspace_root: str):
        self.path = path
        self.workspace_root = workspace_root
        super().__init__(
            f"路径越界:{path} 不在工作区 {workspace_root} 下"
        )


# write_text op 允许的扩展名(脑暴 §8 边界:agent 仅写 CSV/文本,不写音频/MIDI 二进制)。
# 用 allow-list 比 deny-list 安全 —— 新增 audio 扩展时不需要回头改这里。
_WRITE_TEXT_ALLOWED_EXTS = frozenset({
    ".csv", ".txt", ".md", ".json", ".log", ".toml", ".yaml", ".yml",
})


def _is_within(path: str, root: str) -> bool:
    """path 是否在 root 目录树内(normpath + commonpath 双保险,Win 大小写容忍)。"""
    try:
        p = os.path.normcase(os.path.normpath(os.path.abspath(path)))
        r = os.path.normcase(os.path.normpath(os.path.abspath(root)))
        return p == r or p.startswith(r + os.sep)
    except Exception:
        return False


def _validate_op_paths(op: dict, workspace_root: str) -> None:
    """检查单个 op 引用的所有路径都在工作区内。越界即抛 PathOutsideWorkspaceError。"""
    op_type = op.get("type")
    paths_to_check: list[str] = []
    if op_type == "rename":
        paths_to_check = [op["src"], op["dst"]]
    elif op_type == "delete":
        paths_to_check = [op["path"]]
    elif op_type == "move":
        # dst_dir 是目标目录;移动后新路径 = dst_dir/basename(src),也要在工作区
        paths_to_check = [op["src"], op["dst_dir"]]
    elif op_type == "copy":
        # 跟 move 同结构,但源不消失;目标路径 = dst_dir/basename(src)
        paths_to_check = [op["src"], op["dst_dir"]]
    elif op_type == "create_dir":
        paths_to_check = [op["path"]]
    elif op_type == "write_text":
        paths_to_check = [op["path"]]
        ext = os.path.splitext(op["path"])[1].lower()
        if ext not in _WRITE_TEXT_ALLOWED_EXTS:
            raise ValueError(
                f"write_text 拒绝扩展名 {ext!r}: 仅允许 "
                f"{sorted(_WRITE_TEXT_ALLOWED_EXTS)}(脑暴 §8 边界)"
            )
    elif op_type == "text_edit":
        paths_to_check = [op["path"]]
        ext = os.path.splitext(op["path"])[1].lower()
        if ext not in _WRITE_TEXT_ALLOWED_EXTS:
            raise ValueError(
                f"text_edit 拒绝扩展名 {ext!r}: 仅允许 "
                f"{sorted(_WRITE_TEXT_ALLOWED_EXTS)}(脑暴 §8 边界)"
            )
    else:
        raise ValueError(f"未知 op 类型:{op_type}")

    for p in paths_to_check:
        if not _is_within(p, workspace_root):
            raise PathOutsideWorkspaceError(p, workspace_root)


def execute_ops(ops: list, workspace_root: str) -> AutofixResult:
    """执行 agent 自构造或 propose_* 产出的 ops 列表。

    所有 op 用 dict 表达(LLM friendly),内部按 `type` 分派。Rename 复用
    `safe_rename` + 路径级联(`path_updates`)逻辑;其余三种是无级联的简单操作。

    工作区白名单**先行整批校验**,任一 op 越界整批拒绝,result.errors 里报具体路径。
    哈希校验不在这里(由 Electron main IPC 层做)。
    """
    result = AutofixResult()

    # --- 整批先校验路径白名单 ---
    for i, op in enumerate(ops):
        try:
            _validate_op_paths(op, workspace_root)
        except PathOutsideWorkspaceError as e:
            result.errors.append(f"op #{i} ({op.get('type')}): {e}")
            return result  # 快失败,不执行任何 op
        except ValueError as e:
            result.errors.append(f"op #{i}: {e}")
            return result

    # --- 逐 op 执行 ---
    for op in ops:
        op_type = op["type"]
        try:
            if op_type == "rename":
                _exec_rename(op, result)
            elif op_type == "delete":
                _exec_delete(op, result)
            elif op_type == "move":
                _exec_move(op, result)
            elif op_type == "copy":
                _exec_copy(op, result)
            elif op_type == "create_dir":
                _exec_create_dir(op, result)
            elif op_type == "write_text":
                _exec_write_text(op, result)
            elif op_type == "text_edit":
                _exec_text_edit(op, result)
        except Exception as e:
            result.errors.append(f"{op_type} 失败 {op}: {e}")

    return result


def simulate_ops(ops: list, workspace_root: str) -> SimulateResult:
    """Dry-run validation:逐 op 静态预测冲突,不碰磁盘。

    模拟 path_updates 级联(rename/move 改变后续 op 的 src 解析)与"已删/已建"集合,
    所以连环操作(先 rename 再 move 同一文件)能正确判断中间状态。

    Conflict codes:
    - PATH_OUTSIDE_WORKSPACE: op 引用的路径越出工作区
    - INVALID_OP: op 结构缺字段 / 未知 type
    - SRC_MISSING: rename/delete/move/copy/text_edit 的 src/path 不存在(且没被前面的 op 建出来)
    - DST_EXISTS: rename/move/copy 目标已存在(且不是 case-only swap)
    - EXT_NOT_ALLOWED: write_text / text_edit 目标扩展名不在白名单
    - EDIT_NOT_FOUND: text_edit 的 old_string 在文件中找不到
    - EDIT_AMBIGUOUS: text_edit 的 old_string 在文件中出现多次且未指定 replace_all=True
    """
    result = SimulateResult()
    seen_creates: set[str] = set()  # 前面 op 已"建"出来的路径(rename/move 的 dst, write_text 的 path)
    seen_deletes: set[str] = set()  # 前面 op 已"删"掉的路径(rename/move 的 src, delete 的 path)

    for i, op in enumerate(ops):
        op_type = op.get("type")
        # write_text / text_edit 的扩展名检查在 _validate_op_paths 内是 ValueError,
        # 需先解出来报 EXT_NOT_ALLOWED
        if op_type in ("write_text", "text_edit"):
            ext = os.path.splitext(op.get("path", ""))[1].lower()
            if ext not in _WRITE_TEXT_ALLOWED_EXTS:
                result.would_conflict.append({
                    "op_index": i, "type": op_type,
                    "code": "EXT_NOT_ALLOWED", "detail": ext,
                })
                continue
        try:
            _validate_op_paths(op, workspace_root)
        except PathOutsideWorkspaceError as e:
            result.would_conflict.append({
                "op_index": i, "type": op_type,
                "code": "PATH_OUTSIDE_WORKSPACE", "detail": str(e),
            })
            continue
        except (KeyError, ValueError) as e:
            result.would_conflict.append({
                "op_index": i, "type": op_type,
                "code": "INVALID_OP", "detail": str(e),
            })
            continue

        if op_type == "rename":
            src = result.predicted_path_updates.get(op["src"], op["src"])
            dst = op["dst"]
            if not _would_exist(src, seen_creates, seen_deletes):
                result.would_conflict.append({"op_index": i, "type": "rename", "code": "SRC_MISSING", "detail": src})
                continue
            if _would_exist(dst, seen_creates, seen_deletes) and os.path.normcase(src) != os.path.normcase(dst):
                result.would_conflict.append({"op_index": i, "type": "rename", "code": "DST_EXISTS", "detail": dst})
                continue
            result.would_execute.append({"type": "rename", "src": src, "dst": dst})
            result.predicted_path_updates[op["src"]] = dst
            seen_deletes.add(src)
            seen_creates.add(dst)
        elif op_type == "delete":
            path = result.predicted_path_updates.get(op["path"], op["path"])
            if not _would_exist(path, seen_creates, seen_deletes):
                result.would_conflict.append({"op_index": i, "type": "delete", "code": "SRC_MISSING", "detail": path})
                continue
            result.would_execute.append({"type": "delete", "path": path})
            seen_deletes.add(path)
        elif op_type == "move":
            src = result.predicted_path_updates.get(op["src"], op["src"])
            dst_dir = op["dst_dir"]
            dst = os.path.join(dst_dir, os.path.basename(src))
            if not _would_exist(src, seen_creates, seen_deletes):
                result.would_conflict.append({"op_index": i, "type": "move", "code": "SRC_MISSING", "detail": src})
                continue
            if _would_exist(dst, seen_creates, seen_deletes):
                result.would_conflict.append({"op_index": i, "type": "move", "code": "DST_EXISTS", "detail": dst})
                continue
            result.would_execute.append({"type": "move", "src": src, "dst": dst})
            result.predicted_path_updates[op["src"]] = dst
            seen_deletes.add(src)
            seen_creates.add(dst)
        elif op_type == "copy":
            src = result.predicted_path_updates.get(op["src"], op["src"])
            dst_dir = op["dst_dir"]
            dst = os.path.join(dst_dir, os.path.basename(src))
            if not _would_exist(src, seen_creates, seen_deletes):
                result.would_conflict.append({"op_index": i, "type": "copy", "code": "SRC_MISSING", "detail": src})
                continue
            if _would_exist(dst, seen_creates, seen_deletes):
                result.would_conflict.append({"op_index": i, "type": "copy", "code": "DST_EXISTS", "detail": dst})
                continue
            result.would_execute.append({"type": "copy", "src": src, "dst": dst})
            # copy 不入 seen_deletes(源保留),只 seen_creates 加 dst
            seen_creates.add(dst)
        elif op_type == "write_text":
            path = op["path"]
            ext = os.path.splitext(path)[1].lower()
            if ext not in _WRITE_TEXT_ALLOWED_EXTS:
                result.would_conflict.append({"op_index": i, "type": "write_text", "code": "EXT_NOT_ALLOWED", "detail": ext})
                continue
            result.would_execute.append({"type": "write_text", "path": path, "bytes": len(op.get("content", "").encode("utf-8"))})
            seen_creates.add(path)
        elif op_type == "text_edit":
            path = result.predicted_path_updates.get(op["path"], op["path"])
            old_string = op.get("old_string", "")
            replace_all = bool(op.get("replace_all", False))
            # 文件得真实存在(text_edit 不能改前面 op 在 seen_creates 里的"虚拟"文件;
            # 那场景应当用 write_text)
            if not os.path.isfile(path) or path in seen_deletes:
                result.would_conflict.append({"op_index": i, "type": "text_edit", "code": "SRC_MISSING", "detail": path})
                continue
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    text = f.read()
            except OSError as e:
                result.would_conflict.append({"op_index": i, "type": "text_edit", "code": "SRC_MISSING", "detail": f"{path}: {e}"})
                continue
            count = text.count(old_string) if old_string else 0
            if count == 0:
                result.would_conflict.append({"op_index": i, "type": "text_edit", "code": "EDIT_NOT_FOUND", "detail": path})
                continue
            if count > 1 and not replace_all:
                result.would_conflict.append({"op_index": i, "type": "text_edit", "code": "EDIT_AMBIGUOUS", "detail": f"{path}: {count} matches"})
                continue
            result.would_execute.append({"type": "text_edit", "path": path, "replacements": count if replace_all else 1})
        elif op_type == "create_dir":
            path = op["path"]
            result.would_execute.append({"type": "create_dir", "path": path})
            seen_creates.add(path)
        else:
            result.would_conflict.append({"op_index": i, "type": op_type, "code": "INVALID_OP", "detail": f"未知 op type: {op_type}"})

    return result


def _would_exist(path: str, seen_creates: set, seen_deletes: set) -> bool:
    """路径在当前模拟状态下是否会存在 = 真实存在 + 前面 op 建过 - 前面 op 删过。"""
    if path in seen_deletes:
        # 删除后又被建出来(rename dst 等)? 优先看 seen_creates 顺序无法区分,简化:删过就当不存在,除非又建出来
        return path in seen_creates
    return os.path.exists(path) or path in seen_creates


def _exec_rename(op: dict, result: AutofixResult) -> None:
    """rename 复用既有 safe_rename + path_updates 级联(execute_autofix_plan 同款)。"""
    original_src = op["src"]
    current_src = result.path_updates.get(original_src, original_src)
    original_dst = op["dst"]
    current_dst = result.path_updates.get(original_dst, original_dst)
    if current_src == current_dst:
        return
    safe_rename(current_src, current_dst)
    result.executed.append({
        "type": "rename",
        "src": current_src,
        "dst": current_dst,
    })
    for old, mapped in list(result.path_updates.items()):
        if mapped == current_src:
            result.path_updates[old] = current_dst
    result.path_updates[original_src] = current_dst


def _exec_delete(op: dict, result: AutofixResult) -> None:
    """delete 走 send2trash(可恢复,符合"agent 操作可逆"原则)。文件/目录皆可。"""
    path = result.path_updates.get(op["path"], op["path"])
    if not os.path.exists(path):
        # 不抛错(可能已被前面的 op 顺手处理),记一下让上层知道
        result.errors.append(f"delete: 路径不存在(可能已删除){path}")
        return
    send2trash(path)
    result.executed.append({"type": "delete", "path": path})


def _exec_move(op: dict, result: AutofixResult) -> None:
    """move = shutil.move,目标是目录,保留原 basename。"""
    src = result.path_updates.get(op["src"], op["src"])
    dst_dir = op["dst_dir"]
    os.makedirs(dst_dir, exist_ok=True)  # 目标目录不存在就建,符合"拖放到不存在的目录"直觉
    dst_path = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst_path):
        raise FileExistsError(f"目标已存在:{dst_path}")
    shutil.move(src, dst_path)
    result.executed.append({"type": "move", "src": src, "dst": dst_path})
    result.path_updates[op["src"]] = dst_path


def _exec_copy(op: dict, result: AutofixResult) -> None:
    """copy = shutil.copy2(保留 mtime / 权限),目标是目录,保留原 basename。源不动。"""
    src = result.path_updates.get(op["src"], op["src"])
    dst_dir = op["dst_dir"]
    os.makedirs(dst_dir, exist_ok=True)
    dst_path = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst_path):
        raise FileExistsError(f"目标已存在:{dst_path}")
    shutil.copy2(src, dst_path)
    result.executed.append({"type": "copy", "src": src, "dst": dst_path})


def _exec_create_dir(op: dict, result: AutofixResult) -> None:
    """create_dir = os.makedirs(exist_ok=True);幂等。"""
    path = op["path"]
    os.makedirs(path, exist_ok=True)
    result.executed.append({"type": "create_dir", "path": path})


def _exec_write_text(op: dict, result: AutofixResult) -> None:
    """write_text = 全文原子写(tmp + os.replace)。

    扩展名白名单已由 _validate_op_paths 整批校验;路径越界已拒。本函数只管写。
    覆盖既有文件不预 backup —— send2trash 不适用(文件还在原路径,只是内容换了),
    回滚靠 review_log + agent 重新生成内容。

    UTF-8 写,不强制 BOM。CRLF 不保留(content 写啥就写啥)—— 跟 api.py /tools/write_text
    现行行为一致。
    """
    path = result.path_updates.get(op["path"], op["path"])
    content = op["content"]
    parent = os.path.dirname(path) or "."
    if not os.path.isdir(parent):
        raise FileNotFoundError(f"父目录不存在:{parent}")
    tmp = path + ".__write_tmp__"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        # 失败时清 tmp,别留 .__write_tmp__ 在工作区污染文件树
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    result.executed.append({
        "type": "write_text",
        "path": path,
        "bytes_written": os.path.getsize(path),
    })


def _exec_text_edit(op: dict, result: AutofixResult) -> None:
    """text_edit = 精确字符串替换。读全文 → count old_string → 替换 → 原子写回。

    校验:old_string 必须出现 >=1 次;>1 次必须 replace_all=True,否则 raise(对应
    simulate 的 EDIT_NOT_FOUND / EDIT_AMBIGUOUS)。replace_all=False 时只换第一处。

    读 utf-8-sig 容忍 BOM;写 utf-8 不带 BOM(content 写啥就写啥)。
    """
    path = result.path_updates.get(op["path"], op["path"])
    old_string = op["old_string"]
    new_string = op["new_string"]
    replace_all = bool(op.get("replace_all", False))

    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()

    count = text.count(old_string) if old_string else 0
    if count == 0:
        raise ValueError(f"text_edit: old_string 未找到 {path}")
    if count > 1 and not replace_all:
        raise ValueError(
            f"text_edit: old_string 在 {path} 出现 {count} 次,需指定 replace_all=True 或给更长上下文"
        )

    if replace_all:
        new_text = text.replace(old_string, new_string)
    else:
        new_text = text.replace(old_string, new_string, 1)

    tmp = path + ".__write_tmp__"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    result.executed.append({
        "type": "text_edit",
        "path": path,
        "replacements": count if replace_all else 1,
    })


def _read_wav_metas(wav_files):
    """读取一组 WAV 的 (path, samplerate, channels, frames)；任一失败则返回错误。"""
    metas = []
    for fp in wav_files:
        try:
            with sf.SoundFile(fp) as f:
                sr = int(f.samplerate)
                ch = int(f.channels)
                frames = int(f.frames)
                if sr <= 0 or frames < 0:
                    raise RuntimeError("采样率或帧数无效")
        except Exception as e:
            return [], f"无法读取 WAV: {fp} - {e}"
        metas.append((fp, sr, ch, frames))
    return metas, None


def _pad_to_target_frames(metas, target_frames):
    """对每个 WAV 在尾部补静音到 target_frames。返回 (padded_count, error)。"""
    padded = 0
    for fp, sr, ch, frames in metas:
        if target_frames <= frames:
            continue
        tmp_path = fp + ".pad_tmp.wav"
        try:
            with sf.SoundFile(fp, mode="r") as in_f:
                with sf.SoundFile(
                    tmp_path,
                    mode="w",
                    samplerate=in_f.samplerate,
                    channels=in_f.channels,
                    format=in_f.format,
                    subtype=in_f.subtype,
                ) as out_f:
                    block = 65536
                    while True:
                        data = in_f.read(block, dtype="int32", always_2d=True)
                        if data.size == 0:
                            break
                        out_f.write(data)

                    remaining = target_frames - frames
                    if remaining > 0:
                        silence_block = np.zeros((min(block, remaining), ch), dtype=np.int32)
                        while remaining > 0:
                            chunk = min(silence_block.shape[0], remaining)
                            out_f.write(silence_block[:chunk])
                            remaining -= chunk

            os.replace(tmp_path, fp)
            padded += 1
        except Exception as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return padded, f"补空白失败：{fp} - {e}"

    return padded, None


def pad_wavs_to_longest(wav_files):
    """把一批 WAV 文件补空白到其中最长的帧数（采样点对齐）。

    若采样率不一致返回 error。
    """
    metas, err = _read_wav_metas(wav_files)
    if err:
        return PadResult(error=err)
    if not metas:
        return PadResult(error="未读取到任何 WAV 文件")

    rates = {sr for _fp, sr, _ch, _frames in metas}
    if len(rates) > 1:
        detail = "; ".join(
            f"{os.path.basename(fp)}: {sr} Hz" for fp, sr, _ch, _frames in metas
        )
        return PadResult(error=f"采样率不一致：{detail}")

    samplerate = next(iter(rates))
    max_frames = max(frames for _fp, _sr, _ch, frames in metas)

    padded, write_err = _pad_to_target_frames(metas, max_frames)
    if write_err:
        return PadResult(padded=padded, error=write_err)

    return PadResult(padded=padded, max_duration=float(max_frames) / float(samplerate))


def pad_song_to_longest(song_path):
    """歌曲级入口：对 分轨wav / 总轨wav / 混音工程原文件 三个目录的一级 WAV 统一时长。"""
    if not song_path or not os.path.isdir(song_path):
        return PadResult(error="无效路径")

    target_dirs = [
        os.path.join(song_path, "分轨wav"),
        os.path.join(song_path, "总轨wav"),
        os.path.join(song_path, "混音工程原文件"),
    ]
    wav_files = collect_top_level_wavs(target_dirs)
    if not wav_files:
        return PadResult(error="未找到可处理的一级 WAV 文件")

    return pad_wavs_to_longest(wav_files)
