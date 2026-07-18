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


# -----------------------------
#  execute_ops:agent 自构造 dict-style ops 写路径
# -----------------------------


def test_execute_ops_rename(tmp_workspace):
    src = os.path.join(tmp_workspace, "a.txt")
    Path(src).write_text("hi", encoding="utf-8")
    dst = os.path.join(tmp_workspace, "b.txt")
    result = fixers.execute_ops(
        [{"type": "rename", "src": src, "dst": dst}],
        workspace_root=tmp_workspace,
    )
    assert result.errors == []
    assert os.path.exists(dst)
    assert not os.path.exists(src)
    assert result.executed[0]["type"] == "rename"


def test_execute_ops_delete_uses_trash(tmp_workspace):
    """delete 走 send2trash 不是 os.remove,源路径应消失。"""
    p = os.path.join(tmp_workspace, "trash_me.txt")
    Path(p).write_text("x", encoding="utf-8")
    result = fixers.execute_ops(
        [{"type": "delete", "path": p}],
        workspace_root=tmp_workspace,
    )
    assert result.errors == []
    assert not os.path.exists(p)
    assert result.executed[0] == {"type": "delete", "path": p}


def test_execute_ops_delete_missing_path_records_error_not_raises(tmp_workspace):
    """删一个不存在的路径不该抛,只该在 errors 里记一笔(可能上一 op 已删)。"""
    p = os.path.join(tmp_workspace, "nonexistent.txt")
    result = fixers.execute_ops(
        [{"type": "delete", "path": p}],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    assert any("路径不存在" in e for e in result.errors)


def test_execute_ops_move_creates_dst_dir_if_missing(tmp_workspace):
    src = os.path.join(tmp_workspace, "orphan.mid")
    Path(src).write_text("midi", encoding="utf-8")
    dst_dir = os.path.join(tmp_workspace, "song", "midi")  # 还不存在

    result = fixers.execute_ops(
        [{"type": "move", "src": src, "dst_dir": dst_dir}],
        workspace_root=tmp_workspace,
    )
    assert result.errors == []
    expected = os.path.join(dst_dir, "orphan.mid")
    assert os.path.exists(expected)
    assert not os.path.exists(src)
    assert result.path_updates[src] == expected


def test_execute_ops_move_rejects_when_dst_exists(tmp_workspace):
    src = os.path.join(tmp_workspace, "a.txt")
    Path(src).write_text("src", encoding="utf-8")
    dst_dir = os.path.join(tmp_workspace, "sub")
    os.makedirs(dst_dir)
    Path(os.path.join(dst_dir, "a.txt")).write_text("blocker", encoding="utf-8")

    result = fixers.execute_ops(
        [{"type": "move", "src": src, "dst_dir": dst_dir}],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    assert any("目标已存在" in e for e in result.errors)
    assert os.path.exists(src)  # 源应保留


def test_execute_ops_create_dir_idempotent(tmp_workspace):
    target = os.path.join(tmp_workspace, "midi")
    os.makedirs(target)  # 已存在
    result = fixers.execute_ops(
        [{"type": "create_dir", "path": target}],
        workspace_root=tmp_workspace,
    )
    assert result.errors == []
    assert os.path.isdir(target)


def test_execute_ops_path_outside_workspace_rejects_whole_batch(tmp_workspace):
    """快失败:任一 op 越界,整批拒绝,不做任何写操作。"""
    inside = os.path.join(tmp_workspace, "ok.txt")
    Path(inside).write_text("safe", encoding="utf-8")
    bad_path = "/etc/passwd" if os.name != "nt" else "C:/Windows/System32/cmd.exe"

    result = fixers.execute_ops(
        [
            {"type": "rename", "src": inside, "dst": inside + ".renamed"},
            {"type": "delete", "path": bad_path},  # 越界
        ],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    assert any("路径越界" in e for e in result.errors)
    assert os.path.exists(inside)  # 第一个 op 也没执行


def test_execute_ops_unknown_type_rejects_whole_batch(tmp_workspace):
    p = os.path.join(tmp_workspace, "a.txt")
    Path(p).write_text("x", encoding="utf-8")
    result = fixers.execute_ops(
        [{"type": "frobnicate", "path": p}],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    assert any("未知 op 类型" in e for e in result.errors)
    assert os.path.exists(p)


def test_execute_ops_mixed_batch(tmp_workspace):
    """rename + delete + create_dir + move 混合一批,顺序执行。"""
    a = os.path.join(tmp_workspace, "a.txt")
    Path(a).write_text("a", encoding="utf-8")
    extra = os.path.join(tmp_workspace, "extra.txt")
    Path(extra).write_text("rm me", encoding="utf-8")
    orphan = os.path.join(tmp_workspace, "orphan.mid")
    Path(orphan).write_text("m", encoding="utf-8")
    midi_dir = os.path.join(tmp_workspace, "midi")

    ops = [
        {"type": "rename", "src": a, "dst": os.path.join(tmp_workspace, "a_normalized.txt")},
        {"type": "delete", "path": extra},
        {"type": "create_dir", "path": midi_dir},
        {"type": "move", "src": orphan, "dst_dir": midi_dir},
    ]
    result = fixers.execute_ops(ops, workspace_root=tmp_workspace)
    assert result.errors == []
    assert len(result.executed) == 4
    assert os.path.exists(os.path.join(tmp_workspace, "a_normalized.txt"))
    assert not os.path.exists(extra)
    assert os.path.isdir(midi_dir)
    assert os.path.exists(os.path.join(midi_dir, "orphan.mid"))


def test_execute_ops_rename_cascade_child_then_parent(tmp_workspace):
    """子路径先改、父目录后改:文件系统层自然带过去,两步都成功。

    cascade 顺序由 build_autofix_plan 排序保证;agent 自构造 ops 时也应遵守同样
    顺序(propose_rename_plan 输出已经排好)。
    """
    parent = os.path.join(tmp_workspace, "old_parent")
    os.makedirs(parent)
    child = os.path.join(parent, "old_child.txt")
    Path(child).write_text("c", encoding="utf-8")
    renamed_child = os.path.join(parent, "new_child.txt")
    new_parent = os.path.join(tmp_workspace, "new_parent")

    ops = [
        {"type": "rename", "src": child, "dst": renamed_child},
        {"type": "rename", "src": parent, "dst": new_parent},
    ]
    result = fixers.execute_ops(ops, workspace_root=tmp_workspace)
    assert result.errors == []
    assert os.path.exists(os.path.join(new_parent, "new_child.txt"))


# -----------------------------
#  copy op
# -----------------------------


def test_execute_ops_copy_preserves_source(tmp_workspace):
    """典型场景:混音工程缺人声,从分轨 copy 一份。源文件留下。"""
    src = os.path.join(tmp_workspace, "stems", "Vocal_A.wav")
    os.makedirs(os.path.dirname(src))
    Path(src).write_text("audio bytes", encoding="utf-8")
    dst_dir = os.path.join(tmp_workspace, "proj")

    result = fixers.execute_ops(
        [{"type": "copy", "src": src, "dst_dir": dst_dir}],
        workspace_root=tmp_workspace,
    )
    assert result.errors == []
    expected_dst = os.path.join(dst_dir, "Vocal_A.wav")
    assert os.path.exists(expected_dst)
    assert os.path.exists(src)  # 源保留
    assert result.executed[0]["type"] == "copy"
    assert result.executed[0]["src"] == src
    assert result.executed[0]["dst"] == expected_dst


def test_execute_ops_copy_creates_dst_dir(tmp_workspace):
    src = os.path.join(tmp_workspace, "a.txt")
    Path(src).write_text("x", encoding="utf-8")
    dst_dir = os.path.join(tmp_workspace, "new_folder", "deeper")  # 不存在

    result = fixers.execute_ops(
        [{"type": "copy", "src": src, "dst_dir": dst_dir}],
        workspace_root=tmp_workspace,
    )
    assert result.errors == []
    assert os.path.exists(os.path.join(dst_dir, "a.txt"))


def test_execute_ops_copy_rejects_when_dst_exists(tmp_workspace):
    src = os.path.join(tmp_workspace, "a.txt")
    Path(src).write_text("src", encoding="utf-8")
    dst_dir = os.path.join(tmp_workspace, "sub")
    os.makedirs(dst_dir)
    Path(os.path.join(dst_dir, "a.txt")).write_text("blocker", encoding="utf-8")

    result = fixers.execute_ops(
        [{"type": "copy", "src": src, "dst_dir": dst_dir}],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    assert any("目标已存在" in e for e in result.errors)
    assert os.path.exists(src)


def test_execute_ops_copy_rejects_out_of_workspace(tmp_workspace):
    src = os.path.join(tmp_workspace, "a.txt")
    Path(src).write_text("x", encoding="utf-8")
    bad_dir = "C:/Windows/Temp" if os.name == "nt" else "/tmp"

    result = fixers.execute_ops(
        [{"type": "copy", "src": src, "dst_dir": bad_dir}],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    assert any("路径越界" in e for e in result.errors)


# -----------------------------
#  text_edit op
# -----------------------------


def test_execute_ops_text_edit_basic(tmp_workspace):
    p = os.path.join(tmp_workspace, "header.csv")
    Path(p).write_text("TIME ,LABEL\n00:01,Intro\n", encoding="utf-8")

    result = fixers.execute_ops(
        [{"type": "text_edit", "path": p, "old_string": "TIME ,LABEL", "new_string": "TIME,LABEL"}],
        workspace_root=tmp_workspace,
    )
    assert result.errors == []
    assert Path(p).read_text(encoding="utf-8") == "TIME,LABEL\n00:01,Intro\n"
    assert result.executed[0]["type"] == "text_edit"
    assert result.executed[0]["replacements"] == 1


def test_execute_ops_text_edit_replace_all(tmp_workspace):
    p = os.path.join(tmp_workspace, "beat.csv")
    Path(p).write_text("0:1,a\n0:2,b\n0:3,c\n", encoding="utf-8")

    result = fixers.execute_ops(
        [{"type": "text_edit", "path": p, "old_string": "0:", "new_string": "00:0", "replace_all": True}],
        workspace_root=tmp_workspace,
    )
    assert result.errors == []
    assert Path(p).read_text(encoding="utf-8") == "00:01,a\n00:02,b\n00:03,c\n"
    assert result.executed[0]["replacements"] == 3


def test_execute_ops_text_edit_not_found_raises(tmp_workspace):
    p = os.path.join(tmp_workspace, "a.txt")
    Path(p).write_text("hello world\n", encoding="utf-8")
    result = fixers.execute_ops(
        [{"type": "text_edit", "path": p, "old_string": "nonexistent", "new_string": "X"}],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    assert any("old_string 未找到" in e for e in result.errors)
    # 文件未被改
    assert Path(p).read_text(encoding="utf-8") == "hello world\n"


def test_execute_ops_text_edit_ambiguous_rejects(tmp_workspace):
    """old_string 多次出现且没指定 replace_all → 报错,不动文件。"""
    p = os.path.join(tmp_workspace, "a.txt")
    Path(p).write_text("foo bar foo\n", encoding="utf-8")
    result = fixers.execute_ops(
        [{"type": "text_edit", "path": p, "old_string": "foo", "new_string": "X"}],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    assert any("出现 2 次" in e for e in result.errors)
    assert Path(p).read_text(encoding="utf-8") == "foo bar foo\n"


def test_execute_ops_text_edit_rejects_disallowed_ext(tmp_workspace):
    """text_edit 复用 write_text 白名单,.wav 不在允许列表。"""
    p = os.path.join(tmp_workspace, "a.wav")
    Path(p).write_text("not actually audio", encoding="utf-8")
    result = fixers.execute_ops(
        [{"type": "text_edit", "path": p, "old_string": "not", "new_string": "x"}],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    assert any("拒绝扩展名" in e for e in result.errors)


# -----------------------------
#  simulate_ops:copy / text_edit dry-run
# -----------------------------


def test_simulate_copy_basic(tmp_workspace):
    src = os.path.join(tmp_workspace, "a.txt")
    Path(src).write_text("x", encoding="utf-8")
    dst_dir = os.path.join(tmp_workspace, "sub")

    sim = fixers.simulate_ops(
        [{"type": "copy", "src": src, "dst_dir": dst_dir}],
        workspace_root=tmp_workspace,
    )
    assert sim.would_conflict == []
    assert sim.would_execute[0]["type"] == "copy"
    # 源还在(simulate 没动磁盘)
    assert os.path.exists(src)


def test_simulate_copy_src_missing(tmp_workspace):
    src = os.path.join(tmp_workspace, "ghost.txt")  # 不存在
    dst_dir = os.path.join(tmp_workspace, "sub")

    sim = fixers.simulate_ops(
        [{"type": "copy", "src": src, "dst_dir": dst_dir}],
        workspace_root=tmp_workspace,
    )
    assert sim.would_execute == []
    assert sim.would_conflict[0]["code"] == "SRC_MISSING"


def test_simulate_copy_dst_exists(tmp_workspace):
    src = os.path.join(tmp_workspace, "a.txt")
    Path(src).write_text("x", encoding="utf-8")
    dst_dir = os.path.join(tmp_workspace, "sub")
    os.makedirs(dst_dir)
    Path(os.path.join(dst_dir, "a.txt")).write_text("blocker", encoding="utf-8")

    sim = fixers.simulate_ops(
        [{"type": "copy", "src": src, "dst_dir": dst_dir}],
        workspace_root=tmp_workspace,
    )
    assert sim.would_execute == []
    assert sim.would_conflict[0]["code"] == "DST_EXISTS"


def test_simulate_text_edit_not_found(tmp_workspace):
    p = os.path.join(tmp_workspace, "a.txt")
    Path(p).write_text("hello\n", encoding="utf-8")
    sim = fixers.simulate_ops(
        [{"type": "text_edit", "path": p, "old_string": "missing", "new_string": "X"}],
        workspace_root=tmp_workspace,
    )
    assert sim.would_execute == []
    assert sim.would_conflict[0]["code"] == "EDIT_NOT_FOUND"


def test_simulate_text_edit_ambiguous(tmp_workspace):
    p = os.path.join(tmp_workspace, "a.txt")
    Path(p).write_text("foo foo foo\n", encoding="utf-8")
    sim = fixers.simulate_ops(
        [{"type": "text_edit", "path": p, "old_string": "foo", "new_string": "X"}],
        workspace_root=tmp_workspace,
    )
    assert sim.would_execute == []
    assert sim.would_conflict[0]["code"] == "EDIT_AMBIGUOUS"
    assert "3 matches" in sim.would_conflict[0]["detail"]


def test_simulate_text_edit_replace_all_ok(tmp_workspace):
    p = os.path.join(tmp_workspace, "a.txt")
    Path(p).write_text("foo foo foo\n", encoding="utf-8")
    sim = fixers.simulate_ops(
        [{"type": "text_edit", "path": p, "old_string": "foo", "new_string": "X", "replace_all": True}],
        workspace_root=tmp_workspace,
    )
    assert sim.would_conflict == []
    assert sim.would_execute[0]["replacements"] == 3


def test_simulate_text_edit_ext_not_allowed(tmp_workspace):
    p = os.path.join(tmp_workspace, "a.wav")
    Path(p).write_text("not audio", encoding="utf-8")
    sim = fixers.simulate_ops(
        [{"type": "text_edit", "path": p, "old_string": "not", "new_string": "X"}],
        workspace_root=tmp_workspace,
    )
    assert sim.would_execute == []
    assert sim.would_conflict[0]["code"] == "EXT_NOT_ALLOWED"
