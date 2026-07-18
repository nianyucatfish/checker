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


def test_review_log_path_uses_log_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKER_LOG_DIR", str(tmp_path))
    assert review_log._log_path() == tmp_path / "review_log.jsonl"


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
#  fs_list_dir
# ============================================================


def test_fs_list_dir_default_depth_two(tmp_workspace):
    song = os.path.join(tmp_workspace, "歌手_望春风_扒谱者")
    sub = os.path.join(song, "分轨wav")
    os.makedirs(sub)
    Path(os.path.join(sub, "Vocal_A.wav")).write_text("x", encoding="utf-8")
    out = mcp_server.fs_list_dir(song)
    assert out["name"] == "歌手_望春风_扒谱者"
    dir_names = [d["name"] for d in out["dirs"]]
    assert "分轨wav" in dir_names
    sub_node = next(d for d in out["dirs"] if d["name"] == "分轨wav")
    assert any(f["name"] == "Vocal_A.wav" for f in sub_node["files"])


def test_fs_list_dir_truncated_beyond_depth(tmp_workspace):
    deep = os.path.join(tmp_workspace, "a", "b", "c")
    os.makedirs(deep)
    out = mcp_server.fs_list_dir(tmp_workspace, max_depth=2)
    a = next(d for d in out["dirs"] if d["name"] == "a")
    b = next(d for d in a["dirs"] if d["name"] == "b")
    assert b.get("truncated") is True


def test_fs_list_dir_missing_path_returns_error(tmp_workspace):
    out = mcp_server.fs_list_dir(os.path.join(tmp_workspace, "nope"))
    assert "error" in out


# ============================================================
#  audit_list_errors
# ============================================================


def test_audit_list_errors_empty_song_reports_missing(tmp_workspace):
    """空 song folder → 报一堆 MISSING_DIR / MISSING_FILE。"""
    song = os.path.join(tmp_workspace, "歌手_A_扒谱者")
    os.makedirs(song)
    out = mcp_server.audit_list_errors(song)
    assert out["by_code"]  # 至少有错
    # MISSING_FILE 错误项应该有 candidates 字段(虽然此时全空)
    for e in out["errors"]:
        if e["code"] == "MISSING_FILE":
            assert "candidates" in e
            assert e["candidates"] == []


def test_audit_list_errors_candidates_finds_orphan_in_sibling_song(tmp_workspace):
    """缺文件错误,工作区另一首歌里有同名 → 候选清单标 other_song。"""
    song = os.path.join(tmp_workspace, "歌手_A_扒谱者")
    for d in ("分轨wav", "总轨wav", "midi", "csv", "混音工程原文件"):
        os.makedirs(os.path.join(song, d))
    # 在另一首歌里放一份 A_Vocal_A.wav(checker 期望的命名带歌曲名前缀)
    sibling = os.path.join(tmp_workspace, "歌手_B_扒谱者", "分轨wav")
    os.makedirs(sibling)
    orphan = os.path.join(sibling, "A_Vocal_A.wav")
    open(orphan, "wb").close()

    out = mcp_server.audit_list_errors(song)
    missing_vocal = [
        e for e in out["errors"]
        if e["code"] == "MISSING_FILE" and e.get("expected", {}).get("filename") == "A_Vocal_A.wav"
    ]
    assert missing_vocal, "应该报 A_Vocal_A.wav 缺失"
    cand = missing_vocal[0]["candidates"]
    assert len(cand) == 1
    assert cand[0]["scope"] == "other_song"
    assert cand[0]["path"] == orphan


def test_audit_list_errors_candidates_finds_orphan_in_wrong_subdir(tmp_workspace):
    """同首歌错的子目录里有同名文件 → 候选清单标 this_song。"""
    song = os.path.join(tmp_workspace, "歌手_A_扒谱者")
    for d in ("分轨wav", "总轨wav", "midi", "csv", "混音工程原文件"):
        os.makedirs(os.path.join(song, d))
    # A_Vocal_A.wav 跑到了 csv/ 下(放错位置)
    wrong = os.path.join(song, "csv", "A_Vocal_A.wav")
    open(wrong, "wb").close()

    out = mcp_server.audit_list_errors(song)
    missing_vocal = [
        e for e in out["errors"]
        if e["code"] == "MISSING_FILE" and e.get("expected", {}).get("filename") == "A_Vocal_A.wav"
    ]
    assert missing_vocal
    cand = missing_vocal[0]["candidates"]
    assert any(c["scope"] == "this_song" and c["path"] == wrong for c in cand)


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


# ============================================================
#  fixers.simulate_ops (dry-run validation)
# ============================================================


def test_simulate_basic_rename(tmp_workspace):
    p = os.path.join(tmp_workspace, "a.csv")
    Path(p).write_text("x", encoding="utf-8")
    sim = fixers.simulate_ops(
        [{"type": "rename", "src": p, "dst": os.path.join(tmp_workspace, "b.csv")}],
        workspace_root=tmp_workspace,
    )
    assert sim.would_conflict == []
    assert len(sim.would_execute) == 1
    assert sim.would_execute[0]["type"] == "rename"


