"""sidecar.state_tree 的单元测试。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from sidecar import state_tree


@pytest.fixture
def tmp_cache(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="state_tree_test_"))
    monkeypatch.setattr(state_tree, "_cache_root", lambda: d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_init_creates_with_18_states(tmp_cache):
    p = state_tree.init_state_tree("歌手_望春风_扒谱者")
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# 歌手_望春风_扒谱者\n")
    assert text.count("- [ ] ") == 18
    assert text.count("- [x] ") == 0
    assert "- [ ] 1.1 分工表完整性" in text
    assert "- [ ] 3.3 标记已验收" in text


def test_init_idempotent(tmp_cache):
    """已存在的 md 不被覆盖(用户进度不能被清)。"""
    p = state_tree.init_state_tree("歌_A_人")
    state_tree.update_state_tree("歌_A_人", "1.1", done=True)
    after_first = p.read_text(encoding="utf-8")
    assert "- [x] 1.1" in after_first

    state_tree.init_state_tree("歌_A_人")
    after_second = p.read_text(encoding="utf-8")
    assert after_second == after_first


def test_read_returns_full_text(tmp_cache):
    state_tree.init_state_tree("歌_A_人")
    text = state_tree.read_state_tree("歌_A_人")
    assert "- [ ] 1.1 分工表完整性" in text
    assert text.endswith("\n")


def test_read_raises_when_not_initialized(tmp_cache):
    with pytest.raises(FileNotFoundError):
        state_tree.read_state_tree("never_inited")


def test_update_done_true(tmp_cache):
    state_tree.init_state_tree("歌_A_人")
    state_tree.update_state_tree("歌_A_人", "1.2", done=True)
    text = state_tree.read_state_tree("歌_A_人")
    assert "- [x] 1.2 文件夹命名 + 5 目录结构" in text
    assert "- [ ] 1.1 分工表完整性" in text


def test_update_with_note(tmp_cache):
    state_tree.init_state_tree("歌_A_人")
    state_tree.update_state_tree(
        "歌_A_人", "1.3", done=False,
        note="缺 BG(干声).wav,工作区无候选",
    )
    text = state_tree.read_state_tree("歌_A_人")
    assert "- [ ] 1.3 各目录文件齐全度 — 缺 BG(干声).wav,工作区无候选" in text


def test_update_note_none_keeps_existing(tmp_cache):
    """note=None 不动现有 note(只翻 checkbox 的场景)。"""
    state_tree.init_state_tree("歌_A_人")
    state_tree.update_state_tree("歌_A_人", "1.3", done=False, note="缺文件")
    state_tree.update_state_tree("歌_A_人", "1.3", done=True, note=None)
    text = state_tree.read_state_tree("歌_A_人")
    assert "- [x] 1.3 各目录文件齐全度 — 缺文件" in text


def test_update_note_empty_clears(tmp_cache):
    """note='' 清空现有 note。"""
    state_tree.init_state_tree("歌_A_人")
    state_tree.update_state_tree("歌_A_人", "1.3", done=False, note="临时备注")
    state_tree.update_state_tree("歌_A_人", "1.3", done=True, note="")
    text = state_tree.read_state_tree("歌_A_人")
    assert "- [x] 1.3 各目录文件齐全度\n" in text
    assert "临时备注" not in text


def test_update_invalid_state_id_raises(tmp_cache):
    state_tree.init_state_tree("歌_A_人")
    with pytest.raises(state_tree.StateTreeError, match="不在白名单"):
        state_tree.update_state_tree("歌_A_人", "9.9", done=True)


def test_update_preserves_unrelated_lines(tmp_cache):
    """改一行不能误伤其他行的 note。"""
    state_tree.init_state_tree("歌_A_人")
    state_tree.update_state_tree("歌_A_人", "1.1", done=True, note="all good")
    state_tree.update_state_tree("歌_A_人", "1.5", done=False, note="Mix_A 短 47 帧")
    state_tree.update_state_tree("歌_A_人", "2.3", done=True)

    text = state_tree.read_state_tree("歌_A_人")
    assert "- [x] 1.1 分工表完整性 — all good" in text
    assert "- [ ] 1.5 WAV 物理格式 / 时长 — Mix_A 短 47 帧" in text
    assert "- [x] 2.3 混音台 session 1(分轨 + 总轨)" in text
    assert text.count("- [x] ") == 2
    assert text.count("- [ ] ") == 16


def test_song_scoped_shared_across_calls(tmp_cache):
    """Scope = song:同一首歌的多次 read/update 共享一份 md(不再 keyed by chat_id)。"""
    state_tree.init_state_tree("歌_A_人")
    state_tree.update_state_tree("歌_A_人", "1.1", done=True, note="from chat A")

    # 模拟另一个 chat 进来读 → 应该看到上一次的进度
    text = state_tree.read_state_tree("歌_A_人")
    assert "- [x] 1.1 分工表完整性 — from chat A" in text


def test_path_traversal_rejected(tmp_cache):
    with pytest.raises(state_tree.StateTreeError, match="非法字符"):
        state_tree.init_state_tree("../escape")
    with pytest.raises(state_tree.StateTreeError, match="非法字符"):
        state_tree.init_state_tree("with/slash")


def test_update_flip_back_to_undone(tmp_cache):
    """[x] → [ ]:1.7 fail 后用户重检某态时可能用到。"""
    state_tree.init_state_tree("歌_A_人")
    state_tree.update_state_tree("歌_A_人", "1.4", done=True)
    state_tree.update_state_tree("歌_A_人", "1.4", done=False, note="发现还有命名漏修")
    text = state_tree.read_state_tree("歌_A_人")
    assert "- [ ] 1.4 文件命名归一化 — 发现还有命名漏修" in text
