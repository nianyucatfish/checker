"""sidecar.fixers 的单元测试 —— 用 tempfile + soundfile 构造合成数据。"""

import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from sidecar import fixers


@pytest.fixture
def tmp_workspace():
    d = tempfile.mkdtemp(prefix="sidecar_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write_wav(path: str, frames: int, sr: int = 96000, channels: int = 2, subtype: str = "PCM_24"):
    """写一个指定帧数 / 采样率 / 通道数的 WAV 全零样本。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = np.zeros((frames, channels), dtype=np.int32)
    sf.write(path, data, sr, subtype=subtype)


def _make_song(workspace: str, song_name: str = "歌手_望春风_扒谱者"):
    """在工作区下铺一棵最小可用的歌曲文件夹结构。"""
    song = os.path.join(workspace, song_name)
    for d in ("分轨wav", "总轨wav", "midi", "csv", "混音工程原文件"):
        os.makedirs(os.path.join(song, d), exist_ok=True)
    return song


# -----------------------------
#  safe_rename
# -----------------------------


def test_safe_rename_basic(tmp_workspace):
    src = os.path.join(tmp_workspace, "a.txt")
    Path(src).write_text("hi", encoding="utf-8")
    dst = os.path.join(tmp_workspace, "b.txt")
    out = fixers.safe_rename(src, dst)
    assert out == dst
    assert os.path.exists(dst)
    assert not os.path.exists(src)


def test_safe_rename_case_only(tmp_workspace):
    """case-only rename 在 NTFS / APFS 默认大小写不敏感时应能改大小写。"""
    src = os.path.join(tmp_workspace, "lower.txt")
    Path(src).write_text("hi", encoding="utf-8")
    dst = os.path.join(tmp_workspace, "Lower.txt")
    fixers.safe_rename(src, dst)
    # 在大小写不敏感的文件系统上 listdir 应反映大小写
    listed = os.listdir(tmp_workspace)
    assert "Lower.txt" in listed


def test_safe_rename_missing_src(tmp_workspace):
    with pytest.raises(FileNotFoundError):
        fixers.safe_rename(os.path.join(tmp_workspace, "nope"), os.path.join(tmp_workspace, "x"))


# -----------------------------
#  collect helpers
# -----------------------------


def test_collect_top_level_wavs_skips_subdirs(tmp_workspace):
    sub = os.path.join(tmp_workspace, "sub")
    os.makedirs(sub)
    Path(os.path.join(tmp_workspace, "a.wav")).write_bytes(b"")
    Path(os.path.join(tmp_workspace, "b.txt")).write_bytes(b"")
    Path(os.path.join(sub, "deep.wav")).write_bytes(b"")
    found = fixers.collect_top_level_wavs([tmp_workspace])
    names = [os.path.basename(p) for p in found]
    assert "a.wav" in names
    assert "deep.wav" not in names
    assert "b.txt" not in names


def test_collect_top_level_wavs_handles_missing_dir(tmp_workspace):
    found = fixers.collect_top_level_wavs([os.path.join(tmp_workspace, "missing")])
    assert found == []


def test_collect_song_folders(tmp_workspace):
    _make_song(tmp_workspace, "A_x_y")
    _make_song(tmp_workspace, "B_x_y")
    Path(os.path.join(tmp_workspace, "loose_file.txt")).write_text("hi", encoding="utf-8")
    folders = fixers.collect_song_folders(tmp_workspace)
    names = [os.path.basename(p) for p in folders]
    assert names == ["A_x_y", "B_x_y"]


# -----------------------------
#  build_autofix_plan / execute_autofix_plan
# -----------------------------


def test_build_autofix_plan_normalizes_simple_renames(tmp_workspace):
    """带多余空格的文件名应该被规则识别。"""
    song = _make_song(tmp_workspace, "歌手_望春风_扒谱者")
    # 故意制造一个带空格的命名错误
    bad = os.path.join(song, "分轨wav", " 歌手_望春风_扒谱者_Vocal_A.wav".strip())
    # 上面 strip 会把空格去掉，要构造真错误得在内部
    bad = os.path.join(song, "分轨wav", "歌手_望春风_扒谱者_Vocal_A .wav")
    Path(bad).write_bytes(b"")
    plan = fixers.build_autofix_plan([song])
    # 至少识别出一个 rename 操作
    assert any(
        "Vocal_A" in os.path.basename(op.dst)
        for op in plan.ops
    ), f"应识别 Vocal_A，得到 {[op.to_dict() for op in plan.ops]}"


def test_build_autofix_plan_no_ops_when_clean(tmp_workspace):
    song = _make_song(tmp_workspace, "歌手_望春风_扒谱者")
    plan = fixers.build_autofix_plan([song])
    assert plan.ops == []
    assert plan.conflicts == []


def test_execute_autofix_plan_runs_and_returns_executed(tmp_workspace):
    src = os.path.join(tmp_workspace, "a.txt")
    Path(src).write_text("hi", encoding="utf-8")
    dst = os.path.join(tmp_workspace, "b.txt")
    op = fixers.RenameOp(src=src, dst=dst, kind="file")
    result = fixers.execute_autofix_plan([op])
    assert len(result.executed) == 1
    assert result.executed[0].dst == dst
    assert result.errors == []
    assert os.path.exists(dst)


def test_execute_autofix_plan_handles_missing_src(tmp_workspace):
    op = fixers.RenameOp(
        src=os.path.join(tmp_workspace, "missing"),
        dst=os.path.join(tmp_workspace, "x"),
        kind="file",
    )
    result = fixers.execute_autofix_plan([op])
    assert result.executed == []
    assert len(result.errors) == 1


def test_execute_autofix_plan_cascades_path_updates(tmp_workspace):
    """先改文件，再改父文件夹时，第二步的 src 应自动用更新后的路径。"""
    folder = os.path.join(tmp_workspace, "old_dir")
    os.makedirs(folder)
    file_path = os.path.join(folder, "old_name.txt")
    Path(file_path).write_text("hi", encoding="utf-8")

    new_file_path = os.path.join(folder, "new_name.txt")
    new_folder = os.path.join(tmp_workspace, "new_dir")

    ops = [
        fixers.RenameOp(src=file_path, dst=new_file_path, kind="file"),
        fixers.RenameOp(src=folder, dst=new_folder, kind="managed_dir"),
    ]
    result = fixers.execute_autofix_plan(ops)
    assert len(result.executed) == 2
    assert os.path.exists(os.path.join(new_folder, "new_name.txt"))


# -----------------------------
#  pad_wavs_to_longest
# -----------------------------


def test_pad_wavs_to_longest_extends_shorter(tmp_workspace):
    sr = 48000
    short = os.path.join(tmp_workspace, "short.wav")
    long_ = os.path.join(tmp_workspace, "long.wav")
    _write_wav(short, frames=sr * 1, sr=sr)        # 1s
    _write_wav(long_, frames=sr * 2, sr=sr)        # 2s
    result = fixers.pad_wavs_to_longest([short, long_])
    assert result.error is None
    assert result.padded == 1  # 只有 short 被改写
    assert result.max_duration == pytest.approx(2.0, abs=1e-6)
    # 验证补完后 short 也变 2s
    with sf.SoundFile(short) as f:
        assert f.frames == sr * 2


def test_pad_wavs_to_longest_no_op_when_equal(tmp_workspace):
    sr = 48000
    a = os.path.join(tmp_workspace, "a.wav")
    b = os.path.join(tmp_workspace, "b.wav")
    _write_wav(a, frames=sr, sr=sr)
    _write_wav(b, frames=sr, sr=sr)
    result = fixers.pad_wavs_to_longest([a, b])
    assert result.error is None
    assert result.padded == 0


def test_pad_wavs_to_longest_rejects_mixed_sample_rates(tmp_workspace):
    a = os.path.join(tmp_workspace, "a.wav")
    b = os.path.join(tmp_workspace, "b.wav")
    _write_wav(a, frames=48000, sr=48000)
    _write_wav(b, frames=96000, sr=96000)
    result = fixers.pad_wavs_to_longest([a, b])
    assert result.error is not None
    assert "采样率不一致" in result.error


def test_pad_song_to_longest_end_to_end(tmp_workspace):
    song = _make_song(tmp_workspace, "X_望春风_Y")
    sr = 48000
    _write_wav(os.path.join(song, "分轨wav", "X_望春风_Y_Vocal_A.wav"), frames=sr * 2, sr=sr)
    _write_wav(os.path.join(song, "总轨wav", "X_望春风_Y_Mix_A.wav"), frames=sr * 3, sr=sr)
    result = fixers.pad_song_to_longest(song)
    assert result.error is None
    assert result.padded == 1
    assert result.max_duration == pytest.approx(3.0, abs=1e-6)