def test_simulate_detects_dst_exists(tmp_workspace):
    src = os.path.join(tmp_workspace, "a.csv")
    dst = os.path.join(tmp_workspace, "b.csv")
    Path(src).write_text("x", encoding="utf-8")
    Path(dst).write_text("y", encoding="utf-8")
    sim = fixers.simulate_ops(
        [{"type": "rename", "src": src, "dst": dst}],
        workspace_root=tmp_workspace,
    )
    assert len(sim.would_conflict) == 1
    assert sim.would_conflict[0]["code"] == "DST_EXISTS"


def test_simulate_detects_src_missing(tmp_workspace):
    sim = fixers.simulate_ops(
        [{"type": "delete", "path": os.path.join(tmp_workspace, "nope.csv")}],
        workspace_root=tmp_workspace,
    )
    assert len(sim.would_conflict) == 1
    assert sim.would_conflict[0]["code"] == "SRC_MISSING"


def test_simulate_detects_path_outside_workspace(tmp_workspace):
    sim = fixers.simulate_ops(
        [{"type": "delete", "path": "/etc/passwd" if os.name != "nt" else "C:/Windows/notepad.exe"}],
        workspace_root=tmp_workspace,
    )
    assert len(sim.would_conflict) == 1
    assert sim.would_conflict[0]["code"] == "PATH_OUTSIDE_WORKSPACE"


def test_simulate_detects_ext_not_allowed(tmp_workspace):
    sim = fixers.simulate_ops(
        [{"type": "write_text", "path": os.path.join(tmp_workspace, "x.wav"), "content": "fake"}],
        workspace_root=tmp_workspace,
    )
    assert len(sim.would_conflict) == 1
    assert sim.would_conflict[0]["code"] == "EXT_NOT_ALLOWED"


def test_simulate_chained_rename_then_move(tmp_workspace):
    """先 rename a→b,再 move b 到 sub/。simulate 应理解链式状态。"""
    a = os.path.join(tmp_workspace, "a.csv")
    b = os.path.join(tmp_workspace, "b.csv")
    sub = os.path.join(tmp_workspace, "sub")
    os.makedirs(sub)
    Path(a).write_text("x", encoding="utf-8")
    sim = fixers.simulate_ops(
        [
            {"type": "rename", "src": a, "dst": b},
            {"type": "move", "src": a, "dst_dir": sub},  # 用原始 src,simulate 通过 predicted_path_updates 解
        ],
        workspace_root=tmp_workspace,
    )
    assert sim.would_conflict == []
    assert len(sim.would_execute) == 2


# ============================================================
#  fix_execute_plan: simulate + auto-mode gating + diff echo
# ============================================================


@pytest.fixture
def reset_simulate_cache():
    """每个测试前清空 simulate cache,避免互相影响。"""
    mcp_server._simulate_cache.clear()
    yield
    mcp_server._simulate_cache.clear()


@pytest.fixture
def auto_mode(monkeypatch):
    """临时把 execution_mode 切到 auto。"""
    from sidecar import config
    monkeypatch.setattr(
        config, "get_config",
        lambda: config.Config(preferences=config.PreferencesConfig(execution_mode="auto"))
    )
    # mcp_server import 时 from sidecar.config import get_config,模块内绑定也要 patch
    monkeypatch.setattr(
        mcp_server, "get_config",
        lambda: config.Config(preferences=config.PreferencesConfig(execution_mode="auto"))
    )


def test_fix_execute_plan_simulate_returns_preview(tmp_workspace, reset_simulate_cache):
    p = os.path.join(tmp_workspace, "a.csv")
    Path(p).write_text("x", encoding="utf-8")
    ops = [{"type": "rename", "src": p, "dst": os.path.join(tmp_workspace, "b.csv")}]
    out = mcp_server.fix_execute_plan(ops, workspace_root=tmp_workspace, simulate=True)
    assert out["simulated"] is True
    assert len(out["would_execute"]) == 1
    assert "ops_hash" in out
    # simulate 不碰磁盘
    assert os.path.exists(p)


def test_fix_execute_plan_auto_mode_rejects_without_simulate(tmp_workspace, reset_simulate_cache, auto_mode):
    p = os.path.join(tmp_workspace, "a.csv")
    Path(p).write_text("x", encoding="utf-8")
    out = mcp_server.fix_execute_plan(
        [{"type": "rename", "src": p, "dst": os.path.join(tmp_workspace, "b.csv")}],
        workspace_root=tmp_workspace,
    )
    assert out["ok"] is False
    assert out["code"] == "SIMULATE_REQUIRED"
    # 文件没动
    assert os.path.exists(p)


