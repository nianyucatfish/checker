"""
sidecar.fixers — 把 main_window.py 里的写操作核心逻辑剥离出来，纯函数风格。
设计原则：
- 不依赖 PyQt（无 QMessageBox / QDialog / 信号）
- 异常以返回值/exception 形式表达
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf

from logic_checker import LogicChecker


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
    剥离自 main_window.py:2105 _build_autofix_plan。
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
    剥离自 main_window.py:2199-2218 _run_autofix 的执行循环。
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
    """对每个 WAV 在尾部补静音到 target_frames。返回 (padded_count, error)。

    剥离自 main_window.py:602 _do_pad_wavs，去掉 watcher / QMessageBox。
    """
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

    若采样率不一致返回 error。剥离自 main_window.py:555 _pad_wavs_to_longest。
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
    """歌曲级入口：对 分轨wav / 总轨wav / 混音工程原文件 三个目录的一级 WAV 统一时长。

    剥离自 main_window.py:504 trim_song_wavs_to_shortest 的纯逻辑（去掉 UI 确认与 log）。
    """
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
