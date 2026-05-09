"""α-1 测试:fs/audit/fix 新增 MCP 工具 + review_log 基建。

不启 MCP server stdio,直接调底层 Python 函数 —— @mcp.tool() 装饰器保留 callable。
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from sidecar import fixers, mcp_server, review_log


# ============================================================
#  fixtures
# ============================================================


@pytest.fixture
def tmp_workspace():
    d = tempfile.mkdtemp(prefix="alpha1_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def tmp_review_log(monkeypatch, tmp_path):
    """重定向 review_log 文件到 tmp_path,避免污染真 cache/review_log.jsonl。"""
    log = tmp_path / "review_log.jsonl"
    monkeypatch.setattr(review_log, "_log_path", lambda: log)
    return log


# ============================================================
#  review_log
# ============================================================


def test_review_log_append_and_iter(tmp_review_log):
    review_log.append(
        chat_id="c1", song="望春风", state="1.4",
        result="pass", summary="改了 3 个文件名", details={"renamed": 3},
    )
    review_log.append(
        chat_id="c1", song="望春风", state="1.7",
        result="pass", summary="复检干净",
    )
    entries = list(review_log.iter_entries())
    assert len(entries) == 2
    assert entries[0]["state"] == "1.4"
    assert entries[1]["state"] == "1.7"
    assert entries[0]["details"] == {"renamed": 3}
    # timestamp 自动填且是 ISO
    assert entries[0]["timestamp"].endswith("Z")


def test_review_log_get_prior_review_filters_song(tmp_review_log):
    review_log.append(chat_id="c1", song="望春风", state="1.4", result="pass")
    review_log.append(chat_id="c1", song="月亮代表我的心", state="1.4", result="fail")
    review_log.append(chat_id="c2", song="望春风", state="1.7", result="pass")

    out = review_log.get_prior_review("望春风")
    assert len(out) == 2
    assert {e["song"] for e in out} == {"望春风"}
    # 时序倒序(最新在前):c2/1.7 在 c1/1.4 之前
    assert out[0]["state"] == "1.7"


def test_review_log_get_prior_review_chat_filter(tmp_review_log):
    review_log.append(chat_id="c1", song="望春风", state="1.4", result="pass")
    review_log.append(chat_id="c2", song="望春风", state="1.4", result="fail")

    chat1 = review_log.get_prior_review("望春风", chat_id="c1")
    assert len(chat1) == 1
    assert chat1[0]["chat_id"] == "c1"


def test_review_log_iter_skips_corrupt_lines(tmp_review_log):
    tmp_review_log.parent.mkdir(parents=True, exist_ok=True)
    tmp_review_log.write_text(
        '{"chat_id":"c","song":"a","state":"1.1","result":"pass","summary":"","details":{},"timestamp":"2026-01-01T00:00:00Z"}\n'
        'NOT JSON\n'
        '{"chat_id":"c","song":"b","state":"1.2","result":"pass","summary":"","details":{},"timestamp":"2026-01-02T00:00:00Z"}\n',
        encoding="utf-8",
    )
    entries = list(review_log.iter_entries())
    assert len(entries) == 2
    assert {e["song"] for e in entries} == {"a", "b"}


def test_review_log_empty_file(tmp_review_log):
    assert list(review_log.iter_entries()) == []
    assert review_log.get_prior_review("望春风") == []


def test_review_log_explicit_timestamp_preserved(tmp_review_log):
    review_log.append(
        chat_id="c", song="x", state="1.1", result="pass",
        timestamp="2026-05-09T10:00:00Z",
    )
    entries = list(review_log.iter_entries())
    assert entries[0]["timestamp"] == "2026-05-09T10:00:00Z"


# ============================================================
#  fs_song_exists
# ============================================================


def test_fs_song_exists_true(tmp_workspace):
    song_dir = os.path.join(tmp_workspace, "歌手_望春风_扒谱者")
    os.makedirs(song_dir)
    out = mcp_server.fs_song_exists(tmp_workspace, "歌手_望春风_扒谱者")
    assert out["exists"] is True
    assert out["song_path"] == song_dir


def test_fs_song_exists_false(tmp_workspace):
    out = mcp_server.fs_song_exists(tmp_workspace, "不存在的歌")
    assert out["exists"] is False


def test_fs_song_exists_path_is_file_returns_false(tmp_workspace):
    """同名 file 不算"歌存在"——只接受目录。"""
    p = os.path.join(tmp_workspace, "fake")
    Path(p).write_text("x", encoding="utf-8")
    out = mcp_server.fs_song_exists(tmp_workspace, "fake")
    assert out["exists"] is False


# ============================================================
#  audit_run_workspace_check
# ============================================================


def test_audit_run_workspace_check_aggregates_by_song(tmp_workspace):
    """两首"歌"(空目录)→ 每首会被 checker 报缺一堆东西。"""
    for name in ["歌手_A_扒谱者", "歌手_B_扒谱者"]:
        os.makedirs(os.path.join(tmp_workspace, name))
    out = mcp_server.audit_run_workspace_check(tmp_workspace)
    assert "by_song" in out
    assert "total_errors" in out
    assert out["total_errors"] > 0
    # 每首歌都该有自己的 bucket
    song_keys = set(out["by_song"].keys())
    assert any("歌手_A_扒谱者" in k for k in song_keys)
    assert any("歌手_B_扒谱者" in k for k in song_keys)


def test_audit_run_workspace_check_empty_workspace(tmp_workspace):
    out = mcp_server.audit_run_workspace_check(tmp_workspace)
    assert out["by_song"] == {}
    assert out["total_errors"] == 0


# ============================================================
#  audit_get_prior_review
# ============================================================


def test_audit_get_prior_review_returns_entries(tmp_review_log):
    review_log.append(chat_id="c1", song="望春风", state="1.4", result="pass", summary="ok")
    out = mcp_server.audit_get_prior_review("望春风")
    assert "entries" in out
    assert len(out["entries"]) == 1
    assert out["entries"][0]["state"] == "1.4"


def test_audit_get_prior_review_no_history(tmp_review_log):
    out = mcp_server.audit_get_prior_review("从未见过")
    assert out["entries"] == []


# ============================================================
#  fix_propose_csv_header_rewrite
# ============================================================


def test_csv_header_rewrite_beat_wrong_case(tmp_workspace):
    p = os.path.join(tmp_workspace, "song_Beat.csv")
    Path(p).write_text("time,label\n00:00,intro\n", encoding="utf-8")
    out = mcp_server.fix_propose_csv_header_rewrite(p)
    assert len(out["ops"]) == 1
    op = out["ops"][0]
    assert op["type"] == "write_text"
    assert op["path"] == p
    assert op["content"].startswith("TIME,LABEL\n")
    assert "00:00,intro" in op["content"]


def test_csv_header_rewrite_beat_already_correct(tmp_workspace):
    p = os.path.join(tmp_workspace, "song_Beat.csv")
    Path(p).write_text("TIME,LABEL\n00:00,intro\n", encoding="utf-8")
    out = mcp_server.fix_propose_csv_header_rewrite(p)
    assert out["ops"] == []
    assert "已正确" in out["skipped"]


def test_csv_header_rewrite_instr_map_csv(tmp_workspace):
    p = os.path.join(tmp_workspace, "乐器音源对照表.csv")
    Path(p).write_text("instrument,source\n钢琴1,Kontakt\n", encoding="utf-8")
    out = mcp_server.fix_propose_csv_header_rewrite(p)
    assert len(out["ops"]) == 1
    assert out["ops"][0]["content"].startswith("乐器,音源\n")


def test_csv_header_rewrite_structure_skips(tmp_workspace):
    """Structure.csv 表头是内容驱动,无法自动修。"""
    p = os.path.join(tmp_workspace, "song_Structure.csv")
    Path(p).write_text("intro,verse\n00:00,00:30\n", encoding="utf-8")
    out = mcp_server.fix_propose_csv_header_rewrite(p)
    assert out["ops"] == []
    assert "Structure" in out["skipped"]


def test_csv_header_rewrite_unknown_type_skips(tmp_workspace):
    p = os.path.join(tmp_workspace, "random.csv")
    Path(p).write_text("a,b\n1,2\n", encoding="utf-8")
    out = mcp_server.fix_propose_csv_header_rewrite(p)
    assert out["ops"] == []
    assert "未知 CSV 类型" in out["skipped"]


def test_csv_header_rewrite_file_not_found(tmp_workspace):
    p = os.path.join(tmp_workspace, "nonexistent_Beat.csv")
    out = mcp_server.fix_propose_csv_header_rewrite(p)
    assert out["ok"] is False
    assert out["code"] == "FILE_NOT_FOUND"


# ============================================================
#  fix_propose_csv_time_zero_pad
# ============================================================


def test_csv_time_zero_pad_fixes_single_digits(tmp_workspace):
    p = os.path.join(tmp_workspace, "song_Beat.csv")
    Path(p).write_text("TIME,LABEL\n0:5,intro\n1:30,verse\n0:0,outro\n", encoding="utf-8")
    out = mcp_server.fix_propose_csv_time_zero_pad(p)
    assert out["fixes"] == 3  # 0:5 / 1:30 / 0:0
    op = out["ops"][0]
    assert "00:05,intro" in op["content"]
    assert "01:30,verse" in op["content"]
    assert "00:00,outro" in op["content"]


def test_csv_time_zero_pad_already_correct(tmp_workspace):
    p = os.path.join(tmp_workspace, "song_Beat.csv")
    Path(p).write_text("TIME,LABEL\n00:05,intro\n01:30,verse\n", encoding="utf-8")
    out = mcp_server.fix_propose_csv_time_zero_pad(p)
    assert out["fixes"] == 0
    assert out["ops"] == []


def test_csv_time_zero_pad_no_times_in_file(tmp_workspace):
    p = os.path.join(tmp_workspace, "乐器音源对照表.csv")
    Path(p).write_text("乐器,音源\n钢琴1,Kontakt\n", encoding="utf-8")
    out = mcp_server.fix_propose_csv_time_zero_pad(p)
    assert out["fixes"] == 0
    assert out["ops"] == []


def test_csv_time_zero_pad_structure_csv(tmp_workspace):
    p = os.path.join(tmp_workspace, "song_Structure.csv")
    Path(p).write_text(
        "Intro,Verse,Chorus\n0:2,0:37,1:33\n",
        encoding="utf-8",
    )
    out = mcp_server.fix_propose_csv_time_zero_pad(p)
    assert out["fixes"] == 3  # 0:2 / 0:37 / 1:33
    assert "00:02" in out["ops"][0]["content"]
    assert "00:37" in out["ops"][0]["content"]
    assert "01:33" in out["ops"][0]["content"]


def test_csv_time_zero_pad_file_not_found(tmp_workspace):
    p = os.path.join(tmp_workspace, "nonexistent.csv")
    out = mcp_server.fix_propose_csv_time_zero_pad(p)
    assert out["ok"] is False
    assert out["code"] == "FILE_NOT_FOUND"


# ============================================================
#  fix.execute_ops with write_text op
# ============================================================


def test_execute_ops_write_text_csv(tmp_workspace):
    p = os.path.join(tmp_workspace, "test.csv")
    Path(p).write_text("old,content\n", encoding="utf-8")
    result = fixers.execute_ops(
        [{"type": "write_text", "path": p, "content": "new,content\nrow2,here\n"}],
        workspace_root=tmp_workspace,
    )
    assert result.errors == []
    assert len(result.executed) == 1
    assert result.executed[0]["type"] == "write_text"
    assert Path(p).read_text(encoding="utf-8") == "new,content\nrow2,here\n"


def test_execute_ops_write_text_creates_new_file(tmp_workspace):
    """写不存在的文件:父目录在工作区内 → 直接创建。"""
    p = os.path.join(tmp_workspace, "new_file.txt")
    result = fixers.execute_ops(
        [{"type": "write_text", "path": p, "content": "hello"}],
        workspace_root=tmp_workspace,
    )
    assert result.errors == []
    assert Path(p).read_text(encoding="utf-8") == "hello"


def test_execute_ops_write_text_rejects_wav_ext(tmp_workspace):
    """write_text 拒绝 .wav 扩展名(脑暴 §8 边界:agent 不写音频二进制)。"""
    p = os.path.join(tmp_workspace, "audio.wav")
    Path(p).write_text("placeholder", encoding="utf-8")
    result = fixers.execute_ops(
        [{"type": "write_text", "path": p, "content": "fake audio"}],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    assert any(".wav" in e for e in result.errors)
    # 整批 fail-fast → 文件没动
    assert Path(p).read_text(encoding="utf-8") == "placeholder"


def test_execute_ops_write_text_rejects_mid_ext(tmp_workspace):
    p = os.path.join(tmp_workspace, "song.mid")
    Path(p).write_text("placeholder", encoding="utf-8")
    result = fixers.execute_ops(
        [{"type": "write_text", "path": p, "content": "fake midi"}],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    assert any(".mid" in e for e in result.errors)


def test_execute_ops_write_text_rejects_outside_workspace(tmp_workspace):
    bad = "/etc/passwd" if os.name != "nt" else "C:/Windows/notepad.exe"
    result = fixers.execute_ops(
        [{"type": "write_text", "path": bad, "content": "evil"}],
        workspace_root=tmp_workspace,
    )
    assert result.executed == []
    # 越界 / 扩展名两关之一会拒;两边都不该让它过
    assert len(result.errors) >= 1


def test_execute_ops_write_text_in_mixed_batch(tmp_workspace):
    """rename + write_text 混合一批,顺序执行。"""
    csv_path = os.path.join(tmp_workspace, "old_name.csv")
    Path(csv_path).write_text("a,b\n", encoding="utf-8")
    new_csv = os.path.join(tmp_workspace, "new_name.csv")

    ops = [
        {"type": "rename", "src": csv_path, "dst": new_csv},
        {"type": "write_text", "path": new_csv, "content": "TIME,LABEL\n00:00,intro\n"},
    ]
    result = fixers.execute_ops(ops, workspace_root=tmp_workspace)
    # 注:write_text 不消费 path_updates,必须传新路径(LLM 自构造时要注意)
    assert result.errors == []
    assert len(result.executed) == 2
    assert Path(new_csv).read_text(encoding="utf-8") == "TIME,LABEL\n00:00,intro\n"