def test_fix_execute_plan_auto_mode_accepts_after_simulate(tmp_workspace, reset_simulate_cache, auto_mode):
    p = os.path.join(tmp_workspace, "a.csv")
    Path(p).write_text("x", encoding="utf-8")
    ops = [{"type": "rename", "src": p, "dst": os.path.join(tmp_workspace, "b.csv")}]
    mcp_server.fix_execute_plan(ops, workspace_root=tmp_workspace, simulate=True)
    out = mcp_server.fix_execute_plan(ops, workspace_root=tmp_workspace, simulate=False)
    assert "executed" in out
    assert len(out["executed"]) == 1
    assert not os.path.exists(p)
    assert os.path.exists(os.path.join(tmp_workspace, "b.csv"))


def test_fix_execute_plan_confirm_mode_no_simulate_required(tmp_workspace, reset_simulate_cache):
    """confirm 模式(默认)不查 simulate 集合 —— 用户卡片是 gate,不再二次校验。"""
    p = os.path.join(tmp_workspace, "a.csv")
    Path(p).write_text("x", encoding="utf-8")
    out = mcp_server.fix_execute_plan(
        [{"type": "rename", "src": p, "dst": os.path.join(tmp_workspace, "b.csv")}],
        workspace_root=tmp_workspace,
        simulate=False,
    )
    assert "executed" in out
    assert len(out["executed"]) == 1


def test_fix_execute_plan_auto_mode_hash_mismatch_rejected(tmp_workspace, reset_simulate_cache, auto_mode):
    """auto 模式:simulate 过 op A,但 execute 时换成 op B —— hash 不一致 → 拒。"""
    a = os.path.join(tmp_workspace, "a.csv")
    Path(a).write_text("x", encoding="utf-8")
    mcp_server.fix_execute_plan(
        [{"type": "rename", "src": a, "dst": os.path.join(tmp_workspace, "b.csv")}],
        workspace_root=tmp_workspace,
        simulate=True,
    )
    # execute 时改了 dst
    out = mcp_server.fix_execute_plan(
        [{"type": "rename", "src": a, "dst": os.path.join(tmp_workspace, "c.csv")}],
        workspace_root=tmp_workspace,
        simulate=False,
    )
    assert out.get("code") == "SIMULATE_REQUIRED"


# ============================================================
#  read_text_file
# ============================================================


def test_read_text_file_full(tmp_workspace):
    p = os.path.join(tmp_workspace, "small.csv")
    Path(p).write_text("TIME,LABEL\n00:01,Intro\n00:30,Verse\n", encoding="utf-8")
    out = mcp_server.read_text_file(p)
    assert out["total_lines"] == 3
    assert out["truncated"] is False
    assert "00:01,Intro" in out["content"]


def test_read_text_file_missing(tmp_workspace):
    out = mcp_server.read_text_file(os.path.join(tmp_workspace, "nope.csv"))
    assert out["ok"] is False
    assert out["code"] == "FILE_NOT_FOUND"


def test_read_text_file_line_range(tmp_workspace):
    p = os.path.join(tmp_workspace, "beat.csv")
    Path(p).write_text("\n".join(f"00:{i:02d},X" for i in range(20)) + "\n", encoding="utf-8")
    out = mcp_server.read_text_file(p, line_range=[5, 8])
    assert out["truncated"] is True
    assert out["line_range"] == [5, 8]
    assert out["total_lines"] == 20
    # 1-based: 第 5-8 行,对应 "00:04" 到 "00:07"
    assert "00:04,X" in out["content"]
    assert "00:07,X" in out["content"]
    assert "00:03,X" not in out["content"]
    assert "00:08,X" not in out["content"]


def test_read_text_file_auto_truncates_large(tmp_workspace):
    p = os.path.join(tmp_workspace, "big.csv")
    # 100 行 × ~100 字节 = ~10KB,超过 8KB 阈值
    Path(p).write_text("\n".join(f"row {i}: " + "x" * 100 for i in range(100)) + "\n", encoding="utf-8")
    out = mcp_server.read_text_file(p)
    assert out["truncated"] is True
    assert out["total_lines"] == 100
    assert "omitted_lines" in out
    assert "[omitted" in out["content"]
    # head + tail 都在
    assert "row 0:" in out["content"]
    assert "row 99:" in out["content"]
    # 中段被省
    assert "row 50:" not in out["content"]


def test_read_text_file_bad_range_returns_error(tmp_workspace):
    p = os.path.join(tmp_workspace, "a.txt")
    Path(p).write_text("hi\n", encoding="utf-8")
    out = mcp_server.read_text_file(p, line_range=[1, 2, 3])  # 错误格式
    assert out["ok"] is False
    assert out["code"] == "READ_FAILED"


def test_read_text_file_utf8_bom_stripped(tmp_workspace):
    p = os.path.join(tmp_workspace, "bom.csv")
    # 写 utf-8 BOM + 内容
    with open(p, "wb") as f:
        f.write(b"\xef\xbb\xbfTIME,LABEL\n")
    out = mcp_server.read_text_file(p)
    assert out["content"].startswith("TIME,LABEL")  # 没有 BOM
